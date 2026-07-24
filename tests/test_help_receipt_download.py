import base64
from datetime import date
from decimal import Decimal
import hashlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from dependencies import (
    ActorOrgContext,
    require_verified_actor_org,
    require_verified_case_organization_actor,
    verify_api_key,
)
from routers.casos_ayuda_donantes import router as donor_router
from routers.casos_ayuda_v2 import router as organization_router
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
app.include_router(donor_router)
app.include_router(organization_router)
actor_override = {"value": None}


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


def current_actor():
    return actor_override["value"]


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "server"
app.dependency_overrides[require_verified_actor_org] = current_actor
app.dependency_overrides[require_verified_case_organization_actor] = current_actor
client = TestClient(app)


def donor(uid="donor-1"):
    return ActorOrgContext("", "donor", f"{uid}@example.test", uid=uid)


def manager(role="coordinador", organization_id="org-1"):
    return ActorOrgContext(organization_id, role, f"{role}@example.test", uid=f"{role}-1")


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-1,org-2")
    actor_override["value"] = donor()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_receipt(tmp_path, *, organization_id="org-1", donor_id="donor-1"):
    plaintext = b"%PDF-1.7 PROTECTED-RECEIPT-CONTENT"
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id=organization_id,
            nombres_apellidos_cifrado=cipher.encrypt("Persona", field="beneficiary.name"),
            cedula_hash=("a" if organization_id == "org-1" else "b") * 64,
            cedula_cifrada=cipher.encrypt("V-123", field="beneficiary.identity"),
            creado_por="creator@example.test",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id=f"receipt-{organization_id}",
            organizacion_id=organization_id,
            beneficiario_id=beneficiary.id,
            titulo_interno="Caso",
            categoria="medicamentos",
            meta_monto=Decimal("100.00"),
            meta_moneda="USD",
            estado="publicado",
            creado_por="creator@example.test",
        )
        db.add(case)
        db.flush()
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="principal",
            version=1,
            tipo_titular="beneficiario",
            titular_nombre_cifrado=cipher.encrypt("Persona", field="account.holder_name"),
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda="USD",
            identificador_cifrado=cipher.encrypt("0102-private", field="account.identifier"),
            responsable_nombre_cifrado=cipher.encrypt("Responsable", field="account.responsible_name"),
            responsable_email_hash="c" * 64,
            responsable_email_cifrado=cipher.encrypt("responsable@example.test", field="account.responsible_email"),
            consentimiento_version=1,
            estado="aprobada",
            creado_por="creator@example.test",
        )
        db.add(account)
        db.flush()
        operation = models.OperacionIdempotenteAyuda(
            actor_id=donor_id,
            organizacion_id=organization_id,
            operacion="report_help",
            idempotency_key=f"receipt-download-{organization_id}-{donor_id}",
            request_hash="d" * 64,
            estado="completed",
            response_status=201,
            response_body="{}",
        )
        db.add(operation)
        db.flush()
        aid = models.AyudaMonetaria(
            caso_id=case.id,
            cuenta_id=account.id,
            cuenta_version=1,
            donante_id=donor_id,
            monto_reportado=Decimal("10.00"),
            moneda_reportada="USD",
            fecha_transferencia=date(2026, 7, 20),
            estado="pendiente_confirmacion",
            idempotency_id=operation.id,
        )
        db.add(aid)
        db.flush()
        relative_path = f"private/casos-ayuda/{case.id}/receipt.enc"
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True)
        destination.write_bytes(cipher.encrypt_bytes(plaintext, field="aid.receipt"))
        receipt = models.ComprobanteAyuda(
            ayuda_id=aid.id,
            version=2,
            storage_path=relative_path,
            nombre_original_cifrado=cipher.encrypt("../../secret name.pdf", field="receipt.original_name"),
            content_type="application/pdf",
            size_bytes=len(plaintext),
            checksum_sha256=hashlib.sha256(plaintext).hexdigest(),
            estado="vigente",
            cargado_por=donor_id,
        )
        db.add(receipt)
        db.commit()
        return case.id, aid.id, receipt.id, plaintext


def donor_download(aid_id, receipt_id):
    return client.get(f"/api/v2/donante/ayudas/{aid_id}/comprobantes/{receipt_id}/descargar")


def organization_download(case_id, aid_id, receipt_id, reason=None):
    suffix = f"?motivo={reason}" if reason is not None else ""
    return client.get(
        f"/api/v2/casos-ayuda/{case_id}/ayudas/{aid_id}/comprobantes/{receipt_id}/descargar{suffix}"
    )


def assert_download_audit(*, receipt_id, actor_type, reason=None):
    with TestingSessionLocal() as db:
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="comprobante_descargado").one()
        assert audit.actor_tipo == actor_type
        assert audit.entidad_tipo == "comprobante"
        assert audit.entidad_id == str(receipt_id)
        assert audit.motivo_codigo == reason
        assert json.loads(audit.metadata_json) == {"comprobante_id": receipt_id, "version": 2}
        sensitive = audit.metadata_json + (audit.motivo_codigo or "")
        assert "secret name" not in sensitive
        assert "private/casos" not in sensitive
        assert "PROTECTED-RECEIPT" not in sensitive
        assert hashlib.sha256(b"%PDF-1.7 PROTECTED-RECEIPT-CONTENT").hexdigest() not in sensitive


def test_donor_downloads_own_receipt_with_safe_headers_and_audit(tmp_path):
    case_id, aid_id, receipt_id, plaintext = create_receipt(tmp_path)

    response = donor_download(aid_id, receipt_id)

    assert response.status_code == 200
    assert response.content == plaintext
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="comprobante-ayuda-{aid_id}-v2.pdf"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "storage" not in response.headers
    assert_download_audit(receipt_id=receipt_id, actor_type="donante")


def test_organization_aid_summary_exposes_only_current_receipt_id(tmp_path):
    case_id, _aid_id, receipt_id, _plaintext = create_receipt(tmp_path)
    actor_override["value"] = manager()

    response = client.get(f"/api/v2/casos-ayuda/{case_id}/ayudas")

    assert response.status_code == 200
    assert response.json()[0]["receipt_id"] == receipt_id
    assert "storage_path" not in response.text


def test_cross_donor_is_not_found_and_does_not_decrypt(tmp_path, monkeypatch):
    _case_id, aid_id, receipt_id, _ = create_receipt(tmp_path)
    actor_override["value"] = donor("donor-2")
    decrypt_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal decrypt_called
        decrypt_called = True
        raise AssertionError("denied receipt must not be decrypted")

    monkeypatch.setattr(HelpDataCipher, "decrypt_bytes", fail_if_called)

    response = donor_download(aid_id, receipt_id)

    assert response.status_code == 404
    assert decrypt_called is False


@pytest.mark.parametrize("role", ["coordinador", "admin"])
def test_scoped_manager_downloads_receipt_for_case_organization(tmp_path, role):
    case_id, aid_id, receipt_id, plaintext = create_receipt(tmp_path)
    actor_override["value"] = manager(role)

    response = organization_download(case_id, aid_id, receipt_id)

    assert response.status_code == 200
    assert response.content == plaintext
    assert_download_audit(receipt_id=receipt_id, actor_type=role)


def test_cross_organization_is_not_found_and_does_not_decrypt(tmp_path, monkeypatch):
    case_id, aid_id, receipt_id, _ = create_receipt(tmp_path)
    actor_override["value"] = manager("coordinador", "org-2")
    decrypt_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal decrypt_called
        decrypt_called = True
        raise AssertionError("denied receipt must not be decrypted")

    monkeypatch.setattr(HelpDataCipher, "decrypt_bytes", fail_if_called)

    response = organization_download(case_id, aid_id, receipt_id)

    assert response.status_code == 404
    assert decrypt_called is False


@pytest.mark.parametrize("role", ["admin", "super_admin"])
@pytest.mark.parametrize("reason", ["revision_incidencia", "auditoria", "soporte_autorizado"])
def test_global_manager_requires_and_audits_closed_reason(tmp_path, role, reason):
    case_id, aid_id, receipt_id, plaintext = create_receipt(tmp_path)
    actor_override["value"] = manager(role, "")

    response = organization_download(case_id, aid_id, receipt_id, reason)

    assert response.status_code == 200
    assert response.content == plaintext
    assert_download_audit(receipt_id=receipt_id, actor_type=role, reason=reason)


@pytest.mark.parametrize(
    ("reason", "status_code"),
    [(None, 400), ("curiosidad", 403)],
)
def test_global_manager_reason_denial_happens_before_decryption(
    tmp_path, monkeypatch, reason, status_code
):
    case_id, aid_id, receipt_id, _ = create_receipt(tmp_path)
    actor_override["value"] = manager("super_admin", "")
    decrypt_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal decrypt_called
        decrypt_called = True
        raise AssertionError("denied receipt must not be decrypted")

    monkeypatch.setattr(HelpDataCipher, "decrypt_bytes", fail_if_called)

    response = organization_download(case_id, aid_id, receipt_id, reason)

    assert response.status_code == status_code
    assert decrypt_called is False


@pytest.mark.parametrize("corruption", ["size", "checksum"])
def test_download_rejects_receipt_integrity_mismatch_without_audit(tmp_path, corruption):
    _case_id, aid_id, receipt_id, _ = create_receipt(tmp_path)
    with TestingSessionLocal() as db:
        receipt = db.get(models.ComprobanteAyuda, receipt_id)
        if corruption == "size":
            receipt.size_bytes += 1
        else:
            receipt.checksum_sha256 = "0" * 64
        db.commit()

    response = donor_download(aid_id, receipt_id)

    assert response.status_code == 404
    with TestingSessionLocal() as db:
        assert db.query(models.AuditoriaCasoAyuda).count() == 0


def test_download_rejects_unsafe_storage_path_before_decryption(tmp_path, monkeypatch):
    _case_id, aid_id, receipt_id, _ = create_receipt(tmp_path)
    with TestingSessionLocal() as db:
        db.get(models.ComprobanteAyuda, receipt_id).storage_path = "private/../outside.enc"
        db.commit()
    decrypt_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal decrypt_called
        decrypt_called = True
        raise AssertionError("unsafe path must not be decrypted")

    monkeypatch.setattr(HelpDataCipher, "decrypt_bytes", fail_if_called)

    response = donor_download(aid_id, receipt_id)

    assert response.status_code == 404
    assert decrypt_called is False
