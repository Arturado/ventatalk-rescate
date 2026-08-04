from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import ActorOrgContext
import models
from services.help_cases import OrganizationAccessError
from services.help_beneficiaries import (
    BeneficiaryDomainError,
    CedulaMatchRateLimitError,
    CEDULA_MATCH_RATE_LIMIT_MAX_REQUESTS,
    find_cedula_matches,
    get_or_create_beneficiary,
)


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
IDENTITY_HASH = "c" * 64


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def actor(*, organization_id="org-1", role="coordinador", email="coordinador@example.com"):
    return ActorOrgContext(organizacion_id=organization_id, role=role, email=email)


def seed_case(db, *, organization_id, public=False, category="salud", title="Necesidad de prueba"):
    beneficiary, _ = get_or_create_beneficiary(
        db,
        organization_id=organization_id,
        identity_hash=IDENTITY_HASH,
        identity_encrypted="encrypted-id",
        name_encrypted="encrypted-name",
        actor_email="coordinador@example.com",
    )
    case = models.CasoAyudaV2(
        public_id=f"case-{organization_id}",
        organizacion_id=organization_id,
        beneficiario_id=beneficiary.id,
        titulo_interno=title,
        categoria=category,
        meta_monto=Decimal("100.00"),
        meta_moneda="USD",
        estado="publicado" if public else "borrador",
        creado_por="coordinador@example.com",
    )
    db.add(case)
    db.flush()
    if public:
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Publico",
            titulo_publico="Ayuda publica de prueba",
            descripcion_publica="Descripcion publica",
            version=1,
            activa=True,
        ))
    db.commit()
    return case


def test_get_or_create_beneficiary_reuses_existing_row_in_same_organization():
    with TestingSessionLocal() as db:
        first, created_first = get_or_create_beneficiary(
            db,
            organization_id="org-1",
            identity_hash=IDENTITY_HASH,
            identity_encrypted="encrypted-id",
            name_encrypted="encrypted-name",
            actor_email="coordinador@example.com",
        )
        db.commit()
        second, created_second = get_or_create_beneficiary(
            db,
            organization_id="org-1",
            identity_hash=IDENTITY_HASH,
            identity_encrypted="encrypted-id-2",
            name_encrypted="encrypted-name-2",
            actor_email="coordinador2@example.com",
        )
        db.commit()

        assert created_first is True
        assert created_second is False
        assert first.id == second.id
        assert db.query(models.BeneficiarioAyuda).count() == 1


def test_get_or_create_beneficiary_allows_same_identity_in_different_organization():
    with TestingSessionLocal() as db:
        get_or_create_beneficiary(
            db, organization_id="org-1", identity_hash=IDENTITY_HASH,
            identity_encrypted="enc", name_encrypted="enc-name", actor_email="a@example.com",
        )
        get_or_create_beneficiary(
            db, organization_id="org-2", identity_hash=IDENTITY_HASH,
            identity_encrypted="enc", name_encrypted="enc-name", actor_email="a@example.com",
        )
        db.commit()

        assert db.query(models.BeneficiarioAyuda).count() == 2


def test_own_organization_match_returns_internal_summary():
    with TestingSessionLocal() as db:
        case = seed_case(db, organization_id="org-1", public=False, title="Necesidad interna")

        result = find_cedula_matches(db, actor=actor(organization_id="org-1"), identity_hash=IDENTITY_HASH)

        assert len(result.own_organization_matches) == 1
        match = result.own_organization_matches[0]
        assert match.id == case.id
        assert match.internal_title == "Necesidad interna"
        assert match.category == "salud"
        assert match.state == "borrador"
        assert result.external_public_matches == []
        assert result.other_private_matches_present is False


def test_other_organization_public_case_returns_public_summary_only():
    with TestingSessionLocal() as db:
        seed_case(db, organization_id="org-2", public=True)

        result = find_cedula_matches(db, actor=actor(organization_id="org-1"), identity_hash=IDENTITY_HASH)

        assert result.own_organization_matches == []
        assert len(result.external_public_matches) == 1
        external = result.external_public_matches[0]
        assert external.public_id == "case-org-2"
        assert external.title == "Ayuda publica de prueba"
        assert result.other_private_matches_present is False


def test_other_organization_private_case_returns_only_boolean_signal():
    with TestingSessionLocal() as db:
        seed_case(db, organization_id="org-2", public=False)

        result = find_cedula_matches(db, actor=actor(organization_id="org-1"), identity_hash=IDENTITY_HASH)

        assert result.own_organization_matches == []
        assert result.external_public_matches == []
        assert result.other_private_matches_present is True


def test_no_match_returns_empty_result():
    with TestingSessionLocal() as db:
        result = find_cedula_matches(db, actor=actor(organization_id="org-1"), identity_hash=IDENTITY_HASH)

        assert result.own_organization_matches == []
        assert result.external_public_matches == []
        assert result.other_private_matches_present is False


def test_global_actor_without_organization_only_sees_external_view():
    with TestingSessionLocal() as db:
        seed_case(db, organization_id="org-1", public=False)

        result = find_cedula_matches(
            db,
            actor=actor(organization_id="", role="super_admin", email="root@example.com"),
            identity_hash=IDENTITY_HASH,
        )

        assert result.own_organization_matches == []
        assert result.other_private_matches_present is True


def test_actor_without_case_management_role_is_rejected():
    with TestingSessionLocal() as db:
        with pytest.raises(OrganizationAccessError):
            find_cedula_matches(
                db,
                actor=actor(organization_id="org-1", role="donor", email="donor@example.com"),
                identity_hash=IDENTITY_HASH,
            )


def test_invalid_identity_hash_is_rejected():
    with TestingSessionLocal() as db:
        with pytest.raises(BeneficiaryDomainError):
            find_cedula_matches(db, actor=actor(), identity_hash="not-a-hash")


def test_rate_limit_blocks_excessive_queries():
    with TestingSessionLocal() as db:
        same_actor = actor(organization_id="org-1")
        for _ in range(CEDULA_MATCH_RATE_LIMIT_MAX_REQUESTS):
            find_cedula_matches(db, actor=same_actor, identity_hash=IDENTITY_HASH)
            db.commit()

        with pytest.raises(CedulaMatchRateLimitError):
            find_cedula_matches(db, actor=same_actor, identity_hash=IDENTITY_HASH)
