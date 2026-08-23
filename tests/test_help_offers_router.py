import base64
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_actor_org, require_verified_case_organization_actor, verify_api_key
import models
from routers.casos_ayuda_ofertas import router as donor_offers_router
from routers.casos_ayuda_publicos import router as public_cases_router
from routers.casos_ayuda_v2 import router as management_router


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
app.include_router(donor_offers_router)
app.include_router(management_router)
app.include_router(public_cases_router)


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


def donor_actor():
    return ActorOrgContext("", "donor", "donante@example.com", uid="firebase-donor-1", ip_hash="a" * 64)


def coordinator_actor():
    return ActorOrgContext("org-1", "coordinador", "coordinador@example.com")


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "server@example.com"
app.dependency_overrides[require_verified_actor_org] = donor_actor
app.dependency_overrides[require_verified_case_organization_actor] = coordinator_actor
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path / "uploads"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_published_campaign_case(*, public_id="oferta-publicada-1", organization_id="org-1"):
    with TestingSessionLocal() as db:
        case = models.CasoAyudaV2(
            public_id=public_id,
            organizacion_id=organization_id,
            tipo_sujeto="campana_organizacion",
            titulo_interno="Colecta de colchones",
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
            nombre_publico="Colecta de colchones",
            titulo_publico="Colecta de colchones",
            descripcion_publica="Descripcion publica suficiente para la colecta.",
            version=1,
            activa=True,
        ))
        db.commit()
        return case.id, case.public_id


def create_published_employment_case(
    *, public_id="empleo-publicado-1", organization_id="org-1", state="publicado",
):
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id=organization_id,
            nombres_apellidos_cifrado="encrypted-beneficiary",
            cedula_hash=(public_id.encode().hex() + "0" * 64)[:64],
            cedula_cifrada="encrypted-identity",
            creado_por="coordinador@example.com",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id=public_id,
            organizacion_id=organization_id,
            beneficiario_id=beneficiary.id,
            tipo_sujeto="persona",
            titulo_interno="Busqueda de empleo",
            categoria="empleo",
            acepta_ayuda_monetaria=False,
            acepta_ayuda_directa=False,
            estado=state,
            creado_por="coordinador@example.com",
        )
        db.add(case)
        db.flush()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Persona con experiencia",
            titulo_publico="Busca una oportunidad laboral",
            descripcion_publica="Descripcion publica suficiente para el caso laboral.",
            version=1,
            activa=True,
        ))
        db.commit()
        return case.id, case.public_id


def create_offer(public_id, *, description="Tengo 10 colchones nuevos para donar.", contact="+58 412-1234567", key="offer-router-key-0000001"):
    return client.post(
        f"/api/v2/donante/casos-ayuda/{public_id}/ofertas",
        headers={"Idempotency-Key": key},
        json={"description": description, "contact_details": contact},
    )


def create_labor_offer_request(public_id, *, key="labor-router-key-0000001", override=None):
    payload = {
        "tipo_trabajo": "Contrato por proyecto",
        "descripcion": "Diseno de materiales informativos accesibles.",
        "remuneracion_estimada": "USD 450 por proyecto",
        "telefono": "+58 412-0000000",
        "correo": "Oferta@Example.Test",
        **(override or {}),
    }
    return client.post(
        f"/api/v2/donante/casos-ayuda/{public_id}/ofertas-laborales",
        headers={"Idempotency-Key": key},
        json=payload,
    )


def create_draft_case_for_gallery(*, public_id="gallery-upload-case"):
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-1",
            nombres_apellidos_cifrado="encrypted-beneficiary",
            cedula_hash=(public_id.encode().hex() + "0" * 64)[:64],
            cedula_cifrada="encrypted-identity",
            creado_por="coordinador@example.com",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id=public_id,
            organizacion_id="org-1",
            beneficiario_id=beneficiary.id,
            tipo_sujeto="persona",
            titulo_interno="Caso para galeria",
            categoria="salud",
            acepta_ayuda_monetaria=True,
            acepta_ayuda_directa=False,
            meta_monto=100,
            meta_moneda="USD",
            estado="borrador",
            creado_por="coordinador@example.com",
        )
        db.add(case)
        db.commit()
        return case.id


def upload_gallery(case_id, *, raw, content_type, file_name, key="foto_galeria:prueba"):
    return client.post(
        f"/api/v2/casos-ayuda/{case_id}/documentos",
        json={
            "document_key": key,
            "document_type": "foto_galeria",
            "classification": "publico",
            "file_name": file_name,
            "content_type": content_type,
            "content_base64": base64.b64encode(raw).decode(),
        },
    )


def test_donor_can_create_private_idempotent_labor_offer():
    _, public_id = create_published_employment_case()

    first = create_labor_offer_request(public_id)
    second = create_labor_offer_request(public_id)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert first.json()["tipo"] == "empleo"
    assert first.json()["status"] == "pendiente_respuesta"
    for private_value in ("proyecto", "materiales", "412-0000000", "oferta@example.test"):
        assert private_value not in first.text.lower()
    with TestingSessionLocal() as db:
        assert db.query(models.OfertaAyudaDirecta).count() == 1


@pytest.mark.parametrize(
    ("raw", "content_type", "file_name"),
    [
        (b"\xff\xd8\xffreal-jpeg", "image/jpeg", "galeria.jpg"),
        (b"\x89PNG\r\n\x1a\nreal-png", "image/png", "galeria.png"),
    ],
)
def test_gallery_real_upload_accepts_matching_image_bytes(raw, content_type, file_name):
    case_id = create_draft_case_for_gallery(public_id=f"gallery-valid-{content_type[-3:]}")

    response = upload_gallery(case_id, raw=raw, content_type=content_type, file_name=file_name)

    assert response.status_code == 201
    assert response.json()["tipo"] == "foto_galeria"
    assert response.json()["content_type"] == content_type


@pytest.mark.parametrize(
    ("raw", "content_type", "file_name", "expected_status"),
    [
        (b"arbitrary-not-an-image", "image/jpeg", "galeria.jpg", 400),
        (b"\x89PNG\r\n\x1a\npng-as-jpeg", "image/jpeg", "galeria.jpg", 400),
        (b"\xff\xd8\xffjpeg-wrong-extension", "image/jpeg", "galeria.png", 422),
    ],
)
def test_gallery_real_upload_rejects_fake_mime_and_incompatible_extension(
    raw, content_type, file_name, expected_status,
):
    case_id = create_draft_case_for_gallery(public_id=f"gallery-invalid-{expected_status}-{len(raw)}")

    response = upload_gallery(case_id, raw=raw, content_type=content_type, file_name=file_name)

    assert response.status_code == expected_status
    assert base64.b64encode(raw).decode() not in response.text
    with TestingSessionLocal() as db:
        assert db.query(models.DocumentoCasoAyuda).count() == 0


def test_employment_public_response_has_only_labor_mode_without_financial_or_org_fields():
    _, public_id = create_published_employment_case(public_id="empleo-public-contract")

    response = client.get(f"/api/v2/public/casos-ayuda/{public_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["modalidades"] == ["oferta_laboral"]
    assert payload["acepta_ayuda_monetaria"] is False
    assert payload["acepta_ayuda_directa"] is False
    for forbidden in (
        "organizacion_id",
        "organizacion_nombre",
        "prioridad_especial",
        "meta_monto",
        "meta_moneda",
        "monto_confirmado",
        "ayudas_confirmadas",
    ):
        assert forbidden not in payload


def test_labor_offer_router_rejects_non_labor_case_and_extra_actor_fields():
    _, direct_public_id = create_published_campaign_case()
    _, labor_public_id = create_published_employment_case(public_id="empleo-extra-fields")

    assert create_labor_offer_request(direct_public_id).status_code == 404
    response = create_labor_offer_request(labor_public_id, override={"organizacion_id": "org-atacante"})
    assert response.status_code == 422
    assert "org-atacante" not in response.text


@pytest.mark.parametrize("state", ["pausado", "meta_alcanzada", "cerrado"])
def test_labor_offer_router_rejects_non_actionable_employment_states(state):
    _, public_id = create_published_employment_case(
        public_id=f"empleo-{state}", state=state,
    )

    response = create_labor_offer_request(public_id)

    assert response.status_code == 404
    assert response.json() == {"detail": "Caso laboral publicado no encontrado"}


def test_labor_offer_router_rejects_missing_case_and_invalid_modality():
    missing = create_labor_offer_request("empleo-inexistente")
    _, public_id = create_published_employment_case(public_id="empleo-modalidad-invalida")
    invalid_mode = create_labor_offer_request(public_id, override={"aid_modes": ["directa"]})

    assert missing.status_code == 404
    assert invalid_mode.status_code == 422
    assert invalid_mode.json() == {"detail": "Payload laboral invalido"}
    assert "directa" not in invalid_mode.text


def test_labor_offer_router_rejects_absent_signed_actor(monkeypatch):
    _, public_id = create_published_employment_case(public_id="empleo-actor-ausente")
    app.dependency_overrides.pop(require_verified_actor_org)
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", "test-only-actor-secret")
    try:
        response = create_labor_offer_request(public_id)
    finally:
        app.dependency_overrides[require_verified_actor_org] = donor_actor

    assert response.status_code == 401
    assert response.json() == {"detail": "Falta la firma de organización del actor"}


def test_labor_offer_sensitive_sentinels_never_leave_private_storage(
    caplog, capsys,
):
    _, public_id = create_published_employment_case(public_id="empleo-privacidad-centinelas")
    sentinels = {
        "telefono": "+58-TELEFONO-CENTINELA-005",
        "correo": "CORREO-CENTINELA-005@example.test",
        "descripcion": "DESCRIPCION-CENTINELA-005 suficientemente extensa",
        "remuneracion_estimada": "REMUNERACION-CENTINELA-005",
    }
    with caplog.at_level(logging.DEBUG):
        created = create_labor_offer_request(
            public_id, key="labor-sensitive-key-0001", override=sentinels,
        )
        conflict = create_labor_offer_request(
            public_id,
            key="labor-sensitive-key-0001",
            override={**sentinels, "descripcion": "DESCRIPCION-CENTINELA-005-DISTINTA"},
        )
        invalid = create_labor_offer_request(
            public_id,
            key="labor-sensitive-key-0002",
            override={**sentinels, "aid_modes": ["CENTINELA-MODALIDAD-005"]},
        )

    captured = capsys.readouterr()
    assert created.status_code == 201
    assert conflict.status_code == 409
    assert invalid.status_code == 422
    with TestingSessionLocal() as db:
        offer = db.query(models.OfertaAyudaDirecta).filter_by(tipo="empleo").one()
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(
            accion="oferta_laboral_creada",
        ).one()
        safe_surfaces = "\n".join([
            created.text,
            conflict.text,
            invalid.text,
            audit.metadata_json,
            captured.out,
            captured.err,
            caplog.text,
        ]).lower()
        for sentinel in (*sentinels.values(), "DESCRIPCION-CENTINELA-005-DISTINTA", "CENTINELA-MODALIDAD-005"):
            assert sentinel.lower() not in safe_surfaces
            assert sentinel.lower() not in offer.contacto_cifrado.lower()
            assert sentinel.lower() not in offer.payload_cifrado.lower()


def test_phase1_does_not_release_labor_contact_through_donor_or_public_endpoints():
    _, public_id = create_published_employment_case(public_id="empleo-sin-liberacion-contacto")
    sentinels = {
        "telefono": "+58-TELEFONO-NO-LIBERAR-005",
        "correo": "no-liberar-005@example.test",
    }

    created = create_labor_offer_request(public_id, override=sentinels)
    public_list = client.get("/api/v2/public/casos-ayuda")
    public_detail = client.get(f"/api/v2/public/casos-ayuda/{public_id}")

    assert created.status_code == 201
    assert public_list.status_code == 200
    assert public_detail.status_code == 200
    combined = f"{created.text}\n{public_list.text}\n{public_detail.text}".lower()
    assert sentinels["telefono"].lower() not in combined
    assert sentinels["correo"].lower() not in combined


def test_donor_can_create_offer_for_published_case_accepting_directa():
    _, public_id = create_published_campaign_case()

    response = create_offer(public_id)

    assert response.status_code == 201
    assert response.json()["status"] == "pendiente"
    assert response.json()["case_public_id"] == public_id
    assert "colchones" not in response.text
    assert "412-1234567" not in response.text


def test_donor_offer_creation_is_idempotent():
    _, public_id = create_published_campaign_case()

    first = create_offer(public_id)
    second = create_offer(public_id)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    with TestingSessionLocal() as db:
        assert db.query(models.OfertaAyudaDirecta).count() == 1


def test_donor_offer_creation_rejects_case_without_modalidad_directa():
    with TestingSessionLocal() as db:
        case = models.CasoAyudaV2(
            public_id="oferta-monetaria-1",
            organizacion_id="org-1",
            tipo_sujeto="campana_organizacion",
            titulo_interno="Tratamiento",
            categoria="salud",
            acepta_ayuda_monetaria=True,
            acepta_ayuda_directa=False,
            meta_monto=100,
            meta_moneda="USD",
            estado="publicado",
            creado_por="coordinador@example.com",
        )
        db.add(case)
        db.flush()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Tratamiento",
            titulo_publico="Tratamiento",
            descripcion_publica="Descripcion publica suficiente.",
            version=1,
            activa=True,
        ))
        db.commit()

    response = create_offer("oferta-monetaria-1")

    assert response.status_code == 404


def test_organization_can_list_and_transition_offers():
    case_id, public_id = create_published_campaign_case()
    create_offer(public_id)

    listed = client.get(f"/api/v2/casos-ayuda/{case_id}/ofertas")
    offer_id = listed.json()[0]["id"]
    contacted = client.post(f"/api/v2/casos-ayuda/{case_id}/ofertas/{offer_id}/contactar", json={})
    completed = client.post(f"/api/v2/casos-ayuda/{case_id}/ofertas/{offer_id}/completar", json={})

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["description"] == "Tengo 10 colchones nuevos para donar."
    assert contacted.status_code == 200
    assert contacted.json()["estado"] == "contactada"
    assert completed.status_code == 200
    assert completed.json()["estado"] == "completada"


def test_organization_transition_rejects_invalid_action_value():
    case_id, public_id = create_published_campaign_case()
    create_offer(public_id)
    listed = client.get(f"/api/v2/casos-ayuda/{case_id}/ofertas")
    offer_id = listed.json()[0]["id"]

    response = client.post(f"/api/v2/casos-ayuda/{case_id}/ofertas/{offer_id}/borrar", json={})

    assert response.status_code == 422


def test_organization_transition_rejects_invalid_state_transition():
    case_id, public_id = create_published_campaign_case()
    create_offer(public_id)
    listed = client.get(f"/api/v2/casos-ayuda/{case_id}/ofertas")
    offer_id = listed.json()[0]["id"]

    response = client.post(f"/api/v2/casos-ayuda/{case_id}/ofertas/{offer_id}/completar", json={})

    assert response.status_code == 409


def test_organization_cannot_list_offers_from_another_organization():
    case_id, public_id = create_published_campaign_case(organization_id="org-2")
    create_offer(public_id)

    response = client.get(f"/api/v2/casos-ayuda/{case_id}/ofertas")

    assert response.status_code == 404
