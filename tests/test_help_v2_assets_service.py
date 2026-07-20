from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import ActorOrgContext
import models
from services.help_cases import (
    CaseNotFoundError,
    HelpCaseDomainError,
    approve_exceptional_account,
    create_account_version,
    create_help_case,
    register_case_document,
    register_help_case_consent,
    review_case_document,
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


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def actor(*, organization_id="org-1", email="coordinador@example.com"):
    return ActorOrgContext(organization_id, "coordinador", email)


def create_draft(db, *, suffix="1"):
    return create_help_case(
        db,
        actor=actor(),
        organization_id="org-1",
        public_id=f"asset-case-{suffix}",
        beneficiary_name_encrypted="encrypted-name",
        beneficiary_identity_hash=(suffix * 64)[:64],
        beneficiary_identity_encrypted="encrypted-id",
        internal_title="Caso de activos",
        category="medicamentos",
        goal_amount=Decimal("100.00"),
        goal_currency="USD",
    )


def add_consent(db, case):
    evidence = models.DocumentoCasoAyuda(
        caso_id=case.id,
        document_key="consent-evidence",
        version=1,
        tipo="consentimiento",
        clasificacion="privado",
        estado_revision="aprobado",
        storage_path=f"private/cases/{case.id}/consent.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        checksum_sha256="e" * 64,
        cargado_por="coordinador@example.com",
    )
    db.add(evidence)
    db.flush()
    return register_help_case_consent(
        db,
        case_id=case.id,
        actor=actor(),
        text_version="mvp1-v1",
        scope_json="{}",
        signer_name_encrypted="encrypted-signer",
        signer_type="beneficiario",
        evidence_document_id=evidence.id,
    )


def account_kwargs(*, account_key="account-1", owner_type="beneficiario", justification=None):
    return {
        "account_key": account_key,
        "owner_type": owner_type,
        "holder_name_encrypted": "encrypted-holder",
        "beneficiary_relationship": "propia" if owner_type == "beneficiario" else "tercero autorizado",
        "medium": "banco_venezolano",
        "currency": "USD",
        "identifier_encrypted": "encrypted-account",
        "instructions_encrypted": "encrypted-instructions",
        "justification_encrypted": justification,
        "responsible_name_encrypted": "encrypted-responsible",
        "responsible_email_hash": "r" * 64,
        "responsible_email_encrypted": "encrypted-email",
    }


def test_document_registration_preserves_versions_and_private_paths():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        first = register_case_document(
            db,
            case_id=case.id,
            actor=actor(),
            document_key="identity",
            document_type="identificacion",
            classification="privado",
            storage_path="private/cases/identity-v1.pdf",
            content_type="application/pdf",
            size_bytes=100,
            checksum_sha256="a" * 64,
        )
        second = register_case_document(
            db,
            case_id=case.id,
            actor=actor(),
            document_key="identity",
            document_type="identificacion",
            classification="privado",
            storage_path="private/cases/identity-v2.pdf",
            content_type="application/pdf",
            size_bytes=200,
            checksum_sha256="b" * 64,
        )

        assert (first.version, second.version) == (1, 2)
        assert db.query(models.DocumentoCasoAyuda).count() == 2


def test_public_document_requires_current_consent_and_public_path():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        document_data = {
            "case_id": case.id,
            "actor": actor(),
            "document_key": "medical-report",
            "document_type": "informe_medico",
            "classification": "publico",
            "storage_path": "public/cases/report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100,
            "checksum_sha256": "a" * 64,
        }
        with pytest.raises(HelpCaseDomainError):
            register_case_document(db, **document_data)

        consent = add_consent(db, case)
        document_data["storage_path"] = "private/cases/report.pdf"
        with pytest.raises(HelpCaseDomainError):
            register_case_document(db, **document_data)

        document_data["storage_path"] = "public/cases/report.pdf"
        document = register_case_document(db, **document_data)
        assert document.consentimiento_version == consent.version


def test_public_medical_document_requires_distinct_reviewer():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_consent(db, case)
        document = register_case_document(
            db,
            case_id=case.id,
            actor=actor(),
            document_key="medical-report",
            document_type="informe_medico",
            classification="publico",
            storage_path="public/cases/report.pdf",
            content_type="application/pdf",
            size_bytes=100,
            checksum_sha256="a" * 64,
        )

        with pytest.raises(HelpCaseDomainError):
            review_case_document(db, document_id=document.id, actor=actor(), approve=True)

        reviewed = review_case_document(
            db,
            document_id=document.id,
            actor=actor(email="revisor@example.com"),
            approve=True,
        )
        assert reviewed.estado_revision == "aprobado"
        assert reviewed.revisado_por == "revisor@example.com"


def test_document_review_hides_document_from_another_organization():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        document = register_case_document(
            db,
            case_id=case.id,
            actor=actor(),
            document_key="identity",
            document_type="identificacion",
            classification="privado",
            storage_path="private/cases/identity.pdf",
            content_type="application/pdf",
            size_bytes=100,
            checksum_sha256="a" * 64,
        )

        with pytest.raises(CaseNotFoundError):
            review_case_document(
                db,
                document_id=document.id,
                actor=actor(organization_id="org-2"),
                approve=True,
            )


def test_regular_account_is_approved_against_current_consent():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        consent = add_consent(db, case)
        account = create_account_version(db, case_id=case.id, actor=actor(), **account_kwargs())

        assert account.version == 1
        assert account.estado == "aprobada"
        assert account.consentimiento_version == consent.version
        assert account.aprobado_por == "coordinador@example.com"


def test_exceptional_account_requires_justification_and_distinct_approval():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_consent(db, case)
        with pytest.raises(HelpCaseDomainError):
            create_account_version(
                db,
                case_id=case.id,
                actor=actor(),
                **account_kwargs(owner_type="tercero"),
            )
        account = create_account_version(
            db,
            case_id=case.id,
            actor=actor(),
            **account_kwargs(owner_type="tercero", justification="encrypted-justification"),
        )
        assert account.estado == "pendiente"

        with pytest.raises(HelpCaseDomainError):
            approve_exceptional_account(db, account_id=account.id, actor=actor())

        approved = approve_exceptional_account(
            db,
            account_id=account.id,
            actor=actor(email="revisor@example.com"),
        )
        assert approved.estado == "aprobada"
        assert approved.aprobado_por == "revisor@example.com"


def test_new_account_version_inactivates_previous_and_revokes_linked_access():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_consent(db, case)
        first = create_account_version(db, case_id=case.id, actor=actor(), **account_kwargs())
        now = datetime.now(timezone.utc)
        access = models.AccesoTemporalAyuda(
            destinatario_email_hash="d" * 64,
            destinatario_email_cifrado="encrypted-email",
            challenge_hash="h" * 64,
            challenge_expires_at=now + timedelta(minutes=15),
            solicitado_por="coordinador@example.com",
        )
        db.add(access)
        db.flush()
        db.add(models.AlcanceCuentaAccesoTemporal(
            acceso_id=access.id,
            caso_id=case.id,
            cuenta_id=first.id,
            cuenta_version=first.version,
        ))
        db.flush()

        second = create_account_version(db, case_id=case.id, actor=actor(), **account_kwargs())

        assert second.version == 2
        assert first.estado == "inactiva"
        assert access.estado == "revocado"
        assert access.revocado_por == "coordinador@example.com"
        assert access.motivo_revocacion == "cuenta_versionada"


def test_account_version_uses_new_consent_after_reconsent():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        first_consent = add_consent(db, case)
        first = create_account_version(db, case_id=case.id, actor=actor(), **account_kwargs())
        evidence_id = first_consent.evidencia_documento_id
        second_consent = register_help_case_consent(
            db,
            case_id=case.id,
            actor=actor(),
            text_version="mvp1-v2",
            scope_json="{}",
            signer_name_encrypted="encrypted-signer",
            signer_type="beneficiario",
            evidence_document_id=evidence_id,
        )
        second = create_account_version(db, case_id=case.id, actor=actor(), **account_kwargs())

        assert first.consentimiento_version == 1
        assert second.consentimiento_version == second_consent.version == 2
