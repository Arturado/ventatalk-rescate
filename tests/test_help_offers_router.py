import base64

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
def isolated_database(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
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


def create_offer(public_id, *, description="Tengo 10 colchones nuevos para donar.", contact="+58 412-1234567", key="offer-router-key-0000001"):
    return client.post(
        f"/api/v2/donante/casos-ayuda/{public_id}/ofertas",
        headers={"Idempotency-Key": key},
        json={"description": description, "contact_details": contact},
    )


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
