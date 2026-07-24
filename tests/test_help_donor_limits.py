from datetime import datetime, timedelta, timezone

import models
import base64
import pytest
from database import Base
from dependencies import ActorOrgContext, require_verified_actor_org
from services.help_donors import DonorRateLimitError
from test_help_donor_router import (
    TestingSessionLocal,
    accept_current_terms,
    app,
    client,
    create_published_case,
    donor,
    report_aid,
)


def _actor(uid, ip_hash):
    return ActorOrgContext(
        "", "donor", f"{uid}@example.test", uid=uid, ip_hash=ip_hash
    )


@pytest.fixture(autouse=True)
def isolated_donor_database(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    Base.metadata.drop_all(bind=TestingSessionLocal.kw["bind"])
    Base.metadata.create_all(bind=TestingSessionLocal.kw["bind"])
    yield
    Base.metadata.drop_all(bind=TestingSessionLocal.kw["bind"])


def test_account_actor_threshold_is_configurable_and_denial_is_persistent(monkeypatch):
    monkeypatch.setenv("DONOR_ACCOUNT_ACTOR_LIMIT", "1")
    public_id = create_published_case()
    accept_current_terms()

    assert client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas").status_code == 200
    denied = client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas")

    assert denied.status_code == 429
    with TestingSessionLocal() as db:
        marker = db.query(models.AuditoriaCasoAyuda).filter_by(
            accion="limite_seguridad_denegado"
        ).one()
        case = db.query(models.CasoAyudaV2).filter_by(public_id=public_id).one()
        assert marker.organizacion_id == case.organizacion_id
        assert marker.caso_id == case.id
        assert marker.motivo_codigo == "account_actor_limit"
        assert marker.actor_id == "redacted"
        assert marker.metadata_json == "{}"


def test_account_limit_window_expires(monkeypatch):
    monkeypatch.setenv("DONOR_ACCOUNT_ACTOR_LIMIT", "1")
    monkeypatch.setenv("DONOR_ACCOUNT_LIMIT_WINDOW_SECONDS", "60")
    public_id = create_published_case()
    accept_current_terms()
    assert client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas").status_code == 200
    with TestingSessionLocal() as db:
        access = db.query(models.AccesoCuentaDonante).one()
        access.accessed_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        db.commit()

    assert client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas").status_code == 200


def test_shared_ip_limit_applies_per_case(monkeypatch):
    monkeypatch.setenv("DONOR_ACCOUNT_ACTOR_LIMIT", "10")
    monkeypatch.setenv("DONOR_ACCOUNT_IP_LIMIT", "1")
    public_id = create_published_case()
    accept_current_terms()
    assert client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas").status_code == 200

    second = _actor("firebase-donor-2", "a" * 64)
    app.dependency_overrides[require_verified_actor_org] = lambda: second
    try:
        accept_current_terms()
        denied = client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas")
    finally:
        app.dependency_overrides[require_verified_actor_org] = donor

    assert denied.status_code == 429


def test_account_actor_limit_is_independent_across_cases(monkeypatch):
    monkeypatch.setenv("DONOR_ACCOUNT_ACTOR_LIMIT", "1")
    first = create_published_case(public_id="case-limit-a", identity_suffix="a")
    second = create_published_case(public_id="case-limit-b", identity_suffix="b")
    accept_current_terms()

    assert client.get(f"/api/v2/donante/casos-ayuda/{first}/cuentas").status_code == 200
    assert client.get(f"/api/v2/donante/casos-ayuda/{second}/cuentas").status_code == 200


def test_report_threshold_and_window_are_configurable(monkeypatch):
    monkeypatch.setenv("DONOR_REPORT_LIMIT", "1")
    monkeypatch.setenv("DONOR_REPORT_LIMIT_WINDOW_SECONDS", "3600")
    public_id = create_published_case()
    accept_current_terms()

    assert report_aid(public_id).status_code == 201
    denied = report_aid(public_id, key="second-report-limit-key")

    assert denied.status_code == 429
    with TestingSessionLocal() as db:
        marker = db.query(models.AuditoriaCasoAyuda).filter_by(
            accion="limite_seguridad_denegado", motivo_codigo="report_actor_limit"
        ).one()
        assert marker.actor_id == "redacted"
