import base64
from datetime import datetime, timezone
import os
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_actor_org, verify_api_key
import models
from routers.casos_ayuda_laboral import LaborNoStoreMiddleware, router
from services.help_crypto import HelpDataCipher
from services.help_offers import create_labor_offer


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="disposable PostgreSQL is required")


@pytest.fixture()
def client(monkeypatch):
    assert urlsplit(TEST_DATABASE_URL).path.endswith("_test")
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_CASES_V2_ENABLED", "true")
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-http")
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with sessions() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-http", nombres_apellidos_cifrado="enc-name",
            cedula_hash="c" * 64, cedula_cifrada="enc-id", creado_por="coord@example.test",
        )
        db.add(beneficiary); db.flush()
        case = models.CasoAyudaV2(
            public_id="labor-http-case", organizacion_id="org-http",
            beneficiario_id=beneficiary.id, tipo_sujeto="persona", titulo_interno="Labor HTTP",
            categoria="empleo", acepta_ayuda_monetaria=False, acepta_ayuda_directa=False,
            estado="publicado", creado_por="coord@example.test",
        )
        db.add(case); db.flush()
        offer, _ = create_labor_offer(
            db, actor=ActorOrgContext("", "donor", "donor@example.test", uid="donor-http", ip_hash="a" * 64),
            public_id=case.public_id, tipo_trabajo="Proyecto", descripcion="Descripcion HTTP segura",
            remuneracion_estimada="USD 100", telefono="+58 412-0000000",
            correo="job@example.test", idempotency_key="create-http-labor-01",
            cipher=HelpDataCipher.from_environment(),
        )
        db.commit(); offer_id = offer["id"]
    app = FastAPI(); app.add_middleware(LaborNoStoreMiddleware); app.include_router(router)
    actor_box = {"actor": ActorOrgContext("org-http", "coordinador", "coord@example.test", uid="coord-http")}
    def database():
        with sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[verify_api_key] = lambda: "functions"
    app.dependency_overrides[require_verified_actor_org] = lambda: actor_box["actor"]
    with TestClient(app) as value:
        yield value, offer_id, actor_box, sessions
    Base.metadata.drop_all(engine); engine.dispose()


def test_http_reusable_link_context_csrf_terminal_and_no_store(client):
    http, offer_id, _, _ = client
    issued = http.post(f"/api/v2/ofertas-laborales/{offer_id}/enlaces", json={
        "subject_type": "beneficiario", "subject_uid": "beneficiary-http",
    })
    assert issued.status_code == 201
    token = issued.json()["link_token"]
    first = http.post("/api/v2/ofertas-laborales/acceso/canjear", json={
        "link_token": token, "client_context": "context-hash-from-functions",
    })
    second = http.post("/api/v2/ofertas-laborales/acceso/canjear", json={
        "link_token": token, "client_context": "context-hash-from-functions",
    })
    assert first.json()["session_id"] == second.json()["session_id"]
    assert first.json()["expires_at"] == second.json()["expires_at"]
    session = first.json()["session_token"]; csrf = first.json()["csrf_token"]
    context = http.get("/api/v2/ofertas-laborales/acceso/contexto", headers={"x-labor-session": session})
    assert context.status_code == 200
    assert "telefono" not in context.text and "correo" not in context.text
    missing = http.post("/api/v2/ofertas-laborales/acceso/decision", headers={"x-labor-session": session}, json={
        "decision": "aceptada", "idempotency_key": "http-accept-missing-csrf",
    })
    assert missing.status_code == 403
    accepted = http.post("/api/v2/ofertas-laborales/acceso/decision", headers={
        "x-labor-session": session, "x-labor-csrf": csrf,
    }, json={"decision": "aceptada", "idempotency_key": "http-accept-valid-csrf"})
    assert accepted.status_code == 200 and accepted.json()["contact"]
    for response in (issued, first, second, context, missing, accepted):
        assert response.headers["cache-control"] == "no-store"


def test_http_rejects_bad_csrf_and_sanitizes_unknown_link(client):
    http, offer_id, _, _ = client
    unknown = http.post("/api/v2/ofertas-laborales/acceso/canjear", json={
        "link_token": "secret-unknown-token", "client_context": "ctx",
    })
    assert unknown.status_code == 401
    assert "secret-unknown-token" not in unknown.text
    issued = http.post(f"/api/v2/ofertas-laborales/{offer_id}/enlaces", json={
        "subject_type": "beneficiario", "subject_uid": "beneficiary-http",
    }).json()
    redeemed = http.post("/api/v2/ofertas-laborales/acceso/canjear", json={
        "link_token": issued["link_token"], "client_context": "ctx",
    }).json()
    bad = http.post("/api/v2/ofertas-laborales/acceso/decision", headers={
        "x-labor-session": redeemed["session_token"], "x-labor-csrf": "wrong-secret",
    }, json={"decision": "rechazada", "idempotency_key": "http-reject-bad-csrf"})
    assert bad.status_code == 403 and bad.headers["cache-control"] == "no-store"


def test_http_close_requires_csrf_and_revokes_session(client):
    http, offer_id, _, _ = client
    issued = http.post(f"/api/v2/ofertas-laborales/{offer_id}/enlaces", json={
        "subject_type": "beneficiario", "subject_uid": "beneficiary-http",
    }).json()
    redeemed = http.post("/api/v2/ofertas-laborales/acceso/canjear", json={
        "link_token": issued["link_token"], "client_context": "ctx",
    }).json()
    headers = {"x-labor-session": redeemed["session_token"]}
    assert http.post("/api/v2/ofertas-laborales/acceso/cerrar", headers=headers).status_code == 403
    closed = http.post("/api/v2/ofertas-laborales/acceso/cerrar", headers={
        **headers, "x-labor-csrf": redeemed["csrf_token"],
    })
    assert closed.status_code == 200
    assert http.get("/api/v2/ofertas-laborales/acceso/contexto", headers=headers).status_code == 401


def test_http_donor_status_and_cancellation_are_owner_scoped(client):
    http, offer_id, actor_box, _ = client
    actor_box["actor"] = ActorOrgContext("", "donor", "donor@example.test", uid="other-donor")
    assert http.get(f"/api/v2/ofertas-laborales/{offer_id}/estado").status_code == 403
    actor_box["actor"] = ActorOrgContext("", "donor", "donor@example.test", uid="donor-http")
    status = http.get(f"/api/v2/ofertas-laborales/{offer_id}/estado")
    assert status.status_code == 200 and set(status.json()) <= {"offer_id", "state", "created_at", "expires_at"}
    cancelled = http.post(f"/api/v2/ofertas-laborales/{offer_id}/cancelar", json={
        "reason": "donor_voluntaria", "idempotency_key": "http-donor-cancel-001",
    })
    assert cancelled.status_code == 200


def test_http_moderation_projection_is_redacted_and_delegate_forbidden(client):
    http, offer_id, actor_box, _ = client
    listed = http.get("/api/v2/ofertas-laborales/moderacion")
    assert listed.status_code == 200 and listed.json()[0]["offer_id"] == offer_id
    assert "job@example.test" not in listed.text and "+58" not in listed.text
    actor_box["actor"] = ActorOrgContext("org-http", "acopio", "delegate@example.test", uid="delegate")
    denied = http.get("/api/v2/ofertas-laborales/moderacion")
    assert denied.status_code == 403 and denied.headers["cache-control"] == "no-store"
    malformed = http.post("/api/v2/ofertas-laborales/acceso/canjear", content="not-json", headers={
        "content-type": "application/json",
    })
    assert malformed.status_code == 422 and malformed.headers["cache-control"] == "no-store"
