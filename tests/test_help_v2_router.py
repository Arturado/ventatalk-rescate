from decimal import Decimal
import base64
from pathlib import Path
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
import models
from routers.casos_ayuda_v2 import router
from routers.casos_ayuda_publicos import router as public_router
from services.help_cases import create_help_case
from services.help_cases import publish_help_case


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


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


def coordinator():
    return ActorOrgContext("org-1", "coordinador", "coordinador@example.com")


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "coordinador@example.com"
app.dependency_overrides[require_verified_case_organization_actor] = coordinator
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def crypto_environment(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())


def create_case(*, organization_id, suffix, private_marker):
    with TestingSessionLocal() as db:
        actor = ActorOrgContext(organization_id, "coordinador", f"{organization_id}@example.com")
        case = create_help_case(
            db,
            actor=actor,
            organization_id=organization_id,
            public_id=f"router-case-{suffix}",
            beneficiary_name_encrypted=private_marker,
            beneficiary_identity_hash=(suffix * 64)[:64],
            beneficiary_identity_encrypted=private_marker,
            internal_title=f"Caso {suffix}",
            category="medicamentos",
            goal_amount=Decimal("100.00"),
            goal_currency="USD",
        )
        db.commit()
        return case.id


def test_list_endpoint_returns_only_safe_fields_from_actor_organization():
    private_marker = "NEVER-EXPOSE-ENCRYPTED"
    expected_id = create_case(organization_id="org-1", suffix="1", private_marker=private_marker)
    create_case(organization_id="org-2", suffix="2", private_marker=private_marker)

    response = client.get("/api/v2/casos-ayuda")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [expected_id]
    assert set(response.json()[0]) == {
        "id",
        "public_id",
        "organizacion_id",
        "titulo_interno",
        "categoria",
        "tipo_sujeto",
        "acepta_ayuda_monetaria",
        "acepta_ayuda_directa",
        "meta_monto",
        "meta_moneda",
        "monto_confirmado",
        "ayudas_confirmadas",
        "estado",
        "prioridad_especial",
        "created_at",
        "updated_at",
    }
    assert private_marker not in response.text


def test_list_endpoint_rejects_cross_organization_target():
    response = client.get("/api/v2/casos-ayuda", params={"organizacion_id": "org-2"})

    assert response.status_code == 403


def test_lifecycle_endpoints_apply_role_policy_and_return_updated_state():
    case_id = create_case(organization_id="org-1", suffix="l", private_marker="encrypted")
    with TestingSessionLocal() as db:
        db.get(models.CasoAyudaV2, case_id).estado = "publicado"
        db.commit()

    paused = client.post(f"/api/v2/casos-ayuda/{case_id}/pausar", json={})
    denied = client.post(
        f"/api/v2/casos-ayuda/{case_id}/suspender",
        json={"reason_code": "revision_administrativa"},
    )
    app.dependency_overrides[require_verified_case_organization_actor] = lambda: ActorOrgContext(
        "org-1", "admin", "admin@example.com",
    )
    try:
        suspended = client.post(
            f"/api/v2/casos-ayuda/{case_id}/suspender",
            json={"reason_code": "revision_administrativa"},
        )
        reactivated = client.post(f"/api/v2/casos-ayuda/{case_id}/reactivar", json={})
    finally:
        app.dependency_overrides[require_verified_case_organization_actor] = coordinator

    assert paused.status_code == 200
    assert paused.json()["estado"] == "pausado"
    assert denied.status_code == 403
    assert suspended.status_code == 200
    assert suspended.json()["estado"] == "suspendido"
    assert reactivated.status_code == 200
    assert reactivated.json()["estado"] == "pausado"


def test_pilot_gate_denies_list_and_hides_direct_case(monkeypatch, crypto_environment):
    case_id = create_case(organization_id="org-1", suffix="p", private_marker="encrypted")
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-2")

    listed = client.get("/api/v2/casos-ayuda")
    detail = client.get(f"/api/v2/casos-ayuda/{case_id}")

    assert listed.status_code == 403
    assert detail.status_code == 404


def test_list_endpoint_filters_by_closed_status_values():
    create_case(organization_id="org-1", suffix="1", private_marker="encrypted")

    valid = client.get("/api/v2/casos-ayuda", params={"estado": "borrador"})
    invalid = client.get("/api/v2/casos-ayuda", params={"estado": "inventado"})

    assert valid.status_code == 200
    assert len(valid.json()) == 1
    assert invalid.status_code == 422


def test_create_endpoint_encrypts_identity_before_persistence(crypto_environment):
    marker_name = "PLAINTEXT-NAME-MARKER"
    marker_identity = "V-12.345.678"
    response = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": marker_name,
        "beneficiary_identity": marker_identity,
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    })

    assert response.status_code == 201
    assert marker_name not in response.text
    assert marker_identity not in response.text
    with TestingSessionLocal() as db:
        beneficiary = db.query(models.BeneficiarioAyuda).one()
        assert beneficiary.nombres_apellidos_cifrado.startswith("enc:v1:")
        assert beneficiary.cedula_cifrada.startswith("enc:v1:")
        assert marker_name not in beneficiary.nombres_apellidos_cifrado
        assert marker_identity not in beneficiary.cedula_cifrada
        assert marker_identity not in beneficiary.cedula_hash
        audit_payload = " ".join(event.metadata_json for event in db.query(models.AuditoriaCasoAyuda))
        assert marker_name not in audit_payload
        assert marker_identity not in audit_payload


def test_detail_endpoint_returns_protected_beneficiary_summary_and_readiness(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-12345678",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()

    response = client.get(f"/api/v2/casos-ayuda/{created['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert set(response.json()["readiness_blockers"]) == {
        "publicacion_no_configurada",
        "consentimiento_vigente_faltante",
        "cuenta_aprobada_faltante",
        "foto_principal_faltante",
    }
    assert response.json()["beneficiario"] == {
        "nombre_legal": "Nombre privado",
        "cedula_enmascarada": "*****678",
    }
    assert "V-12345678" not in response.text


def test_update_endpoint_encrypts_private_story_and_audits_fields(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-87654321",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    marker = "RELATO-PRIVADO-MARKER"

    response = client.patch(f"/api/v2/casos-ayuda/{created['id']}", json={
        "internal_title": "Tratamiento actualizado",
        "private_story": marker,
    })

    assert response.status_code == 200
    assert response.json()["titulo_interno"] == "Tratamiento actualizado"
    assert response.json()["relato_privado"] == marker
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, created["id"])
        assert case.relato_privado_cifrado.startswith("enc:v1:")
        assert marker not in case.relato_privado_cifrado
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="caso_actualizado").one()
        assert marker not in audit.metadata_json


def test_update_endpoint_encrypts_profession_inside_conditional_detail(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-13579246",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    marker = "PROFESION-PRIVADA-MARKER"

    response = client.patch(f"/api/v2/casos-ayuda/{created['id']}", json={"profession": marker})

    assert response.status_code == 200
    assert response.json()["profesion"] == marker
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, created["id"])
        assert case.detalle_condicional_cifrado.startswith("enc:v1:")
        assert marker not in case.detalle_condicional_cifrado

    updated_marker = "PROFESION-ACTUALIZADA-MARKER"
    second_response = client.patch(f"/api/v2/casos-ayuda/{created['id']}", json={"profession": updated_marker})
    assert second_response.json()["profesion"] == updated_marker


def test_submit_and_ready_endpoints_expose_only_blocker_codes(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-11223344",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()

    submitted = client.post(f"/api/v2/casos-ayuda/{created['id']}/enviar-validacion")
    ready = client.post(f"/api/v2/casos-ayuda/{created['id']}/marcar-listo")

    assert submitted.status_code == 200
    assert submitted.json()["estado"] == "pendiente_validacion"
    assert ready.status_code == 422
    assert "blockers" in ready.json()["detail"]
    assert "Nombre privado" not in ready.text


def test_verification_endpoint_encrypts_private_data_and_updates_blockers(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-44556677",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    representative_marker = "REPRESENTANTE-PRIVADO-MARKER"

    response = client.post(f"/api/v2/casos-ayuda/{created['id']}/verificacion", json={
        "is_minor": True,
        "representative_name": representative_marker,
        "representative_relationship": "madre",
        "representative_authority_verified": True,
    })

    assert response.status_code == 200
    assert "autoridad_representante_no_verificada" not in response.json()["readiness_blockers"]
    assert representative_marker not in response.text
    with TestingSessionLocal() as db:
        beneficiary = db.query(models.BeneficiarioAyuda).one()
        assert beneficiary.representante_nombre_cifrado.startswith("enc:v1:")
        assert representative_marker not in beneficiary.representante_nombre_cifrado


def test_publication_endpoint_updates_version_and_readiness(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-55667788",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    payload = {
        "public_name": "Ana",
        "public_title": "Ayuda para tratamiento",
        "public_description": "Descripción autorizada",
        "general_location": "Caracas",
        "social_networks": {"instagram": "@cuenta_autorizada"},
    }

    first = client.put(f"/api/v2/casos-ayuda/{created['id']}/publicacion", json=payload)
    second = client.put(f"/api/v2/casos-ayuda/{created['id']}/publicacion", json={
        **payload,
        "public_title": "Ayuda para tratamiento actualizado",
    })

    assert first.status_code == 200
    assert second.status_code == 200
    assert "publicacion_no_configurada" not in second.json()["readiness_blockers"]
    with TestingSessionLocal() as db:
        publication = db.query(models.PublicacionCasoAyuda).one()
        assert publication.version == 2
        assert publication.titulo_publico == "Ayuda para tratamiento actualizado"


def test_published_publication_update_requires_bounded_current_consent_assertion(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-55667789",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    with TestingSessionLocal() as db:
        make_publishable_case(db, created["id"])
        db.query(models.CuentaCasoAyuda).filter_by(caso_id=created["id"]).delete()
        publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=created["id"]).one()
        publication.activa = True
        db.get(models.CasoAyudaV2, created["id"]).estado = "publicado"
        db.commit()
    app.dependency_overrides[require_verified_case_organization_actor] = lambda: ActorOrgContext(
        "org-1", "admin", "admin@example.com",
    )
    payload = {
        "public_name": "Ana",
        "public_title": "Ayuda para tratamiento actualizado",
        "public_description": "Descripcion autorizada actualizada",
        "general_location": "Caracas",
        "social_networks": {},
    }
    try:
        absent = client.put(f"/api/v2/casos-ayuda/{created['id']}/publicacion", json=payload)
        denied = client.put(
            f"/api/v2/casos-ayuda/{created['id']}/publicacion",
            json={**payload, "within_current_consent_scope": False},
        )
        accepted = client.put(
            f"/api/v2/casos-ayuda/{created['id']}/publicacion",
            json={**payload, "within_current_consent_scope": True},
        )
    finally:
        app.dependency_overrides[require_verified_case_organization_actor] = coordinator

    assert absent.status_code == 400
    assert denied.status_code == 400
    assert accepted.status_code == 200
    with TestingSessionLocal() as db:
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(
            accion="publicacion_configurada",
            actor_id="admin@example.com",
        ).one()
        assert json.loads(audit.metadata_json) == {
            "dentro_alcance_consentimiento_vigente": True,
            "version_publicacion_anterior": 1,
            "version_publicacion_nueva": 2,
        }


def test_expanded_consent_action_withdraws_consent_and_hides_publication(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-55667790",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    with TestingSessionLocal() as db:
        make_publishable_case(db, created["id"])
        db.query(models.CuentaCasoAyuda).filter_by(caso_id=created["id"]).delete()
        case = db.get(models.CasoAyudaV2, created["id"])
        case.estado = "publicado"
        db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id).one().activa = True
        db.commit()
    app.dependency_overrides[require_verified_case_organization_actor] = lambda: ActorOrgContext(
        "", "super_admin", "superadmin@example.com",
    )
    try:
        response = client.post(
            f"/api/v2/casos-ayuda/{created['id']}/publicacion/requerir-consentimiento-ampliado"
        )
    finally:
        app.dependency_overrides[require_verified_case_organization_actor] = coordinator

    assert response.status_code == 200
    assert response.json()["estado"] == "pendiente_validacion"
    assert response.json()["publicacion"]["activa"] is False
    assert response.json()["consentimiento"]["vigente"] is False
    assert "consentimiento_vigente_faltante" in response.json()["readiness_blockers"]
    assert client.get(f"/api/v2/public/casos-ayuda/{created['public_id']}").status_code == 404
    with TestingSessionLocal() as db:
        consent = db.query(models.ConsentimientoCasoAyuda).filter_by(caso_id=created["id"]).one()
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(
            accion="consentimiento_ampliado_requerido"
        ).one()
        assert consent.vigente is False
        assert consent.motivo_retiro == "ampliacion_alcance_publicacion"
        assert json.loads(audit.metadata_json) == {
            "estado_anterior": "publicado",
            "estado_nuevo": "pendiente_validacion",
            "consentimiento_version": 1,
            "publicacion_version": 1,
        }


def test_consent_endpoint_encrypts_private_evidence_and_updates_readiness(crypto_environment, tmp_path, monkeypatch):
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path))
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-66778899",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    evidence = b"%PDF-1.4 CONSENT-EVIDENCE-PLAINTEXT-MARKER"

    response = client.post(f"/api/v2/casos-ayuda/{created['id']}/consentimiento", json={
        "text_version": "mvp1-v1",
        "scope": {"historia": True, "meta": True, "cuentas": True},
        "signer_name": "Firmante privado",
        "signer_type": "beneficiario",
        "evidence_file_name": "consentimiento.pdf",
        "evidence_content_type": "application/pdf",
        "evidence_base64": base64.b64encode(evidence).decode(),
    })

    assert response.status_code == 200
    assert "consentimiento_vigente_faltante" not in response.json()["readiness_blockers"]
    assert "evidencia_consentimiento_faltante" not in response.json()["readiness_blockers"]
    files = list(Path(tmp_path).rglob("*.enc"))
    assert len(files) == 1
    assert evidence not in files[0].read_bytes()
    with TestingSessionLocal() as db:
        consent = db.query(models.ConsentimientoCasoAyuda).one()
        document = db.get(models.DocumentoCasoAyuda, consent.evidencia_documento_id)
        assert document.storage_path.startswith("private/casos-ayuda/")
        assert document.nombre_original_cifrado.startswith("enc:v1:")


def test_consent_endpoint_accepts_electronic_consent_without_file(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-10000123",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()

    response = client.post(f"/api/v2/casos-ayuda/{created['id']}/consentimiento", json={
        "text_version": "mvp1-v1",
        "scope": {"historia": True, "meta": True, "cuentas": True},
        "signer_name": "Nombre privado",
        "signer_type": "beneficiario",
    })

    assert response.status_code == 200
    assert response.json()["consentimiento"] == {
        "registrado": True,
        "version": 1,
        "tipo_firmante": "beneficiario",
        "evidencia_adjunta": False,
        "vigente": True,
    }
    assert "consentimiento_vigente_faltante" not in response.json()["readiness_blockers"]
    assert "evidencia_consentimiento_faltante" not in response.json()["readiness_blockers"]


def add_current_consent(case_id):
    with TestingSessionLocal() as db:
        evidence = models.DocumentoCasoAyuda(
            caso_id=case_id,
            document_key=f"consent-{case_id}",
            version=1,
            tipo="consentimiento",
            clasificacion="privado",
            estado_revision="aprobado",
            storage_path=f"private/casos-ayuda/{case_id}/consent.enc",
            content_type="application/pdf",
            size_bytes=100,
            checksum_sha256="c" * 64,
            cargado_por="coordinador@example.com",
        )
        db.add(evidence)
        db.flush()
        db.add(models.ConsentimientoCasoAyuda(
            caso_id=case_id,
            version=1,
            texto_version="mvp1-v1",
            alcance_json="{}",
            firmante_nombre_cifrado="enc:v1:test",
            evidencia_documento_id=evidence.id,
            registrado_por="coordinador@example.com",
        ))
        db.commit()


def test_account_endpoint_encrypts_bank_and_responsible_data(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-77889900",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    add_current_consent(created["id"])
    bank_marker = "BANK-ACCOUNT-PLAINTEXT-MARKER"
    email_marker = "responsable@example.com"

    response = client.post(f"/api/v2/casos-ayuda/{created['id']}/cuentas", json={
        "account_key": "principal",
        "owner_type": "beneficiario",
        "holder_name": "Titular privado",
        "beneficiary_relationship": "propia",
        "medium": "banco_venezolano",
        "currency": "USD",
        "identifier": bank_marker,
        "instructions": "Transferencia bancaria",
        "responsible_name": "Responsable privado",
        "responsible_email": email_marker,
    })

    assert response.status_code == 200
    assert "cuenta_aprobada_faltante" not in response.json()["readiness_blockers"]
    account_summary = response.json()["accounts"][0]
    assert account_summary["estado"] == "aprobada"
    assert account_summary["titular_nombre"] == "Titular privado"
    assert account_summary["relacion_beneficiario"] == "propia"
    assert account_summary["instrucciones"] == "Transferencia bancaria"
    assert account_summary["responsable_nombre"] == "Responsable privado"
    assert account_summary["identificador_enmascarado"].endswith("RKER")
    assert account_summary["responsable_email"] == email_marker
    assert account_summary["responsable_email_enmascarado"] == "r***@example.com"
    assert bank_marker not in response.text
    with TestingSessionLocal() as db:
        account = db.query(models.CuentaCasoAyuda).one()
        assert account.identificador_cifrado.startswith("enc:v1:")
        assert account.responsable_email_cifrado.startswith("enc:v1:")
        assert bank_marker not in account.identificador_cifrado
        assert email_marker not in account.responsable_email_cifrado
        assert len(account.responsable_email_hash) == 64


def test_exceptional_account_is_pending_and_creator_cannot_approve(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-88990011",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    add_current_consent(created["id"])
    response = client.post(f"/api/v2/casos-ayuda/{created['id']}/cuentas", json={
        "account_key": "tercero",
        "owner_type": "tercero",
        "holder_name": "Titular privado",
        "beneficiary_relationship": "familiar",
        "medium": "zelle",
        "currency": "USD",
        "identifier": "private@example.com",
        "justification": "Cuenta autorizada excepcionalmente",
        "responsible_name": "Responsable privado",
        "responsible_email": "responsable@example.com",
    })
    account_id = response.json()["accounts"][0]["id"]
    approval = client.post(f"/api/v2/casos-ayuda/{created['id']}/cuentas/{account_id}/aprobar")

    assert response.status_code == 200
    assert response.json()["accounts"][0]["estado"] == "pendiente"
    assert approval.status_code == 400


def make_publishable_case(db, case_id):
    case = db.get(models.CasoAyudaV2, case_id)
    evidence = models.DocumentoCasoAyuda(
        caso_id=case.id,
        document_key=f"consent-publish-{case.id}",
        version=1,
        tipo="consentimiento",
        clasificacion="privado",
        estado_revision="aprobado",
        storage_path=f"private/casos-ayuda/{case.id}/consent.enc",
        content_type="application/pdf",
        size_bytes=100,
        checksum_sha256="d" * 64,
        cargado_por="coordinador@example.com",
    )
    db.add(evidence)
    db.flush()
    db.add(models.ConsentimientoCasoAyuda(
        caso_id=case.id,
        version=1,
        texto_version="mvp1-v1",
        alcance_json="{}",
        firmante_nombre_cifrado="enc:v1:signer",
        evidencia_documento_id=evidence.id,
        registrado_por="coordinador@example.com",
    ))
    db.add(models.PublicacionCasoAyuda(
        caso_id=case.id,
        nombre_publico="Ana",
        titulo_publico="Ayuda para tratamiento",
        descripcion_publica="Descripcion pública autorizada",
        localidad_general="Caracas",
        version=1,
        configurado_por="coordinador@example.com",
    ))
    db.add(models.CuentaCasoAyuda(
        caso_id=case.id,
        account_key="principal",
        version=1,
        tipo_titular="beneficiario",
        titular_nombre_cifrado="enc:v1:holder",
        relacion_beneficiario="propia",
        medio="banco_venezolano",
        moneda="USD",
        identificador_cifrado="enc:v1:bank",
        responsable_nombre_cifrado="enc:v1:responsible",
        responsable_email_hash="e" * 64,
        responsable_email_cifrado="enc:v1:email",
        consentimiento_version=1,
        estado="aprobada",
        creado_por="coordinador@example.com",
        aprobado_por="coordinador@example.com",
    ))
    db.add(models.DocumentoCasoAyuda(
        caso_id=case.id,
        document_key=f"foto-principal-{case.id}",
        version=1,
        tipo="foto_principal",
        clasificacion="publico",
        estado_revision="aprobado",
        storage_path=f"public/casos-ayuda/{case.id}/photo.enc",
        content_type="image/jpeg",
        size_bytes=100,
        checksum_sha256="p" * 64,
        consentimiento_version=1,
        cargado_por="coordinador@example.com",
        revisado_por="coordinador@example.com",
    ))
    db.flush()
    case.estado = "listo_publicar"
    db.commit()


def test_publish_endpoint_requires_ready_case_and_public_api_excludes_private_fields(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-99001122",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    with TestingSessionLocal() as db:
        make_publishable_case(db, created["id"])

    published = client.post(f"/api/v2/casos-ayuda/{created['id']}/publicar")
    public_list = client.get("/api/v2/public/casos-ayuda")
    public_item = next(item for item in public_list.json() if item["id"] == created["id"])
    public_detail = client.get(f"/api/v2/public/casos-ayuda/{public_item['public_id']}")

    assert published.status_code == 200
    assert public_list.status_code == 200
    assert public_detail.status_code == 200
    assert "Nombre privado" not in public_list.text
    assert "cedula" not in public_list.text.lower()
    assert "identificador" not in public_list.text.lower()


def test_public_endpoints_do_not_500_for_a_published_non_monetary_case(crypto_environment):
    with TestingSessionLocal() as db:
        case = models.CasoAyudaV2(
            public_id="public-non-monetary-1",
            organizacion_id="org-1",
            tipo_sujeto="campana_organizacion",
            titulo_interno="Campana sin meta monetaria",
            categoria="insumo_recurso",
            acepta_ayuda_monetaria=False,
            acepta_ayuda_directa=True,
            estado="publicado",
            creado_por="coordinador@example.com",
        )
        db.add(case)
        db.flush()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Campana sin meta monetaria",
            titulo_publico="Campana sin meta monetaria",
            descripcion_publica="Descripcion publica de una campana sin monto ni moneda.",
            version=1,
            activa=True,
            configurado_por="coordinador@example.com",
        ))
        db.commit()
        public_id = case.public_id

    detail = client.get(f"/api/v2/public/casos-ayuda/{public_id}")
    listing = client.get("/api/v2/public/casos-ayuda")

    assert detail.status_code == 200
    assert detail.json()["meta_monto"] is None
    assert detail.json()["meta_moneda"] is None
    assert listing.status_code == 200


def test_preview_matches_public_detail_after_publication_except_lifecycle_fields(crypto_environment):
    created = client.post("/api/v2/casos-ayuda", json={
        "beneficiary_name": "Nombre privado",
        "beneficiary_identity": "V-10101010",
        "internal_title": "Tratamiento inicial",
        "category": "medicamentos",
        "goal_amount": "250.00",
        "goal_currency": "USD",
    }).json()
    with TestingSessionLocal() as db:
        make_publishable_case(db, created["id"])

    preview = client.get(f"/api/v2/casos-ayuda/{created['id']}/preview")
    published = client.post(f"/api/v2/casos-ayuda/{created['id']}/publicar")
    detail = client.get(f"/api/v2/public/casos-ayuda/{created['public_id']}")

    assert preview.status_code == 200
    assert published.status_code == 200
    assert detail.status_code == 200
    preview_dto = preview.json()
    public_dto = detail.json()
    assert preview_dto.pop("estado") == "listo_publicar"
    assert public_dto.pop("estado") == "publicado"
    assert preview_dto.pop("publicado_at") is None
    assert public_dto.pop("publicado_at") is not None
    assert preview_dto == public_dto
    assert "organizacion_id" not in preview.text
    assert "beneficiario" not in preview.text
    assert "accounts" not in preview.text


def test_preview_hides_case_from_another_organization(crypto_environment):
    case_id = create_case(organization_id="org-1", suffix="preview-org", private_marker="encrypted")
    app.dependency_overrides[require_verified_case_organization_actor] = lambda: ActorOrgContext(
        "org-2", "coordinador", "other@example.com",
    )
    try:
        response = client.get(f"/api/v2/casos-ayuda/{case_id}/preview")
    finally:
        app.dependency_overrides[require_verified_case_organization_actor] = coordinator

    assert response.status_code == 404


def test_public_manifest_uses_only_latest_approved_public_document_under_current_consent(
    crypto_environment,
):
    case_id = create_case(organization_id="org-1", suffix="manifest", private_marker="encrypted")
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, case_id)
        publication = models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Ana",
            titulo_publico="Ayuda para tratamiento",
            descripcion_publica="Descripcion publica autorizada",
            localidad_general="Caracas",
            version=1,
            configurado_por="coordinador@example.com",
        )
        old_consent = models.ConsentimientoCasoAyuda(
            caso_id=case.id,
            version=1,
            texto_version="mvp1-v1",
            alcance_json="{}",
            firmante_nombre_cifrado="encrypted-signer",
            registrado_por="coordinador@example.com",
            vigente=False,
        )
        current_consent = models.ConsentimientoCasoAyuda(
            caso_id=case.id,
            version=2,
            texto_version="mvp1-v2",
            alcance_json="{}",
            firmante_nombre_cifrado="encrypted-signer",
            registrado_por="coordinador@example.com",
            vigente=True,
        )
        db.add_all([publication, old_consent, current_consent])
        db.flush()

        document_specs = [
            ("current", 1, "publico", "aprobado", 2),
            ("current", 2, "publico", "aprobado", 2),
            ("now-private", 1, "publico", "aprobado", 2),
            ("now-private", 2, "privado", "aprobado", None),
            ("now-rejected", 1, "publico", "aprobado", 2),
            ("now-rejected", 2, "publico", "rechazado", 2),
            ("stale-consent", 1, "publico", "aprobado", 1),
        ]
        documents = []
        for index, (document_key, version, classification, review_state, consent_version) in enumerate(
            document_specs,
            start=1,
        ):
            document = models.DocumentoCasoAyuda(
                caso_id=case.id,
                document_key=document_key,
                version=version,
                tipo="informe_medico",
                clasificacion=classification,
                estado_revision=review_state,
                storage_path=f"{classification}/cases/{case.id}/document-{index}.pdf",
                content_type="application/pdf",
                size_bytes=100,
                checksum_sha256=f"{index:x}" * 64,
                consentimiento_version=consent_version,
                cargado_por="uploader@example.com",
                revisado_por="reviewer@example.com",
            )
            documents.append(document)
            db.add(document)
        db.commit()
        expected_document_id = documents[1].id

    preview = client.get(f"/api/v2/casos-ayuda/{case_id}/preview")
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, case_id)
        case.estado = "publicado"
        db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case_id).one().activa = True
        db.commit()
        public_id = case.public_id
    detail = client.get(f"/api/v2/public/casos-ayuda/{public_id}")

    assert preview.status_code == 200
    assert detail.status_code == 200
    expected_manifest = [{
        "id": expected_document_id,
        "tipo": "informe_medico",
        "content_type": "application/pdf",
    }]
    assert preview.json()["documentos"] == expected_manifest
    assert detail.json()["documentos"] == expected_manifest
