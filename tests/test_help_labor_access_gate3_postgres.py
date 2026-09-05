import base64
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database import Base
from dependencies import ActorOrgContext
import models
from services.help_crypto import HelpDataCipher
from services.help_offers import create_labor_offer
from services.help_labor_access import (
    LaborAccessError,
    cancel_labor_offer,
    expire_labor_offer,
    get_labor_offer_context,
    issue_labor_access_link,
    list_labor_offers_for_moderation,
    redeem_labor_access_link,
    decide_labor_offer,
)


TEST_DATABASE_URL = __import__("os").getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DATABASE_URL, reason="disposable PostgreSQL is required")

SENSITIVE = {
    "tipo_trabajo": "Cuidador",
    "descripcion": "DESCRIPCION_GATE3_CENTINELA",
    "remuneracion_estimada": "REMUNERACION_GATE3_CENTINELA",
    "telefono": "+58-GATE3-TELEFONO",
    "correo": "contacto-gate3@example.test",
}


@pytest.fixture()
def engine(monkeypatch):
    assert urlsplit(TEST_DATABASE_URL).path.endswith("_test")
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_CASES_V2_ENABLED", "true")
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-gate3")
    value = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(value)
    Base.metadata.create_all(value)
    try:
        yield value
    finally:
        Base.metadata.drop_all(value)
        value.dispose()


@pytest.fixture()
def prepared(engine):
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    cipher = HelpDataCipher.from_environment()
    with sessions() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-gate3", nombres_apellidos_cifrado="enc-name",
            cedula_hash="c" * 64, cedula_cifrada="enc-id", creado_por="coord@example.test",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id="gate3-labor-case", organizacion_id="org-gate3",
            beneficiario_id=beneficiary.id, tipo_sujeto="persona", titulo_interno="Labor",
            categoria="empleo", acepta_ayuda_monetaria=False, acepta_ayuda_directa=False,
            estado="publicado", creado_por="coord@example.test",
        )
        db.add(case)
        db.flush()
        offer, _ = create_labor_offer(
            db, actor=ActorOrgContext("", "donor", "donor@example.test", uid="donor-uid", ip_hash="a" * 64),
            public_id=case.public_id, idempotency_key="gate3-create-0001", cipher=cipher, **SENSITIVE,
        )
        db.commit()
        return sessions, offer["id"], case.id, beneficiary.id


def coordinator():
    return ActorOrgContext("org-gate3", "coordinador", "coord@example.test", uid="coord-uid")


def donor(uid="donor-uid"):
    return ActorOrgContext("", "donor", "donor@example.test", uid=uid)


def _issue(prepared, *, now=None, token="gate3-link-token"):
    sessions, offer_id, _, _ = prepared
    with sessions() as db:
        result = issue_labor_access_link(
            db, offer_id=offer_id, subject_type="beneficiario", subject_uid="beneficiary-uid",
            issuer=coordinator(), now=now, token=token,
        )
        db.commit()
        return result


def _redeem(prepared, token="gate3-link-token", context="browser-a", now=None):
    sessions, _, _, _ = prepared
    with sessions() as db:
        result = redeem_labor_access_link(db, token=token, client_context=context, now=now)
        db.commit()
        return result


def test_link_is_reusable_for_exactly_seven_days_and_stores_only_hash(prepared):
    now = datetime.now(timezone.utc)
    issued = _issue(prepared, now=now)
    assert issued["expires_at"] == now + timedelta(days=7)
    sessions, _, _, _ = prepared
    with sessions() as db:
        challenge = db.query(models.DesafioAccesoOfertaLaboral).one()
        assert challenge.token_hash == hashlib.sha256(b"gate3-link-token").hexdigest()
        assert "gate3-link-token" not in " ".join(str(v) for v in vars(challenge).values())
    first = _redeem(prepared, now=now + timedelta(minutes=1))
    second = _redeem(prepared, now=now + timedelta(minutes=2))
    assert first["session_id"] == second["session_id"]
    assert first["expires_at"] == second["expires_at"]
    with pytest.raises(LaborAccessError) as error:
        _redeem(prepared, now=now + timedelta(days=7))
    assert error.value.code == "labor_access_unavailable"


def test_redeem_creates_new_session_after_previous_expires_and_caps_remaining_window(prepared):
    now = datetime.now(timezone.utc)
    _issue(prepared, now=now)
    first = _redeem(prepared, now=now + timedelta(hours=1))
    assert first["expires_at"] == now + timedelta(hours=25)
    second = _redeem(prepared, now=now + timedelta(hours=26))
    assert second["session_id"] != first["session_id"]
    final = _redeem(prepared, context="browser-b", now=now + timedelta(days=6, hours=23))
    assert final["expires_at"] == now + timedelta(days=7)


def test_new_link_revokes_old_and_unknown_or_altered_tokens_are_sanitized(prepared):
    _issue(prepared, token="old-link")
    old_session = _redeem(prepared, token="old-link")
    _issue(prepared, token="new-link")
    for bad in ("old-link", "new-linx", "unknown"):
        with pytest.raises(LaborAccessError) as error:
            _redeem(prepared, token=bad)
        assert error.value.code == "labor_access_unavailable"
        assert bad not in str(error.value)
    with prepared[0]() as db, pytest.raises(LaborAccessError):
        get_labor_offer_context(db, session_token=old_session["session_token"])
    assert _redeem(prepared, token="new-link")["session_id"]


def test_rate_limit_is_per_link_and_context_with_sanitized_error(prepared):
    _issue(prepared)
    for _ in range(10):
        _redeem(prepared)
    with pytest.raises(LaborAccessError) as error:
        _redeem(prepared)
    assert error.value.code == "labor_access_rate_limited"
    assert "gate3-link-token" not in str(error.value)


def test_context_is_redacted_and_never_reveals_beneficiary_or_contact(prepared):
    _issue(prepared)
    session = _redeem(prepared)
    sessions, _, _, _ = prepared
    with sessions() as db:
        context = get_labor_offer_context(db, session_token=session["session_token"])
    assert context == {
        "offer_id": prepared[1], "state": "pendiente_respuesta", "tipo_trabajo": "Cuidador",
        "descripcion": SENSITIVE["descripcion"], "remuneracion_estimada": SENSITIVE["remuneracion_estimada"],
        "cache_control": "no-store",
    }
    assert SENSITIVE["telefono"] not in json.dumps(context)


@pytest.mark.parametrize("subject_type", ["beneficiario", "representante"])
def test_authorized_subject_can_accept_and_get_contact_once(subject_type, prepared):
    sessions, offer_id, _, _ = prepared
    with sessions() as db:
        issued = issue_labor_access_link(
            db, offer_id=offer_id, subject_type=subject_type, subject_uid=f"{subject_type}-uid",
            issuer=coordinator(), token=f"{subject_type}-link",
        )
        db.commit()
    session = _redeem(prepared, token=f"{subject_type}-link")
    _redeem(prepared, token=f"{subject_type}-link", context="second-browser")
    with sessions() as db:
        result = decide_labor_offer(
            db, session_token=session["session_token"], decision="aceptada",
            idempotency_key=f"accept-{subject_type}-0001",
        )
        db.commit()
        assert result["contact"] == {"telefono": SENSITIVE["telefono"], "correo": SENSITIVE["correo"]}
        assert db.query(models.TransicionTerminalOfertaLaboral).count() == 1
        assert db.query(models.LiberacionContactoOfertaLaboral).count() == 1
        assert db.query(models.NotificacionCasoAyuda).count() == 1
        assert all(row.revoked_at is not None for row in db.query(models.SesionOfertaLaboral).all())
        ledger = db.query(models.LiberacionContactoOfertaLaboral).one()
        assert not ({"contacto", "correo", "telefono", "descripcion", "remuneracion", "payload"} & set(vars(ledger)))
    with pytest.raises(LaborAccessError):
        _redeem(prepared, token=f"{subject_type}-link")


def test_reject_has_no_ledger_and_idempotent_retry_has_no_duplicate(prepared):
    _issue(prepared)
    session = _redeem(prepared)
    sessions, _, _, _ = prepared
    with sessions() as db:
        first = decide_labor_offer(db, session_token=session["session_token"], decision="rechazada", idempotency_key="reject-gate3-0001")
        db.commit()
    with sessions() as db:
        second = decide_labor_offer(db, session_token=session["session_token"], decision="rechazada", idempotency_key="reject-gate3-0001")
        db.commit()
        assert first["state"] == second["state"] == "rechazada"
        assert db.query(models.TransicionTerminalOfertaLaboral).count() == 1
        assert db.query(models.LiberacionContactoOfertaLaboral).count() == 0
        assert db.query(models.NotificacionCasoAyuda).count() == 1


def test_idempotency_key_conflicts_when_decision_changes(prepared):
    _issue(prepared)
    session = _redeem(prepared)
    sessions, _, _, _ = prepared
    with sessions() as db:
        decide_labor_offer(db, session_token=session["session_token"], decision="rechazada", idempotency_key="decision-conflict-0001")
        db.commit()
    with sessions() as db, pytest.raises(LaborAccessError) as error:
        decide_labor_offer(db, session_token=session["session_token"], decision="aceptada", idempotency_key="decision-conflict-0001")
    assert error.value.code == "idempotency_conflict"


def test_donor_only_cancels_own_offer_and_moderation_uses_closed_reason(prepared):
    sessions, offer_id, _, _ = prepared
    with sessions() as db, pytest.raises(LaborAccessError):
        cancel_labor_offer(db, offer_id=offer_id, actor=donor("other"), reason="donor_voluntaria", idempotency_key="cancel-other-0001")
    with sessions() as db:
        result = cancel_labor_offer(db, offer_id=offer_id, actor=coordinator(), reason="contenido_inapropiado", idempotency_key="cancel-mod-000001")
        db.commit()
        assert result["state"] == "cancelada"


def test_owner_donor_can_cancel_voluntarily(prepared):
    sessions, offer_id, _, _ = prepared
    with sessions() as db:
        result = cancel_labor_offer(
            db, offer_id=offer_id, actor=donor(), reason="donor_voluntaria",
            idempotency_key="owner-cancel-000001",
        )
        db.commit()
        assert result["state"] == "cancelada"
        transition = db.query(models.TransicionTerminalOfertaLaboral).one()
        assert transition.actor_tipo == "donante"
        assert transition.razon_codigo == "donor_voluntaria"


@pytest.mark.parametrize("role", ["coordinador", "admin", "super_admin"])
def test_each_moderation_role_cancels_with_closed_reason(role, prepared):
    sessions, offer_id, _, _ = prepared
    organization = "" if role == "super_admin" else "org-gate3"
    actor = ActorOrgContext(organization, role, f"{role}@example.test", uid=f"{role}-uid")
    with sessions() as db:
        result = cancel_labor_offer(
            db, offer_id=offer_id, actor=actor, reason="riesgo_privacidad",
            idempotency_key=f"moderate-{role}-00001",
        )
        db.commit()
        assert result["state"] == "cancelada"
        assert db.query(models.LiberacionContactoOfertaLaboral).count() == 0


def test_delegate_cannot_issue_or_moderate_and_case_manager_never_gets_contact(prepared):
    sessions, offer_id, _, _ = prepared
    delegate = ActorOrgContext("org-gate3", "acopio", "delegate@example.test", uid="delegate-uid")
    with sessions() as db, pytest.raises(LaborAccessError):
        issue_labor_access_link(db, offer_id=offer_id, subject_type="beneficiario", subject_uid="beneficiary-uid", issuer=delegate)
    with sessions() as db, pytest.raises(LaborAccessError):
        cancel_labor_offer(db, offer_id=offer_id, actor=delegate, reason="contenido_inapropiado", idempotency_key="delegate-cancel-01")


def test_coord_admin_and_super_admin_only_receive_redacted_moderation_projection(prepared):
    sessions, offer_id, _, _ = prepared
    actors = (
        coordinator(),
        ActorOrgContext("org-gate3", "admin", "admin@example.test", uid="admin-uid"),
        ActorOrgContext("", "super_admin", "root@example.test", uid="root-uid"),
    )
    for actor in actors:
        with sessions() as db:
            rows = list_labor_offers_for_moderation(db, actor=actor)
            db.commit()
            assert rows[0]["offer_id"] == offer_id
            serialized = json.dumps(rows, default=str)
            assert not any(value in serialized for value in SENSITIVE.values())


def test_plain_tokens_and_sensitive_payload_never_reach_persistence_or_errors(prepared):
    _issue(prepared)
    session = _redeem(prepared)
    sessions, _, _, _ = prepared
    with sessions() as db:
        values = []
        for model in (models.DesafioAccesoOfertaLaboral, models.SesionOfertaLaboral,
                      models.AuditoriaCasoAyuda, models.NotificacionCasoAyuda,
                      models.LiberacionContactoOfertaLaboral):
            for row in db.query(model).all():
                values.extend(str(value) for key, value in vars(row).items()
                              if not key.startswith("_") and value is not None)
        persisted = " ".join(values)
    assert "gate3-link-token" not in persisted
    assert session["session_token"] not in persisted
    assert session["csrf_token"] not in persisted
    with pytest.raises(LaborAccessError) as error:
        _redeem(prepared, token="ALTERED-SECRET-TOKEN")
    assert "ALTERED-SECRET-TOKEN" not in str(error.value)


def test_expiration_requires_worker_and_is_idempotent(prepared):
    sessions, offer_id, _, _ = prepared
    with sessions() as db:
        offer = db.get(models.OfertaAyudaDirecta, offer_id)
        expired_now = offer.expires_at + timedelta(seconds=1)
    with sessions() as db, pytest.raises(LaborAccessError):
        expire_labor_offer(db, offer_id=offer_id, worker_authorized=False, now=expired_now)
    with sessions() as db:
        assert expire_labor_offer(db, offer_id=offer_id, worker_authorized=True, now=expired_now)["state"] == "vencida"
        db.commit()
    with sessions() as db:
        assert expire_labor_offer(db, offer_id=offer_id, worker_authorized=True, now=expired_now)["state"] == "vencida"
        db.commit()
        assert db.query(models.TransicionTerminalOfertaLaboral).count() == 1
        assert db.query(models.NotificacionCasoAyuda).count() == 1


@pytest.mark.parametrize("left,right", [
    ("aceptada", "rechazada"), ("aceptada", "cancelada"),
    ("aceptada", "moderada"), ("rechazada", "cancelada"),
    ("aceptada", "aceptada"), ("rechazada", "rechazada"),
])
def test_competing_terminals_leave_one_transition_ledger_and_outbox(left, right, prepared):
    _issue(prepared)
    session = _redeem(prepared)
    sessions, offer_id, _, _ = prepared
    barrier = threading.Barrier(2)

    def run(item):
        index, kind = item
        with sessions() as db:
            barrier.wait()
            try:
                if kind in {"aceptada", "rechazada"}:
                    result = decide_labor_offer(db, session_token=session["session_token"], decision=kind, idempotency_key=f"race-{kind}-{index}-000001")
                elif kind == "moderada":
                    result = cancel_labor_offer(db, offer_id=offer_id, actor=coordinator(), reason="fraude_sospechado", idempotency_key=f"race-moderate-{index}-0001")
                else:
                    result = cancel_labor_offer(db, offer_id=offer_id, actor=donor(), reason="donor_voluntaria", idempotency_key=f"race-cancel-{index}-00001")
                db.commit()
                return result["state"]
            except LaborAccessError as exc:
                db.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(run, enumerate((left, right))))
    assert sum(value in {"aceptada", "rechazada", "cancelada"} for value in outcomes) == 1
    with sessions() as db:
        assert db.query(models.TransicionTerminalOfertaLaboral).count() == 1
        assert db.query(models.LiberacionContactoOfertaLaboral).count() <= 1
        assert db.query(models.NotificacionCasoAyuda).count() == 1


def test_accept_vs_expire_has_one_terminal_ledger_and_outbox(prepared):
    base = datetime.now(timezone.utc)
    _issue(prepared, now=base)
    session = _redeem(prepared, now=base + timedelta(days=6, hours=23))
    sessions, offer_id, _, _ = prepared
    barrier = threading.Barrier(2)

    def accept():
        with sessions() as db:
            barrier.wait()
            try:
                result = decide_labor_offer(
                    db, session_token=session["session_token"], decision="aceptada",
                    idempotency_key="race-expiry-accept-01",
                    now=base + timedelta(days=7) - timedelta(microseconds=1),
                )
                db.commit()
                return result["state"]
            except LaborAccessError as exc:
                db.rollback()
                return exc.code

    def expire():
        with sessions() as db:
            barrier.wait()
            try:
                result = expire_labor_offer(
                    db, offer_id=offer_id, worker_authorized=True,
                    now=base + timedelta(days=7),
                )
                db.commit()
                return result["state"]
            except LaborAccessError as exc:
                db.rollback()
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [pool.submit(accept), pool.submit(expire)]
        values = [future.result(timeout=10) for future in outcomes]
    assert sum(value in {"aceptada", "vencida"} for value in values) == 1
    with sessions() as db:
        assert db.query(models.TransicionTerminalOfertaLaboral).count() == 1
        assert db.query(models.LiberacionContactoOfertaLaboral).count() <= 1
        assert db.query(models.NotificacionCasoAyuda).count() == 1


def test_concurrent_redeems_recover_one_session_without_business_events(prepared):
    _issue(prepared)
    sessions, _, _, _ = prepared
    barrier = threading.Barrier(2)
    def run(_):
        with sessions() as db:
            barrier.wait()
            result = redeem_labor_access_link(db, token="gate3-link-token", client_context="same-browser")
            db.commit()
            return result["session_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run, range(2)))
    assert len(set(ids)) == 1
    with sessions() as db:
        assert db.query(models.SesionOfertaLaboral).count() == 1
        assert db.query(models.TransicionTerminalOfertaLaboral).count() == 0
        assert db.query(models.NotificacionCasoAyuda).count() == 0
