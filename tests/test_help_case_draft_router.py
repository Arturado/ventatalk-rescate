import base64
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
from routers.casos_ayuda_v2 import router


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI()
app.include_router(router)


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


_current_actor = {"organizacion_id": "org-1", "role": "coordinador", "email": "coordinador@example.com"}


def override_actor():
    return ActorOrgContext(**_current_actor)


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "coordinador@example.com"
app.dependency_overrides[require_verified_case_organization_actor] = override_actor
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _current_actor.update(organizacion_id="org-1", role="coordinador", email="coordinador@example.com")
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def cipher_env(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"0" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"1" * 32).decode())
    monkeypatch.setenv("HELP_CASES_V2_ENABLED", "true")
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-1,org-2")


def persona_payload(**overrides):
    data = dict(
        category="salud",
        subject_type="persona",
        aid_modes=["monetaria"],
        beneficiary_name="Ana Perez",
        beneficiary_identity="V-11111111",
        title="Necesita tratamiento",
        story="Historia clinica resumida de la persona" * 2,
        goal_amount="100.00",
        goal_currency="USD",
    )
    data.update(overrides)
    return data


def campana_payload(**overrides):
    data = dict(
        category="insumo_recurso",
        subject_type="campana_organizacion",
        aid_modes=["directa"],
        title="Colecta de colchones",
        story="Necesitamos colchones para el centro de refugio" * 2,
        direct_request="Colchones individuales",
    )
    data.update(overrides)
    return data


def test_get_campaign_draft_detail_returns_null_beneficiary_not_500():
    headers = {"Idempotency-Key": "router-test-key-campana-detail"}
    created = client.post(
        "/api/v2/casos-ayuda/borradores", json=campana_payload(), headers=headers,
    ).json()

    response = client.get(f"/api/v2/casos-ayuda/{created['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["beneficiario"] is None


def test_list_organization_cases_includes_non_monetary_draft_without_500():
    headers = {"Idempotency-Key": "router-test-key-campana-list"}
    client.post("/api/v2/casos-ayuda/borradores", json=campana_payload(), headers=headers)

    response = client.get("/api/v2/casos-ayuda", params={"organizacion_id": "org-1"})

    assert response.status_code == 200
    body = response.json()
    non_monetary = [item for item in body if item["categoria"] == "insumo_recurso"]
    assert len(non_monetary) == 1
    assert non_monetary[0]["meta_monto"] is None
    assert non_monetary[0]["meta_moneda"] is None


def test_create_draft_requires_idempotency_key_header():
    response = client.post("/api/v2/casos-ayuda/borradores", json=persona_payload())

    assert response.status_code == 422


def test_create_draft_returns_contract_summary_and_is_idempotent():
    headers = {"Idempotency-Key": "router-test-key-000001"}
    first = client.post("/api/v2/casos-ayuda/borradores", json=persona_payload(), headers=headers)
    assert first.status_code == 201
    body = first.json()
    assert body["category"] == "salud"
    assert body["aid_modes"] == ["monetaria"]
    assert Decimal(str(body["goal_amount"])) == Decimal("100.00")

    second = client.post("/api/v2/casos-ayuda/borradores", json=persona_payload(), headers=headers)
    assert second.status_code == 201
    assert second.json()["public_id"] == body["public_id"]


def test_create_draft_conflicts_on_reused_key_with_different_payload():
    headers = {"Idempotency-Key": "router-test-key-000002"}
    client.post("/api/v2/casos-ayuda/borradores", json=persona_payload(), headers=headers)

    conflict = client.post(
        "/api/v2/casos-ayuda/borradores",
        json=persona_payload(title="Un titulo completamente distinto"),
        headers=headers,
    )

    assert conflict.status_code == 409


def test_cedula_matches_own_organization_returns_internal_summary():
    headers = {"Idempotency-Key": "router-test-key-000003"}
    client.post("/api/v2/casos-ayuda/borradores", json=persona_payload(), headers=headers)

    response = client.get(
        "/api/v2/casos-ayuda/coincidencias-cedula",
        params={"identity": "V-11111111"},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["own_organization_matches"]) == 1
    assert body["own_organization_matches"][0]["category"] == "salud"
    assert body["other_private_matches_present"] is False


def test_cedula_matches_other_organization_only_reports_boolean_signal():
    headers = {"Idempotency-Key": "router-test-key-000004"}
    client.post("/api/v2/casos-ayuda/borradores", json=persona_payload(), headers=headers)

    _current_actor.update(organizacion_id="org-2", role="coordinador", email="coordinador2@example.com")
    response = client.get(
        "/api/v2/casos-ayuda/coincidencias-cedula",
        params={"identity": "V-11111111"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["own_organization_matches"] == []
    assert body["external_public_matches"] == []
    assert body["other_private_matches_present"] is True
