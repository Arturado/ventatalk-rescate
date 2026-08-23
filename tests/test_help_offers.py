import base64

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import ActorOrgContext
import models
import schemas
from services.help_case_drafts import create_help_case_draft
from services.help_cases import publish_help_case, register_help_case_consent
from services.help_crypto import HelpDataCipher
from services.help_offers import (
    HelpOfferDomainError,
    OfferAccessError,
    OfferIdempotencyConflictError,
    OfferNotFoundError,
    OfferStateError,
    create_direct_offer,
    create_labor_offer,
    list_case_offers,
    transition_offer,
)
from services.help_cases import CaseNotFoundError


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


def coordinator(*, organization_id="org-1"):
    return ActorOrgContext(organizacion_id=organization_id, role="coordinador", email="coordinador@example.com")


def donor(*, uid="donor-uid-1", email="donante@example.com"):
    return ActorOrgContext(organizacion_id="", role="donor", email=email, uid=uid, ip_hash="a" * 64)


def create_campaign_case(db, *, organization_id="org-1", suffix="1"):
    summary, _created = create_help_case_draft(
        db,
        actor=coordinator(organization_id=organization_id),
        organization_id=organization_id,
        public_id=f"offer-campaign-{suffix}",
        payload=schemas.CasoAyudaV2DraftCreateRequest(
            category="insumo_recurso",
            subject_type="campana_organizacion",
            aid_modes=["directa"],
            title="Colecta de colchones",
            story="Necesitamos colchones para el centro de refugio de la zona.",
        ),
        idempotency_key=f"offer-campaign-key-{suffix}-0000001",
        cipher=HelpDataCipher.from_environment(),
    )
    return db.query(models.CasoAyudaV2).filter_by(id=summary["id"]).one()


def create_monetary_only_case(db, *, organization_id="org-1", suffix="1"):
    summary, _created = create_help_case_draft(
        db,
        actor=coordinator(organization_id=organization_id),
        organization_id=organization_id,
        public_id=f"offer-monetary-{suffix}",
        payload=schemas.CasoAyudaV2DraftCreateRequest(
            category="salud",
            subject_type="persona",
            aid_modes=["monetaria"],
            beneficiary_name="Beneficiario de Prueba",
            beneficiary_identity=f"V-{(str(suffix) * 9)[:9]}",
            title="Tratamiento",
            story="Historia de prueba para el caso monetario del beneficiario.",
            goal_amount=100,
            goal_currency="USD",
        ),
        idempotency_key=f"offer-monetary-key-{suffix}-0000001",
        cipher=HelpDataCipher.from_environment(),
    )
    return db.query(models.CasoAyudaV2).filter_by(id=summary["id"]).one()


def create_employment_case(db, *, organization_id="org-1", suffix="1"):
    summary, _created = create_help_case_draft(
        db,
        actor=coordinator(organization_id=organization_id),
        organization_id=organization_id,
        public_id=f"offer-employment-{suffix}",
        payload=schemas.CasoAyudaV2DraftCreateRequest(
            category="empleo",
            subject_type="persona",
            aid_modes=["oferta_laboral"],
            beneficiary_name="Beneficiario de Prueba",
            beneficiary_identity=f"V-{(str(suffix) * 9)[:9]}",
            title="Busqueda de empleo",
            story="Historia de prueba para el caso de busqueda de empleo del beneficiario.",
            profession="Electricista",
            beneficiary_access_email="acceso@example.com",
        ),
        idempotency_key=f"offer-employment-key-{suffix}-0000001",
        cipher=HelpDataCipher.from_environment(),
    )
    return db.query(models.CasoAyudaV2).filter_by(id=summary["id"]).one()


def publish_case(db, case, *, signer_type="representante"):
    consent = register_help_case_consent(
        db,
        case_id=case.id,
        actor=coordinator(organization_id=case.organizacion_id),
        text_version="mvp1-v1",
        scope_json="{}",
        signer_name_encrypted="encrypted-signer",
        signer_type=signer_type,
    )
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
        checksum_sha256="f" * 64,
        consentimiento_version=consent.version,
        cargado_por="coordinador@example.com",
        revisado_por="coordinador@example.com",
    ))
    if case.acepta_ayuda_monetaria:
        db.add(models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="principal",
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
            consentimiento_version=consent.version,
            estado="aprobada",
            creado_por="coordinador@example.com",
            aprobado_por="coordinador@example.com",
        ))
    db.flush()
    case.estado = "pendiente_validacion"
    db.flush()
    return publish_help_case(db, case_id=case.id, actor=coordinator(organization_id=case.organizacion_id))


def test_create_direct_offer_succeeds_for_published_case_accepting_directa():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)

        response, created = create_direct_offer(
            db,
            actor=donor(),
            public_id=case.public_id,
            description="Tengo 10 colchones nuevos para donar.",
            contact_details="+58 412-1234567",
            idempotency_key="offer-idem-key-0000000000000001",
            cipher=HelpDataCipher.from_environment(),
        )

        assert created is True
        assert response["status"] == "pendiente"
        assert response["case_public_id"] == case.public_id
        offer = db.query(models.OfertaAyudaDirecta).one()
        assert offer.tipo == "directa"
        assert offer.actor_donante_uid == "donor-uid-1"
        assert offer.actor_donante_email == "donante@example.com"
        assert "colchones" not in offer.payload_cifrado
        assert "412-1234567" not in offer.contacto_cifrado
    finally:
        db.close()


def labor_payload():
    return {
        "tipo_trabajo": "Contrato por proyecto",
        "descripcion": "Diseno de materiales informativos accesibles.",
        "remuneracion_estimada": "USD 450 por proyecto",
        "telefono": "+58 412-0000000",
        "correo": "oferta@example.test",
    }


def test_create_labor_offer_is_separate_private_and_idempotent():
    db = TestingSessionLocal()
    try:
        case = create_employment_case(db)
        publish_case(db, case, signer_type="beneficiario")
        kwargs = {
            "actor": donor(),
            "public_id": case.public_id,
            "idempotency_key": "labor-offer-key-0000001",
            "cipher": HelpDataCipher.from_environment(),
            **labor_payload(),
        }

        first, first_created = create_labor_offer(db, **kwargs)
        second, second_created = create_labor_offer(db, **kwargs)

        assert first_created is True
        assert second_created is False
        assert first == second
        assert first["tipo"] == "empleo"
        assert first["status"] == "pendiente_respuesta"
        assert not set(labor_payload()).intersection(first)
        offer = db.query(models.OfertaAyudaDirecta).one()
        assert offer.tipo == "empleo"
        assert offer.estado == "pendiente_respuesta"
        assert labor_payload()["telefono"] not in offer.contacto_cifrado
        assert labor_payload()["descripcion"] not in offer.payload_cifrado
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="oferta_laboral_creada").one()
        assert audit.metadata_json == '{"tipo":"empleo"}'
    finally:
        db.close()


def test_create_labor_offer_conflicts_on_same_key_with_different_payload():
    db = TestingSessionLocal()
    try:
        case = create_employment_case(db)
        publish_case(db, case, signer_type="beneficiario")
        kwargs = {
            "actor": donor(),
            "public_id": case.public_id,
            "idempotency_key": "labor-conflict-key-0000001",
            "cipher": HelpDataCipher.from_environment(),
            **labor_payload(),
        }
        create_labor_offer(db, **kwargs)

        with pytest.raises(OfferIdempotencyConflictError):
            create_labor_offer(db, **{**kwargs, "descripcion": "Otra descripcion laboral valida."})
        assert db.query(models.OfertaAyudaDirecta).count() == 1
    finally:
        db.close()


@pytest.mark.parametrize("case_kind", ["directa", "empleo_pausado"])
def test_create_labor_offer_rejects_non_labor_or_non_actionable_case(case_kind):
    db = TestingSessionLocal()
    try:
        if case_kind == "directa":
            case = create_campaign_case(db)
        else:
            case = create_employment_case(db)
        publish_case(db, case, signer_type="beneficiario" if case_kind == "empleo_pausado" else "representante")
        if case_kind == "empleo_pausado":
            case.estado = "pausado"
            db.flush()

        with pytest.raises(OfferAccessError):
            create_labor_offer(
                db,
                actor=donor(),
                public_id=case.public_id,
                idempotency_key=f"labor-rejected-{case_kind}-0000001",
                cipher=HelpDataCipher.from_environment(),
                **labor_payload(),
            )
        assert db.query(models.OfertaAyudaDirecta).count() == 0
    finally:
        db.close()


def test_create_direct_offer_is_idempotent():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        kwargs = dict(
            actor=donor(),
            public_id=case.public_id,
            description="Tengo 10 colchones nuevos para donar.",
            contact_details="+58 412-1234567",
            idempotency_key="offer-idem-key-0000000000000002",
            cipher=HelpDataCipher.from_environment(),
        )

        first, first_created = create_direct_offer(db, **kwargs)
        second, second_created = create_direct_offer(db, **kwargs)

        assert first_created is True
        assert second_created is False
        assert first == second
        assert db.query(models.OfertaAyudaDirecta).count() == 1
    finally:
        db.close()


def test_create_direct_offer_rejects_reused_key_with_different_payload():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        create_direct_offer(
            db,
            actor=donor(),
            public_id=case.public_id,
            description="Tengo 10 colchones nuevos para donar.",
            contact_details="+58 412-1234567",
            idempotency_key="offer-idem-key-0000000000000003",
            cipher=HelpDataCipher.from_environment(),
        )

        with pytest.raises(OfferIdempotencyConflictError):
            create_direct_offer(
                db,
                actor=donor(),
                public_id=case.public_id,
                description="Tengo ropa para donar.",
                contact_details="+58 412-1234567",
                idempotency_key="offer-idem-key-0000000000000003",
                cipher=HelpDataCipher.from_environment(),
            )
    finally:
        db.close()


def test_create_direct_offer_rejects_case_without_modalidad_directa():
    db = TestingSessionLocal()
    try:
        case = create_monetary_only_case(db)
        publish_case(db, case, signer_type="beneficiario")

        with pytest.raises(OfferAccessError):
            create_direct_offer(
                db,
                actor=donor(),
                public_id=case.public_id,
                description="Tengo 10 colchones nuevos para donar.",
                contact_details="+58 412-1234567",
                idempotency_key="offer-idem-key-0000000000000004",
                cipher=HelpDataCipher.from_environment(),
            )
    finally:
        db.close()


def test_create_direct_offer_rejects_employment_case():
    db = TestingSessionLocal()
    try:
        case = create_employment_case(db)
        publish_case(db, case, signer_type="beneficiario")

        with pytest.raises(OfferAccessError):
            create_direct_offer(
                db,
                actor=donor(),
                public_id=case.public_id,
                description="Puedo ofrecer capacitacion tecnica.",
                contact_details="+58 412-1234567",
                idempotency_key="offer-idem-key-0000000000000005",
                cipher=HelpDataCipher.from_environment(),
            )
    finally:
        db.close()


def test_create_direct_offer_rejects_unpublished_case():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)

        with pytest.raises(OfferAccessError):
            create_direct_offer(
                db,
                actor=donor(),
                public_id=case.public_id,
                description="Tengo 10 colchones nuevos para donar.",
                contact_details="+58 412-1234567",
                idempotency_key="offer-idem-key-0000000000000006",
                cipher=HelpDataCipher.from_environment(),
            )
    finally:
        db.close()


def _create_offer(db, case, *, suffix="0000000000000010"):
    response, _created = create_direct_offer(
        db,
        actor=donor(),
        public_id=case.public_id,
        description="Tengo 10 colchones nuevos para donar.",
        contact_details="+58 412-1234567",
        idempotency_key=f"offer-idem-key-{suffix}",
        cipher=HelpDataCipher.from_environment(),
    )
    return db.query(models.OfertaAyudaDirecta).filter_by(id=response["id"]).one()


def test_list_case_offers_returns_decrypted_payload_and_filters_by_status():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        offer = _create_offer(db, case)

        all_offers = list_case_offers(db, case_id=case.id, actor=coordinator())
        pending_only = list_case_offers(db, case_id=case.id, actor=coordinator(), status="pendiente")
        contacted_only = list_case_offers(db, case_id=case.id, actor=coordinator(), status="contactada")

        assert len(all_offers) == 1
        assert all_offers[0]["description"] == "Tengo 10 colchones nuevos para donar."
        assert all_offers[0]["actor_donante_email"] == "donante@example.com"
        assert len(pending_only) == 1
        assert len(contacted_only) == 0
        assert offer.id == all_offers[0]["id"]
    finally:
        db.close()


def test_transition_offer_moves_pendiente_to_contactada_to_completada():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        offer = _create_offer(db, case)

        contacted = transition_offer(
            db, case_id=case.id, offer_id=offer.id, actor=coordinator(), action="contactar",
        )
        completed = transition_offer(
            db, case_id=case.id, offer_id=offer.id, actor=coordinator(), action="completar",
        )

        assert contacted["estado"] == "contactada"
        assert completed["estado"] == "completada"
        assert completed["gestionada_por"] == "coordinador@example.com"
        actions = [
            event.accion for event in db.query(models.AuditoriaCasoAyuda).filter(
                models.AuditoriaCasoAyuda.accion.in_({"oferta_contactada", "oferta_completada"})
            ).order_by(models.AuditoriaCasoAyuda.id)
        ]
        assert actions == ["oferta_contactada", "oferta_completada"]
    finally:
        db.close()


@pytest.mark.parametrize("state_action", [("pendiente", "rechazar"), ("contactada", "rechazar")])
def test_transition_offer_rechazar_from_pendiente_or_contactada(state_action):
    starting_state, action = state_action
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        offer = _create_offer(db, case)
        if starting_state == "contactada":
            transition_offer(db, case_id=case.id, offer_id=offer.id, actor=coordinator(), action="contactar")

        rejected = transition_offer(
            db, case_id=case.id, offer_id=offer.id, actor=coordinator(), action=action,
        )

        assert rejected["estado"] == "rechazada"
    finally:
        db.close()


def test_transition_offer_cancelar():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        offer = _create_offer(db, case)

        cancelled = transition_offer(
            db, case_id=case.id, offer_id=offer.id, actor=coordinator(), action="cancelar",
        )

        assert cancelled["estado"] == "cancelada"
    finally:
        db.close()


def test_transition_offer_rejects_invalid_transition():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        offer = _create_offer(db, case)

        with pytest.raises(OfferStateError):
            transition_offer(
                db, case_id=case.id, offer_id=offer.id, actor=coordinator(), action="completar",
            )
    finally:
        db.close()


def test_transition_offer_rejects_unknown_action():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        offer = _create_offer(db, case)

        with pytest.raises(HelpOfferDomainError):
            transition_offer(
                db, case_id=case.id, offer_id=offer.id, actor=coordinator(), action="borrar",
            )
    finally:
        db.close()


def test_transition_offer_rejects_unknown_offer_id():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)

        with pytest.raises(OfferNotFoundError):
            transition_offer(
                db, case_id=case.id, offer_id=999999, actor=coordinator(), action="contactar",
            )
    finally:
        db.close()


def test_transition_offer_rejects_actor_from_different_organization():
    db = TestingSessionLocal()
    try:
        case = create_campaign_case(db)
        publish_case(db, case)
        offer = _create_offer(db, case)

        with pytest.raises(CaseNotFoundError):
            transition_offer(
                db,
                case_id=case.id,
                offer_id=offer.id,
                actor=coordinator(organization_id="org-2"),
                action="contactar",
            )
    finally:
        db.close()
