import base64
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import ActorOrgContext
import models
import schemas
from services.help_case_drafts import create_help_case_draft
from services.help_cases import (
    CaseNotFoundError,
    CaseLifecycleAccessError,
    CaseReadinessError,
    CaseStateError,
    HelpCaseDomainError,
    configure_help_case_publication,
    OrganizationAccessError,
    create_help_case,
    get_public_help_case,
    list_help_cases,
    list_public_help_cases,
    mark_help_case_ready,
    register_beneficiary_verification,
    register_help_case_consent,
    submit_help_case_for_validation,
    transition_help_case,
    update_help_case,
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


def create_campaign_draft(db, *, organization_id="org-1"):
    summary, _created = create_help_case_draft(
        db,
        actor=actor(organization_id=organization_id),
        organization_id=organization_id,
        public_id="service-campaign-1",
        payload=schemas.CasoAyudaV2DraftCreateRequest(
            category="insumo_recurso",
            subject_type="campana_organizacion",
            aid_modes=["directa"],
            title="Colecta de colchones",
            story="Necesitamos colchones para el centro de refugio de la zona.",
        ),
        idempotency_key="service-campaign-key-0000001",
        cipher=HelpDataCipher.from_environment(),
    )
    return db.query(models.CasoAyudaV2).filter_by(id=summary["id"]).one()


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
    photo = models.DocumentoCasoAyuda(
        caso_id=case.id,
        document_key="foto_principal",
        version=1,
        tipo="foto_principal",
        clasificacion="publico",
        estado_revision="aprobado",
        storage_path=f"public/cases/{case.id}/photo.jpg",
        content_type="image/jpeg",
        size_bytes=2048,
        checksum_sha256="f" * 64,
        consentimiento_version=1,
        cargado_por="coordinador@example.com",
        revisado_por="coordinador@example.com",
    )
    db.add_all([consent, publication, account, photo])
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
            "publicacion_no_configurada",
            "consentimiento_vigente_faltante",
            "cuenta_aprobada_faltante",
            "foto_principal_faltante",
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


def test_self_approved_public_medical_document_no_longer_blocks_readiness():
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

        ready = mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert ready.estado == "listo_publicar"


def test_pending_public_document_still_blocks_readiness():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_complete_publication_requirements(db, case)
        db.add(models.DocumentoCasoAyuda(
            caso_id=case.id,
            document_key="medical-report",
            version=1,
            tipo="informe_medico",
            clasificacion="publico",
            estado_revision="pendiente",
            storage_path=f"public/cases/{case.id}/report.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            checksum_sha256="m" * 64,
            consentimiento_version=1,
            cargado_por="coordinador@example.com",
        ))
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseReadinessError) as error:
            mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert error.value.blockers == ["documento_publico_sin_aprobacion"]


def test_case_without_primary_photo_is_blocked():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        publication = models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Ana",
            titulo_publico="Ayuda para tratamiento",
            descripcion_publica="Descripcion autorizada",
            version=1,
            configurado_por="coordinador@example.com",
        )
        consent = models.ConsentimientoCasoAyuda(
            caso_id=case.id,
            version=1,
            texto_version="mvp1-v1",
            alcance_json="{}",
            firmante_nombre_cifrado="encrypted-signer",
            registrado_por="coordinador@example.com",
        )
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="account-1",
            version=1,
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
        db.add_all([publication, consent, account])
        db.flush()
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseReadinessError) as error:
            mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert error.value.blockers == ["foto_principal_faltante"]


def test_complete_case_becomes_ready_and_records_audit_event():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_complete_publication_requirements(db, case)
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        ready = mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert ready.estado == "listo_publicar"
        assert db.query(models.AuditoriaCasoAyuda).filter_by(accion="caso_listo_publicar").count() == 1


def test_global_case_actors_can_list_all_enabled_organizations():
    with TestingSessionLocal() as db:
        first = create_draft(db, suffix="1", organization_id="org-1")
        second = create_draft(db, suffix="2", organization_id="org-2")

        scoped = list_help_cases(db, actor=actor(organization_id="org-1"))
        global_for_org = list_help_cases(
            db,
            actor=actor(organization_id="", role="super_admin"),
            organization_id="org-2",
        )
        global_all = list_help_cases(
            db,
            actor=actor(organization_id="", role="super_admin"),
        )

        assert [case.id for case in scoped] == [first.id]
        assert [case.id for case in global_for_org] == [second.id]
        assert {case.id for case in global_all} == {first.id, second.id}
        with pytest.raises(OrganizationAccessError):
            list_help_cases(db, actor=actor(organization_id="org-1"), organization_id="org-2")


def test_list_cases_filters_status_and_applies_bounded_pagination():
    with TestingSessionLocal() as db:
        draft = create_draft(db, suffix="1")
        submitted = create_draft(db, suffix="2")
        submit_help_case_for_validation(db, case_id=submitted.id, actor=actor())

        results = list_help_cases(
            db,
            actor=actor(),
            status="pendiente_validacion",
            skip=0,
            limit=1,
        )

        assert draft.id != submitted.id
        assert [case.id for case in results] == [submitted.id]


def test_case_lifecycle_transitions_are_closed_and_audited():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        case.estado = "publicado"

        paused = transition_help_case(db, case_id=case.id, actor=actor(), action="pausar")
        resumed = transition_help_case(db, case_id=case.id, actor=actor(), action="reanudar")
        assert paused.id == resumed.id
        assert resumed.estado == "publicado"

        with pytest.raises(CaseStateError):
            transition_help_case(db, case_id=case.id, actor=actor(), action="cerrar")

        case.estado = "meta_alcanzada"
        closed = transition_help_case(db, case_id=case.id, actor=actor(), action="cerrar")
        archived = transition_help_case(db, case_id=case.id, actor=actor(), action="archivar")

        assert archived.estado == "archivado"
        assert closed.cerrado_at is not None
        assert [event.accion for event in db.query(models.AuditoriaCasoAyuda).filter(
            models.AuditoriaCasoAyuda.accion.in_({
                "caso_pausado", "caso_reanudado", "caso_cerrado", "caso_archivado",
            })
        ).order_by(models.AuditoriaCasoAyuda.id)] == [
            "caso_pausado", "caso_reanudado", "caso_cerrado", "caso_archivado",
        ]


def test_reject_only_accepts_prepublication_case_and_bounded_reason():
    with TestingSessionLocal() as db:
        case = create_draft(db)

        with pytest.raises(HelpCaseDomainError):
            transition_help_case(db, case_id=case.id, actor=actor(), action="rechazar")
        with pytest.raises(HelpCaseDomainError):
            transition_help_case(
                db,
                case_id=case.id,
                actor=actor(),
                action="rechazar",
                reason_code="texto libre no permitido",
            )

        rejected = transition_help_case(
            db,
            case_id=case.id,
            actor=actor(),
            action="rechazar",
            reason_code="criterios_no_cumplidos",
        )

        assert rejected.estado == "rechazado"
        assert db.query(models.AuditoriaCasoAyuda).filter_by(
            accion="caso_rechazado",
            motivo_codigo="criterios_no_cumplidos",
        ).count() == 1


def test_only_admin_can_suspend_and_reactivation_restores_previous_state():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        case.estado = "pausado"

        with pytest.raises(CaseLifecycleAccessError):
            transition_help_case(
                db,
                case_id=case.id,
                actor=actor(),
                action="suspender",
                reason_code="revision_administrativa",
            )

        admin = actor(role="admin")
        suspended = transition_help_case(
            db,
            case_id=case.id,
            actor=admin,
            action="suspender",
            reason_code="revision_administrativa",
        )
        assert suspended.estado == "suspendido"
        assert suspended.estado_anterior_suspension == "pausado"

        reactivated = transition_help_case(db, case_id=case.id, actor=admin, action="reactivar")
        assert reactivated.estado == "pausado"
        assert reactivated.estado_anterior_suspension is None


def test_lifecycle_transition_hides_cases_from_another_organization():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        case.estado = "publicado"

        with pytest.raises(CaseNotFoundError):
            transition_help_case(
                db,
                case_id=case.id,
                actor=actor(organization_id="org-2"),
                action="pausar",
            )


def test_public_queries_include_paused_cases_only_for_pilot_organizations(monkeypatch):
    with TestingSessionLocal() as db:
        allowed = create_draft(db, suffix="1", organization_id="org-1")
        blocked = create_draft(db, suffix="2", organization_id="org-2")
        add_complete_publication_requirements(db, allowed)
        add_complete_publication_requirements(db, blocked)
        allowed.estado = "pausado"
        blocked.estado = "publicado"
        for publication in db.query(models.PublicacionCasoAyuda).all():
            publication.activa = True
        db.flush()
        monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-1")

        listed = list_public_help_cases(db)

        assert [case.id for case, _publication in listed] == [allowed.id]
        assert get_public_help_case(db, public_id=allowed.public_id)[0].id == allowed.id
        assert get_public_help_case(db, public_id=blocked.public_id) is None


def test_management_rejects_organization_outside_pilot(monkeypatch):
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-2")
    with TestingSessionLocal() as db:
        with pytest.raises(OrganizationAccessError):
            create_draft(db, organization_id="org-1")


def test_update_case_changes_only_editable_fields_and_audits():
    with TestingSessionLocal() as db:
        case = create_draft(db)

        updated = update_help_case(
            db,
            case_id=case.id,
            actor=actor(),
            internal_title="Titulo actualizado",
            category="tratamiento",
            goal_amount=Decimal("250.50"),
            goal_currency="EUR",
            private_story_encrypted="encrypted-story",
        )

        assert updated.titulo_interno == "Titulo actualizado"
        assert updated.meta_monto == Decimal("250.50")
        assert updated.meta_moneda == "EUR"
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="caso_actualizado").one()
        assert "encrypted-story" not in audit.metadata_json


def test_guided_mutations_reject_another_organization_or_ready_case():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        add_complete_publication_requirements(db, case)
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())
        mark_help_case_ready(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseNotFoundError):
            update_help_case(db, case_id=case.id, actor=actor(organization_id="org-2"), category="otra")
        with pytest.raises(CaseStateError):
            update_help_case(db, case_id=case.id, actor=actor(), category="otra")


def test_minor_verification_requires_complete_representative_authority():
    with TestingSessionLocal() as db:
        case = create_draft(db)

        with pytest.raises(HelpCaseDomainError):
            register_beneficiary_verification(
                db,
                case_id=case.id,
                actor=actor(),
                is_minor=True,
            )

        beneficiary = register_beneficiary_verification(
            db,
            case_id=case.id,
            actor=actor(),
            is_minor=True,
            representative_name_encrypted="encrypted-representative",
            representative_relationship="madre",
            representative_authority_verified=True,
        )
        assert beneficiary.representante_autoridad_verificada_por == "coordinador@example.com"
        assert db.query(models.AuditoriaCasoAyuda).filter_by(accion="beneficiario_verificado").count() == 1


def test_publication_configuration_is_upserted_with_increasing_version():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        first = configure_help_case_publication(
            db,
            case_id=case.id,
            actor=actor(),
            public_name="Ana",
            public_title="Ayuda para tratamiento",
            public_description="Descripcion inicial",
            general_location="Caracas",
            social_networks_json="{}",
        )
        second = configure_help_case_publication(
            db,
            case_id=case.id,
            actor=actor(),
            public_name="Ana",
            public_title="Ayuda para nuevo tratamiento",
            public_description="Descripcion actualizada",
            general_location="Caracas",
            social_networks_json="{}",
        )

        assert first.id == second.id
        assert second.version == 2
        assert db.query(models.PublicacionCasoAyuda).count() == 1
        assert db.query(models.AuditoriaCasoAyuda).filter_by(accion="publicacion_configurada").count() == 2


def test_publication_defaults_public_name_to_public_title_when_omitted():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        publication = configure_help_case_publication(
            db,
            case_id=case.id,
            actor=actor(),
            public_title="Esmeril para busqueda y rescate",
            public_description="Necesitamos esmeril para seguir ayudando",
        )

        assert publication.nombre_publico == "Esmeril para busqueda y rescate"


@pytest.mark.parametrize("role", ["admin", "super_admin"])
def test_admin_can_update_published_public_information_with_history(role):
    with TestingSessionLocal() as db:
        case = create_draft(db)
        publication = configure_help_case_publication(
            db,
            case_id=case.id,
            actor=actor(),
            public_name="Ana",
            public_title="Titulo inicial",
            public_description="Descripcion inicial",
        )
        publication.activa = True
        case.estado = "publicado"

        updated = configure_help_case_publication(
            db,
            case_id=case.id,
            actor=actor(organization_id="" if role == "super_admin" else "org-1", role=role),
            public_name="Ana actualizada",
            public_title="Titulo actualizado",
            public_description="Descripcion actualizada",
            within_current_consent_scope=True,
        )

        assert updated.version == 2
        assert updated.activa is True
        versions = db.query(models.PublicacionCasoAyudaVersion).order_by(
            models.PublicacionCasoAyudaVersion.version
        ).all()
        assert [version.titulo_publico for version in versions] == ["Titulo inicial", "Titulo actualizado"]


def test_coordinator_cannot_update_public_information_after_publication():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        configure_help_case_publication(
            db,
            case_id=case.id,
            actor=actor(),
            public_name="Ana",
            public_title="Titulo inicial",
            public_description="Descripcion inicial",
        )
        case.estado = "publicado"

        with pytest.raises(CaseLifecycleAccessError):
            configure_help_case_publication(
                db,
                case_id=case.id,
                actor=actor(),
                public_name="Cambio no autorizado",
                public_title="Cambio no autorizado",
                public_description="Cambio no autorizado",
            )


def test_consent_evidence_must_be_private_and_belong_to_case():
    with TestingSessionLocal() as db:
        first_case = create_draft(db, suffix="1")
        second_case = create_draft(db, suffix="2")
        evidence = models.DocumentoCasoAyuda(
            caso_id=second_case.id,
            document_key="consent-evidence",
            version=1,
            tipo="consentimiento",
            clasificacion="privado",
            estado_revision="aprobado",
            storage_path="private/cases/other/consent.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            checksum_sha256="z" * 64,
            cargado_por="coordinador@example.com",
        )
        db.add(evidence)
        db.flush()

        with pytest.raises(HelpCaseDomainError):
            register_help_case_consent(
                db,
                case_id=first_case.id,
                actor=actor(),
                text_version="mvp1-v1",
                scope_json="{}",
                signer_name_encrypted="encrypted-signer",
                signer_type="beneficiario",
                evidence_document_id=evidence.id,
            )


def test_new_consent_version_retires_previous_consent_and_audits():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        evidence = models.DocumentoCasoAyuda(
            caso_id=case.id,
            document_key="consent-evidence",
            version=1,
            tipo="consentimiento",
            clasificacion="privado",
            estado_revision="aprobado",
            storage_path="private/cases/current/consent.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            checksum_sha256="y" * 64,
            cargado_por="coordinador@example.com",
        )
        db.add(evidence)
        db.flush()
        first = register_help_case_consent(
            db,
            case_id=case.id,
            actor=actor(),
            text_version="mvp1-v1",
            scope_json="{}",
            signer_name_encrypted="encrypted-signer",
            signer_type="beneficiario",
            evidence_document_id=evidence.id,
        )
        second = register_help_case_consent(
            db,
            case_id=case.id,
            actor=actor(),
            text_version="mvp1-v2",
            scope_json="{}",
            signer_name_encrypted="encrypted-signer",
            signer_type="beneficiario",
            evidence_document_id=evidence.id,
        )

        assert second.version == 2
        assert first.vigente is False
        assert first.retirado_at is not None
        assert first.retirado_por == "coordinador@example.com"
        assert db.query(models.AuditoriaCasoAyuda).filter_by(accion="consentimiento_registrado").count() == 2


def test_representative_cannot_sign_without_verified_authority():
    with TestingSessionLocal() as db:
        case = create_draft(db)
        evidence = models.DocumentoCasoAyuda(
            caso_id=case.id,
            document_key="consent-evidence",
            version=1,
            tipo="consentimiento",
            clasificacion="privado",
            estado_revision="aprobado",
            storage_path="private/cases/representative/consent.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            checksum_sha256="x" * 64,
            cargado_por="coordinador@example.com",
        )
        db.add(evidence)
        db.flush()

        with pytest.raises(HelpCaseDomainError):
            register_help_case_consent(
                db,
                case_id=case.id,
                actor=actor(),
                text_version="mvp1-v1",
                scope_json="{}",
                signer_name_encrypted="encrypted-representative",
                signer_type="representante",
                evidence_document_id=evidence.id,
            )


def test_campaign_case_without_consent_is_blocked_by_generic_consent_check():
    with TestingSessionLocal() as db:
        case = create_campaign_draft(db)
        db.add(models.DocumentoCasoAyuda(
            caso_id=case.id,
            document_key="foto_principal",
            version=1,
            tipo="foto_principal",
            clasificacion="publico",
            estado_revision="aprobado",
            storage_path=f"public/cases/{case.id}/photo.jpg",
            content_type="image/jpeg",
            size_bytes=2048,
            checksum_sha256="c" * 64,
            consentimiento_version=1,
            cargado_por="coordinador@example.com",
            revisado_por="coordinador@example.com",
        ))
        db.flush()
        submit_help_case_for_validation(db, case_id=case.id, actor=actor())

        with pytest.raises(CaseReadinessError) as error:
            mark_help_case_ready(db, case_id=case.id, actor=actor())

        assert "consentimiento_vigente_faltante" in error.value.blockers


def test_campaign_case_can_register_consent_without_beneficiary():
    with TestingSessionLocal() as db:
        case = create_campaign_draft(db)

        consent = register_help_case_consent(
            db,
            case_id=case.id,
            actor=actor(),
            text_version="mvp1-v1",
            scope_json="{}",
            signer_name_encrypted="encrypted-org-representative",
            signer_type="representante",
        )

        assert consent.caso_id == case.id
        assert consent.firmante_tipo == "representante"


def test_campaign_case_rejects_beneficiario_signer_type():
    with TestingSessionLocal() as db:
        case = create_campaign_draft(db)

        with pytest.raises(HelpCaseDomainError):
            register_help_case_consent(
                db,
                case_id=case.id,
                actor=actor(),
                text_version="mvp1-v1",
                scope_json="{}",
                signer_name_encrypted="encrypted-org-representative",
                signer_type="beneficiario",
            )
