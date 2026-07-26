from decimal import Decimal
from datetime import date
import base64
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
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
actor_override = {"value": ActorOrgContext("", "super_admin", "global@example.com")}


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "global@example.com"
app.dependency_overrides[require_verified_case_organization_actor] = lambda: actor_override["value"]
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    actor_override["value"] = ActorOrgContext("", "super_admin", "global@example.com")
    yield
    Base.metadata.drop_all(bind=engine)


def create_case_with_aid():
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-1",
            nombres_apellidos_cifrado=cipher.encrypt("PRIVATE-NAME", field="beneficiary.name"),
            cedula_hash="a" * 64,
            cedula_cifrada=cipher.encrypt("V-PRIVATE-ID", field="beneficiary.identity"),
            creado_por="coordinator@example.com",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id="sensitive-read-case",
            organizacion_id="org-1",
            beneficiario_id=beneficiary.id,
            titulo_interno="PRIVATE-INTERNAL-TITLE",
            categoria="medicamentos",
            meta_monto=Decimal("100.00"),
            meta_moneda="USD",
            creado_por="coordinator@example.com",
        )
        db.add(case)
        db.flush()
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="principal",
            version=1,
            tipo_titular="beneficiario",
            titular_nombre_cifrado=cipher.encrypt("PRIVATE-HOLDER", field="account.holder_name"),
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda="USD",
            identificador_cifrado=cipher.encrypt("PRIVATE-ACCOUNT", field="account.identifier"),
            responsable_nombre_cifrado=cipher.encrypt("PRIVATE-RESPONSIBLE", field="account.responsible_name"),
            responsable_email_hash="b" * 64,
            responsable_email_cifrado=cipher.encrypt("private@example.com", field="account.responsible_email"),
            consentimiento_version=1,
            estado="aprobada",
            creado_por="coordinator@example.com",
        )
        db.add(account)
        db.flush()
        operation = models.OperacionIdempotenteAyuda(
            actor_id="donor-1",
            organizacion_id="org-1",
            operacion="report_help",
            idempotency_key="sensitive-read-key-12345",
            request_hash="c" * 64,
            estado="completed",
        )
        db.add(operation)
        db.flush()
        aid = models.AyudaMonetaria(
            caso_id=case.id,
            cuenta_id=account.id,
            cuenta_version=account.version,
            donante_id="donor-1",
            monto_reportado=Decimal("25.00"),
            moneda_reportada="USD",
            fecha_transferencia=date(2026, 7, 20),
            estado="pendiente_confirmacion",
            idempotency_id=operation.id,
        )
        db.add(aid)
        db.commit()
        return case.id


def test_unscoped_global_management_reads_commit_bounded_audits():
    case_id = create_case_with_aid()

    listed = client.get("/api/v2/casos-ayuda")
    detail = client.get(f"/api/v2/casos-ayuda/{case_id}")
    aids = client.get(f"/api/v2/casos-ayuda/{case_id}/ayudas")

    assert listed.status_code == 200
    assert detail.status_code == 200
    assert aids.status_code == 200
    with TestingSessionLocal() as db:
        audits = db.query(models.AuditoriaCasoAyuda).filter(
            models.AuditoriaCasoAyuda.accion.like("acceso_global_%")
        ).order_by(models.AuditoriaCasoAyuda.id).all()
        assert [audit.accion for audit in audits] == [
            "acceso_global_lista_casos",
            "acceso_global_detalle_caso",
            "acceso_global_ayudas_caso",
        ]
        for audit in audits:
            assert audit.actor_id == "global@example.com"
            assert audit.actor_tipo == "super_admin"
            assert audit.organizacion_id == "org-1"
            assert audit.caso_id == case_id
            assert json.loads(audit.metadata_json) == {}
            assert "PRIVATE" not in audit.metadata_json


def test_scoped_coordinator_reads_do_not_create_global_access_noise():
    case_id = create_case_with_aid()
    actor_override["value"] = ActorOrgContext("org-1", "coordinador", "coordinator@example.com")

    assert client.get("/api/v2/casos-ayuda").status_code == 200
    assert client.get(f"/api/v2/casos-ayuda/{case_id}").status_code == 200
    assert client.get(f"/api/v2/casos-ayuda/{case_id}/ayudas").status_code == 200

    with TestingSessionLocal() as db:
        assert db.query(models.AuditoriaCasoAyuda).filter(
            models.AuditoriaCasoAyuda.accion.like("acceso_global_%")
        ).count() == 0
