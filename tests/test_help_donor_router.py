from decimal import Decimal
import base64
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_actor_org, verify_api_key
import models
from routers.casos_ayuda_donantes import router
from services.help_crypto import HelpDataCipher
from services.help_confirmations import list_help_aids


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


def donor():
    return ActorOrgContext(
        "",
        "donor",
        "donante@example.com",
        uid="firebase-donor-1",
        ip_hash="a" * 64,
    )


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "donante@example.com"
app.dependency_overrides[require_verified_actor_org] = donor
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_published_case(public_id="ayuda-publicada-1", identity_suffix="a"):
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-1",
            nombres_apellidos_cifrado=cipher.encrypt("Persona beneficiaria", field="beneficiary.name"),
            cedula_hash=identity_suffix * 64,
            cedula_cifrada=cipher.encrypt("V-12345678", field="beneficiary.identity"),
            datos_verificacion_cifrado=cipher.encrypt("Verificado", field="beneficiary.verification"),
            creado_por="coordinador@example.com",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id=public_id,
            organizacion_id="org-1",
            beneficiario_id=beneficiary.id,
            titulo_interno="Caso interno",
            categoria="medicamentos",
            meta_monto=Decimal("100.00"),
            meta_moneda="USD",
            estado="publicado",
            creado_por="coordinador@example.com",
        )
        db.add(case)
        db.flush()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Persona",
            titulo_publico="Ayuda publicada",
            descripcion_publica="Descripción pública suficiente",
            version=1,
            activa=True,
        ))
        consent = models.ConsentimientoCasoAyuda(
            caso_id=case.id,
            version=1,
            texto_version="mvp1-v1",
            alcance_json="{}",
            firmante_nombre_cifrado=cipher.encrypt("Persona beneficiaria", field="consent.signer_name"),
            firmante_tipo="beneficiario",
            registrado_por="coordinador@example.com",
            vigente=True,
        )
        db.add(consent)
        db.flush()
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="principal",
            version=1,
            tipo_titular="beneficiario",
            titular_nombre_cifrado=cipher.encrypt("Persona beneficiaria", field="account.holder_name"),
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda="USD",
            identificador_cifrado=cipher.encrypt("0102-1234-5678-9000", field="account.identifier"),
            instrucciones_cifrado=cipher.encrypt("Cuenta corriente", field="account.instructions"),
            responsable_nombre_cifrado=cipher.encrypt("Responsable", field="account.responsible_name"),
            responsable_email_hash="b" * 64,
            responsable_email_cifrado=cipher.encrypt("responsable@example.com", field="account.responsible_email"),
            consentimiento_version=consent.version,
            estado="aprobada",
            creado_por="coordinador@example.com",
        )
        db.add(account)
        db.commit()
        return case.public_id


def accept_current_terms():
    response = client.post("/api/v2/donante/terminos/aceptar", json={"terms_version": "donor-v1"})
    assert response.status_code == 200


def account_id_for(public_id):
    with TestingSessionLocal() as db:
        case = db.query(models.CasoAyudaV2).filter_by(public_id=public_id).one()
        return db.query(models.CuentaCasoAyuda).filter_by(caso_id=case.id).one().id


def monetary_aid_payload(public_id, **overrides):
    selected_account_id = overrides.pop("account_id", None)
    payload = {
        "account_id": selected_account_id if selected_account_id is not None else account_id_for(public_id),
        "amount": "25.50",
        "currency": "USD",
        "transfer_date": "2026-07-20",
        "reference": "REF-PRIVATE-123",
        "comment": "Comentario privado",
    }
    payload.update(overrides)
    return payload


def report_aid(public_id, payload=None, key="report-help-123456789"):
    return client.post(
        f"/api/v2/donante/casos-ayuda/{public_id}/ayudas",
        headers={"Idempotency-Key": key},
        json=payload or monetary_aid_payload(public_id),
    )


def test_requires_current_terms_before_disclosing_accounts():
    public_id = create_published_case()

    response = client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas")

    assert response.status_code == 403
    assert "condiciones" in response.json()["detail"].lower()


def test_pilot_shutdown_blocks_new_help_but_preserves_donor_history(monkeypatch):
    public_id = create_published_case()
    accept_current_terms()
    created = report_aid(public_id)
    assert created.status_code == 201
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-2")

    accounts = client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas")
    blocked_report = report_aid(public_id, key="report-help-blocked-12345")
    history = client.get("/api/v2/donante/ayudas")

    assert accounts.status_code == 404
    assert blocked_report.status_code == 404
    assert history.status_code == 200
    assert [item["id"] for item in history.json()] == [created.json()["id"]]


def test_accepts_terms_and_returns_only_approved_account_with_audit():
    public_id = create_published_case()

    accepted = client.post("/api/v2/donante/terminos/aceptar", json={"terms_version": "donor-v1"})
    response = client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas")

    assert accepted.status_code == 200
    assert accepted.json()["accepted"] is True
    assert response.status_code == 200
    assert response.json() == {
        "case_public_id": public_id,
        "accounts": [{
            "id": response.json()["accounts"][0]["id"],
            "version": 1,
            "holder_name": "Persona beneficiaria",
            "beneficiary_relationship": "propia",
            "medium": "banco_venezolano",
            "currency": "USD",
            "identifier": "0102-1234-5678-9000",
            "instructions": "Cuenta corriente",
        }],
    }
    with TestingSessionLocal() as db:
        acceptance = db.query(models.AceptacionTerminosDonante).one()
        access = db.query(models.AccesoCuentaDonante).one()
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="cuenta_consultada_donante").one()
        assert acceptance.actor_uid == "firebase-donor-1"
        assert access.actor_uid == "firebase-donor-1"
        assert access.ip_hash == "a" * 64
        assert audit.actor_id == "firebase-donor-1"
        assert "0102" not in audit.metadata_json


def test_reports_monetary_aid_without_increasing_public_progress():
    public_id = create_published_case()
    accept_current_terms()

    response = report_aid(public_id)

    assert response.status_code == 201
    body = response.json()
    assert body == {
        "id": body["id"],
        "case_public_id": public_id,
        "account_version": 1,
        "amount": "25.50",
        "currency": "USD",
        "transfer_date": "2026-07-20",
        "status": "pendiente_confirmacion",
        "receipt_attached": False,
        "receipt_id": None,
        "created_at": body["created_at"],
    }
    with TestingSessionLocal() as db:
        aid = db.query(models.AyudaMonetaria).one()
        case = db.query(models.CasoAyudaV2).filter_by(public_id=public_id).one()
        operation = db.query(models.OperacionIdempotenteAyuda).one()
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="ayuda_reportada_donante").one()
        assert aid.donante_id == "firebase-donor-1"
        assert aid.donante_email_hash == HelpDataCipher.from_environment().blind_index(
            "donante@example.com", purpose="donor-email"
        )
        assert aid.donante_email_cifrado.startswith("enc:v1:")
        assert "donante@example.com" not in aid.donante_email_cifrado
        assert aid.estado == "pendiente_confirmacion"
        assert aid.referencia_cifrada.startswith("enc:v1:")
        assert aid.comentario_cifrado.startswith("enc:v1:")
        assert "REF-PRIVATE-123" not in aid.referencia_cifrada
        assert case.monto_confirmado == Decimal("0.00")
        assert case.ayudas_confirmadas == 0
        assert operation.estado == "completed"
        assert operation.response_status == 201
        assert "REF-PRIVATE-123" not in operation.response_body
        assert "Comentario privado" not in audit.metadata_json
        assert "REF-PRIVATE-123" not in audit.metadata_json
        notifications = db.query(models.NotificacionCasoAyuda).all()
        assert {item.destinatario_tipo for item in notifications} == {"responsable", "coordinador"}
        assert {item.evento_tipo for item in notifications} == {"ayuda_reportada"}


def test_replays_same_aid_and_rejects_changed_payload_for_same_key():
    public_id = create_published_case()
    accept_current_terms()
    payload = monetary_aid_payload(public_id)

    first = report_aid(public_id, payload)
    replay = report_aid(public_id, payload)
    conflict = report_aid(public_id, {**payload, "amount": "26.00"})

    assert first.status_code == 201
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert conflict.status_code == 409
    with TestingSessionLocal() as db:
        assert db.query(models.AyudaMonetaria).count() == 1
        assert db.query(models.OperacionIdempotenteAyuda).count() == 1


def test_rejects_wrong_or_stale_account_version():
    public_id = create_published_case()
    accept_current_terms()
    stale_id = account_id_for(public_id)
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        stale = db.get(models.CuentaCasoAyuda, stale_id)
        latest = models.CuentaCasoAyuda(
            caso_id=stale.caso_id,
            account_key=stale.account_key,
            version=2,
            tipo_titular=stale.tipo_titular,
            titular_nombre_cifrado=cipher.encrypt("Persona beneficiaria", field="account.holder_name"),
            relacion_beneficiario=stale.relacion_beneficiario,
            medio=stale.medio,
            moneda=stale.moneda,
            identificador_cifrado=cipher.encrypt("0102-9999", field="account.identifier"),
            responsable_nombre_cifrado=cipher.encrypt("Responsable", field="account.responsible_name"),
            responsable_email_hash="c" * 64,
            responsable_email_cifrado=cipher.encrypt("responsable@example.com", field="account.responsible_email"),
            consentimiento_version=stale.consentimiento_version,
            estado="aprobada",
            creado_por="coordinador@example.com",
        )
        db.add(latest)
        db.commit()

    stale_response = report_aid(public_id, monetary_aid_payload(public_id, account_id=stale_id))
    wrong_response = report_aid(
        public_id,
        monetary_aid_payload(public_id, account_id=999999),
        key="report-help-wrong-12345",
    )

    assert stale_response.status_code == 404
    assert wrong_response.status_code == 404
    assert "cuenta" in stale_response.json()["detail"].lower()
    assert "cuenta" in wrong_response.json()["detail"].lower()
    with TestingSessionLocal() as db:
        assert db.query(models.AyudaMonetaria).count() == 0


def test_lists_only_signed_actor_aids_with_public_title():
    public_id = create_published_case()
    accept_current_terms()
    created = report_aid(public_id).json()

    other_actor = ActorOrgContext(
        "", "donor", "other@example.com", uid="firebase-donor-2", ip_hash="b" * 64
    )
    app.dependency_overrides[require_verified_actor_org] = lambda: other_actor
    try:
        accept_current_terms()
        other_response = client.get("/api/v2/donante/ayudas")
    finally:
        app.dependency_overrides[require_verified_actor_org] = donor
    owner_response = client.get("/api/v2/donante/ayudas")

    assert other_response.status_code == 200
    assert other_response.json() == []
    assert owner_response.status_code == 200
    assert owner_response.json() == [{"public_title": "Ayuda publicada", **created}]


def test_saves_optional_receipt_encrypted_and_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path))
    public_id = create_published_case()
    accept_current_terms()
    receipt = b"%PDF-1.4 PRIVATE-DONOR-RECEIPT"
    payload = monetary_aid_payload(
        public_id,
        receipt_file_name="comprobante-privado.pdf",
        receipt_content_type="application/pdf",
        receipt_base64=base64.b64encode(receipt).decode(),
    )

    response = report_aid(public_id, payload)

    assert response.status_code == 201
    assert response.json()["receipt_attached"] is True
    assert isinstance(response.json()["receipt_id"], int)
    assert "storage_path" not in response.text
    files = list(Path(tmp_path).rglob("*.enc"))
    assert len(files) == 1
    assert receipt not in files[0].read_bytes()
    with TestingSessionLocal() as db:
        stored = db.query(models.ComprobanteAyuda).one()
        operation = db.query(models.OperacionIdempotenteAyuda).one()
        assert stored.version == 1
        assert stored.storage_path.startswith("private/casos-ayuda/")
        assert stored.checksum_sha256 == hashlib.sha256(receipt).hexdigest()
        assert stored.nombre_original_cifrado.startswith("enc:v1:")
        assert payload["receipt_base64"] not in operation.response_body


def test_donor_cancels_own_pending_aid_without_deleting_evidence_or_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path))
    public_id = create_published_case()
    accept_current_terms()
    receipt = b"%PDF-1.4 PRIVATE-CANCEL-RECEIPT"
    created = report_aid(public_id, monetary_aid_payload(
        public_id,
        receipt_file_name="comprobante.pdf",
        receipt_content_type="application/pdf",
        receipt_base64=base64.b64encode(receipt).decode(),
    )).json()

    response = client.post(f"/api/v2/donante/ayudas/{created['id']}/cancelar")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelada"
    assert response.json()["receipt_id"] == created["receipt_id"]
    with TestingSessionLocal() as db:
        aid = db.get(models.AyudaMonetaria, created["id"])
        case = db.query(models.CasoAyudaV2).filter_by(public_id=public_id).one()
        receipt_record = db.get(models.ComprobanteAyuda, created["receipt_id"])
        operation = db.get(models.OperacionIdempotenteAyuda, aid.idempotency_id)
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="ayuda_cancelada_donante").one()
        assert aid.estado == "cancelada"
        assert aid.cuenta_version == 1
        assert receipt_record.estado == "vigente"
        assert operation.estado == "completed"
        assert case.monto_confirmado == Decimal("0.00")
        assert case.ayudas_confirmadas == 0
        organization_summary = list_help_aids(
            db,
            case_id=case.id,
            actor=ActorOrgContext("org-1", "coordinador", "coordinador@example.com"),
        )
        assert organization_summary[0]["status"] == "cancelada"
        assert audit.actor_id == "firebase-donor-1"
        assert audit.actor_tipo == "donante"
        assert audit.metadata_json == '{"cuenta_version":1,"estado_anterior":"pendiente_confirmacion","estado_nuevo":"cancelada"}'
        assert "25.50" not in audit.metadata_json


def test_donor_cancellation_hides_cross_donor_aid():
    public_id = create_published_case()
    accept_current_terms()
    created = report_aid(public_id).json()
    app.dependency_overrides[require_verified_actor_org] = lambda: ActorOrgContext(
        "", "donor", "other@example.com", uid="firebase-donor-2", ip_hash="b" * 64,
    )
    try:
        response = client.post(f"/api/v2/donante/ayudas/{created['id']}/cancelar")
    finally:
        app.dependency_overrides[require_verified_actor_org] = donor

    assert response.status_code == 404
    with TestingSessionLocal() as db:
        assert db.get(models.AyudaMonetaria, created["id"]).estado == "pendiente_confirmacion"


@pytest.mark.parametrize("status", ["problema_reportado", "en_revision", "confirmada", "rechazada", "cancelada"])
def test_donor_cancellation_rejects_non_pending_or_reviewed_aid(status):
    public_id = create_published_case()
    accept_current_terms()
    created = report_aid(public_id).json()
    with TestingSessionLocal() as db:
        aid = db.get(models.AyudaMonetaria, created["id"])
        aid.estado = status
        if status == "problema_reportado":
            aid.problema_tipo = "transferencia_no_recibida"
        db.commit()

    response = client.post(f"/api/v2/donante/ayudas/{created['id']}/cancelar")

    assert response.status_code == 409
    with TestingSessionLocal() as db:
        assert db.get(models.AyudaMonetaria, created["id"]).estado == status


def test_donor_cancellation_rejects_pending_aid_with_problem_marker():
    public_id = create_published_case()
    accept_current_terms()
    created = report_aid(public_id).json()
    with TestingSessionLocal() as db:
        db.get(models.AyudaMonetaria, created["id"]).problema_tipo = "transferencia_no_recibida"
        db.commit()

    response = client.post(f"/api/v2/donante/ayudas/{created['id']}/cancelar")

    assert response.status_code == 409


def test_donor_cancellation_rejects_aid_with_prior_review_audit():
    public_id = create_published_case()
    accept_current_terms()
    created = report_aid(public_id).json()
    with TestingSessionLocal() as db:
        aid = db.get(models.AyudaMonetaria, created["id"])
        case = db.get(models.CasoAyudaV2, aid.caso_id)
        db.add(models.AuditoriaCasoAyuda(
            event_id=str(uuid4()),
            organizacion_id=case.organizacion_id,
            caso_id=case.id,
            accion="ayuda_en_revision",
            actor_id="coordinador@example.com",
            actor_tipo="coordinador",
            entidad_tipo="ayuda",
            entidad_id=str(aid.id),
            metadata_json="{}",
        ))
        db.commit()

    response = client.post(f"/api/v2/donante/ayudas/{created['id']}/cancelar")

    assert response.status_code == 409
