import base64

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import ActorOrgContext, DelegateAffirmation
import models
import schemas
from services.help_case_drafts import create_help_case_draft
from services.help_cases import CaseNotFoundError
from services.help_case_responsibles import (
    DelegateAffirmationMismatchError,
    DuplicateResponsibleError,
    HelpCaseDomainError,
    ResponsibleNotFoundError,
    assign_case_responsible,
    is_active_responsible,
    list_case_responsibles,
    revoke_case_responsible,
)
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


def actor(*, organization_id="org-1", role="coordinador", email="coordinador@example.com"):
    return ActorOrgContext(organizacion_id=organization_id, role=role, email=email)


def create_seeded_case(db, *, organization_id="org-1", actor_context=None, suffix="1"):
    actor_context = actor_context or actor(organization_id=organization_id)
    summary, _created = create_help_case_draft(
        db,
        actor=actor_context,
        organization_id=organization_id,
        public_id=f"responsables-case-{suffix}",
        payload=schemas.CasoAyudaV2DraftCreateRequest(
            category="insumo_recurso",
            subject_type="campana_organizacion",
            aid_modes=["directa"],
            title="Colecta de colchones",
            story="Necesitamos colchones para el centro de refugio de la zona.",
        ),
        idempotency_key=f"responsables-case-key-{suffix}-0000001",
        cipher=HelpDataCipher.from_environment(),
    )
    return db.query(models.CasoAyudaV2).filter_by(id=summary["id"]).one()


def affirmation(
    *,
    operation="assign_responsible",
    case_id,
    organization_id="org-1",
    target_uid="delegate-uid-123",
    target_email="delegado@example.com",
):
    return DelegateAffirmation(
        operation=operation,
        case_id=case_id,
        organization_id=organization_id,
        target_uid=target_uid,
        target_email=target_email,
    )


def test_seed_default_responsible_creates_registrador_on_draft_creation():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        responsibles = list_case_responsibles(db, case_id=case.id, actor=actor())

        assert len(responsibles) == 1
        assert responsibles[0].rol == "registrador"
        assert responsibles[0].usuario_id == "coordinador@example.com"
        assert responsibles[0].estado == "activo"
    finally:
        db.close()


def test_assign_case_responsible_adds_delegate_without_removing_registrador():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        assign_case_responsible(
            db,
            case_id=case.id,
            actor=actor(),
            affirmation=affirmation(case_id=case.id),
            target_uid="delegate-uid-123",
            target_email="delegado@example.com",
        )
        db.flush()
        responsibles = list_case_responsibles(db, case_id=case.id, actor=actor())

        assert len(responsibles) == 2
        roles = sorted(responsible.rol for responsible in responsibles)
        assert roles == ["delegado", "registrador"]
        assert all(responsible.estado == "activo" for responsible in responsibles)
    finally:
        db.close()


def test_assign_case_responsible_allows_multiple_active_delegates():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        assign_case_responsible(
            db,
            case_id=case.id,
            actor=actor(),
            affirmation=affirmation(case_id=case.id, target_uid="uid-a", target_email="a@example.com"),
            target_uid="uid-a",
            target_email="a@example.com",
        )
        assign_case_responsible(
            db,
            case_id=case.id,
            actor=actor(),
            affirmation=affirmation(case_id=case.id, target_uid="uid-b", target_email="b@example.com"),
            target_uid="uid-b",
            target_email="b@example.com",
        )
        db.flush()
        responsibles = list_case_responsibles(db, case_id=case.id, actor=actor())

        assert len(responsibles) == 3
        assert {responsible.usuario_id for responsible in responsibles} == {
            "coordinador@example.com", "a@example.com", "b@example.com",
        }
    finally:
        db.close()


def test_assign_case_responsible_rejects_duplicate_active_assignment():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        assign_case_responsible(
            db,
            case_id=case.id,
            actor=actor(),
            affirmation=affirmation(case_id=case.id),
            target_uid="delegate-uid-123",
            target_email="delegado@example.com",
        )
        db.flush()

        with pytest.raises(DuplicateResponsibleError):
            assign_case_responsible(
                db,
                case_id=case.id,
                actor=actor(),
                affirmation=affirmation(case_id=case.id),
                target_uid="delegate-uid-123",
                target_email="delegado@example.com",
            )
    finally:
        db.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "revoke_responsible"),
        ("case_id", 999999),
        ("organization_id", "org-2"),
        ("target_email", "otro@example.com"),
    ],
)
def test_assign_case_responsible_rejects_tampered_affirmation(field, value):
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        kwargs = dict(
            operation="assign_responsible",
            case_id=case.id,
            organization_id="org-1",
            target_uid="delegate-uid-123",
            target_email="delegado@example.com",
        )
        kwargs[field] = value

        with pytest.raises(DelegateAffirmationMismatchError):
            assign_case_responsible(
                db,
                case_id=case.id,
                actor=actor(),
                affirmation=DelegateAffirmation(**kwargs),
                target_uid="delegate-uid-123",
                target_email="delegado@example.com",
            )
    finally:
        db.close()


def test_assign_case_responsible_rejects_affirmation_target_uid_mismatch():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)

        with pytest.raises(DelegateAffirmationMismatchError):
            assign_case_responsible(
                db,
                case_id=case.id,
                actor=actor(),
                affirmation=affirmation(case_id=case.id, target_uid="uid-signed"),
                target_uid="uid-different",
                target_email="delegado@example.com",
            )
    finally:
        db.close()


def test_assign_case_responsible_rejects_actor_from_different_organization():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db, organization_id="org-1")

        with pytest.raises(CaseNotFoundError):
            assign_case_responsible(
                db,
                case_id=case.id,
                actor=actor(organization_id="org-2", email="coordinador-otra-org@example.com"),
                affirmation=affirmation(case_id=case.id, organization_id="org-2"),
                target_uid="delegate-uid-123",
                target_email="delegado@example.com",
            )
    finally:
        db.close()


def test_revoke_case_responsible_deactivates_and_is_active_responsible_reflects_immediately():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        delegate = assign_case_responsible(
            db,
            case_id=case.id,
            actor=actor(),
            affirmation=affirmation(case_id=case.id),
            target_uid="delegate-uid-123",
            target_email="delegado@example.com",
        )
        db.flush()
        delegate_actor = actor(email="delegado@example.com")
        assert is_active_responsible(db, case_id=case.id, actor=delegate_actor) is True

        revoke_case_responsible(
            db,
            case_id=case.id,
            responsible_id=delegate.id,
            actor=actor(),
            affirmation=affirmation(operation="revoke_responsible", case_id=case.id),
        )
        db.flush()

        assert is_active_responsible(db, case_id=case.id, actor=delegate_actor) is False
        responsibles = list_case_responsibles(db, case_id=case.id, actor=actor(), include_removed=True)
        revoked = next(r for r in responsibles if r.id == delegate.id)
        assert revoked.estado == "removido"
        assert revoked.removido_por_id == "coordinador@example.com"
    finally:
        db.close()


def test_revoke_case_responsible_rejects_already_removed():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        delegate = assign_case_responsible(
            db,
            case_id=case.id,
            actor=actor(),
            affirmation=affirmation(case_id=case.id),
            target_uid="delegate-uid-123",
            target_email="delegado@example.com",
        )
        db.flush()
        revoke_case_responsible(
            db,
            case_id=case.id,
            responsible_id=delegate.id,
            actor=actor(),
            affirmation=affirmation(operation="revoke_responsible", case_id=case.id),
        )
        db.flush()

        with pytest.raises(HelpCaseDomainError):
            revoke_case_responsible(
                db,
                case_id=case.id,
                responsible_id=delegate.id,
                actor=actor(),
                affirmation=affirmation(operation="revoke_responsible", case_id=case.id),
            )
    finally:
        db.close()


def test_revoke_case_responsible_rejects_unknown_id():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)

        with pytest.raises(ResponsibleNotFoundError):
            revoke_case_responsible(
                db,
                case_id=case.id,
                responsible_id=999999,
                actor=actor(),
                affirmation=affirmation(operation="revoke_responsible", case_id=case.id),
            )
    finally:
        db.close()


def test_list_case_responsibles_excludes_removed_by_default():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)
        delegate = assign_case_responsible(
            db,
            case_id=case.id,
            actor=actor(),
            affirmation=affirmation(case_id=case.id),
            target_uid="delegate-uid-123",
            target_email="delegado@example.com",
        )
        db.flush()
        revoke_case_responsible(
            db,
            case_id=case.id,
            responsible_id=delegate.id,
            actor=actor(),
            affirmation=affirmation(operation="revoke_responsible", case_id=case.id),
        )
        db.flush()

        active_only = list_case_responsibles(db, case_id=case.id, actor=actor())
        with_removed = list_case_responsibles(db, case_id=case.id, actor=actor(), include_removed=True)

        assert len(active_only) == 1
        assert len(with_removed) == 2
    finally:
        db.close()


def test_is_active_responsible_false_for_unrelated_actor():
    db = TestingSessionLocal()
    try:
        case = create_seeded_case(db)

        assert is_active_responsible(
            db, case_id=case.id, actor=actor(email="nadie@example.com"),
        ) is False
    finally:
        db.close()
