from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
import models
from routers.casos_ayuda import router


API_KEY = "test-api-key"

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

app = FastAPI()
app.include_router(router)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("API_KEY", API_KEY)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_case_with_contact(status="publicado"):
    with TestingSessionLocal() as db:
        case = models.CasoAyuda(
            cedula="12345678",
            nombre="Caso de prueba",
            relato="Relato de prueba",
            condicion_resumen="Condición de prueba",
            estado=status,
            consentimiento_publicacion=True,
            creado_por="coordinador@example.com",
        )
        db.add(case)
        db.flush()
        db.add(
            models.ContactoCasoAyuda(
                caso_id=case.id,
                telefono="04121234567",
                metodo_pago="Transferencia",
                datos_pago="0102-0000-00-0000000000",
            )
        )
        db.commit()
        return case.id


def test_contact_rejects_request_without_api_key():
    case_id = create_case_with_contact()

    response = client.get(f"/api/casos-ayuda/{case_id}/contacto")

    assert response.status_code == 401


def test_contact_rejects_invalid_api_key():
    case_id = create_case_with_contact()

    response = client.get(
        f"/api/casos-ayuda/{case_id}/contacto",
        headers={"x-api-key": "invalid"},
    )

    assert response.status_code == 401


def test_contact_rejects_unpublished_case_even_with_api_key():
    case_id = create_case_with_contact(status="pendiente_revision")

    response = client.get(
        f"/api/casos-ayuda/{case_id}/contacto",
        headers={"x-api-key": API_KEY},
    )

    assert response.status_code == 404


def test_contact_returns_published_case_and_audits_authenticated_actor():
    case_id = create_case_with_contact()

    response = client.get(
        f"/api/casos-ayuda/{case_id}/contacto",
        headers={
            "x-api-key": API_KEY,
            "x-actor-id": "DONANTE@EXAMPLE.COM",
        },
    )

    assert response.status_code == 200
    assert response.json()["datos_pago"] == "0102-0000-00-0000000000"

    with TestingSessionLocal() as db:
        event = db.query(models.CasoAyudaHistorial).one()
        assert event.tipo_cambio == "contacto_accedido"
        assert event.cambiado_por == "donante@example.com"
