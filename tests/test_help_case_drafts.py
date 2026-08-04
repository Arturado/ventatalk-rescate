import base64
import os
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import ActorOrgContext
import models
import schemas
from services.help_case_drafts import DraftIdempotencyConflictError, create_help_case_draft
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


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def cipher_env(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"0" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"1" * 32).decode())


def actor(*, organization_id="org-1", role="coordinador"):
    return ActorOrgContext(organizacion_id=organization_id, role=role, email="coordinador@example.com")


def persona_payload(**overrides):
    data = dict(
        category="salud",
        subject_type="persona",
        aid_modes=["monetaria"],
        beneficiary_name="Ana Perez",
        beneficiary_identity="V-11111111",
        title="Necesita tratamiento",
        story="Historia clinica resumida de la persona" * 2,
        goal_amount=Decimal("100.00"),
        goal_currency="USD",
    )
    data.update(overrides)
    return schemas.CasoAyudaV2DraftCreateRequest(**data)


def campana_payload(**overrides):
    data = dict(
        category="insumo_recurso",
        subject_type="campana_organizacion",
        aid_modes=["directa"],
        title="Colecta de colchones",
        story="Necesitamos colchones para el centro de refugio" * 2,
        direct_request="Colchones individuales",
    )
    data.update(overrides)
    return schemas.CasoAyudaV2DraftCreateRequest(**data)


def create(db, payload, *, key="draft-key-0000001", organization_id="org-1"):
    return create_help_case_draft(
        db,
        actor=actor(organization_id=organization_id),
        organization_id=organization_id,
        public_id=f"ayuda-{key}",
        payload=payload,
        idempotency_key=key,
        cipher=HelpDataCipher.from_environment(),
    )


def test_creates_persona_draft_with_encrypted_beneficiary_and_monetary_summary():
    with TestingSessionLocal() as db:
        summary, created = create(db, persona_payload())
        db.commit()

        assert created is True
        assert summary["category"] == "salud"
        assert summary["subject_type"] == "persona"
        assert summary["aid_modes"] == ["monetaria"]
        assert summary["goal_amount"] == Decimal("100.00")
        assert summary["state"] == "borrador"

        case = db.query(models.CasoAyudaV2).filter_by(public_id=summary["public_id"]).one()
        assert case.beneficiario_id is not None
        assert case.acepta_ayuda_monetaria is True
        assert case.acepta_ayuda_directa is False
        beneficiary = db.get(models.BeneficiarioAyuda, case.beneficiario_id)
        assert beneficiary.cedula_cifrada != "V-11111111"


def test_creates_campana_draft_without_beneficiary_and_no_financial_fields():
    with TestingSessionLocal() as db:
        summary, created = create(db, campana_payload())
        db.commit()

        assert created is True
        assert summary["subject_type"] == "campana_organizacion"
        assert summary["aid_modes"] == ["directa"]
        assert summary["goal_amount"] is None

        case = db.query(models.CasoAyudaV2).filter_by(public_id=summary["public_id"]).one()
        assert case.beneficiario_id is None
        assert case.detalle_condicional_cifrado is not None


def test_reused_identity_reuses_beneficiary_across_two_drafts_same_organization():
    with TestingSessionLocal() as db:
        create(db, persona_payload(), key="draft-key-0000001")
        db.commit()
        create(db, persona_payload(title="Otra necesidad distinta"), key="draft-key-0000002")
        db.commit()

        assert db.query(models.BeneficiarioAyuda).count() == 1
        assert db.query(models.CasoAyudaV2).count() == 2


def test_same_idempotency_key_and_payload_replays_the_same_case():
    with TestingSessionLocal() as db:
        first_summary, first_created = create(db, persona_payload(), key="draft-key-0000003")
        db.commit()
        second_summary, second_created = create(db, persona_payload(), key="draft-key-0000003")
        db.commit()

        assert first_created is True
        assert second_created is False
        assert first_summary["public_id"] == second_summary["public_id"]
        assert db.query(models.CasoAyudaV2).count() == 1


def test_same_idempotency_key_with_different_payload_conflicts():
    with TestingSessionLocal() as db:
        create(db, persona_payload(), key="draft-key-0000004")
        db.commit()

        with pytest.raises(DraftIdempotencyConflictError):
            create(db, persona_payload(title="Titulo completamente distinto"), key="draft-key-0000004")
