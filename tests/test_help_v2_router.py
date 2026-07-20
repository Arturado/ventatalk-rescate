from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
from routers.casos_ayuda_v2 import router
from services.help_cases import create_help_case


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


def test_list_endpoint_filters_by_closed_status_values():
    create_case(organization_id="org-1", suffix="1", private_marker="encrypted")

    valid = client.get("/api/v2/casos-ayuda", params={"estado": "borrador"})
    invalid = client.get("/api/v2/casos-ayuda", params={"estado": "inventado"})

    assert valid.status_code == 200
    assert len(valid.json()) == 1
    assert invalid.status_code == 422
