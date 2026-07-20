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
    CaseReadinessError,
    CaseStateError,
    OrganizationAccessError,
    create_help_case,
    mark_help_case_ready,
    submit_help_case_for_validation,
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


def actor(*, organization_id="org-1", role="coordinador"):
    return ActorOrgContext(
        organizacion_id=organization_id,
        role=role,
        email="coordinador@example.com",
    )


def create_draft(db, *, suffix="1", organization_id="org-1", actor_context=None):
    return create_help_case(
        db,
        actor=actor_context or actor(organization_id=organization_id),
        organization_id=organization_id,
        public_id=f"service-case-{suffix}",
        beneficiary_name_encrypted="encrypted-name",
        beneficiary_identity_hash=(suffix * 64)[:64],
        beneficiary_identity_encrypted="encrypted-id",
        internal_title="Caso de servicio",
        category="medicamentos",
        goal_amount=Decimal("100.00"),
        goal_currency="USD",
    )


def add_complete_publication_requirements(db, case, *, account_version=1):
    beneficiary = db.get(models.BeneficiarioAyuda, case.beneficiario_id)
    beneficiary.datos_verificacion_cifrado = "encrypted-verification"
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
        revisado_por="coordinador@example.com",
    )
    db.add(evidence)
    db.flush()
    consent = models.ConsentimientoCasoAyuda(
        caso_id=case.id,
        version=1,
        texto_version="mvp1-v1",
        alcance_json="{}",
        firmante_nombre_cifrado="encrypted-signer",
        evidencia_documento_id=evidence.id,
        registrado_por="coordinador@example.com",
    )
    publication = models.PublicacionCasoAyuda(
        caso_id=case.id,
        nombre_publico="Ana",
        titulo_publico="Ayuda para tratamiento",
        descripcion_publica="Descripcion autorizada",
        version=1,
        configurado_por="coordinador@example.com",
    )
    account = models.CuentaCasoAyuda(
        caso_id=case.id,
        account_key="account-1",
        version=account_version,
        tipo_titular="beneficiario",
        titular_nombre_cifrado="encrypted-holder",
        relacion_beneficiario="propia",
        medio="banco_venezolano",
        moneda="USD",
        identificador_cifrado="encrypted-account",
        responsable_nombre_cifrado="encrypted-responsible",
        responsable_email_hash="b" * 64,
        responsable_email_cifrado="encrypted-email",
        consentimiento_version=1,
        estado="aprobada",
        creado_por="coordinador@example.com",
        aprobado_por="coordinador@example.com",
    )
    db.add_all([consent, publication, account])
    db.flush()
    return consent, account


def test_create_case_is_organization_scoped_and_audited_without_committing():
    with TestingSessionLocal() as db:
        case = create_draft(db)

        assert case.organizacion_id == "org-1"
        assert case.estado == "borrador"
        assert db.query(models.BeneficiarioAyuda).count() == 1
        assert [event.accion for event in db.query(models.AuditoriaCasoAyuda).order_by(models.AuditoriaCasoAyuda.id)] == [
            "beneficiario_creado",
            "caso_creado",
        ]


def test_coordinator_cannot_create_case_for_another_organization():
    with TestingSessionLocal() as db:
        with pytest.raises(OrganizationAccessError):
            create_draft(db, organization_id="org-2", actor_context=actor(organization_id="org-1"))

        assert db.query(models.CasoAyudaV2).count() == 0


def test_super_admin_can_create_case_for_explicit_target_organization():
    with TestingSessionLocal() as db:
        case = create_draft(
            db,
            organization_id="org-2",
            actor_context=actor(organization_id="", role="super_admin"),
        )

        assert case.organizacion_id == "org-2"


def test_submit_for_validation_requires_same_organization_and_draft_state():
    with TestingSessionLocal() as db:
        case = create_draft(db)

        with pytest.raises(CaseNotFoundError):
            submit_help_case_for_validation(db, case_id=case.id, actor=actor(organization_id="org-2"))

        submitted = submit_help_case_for_validation(db, case_id=case.id, actor=actor())
        assert submitted.estado == "pendiente_validacion"

        with pytest.raises(CaseStateError):
            submit_help_case_for_validation(db, case_id=case.id, actor=actor())


def test_readiness_reports_missing_requirements_without_changing_state():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseReadinessError) as error:
            mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert set(error.value.blockers) == {
            "identidad_no_verificada",
            "publicacion_no_configurada",
            "consentimiento_vigente_faltante",
            "cuenta_aprobada_faltante",
        }
        assert case.estado == "pendiente_validacion"


def test_minor_requires_verified_representative_authority():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        beneficiary = db.get(models.BeneficiarioAyuda, case.beneficiario_id)
        beneficiary.es_menor = True
        beneficiary.representante_nombre_cifrado = "encrypted-representative"
        beneficiary.representante_relacion = "madre"
        add_complete_publication_requirements(db, case)
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseReadinessError) as error:
            mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert error.value.blockers == ["autoridad_representante_no_verificada"]


def test_latest_account_version_must_be_approved_and_covered_by_consent():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        _, account = add_complete_publication_requirements(db, case)
        db.add(models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key=account.account_key,
            version=2,
            tipo_titular="beneficiario",
            titular_nombre_cifrado="encrypted-holder-v2",
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda="USD",
            identificador_cifrado="encrypted-account-v2",
            responsable_nombre_cifrado="encrypted-responsible",
            responsable_email_hash="c" * 64,
            responsable_email_cifrado="encrypted-email",
            consentimiento_version=1,
            estado="pendiente",
            creado_por="coordinador@example.com",
        ))
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseReadinessError) as error:
            mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert error.value.blockers == ["cuenta_aprobada_faltante"]


def test_public_medical_document_requires_distinct_approval_for_current_consent():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_complete_publication_requirements(db, case)
        db.add(models.DocumentoCasoAyuda(
            caso_id=case.id,
            document_key="medical-report",
            version=1,
            tipo="informe_medico",
            clasificacion="publico",
            estado_revision="aprobado",
            storage_path=f"public/cases/{case.id}/report.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            checksum_sha256="m" * 64,
            consentimiento_version=1,
            cargado_por="coordinador@example.com",
            revisado_por="coordinador@example.com",
        ))
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseReadinessError) as error:
            mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert error.value.blockers == ["documento_publico_sin_aprobacion_reforzada"]


def test_complete_case_becomes_ready_and_records_audit_event():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_complete_publication_requirements(db, case)
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        ready = mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert ready.estado == "listo_publicar"
        assert db.query(models.AuditoriaCasoAyuda).filter_by(accion="caso_listo_publicar").count() == 1
