import base64
from datetime import date
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
from routers.casos_ayuda_publicos import router as public_router
from routers.casos_ayuda_v2 import router
from services.help_crypto import HelpDataCipher


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
app.include_router(public_router)
actor_override = {"value": None}


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


def current_actor():
    return actor_override["value"]


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "coordinador@example.com"
app.dependency_overrides[require_verified_case_organization_actor] = current_actor
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    actor_override["value"] = ActorOrgContext(
        "org-1",
        "coordinador",
        "responsable@example.com",
        uid="coordinator-uid-1",
    )
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_aid(
    *,
    organization_id="org-1",
    responsible_email="responsable@example.com",
    goal_amount="100.00",
    reported_amount="25.00",
    reported_currency="USD",
):
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id=organization_id,
            nombres_apellidos_cifrado=cipher.encrypt("Persona", field="beneficiary.name"),
            cedula_hash=("a" if organization_id == "org-1" else "b") * 64,
            cedula_cifrada=cipher.encrypt("V-12345678", field="beneficiary.identity"),
            creado_por="creator@example.com",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id=f"public-{organization_id}",
            organizacion_id=organization_id,
            beneficiario_id=beneficiary.id,
            titulo_interno="Caso interno",
            categoria="medicamentos",
            meta_monto=Decimal(goal_amount),
            meta_moneda="USD",
            estado="publicado",
            creado_por="creator@example.com",
        )
        db.add(case)
        db.flush()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Persona",
            titulo_publico="Ayuda publicada",
            descripcion_publica="Descripcion publica suficiente",
            version=1,
            activa=True,
        ))
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="principal",
            version=1,
            tipo_titular="beneficiario",
            titular_nombre_cifrado=cipher.encrypt("Persona", field="account.holder_name"),
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda=reported_currency,
            identificador_cifrado=cipher.encrypt("BANK-PRIVATE-123", field="account.identifier"),
            responsable_nombre_cifrado=cipher.encrypt("Responsable", field="account.responsible_name"),
            responsable_email_hash=cipher.blind_index(responsible_email, purpose="responsible-email"),
            responsable_email_cifrado=cipher.encrypt(responsible_email, field="account.responsible_email"),
            consentimiento_version=1,
            estado="aprobada",
            creado_por="creator@example.com",
        )
        db.add(account)
        db.flush()
        report_operation = models.OperacionIdempotenteAyuda(
            actor_id="donor-uid-private",
            organizacion_id=organization_id,
            operacion="report_help",
            idempotency_key=f"report-key-{organization_id}-12345",
            request_hash="a" * 64,
            estado="completed",
            response_status=201,
            response_body="{}",
        )
        db.add(report_operation)
        db.flush()
        aid = models.AyudaMonetaria(
            caso_id=case.id,
            cuenta_id=account.id,
            cuenta_version=account.version,
            donante_id="donor-uid-private",
            monto_reportado=Decimal(reported_amount),
            moneda_reportada=reported_currency,
            fecha_transferencia=date(2026, 7, 20),
            referencia_cifrada=cipher.encrypt("REF-PRIVATE", field="aid.reference"),
            comentario_cifrado=cipher.encrypt("DONOR-COMMENT-PRIVATE", field="aid.comment"),
            estado="pendiente_confirmacion",
            idempotency_id=report_operation.id,
        )
        db.add(aid)
        db.flush()
        db.add(models.ComprobanteAyuda(
            ayuda_id=aid.id,
            version=1,
            storage_path=f"private/casos-ayuda/{case.id}/receipt-private.enc",
            nombre_original_cifrado=cipher.encrypt("receipt.pdf", field="receipt.original_name"),
            content_type="application/pdf",
            size_bytes=100,
            checksum_sha256="c" * 64,
            estado="vigente",
            cargado_por="donor-uid-private",
        ))
        db.commit()
        return case.id, aid.id, case.public_id


def confirmation_payload(**overrides):
    payload = {
        "received_amount": "25.00",
        "received_currency": "USD",
        "effective_date": "2026-07-20",
        "comment": "CONFIRMATION-COMMENT-PRIVATE",
    }
    payload.update(overrides)
    return payload


def confirm(case_id, aid_id, payload=None, key="confirm-help-key-12345"):
    return client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/confirmar",
        headers={"Idempotency-Key": key},
        json=payload or confirmation_payload(),
    )


def test_lists_only_safe_organization_scoped_aid_summaries():
    case_id, aid_id, _ = create_aid()

    response = client.get(f"/api/v2/casos-ayuda/{case_id}/ayudas")

    assert response.status_code == 200
    assert response.json() == [{
        "aid_id": aid_id,
        "reported_amount": "25.00",
        "reported_currency": "USD",
        "transfer_date": "2026-07-20",
        "status": "pendiente_confirmacion",
        "account_id": response.json()[0]["account_id"],
        "account_version": 1,
        "problem_type": None,
        "problem_detail": None,
        "resolution_reason": None,
        "created_at": response.json()[0]["created_at"],
    }]
    for private_value in (
        "BANK-PRIVATE-123",
        "receipt-private.enc",
        "enc:v1:",
        "donor-uid-private",
    ):
        assert private_value not in response.text


def test_assigned_responsible_confirms_pending_aid_and_progresses_once():
    case_id, aid_id, _ = create_aid()

    response = confirm(case_id, aid_id)

    assert response.status_code == 200
    assert response.json() == {
        "aid_id": aid_id,
        "status": "confirmada",
        "received_amount": "25.00",
        "received_currency": "USD",
        "goal_amount_confirmed": "25.00",
        "confirmed_help_count": 1,
        "case_status": "publicado",
        "goal_reached": False,
    }
    with TestingSessionLocal() as db:
        confirmation = db.query(models.ConfirmacionAyuda).one()
        operation = db.query(models.OperacionIdempotenteAyuda).filter_by(operacion="confirm_help").one()
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="ayuda_confirmada").one()
        assert confirmation.ayuda_id == aid_id
        assert confirmation.confirmado_por == "coordinator-uid-1"
        assert confirmation.comentario_cifrado.startswith("enc:v1:")
        assert operation.actor_id == "coordinator-uid-1"
        assert "CONFIRMATION-COMMENT-PRIVATE" not in operation.response_body
        assert "25.00" not in audit.metadata_json
        assert "CONFIRMATION-COMMENT-PRIVATE" not in audit.metadata_json


def test_same_organization_coordinator_directly_confirms_pending_aid_without_being_responsible():
    case_id, aid_id, _ = create_aid(responsible_email="other@example.com")

    response = confirm(case_id, aid_id)

    assert response.status_code == 200
    assert response.json()["status"] == "confirmada"
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, case_id)
        assert db.get(models.AyudaMonetaria, aid_id).estado == "confirmada"
        assert db.query(models.ConfirmacionAyuda).count() == 1
        assert case.monto_confirmado == Decimal("25.00")
        assert case.ayudas_confirmadas == 1


def test_coordinator_can_review_then_confirm_unassigned_aid():
    case_id, aid_id, _ = create_aid(responsible_email="other@example.com")

    reviewed = client.post(f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/en-revision")
    confirmed = confirm(case_id, aid_id)

    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "en_revision"
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "confirmada"
    with TestingSessionLocal() as db:
        review_audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="ayuda_en_revision").one()
        assert review_audit.entidad_id == str(aid_id)


def test_coordinator_reports_problem_with_encrypted_detail_without_progress():
    case_id, aid_id, _ = create_aid()
    private_detail = "TRANSFER-NOT-RECEIVED-PRIVATE-MARKER"

    response = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/problema",
        json={"problem_type": "transferencia_no_recibida", "detail": private_detail},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "problema_reportado"
    assert response.json()["problem_type"] == "transferencia_no_recibida"
    assert response.json()["problem_detail"] == private_detail
    assert response.json()["resolution_reason"] is None
    with TestingSessionLocal() as db:
        aid = db.get(models.AyudaMonetaria, aid_id)
        case = db.get(models.CasoAyudaV2, case_id)
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="problema_reportado").one()
        assert aid.problema_detalle_cifrado.startswith("enc:v1:")
        assert private_detail not in aid.problema_detalle_cifrado
        assert private_detail not in audit.metadata_json
        assert audit.motivo_codigo == "transferencia_no_recibida"
        assert case.monto_confirmado == Decimal("0.00")
        assert case.ayudas_confirmadas == 0


def test_coordinator_reviews_and_rejects_problem_preserving_encrypted_history():
    case_id, aid_id, _ = create_aid()
    private_problem = "DUPLICATE-PRIVATE-PROBLEM"
    private_resolution = "DUPLICATE-PRIVATE-RESOLUTION"
    reported = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/problema",
        json={"problem_type": "duplicado", "detail": private_problem},
    )
    reviewed = client.post(f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/en-revision")
    rejected = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/rechazar",
        json={"reason": private_resolution},
    )
    confirmation = confirm(case_id, aid_id)

    assert reported.status_code == 200
    assert reviewed.status_code == 200
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rechazada"
    assert rejected.json()["problem_detail"] == private_problem
    assert rejected.json()["resolution_reason"] == private_resolution
    assert confirmation.status_code == 409
    with TestingSessionLocal() as db:
        aid = db.get(models.AyudaMonetaria, aid_id)
        case = db.get(models.CasoAyudaV2, case_id)
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="revision_resuelta").one()
        assert aid.estado == "rechazada"
        assert private_problem not in aid.problema_detalle_cifrado
        assert private_resolution not in aid.problema_detalle_cifrado
        assert private_resolution not in audit.metadata_json
        assert audit.motivo_codigo == "rechazada"
        assert case.monto_confirmado == Decimal("0.00")
        assert case.ayudas_confirmadas == 0


def test_problem_and_rejection_require_valid_state_transitions():
    case_id, aid_id, _ = create_aid()

    rejected_pending = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/rechazar",
        json={"reason": "No se pudo verificar"},
    )
    reported = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/problema",
        json={"problem_type": "otro", "detail": "Requiere revisión manual"},
    )
    reported_again = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/problema",
        json={"problem_type": "otro", "detail": "Segundo reporte"},
    )

    assert rejected_pending.status_code == 409
    assert reported.status_code == 200
    assert reported_again.status_code == 409


def test_cross_organization_aid_routes_behave_as_not_found():
    case_id, aid_id, _ = create_aid()
    actor_override["value"] = ActorOrgContext(
        "org-2", "coordinador", "coordinator@org2.example", uid="coordinator-org-2"
    )

    listed = client.get(f"/api/v2/casos-ayuda/{case_id}/ayudas")
    problem = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/problema",
        json={"problem_type": "otro", "detail": "No debe revelar el caso"},
    )
    reviewed = client.post(f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/en-revision")
    rejected = client.post(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/rechazar",
        json={"reason": "No debe revelar el caso"},
    )
    confirmed = confirm(case_id, aid_id)

    assert listed.status_code == 404
    assert problem.status_code == 404
    assert reviewed.status_code == 404
    assert rejected.status_code == 404
    assert confirmed.status_code == 404


def test_confirmation_replays_same_response_and_conflicts_on_changed_payload():
    case_id, aid_id, _ = create_aid()
    payload = confirmation_payload()

    first = confirm(case_id, aid_id, payload)
    replay = confirm(case_id, aid_id, payload)
    conflict = confirm(case_id, aid_id, {**payload, "received_amount": "26.00"})

    assert first.status_code == 200
    assert replay.status_code == 200
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, case_id)
        assert db.query(models.ConfirmacionAyuda).count() == 1
        assert db.query(models.OperacionIdempotenteAyuda).filter_by(operacion="confirm_help").count() == 1
        assert case.monto_confirmado == Decimal("25.00")
        assert case.ayudas_confirmadas == 1


def test_reaching_goal_updates_state_and_keeps_case_publicly_visible():
    case_id, aid_id, public_id = create_aid(goal_amount="25.00")

    confirmed = confirm(case_id, aid_id)
    public_list = client.get("/api/v2/public/casos-ayuda")
    public_detail = client.get(f"/api/v2/public/casos-ayuda/{public_id}")

    assert confirmed.status_code == 200
    assert confirmed.json()["case_status"] == "meta_alcanzada"
    assert confirmed.json()["goal_reached"] is True
    assert public_list.status_code == 200
    assert any(item["public_id"] == public_id for item in public_list.json())
    assert public_detail.status_code == 200
    assert public_detail.json()["estado"] == "meta_alcanzada"


def test_cross_currency_confirmation_is_rejected_without_progress():
    case_id, aid_id, _ = create_aid(reported_currency="EUR")

    response = confirm(
        case_id,
        aid_id,
        confirmation_payload(received_currency="EUR"),
    )

    assert response.status_code == 422
    assert "moneda" in response.json()["detail"].lower()
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, case_id)
        assert db.get(models.AyudaMonetaria, aid_id).estado == "pendiente_confirmacion"
        assert db.query(models.ConfirmacionAyuda).count() == 0
        assert case.monto_confirmado == Decimal("0.00")
        assert case.ayudas_confirmadas == 0
