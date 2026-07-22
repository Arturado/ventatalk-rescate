from decimal import Decimal
import base64

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


def create_published_case():
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-1",
            nombres_apellidos_cifrado=cipher.encrypt("Persona beneficiaria", field="beneficiary.name"),
            cedula_hash="a" * 64,
            cedula_cifrada=cipher.encrypt("V-12345678", field="beneficiary.identity"),
            datos_verificacion_cifrado=cipher.encrypt("Verificado", field="beneficiary.verification"),
            creado_por="coordinador@example.com",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id="ayuda-publicada-1",
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


def test_requires_current_terms_before_disclosing_accounts():
    public_id = create_published_case()

    response = client.get(f"/api/v2/donante/casos-ayuda/{public_id}/cuentas")

    assert response.status_code == 403
    assert "condiciones" in response.json()["detail"].lower()


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
