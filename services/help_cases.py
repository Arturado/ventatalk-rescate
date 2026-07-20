import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext


class HelpCaseDomainError(ValueError):
    pass


class OrganizationAccessError(HelpCaseDomainError):
    pass


class CaseNotFoundError(HelpCaseDomainError):
    pass


class CaseStateError(HelpCaseDomainError):
    pass


class CaseReadinessError(HelpCaseDomainError):
    def __init__(self, blockers):
        self.blockers = blockers
        super().__init__("El caso no cumple los requisitos de publicacion")


def _require_text(value, field_name):
    normalized = str(value or "").strip()
    if not normalized:
        raise HelpCaseDomainError(f"{field_name} es obligatorio")
    return normalized


def _require_organization_access(actor, organization_id):
    organization_id = _require_text(organization_id, "organization_id")
    if not actor.can_manage_organization_cases:
        raise OrganizationAccessError("El actor no puede administrar casos")
    if actor.is_org_scoped and actor.organizacion_id != organization_id:
        raise OrganizationAccessError("El actor no pertenece a la organizacion del caso")
    return organization_id


def _get_managed_case(db, case_id, actor):
    query = db.query(models.CasoAyudaV2).filter(models.CasoAyudaV2.id == case_id)
    if actor.is_org_scoped:
        query = query.filter(models.CasoAyudaV2.organizacion_id == actor.organizacion_id)
    case = query.one_or_none()
    if case is None:
        raise CaseNotFoundError("Caso no encontrado")
    _require_organization_access(actor, case.organizacion_id)
    return case


def _add_audit_event(db, *, case, actor, action, entity_type, entity_id, metadata=None):
    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()),
        organizacion_id=case.organizacion_id,
        caso_id=case.id,
        accion=action,
        actor_id=actor.email,
        actor_tipo=actor.role,
        entidad_tipo=entity_type,
        entidad_id=str(entity_id),
        metadata_json=json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
    ))


def create_help_case(
    db: Session,
    *,
    actor: ActorOrgContext,
    organization_id: str,
    public_id: str,
    beneficiary_name_encrypted: str,
    beneficiary_identity_hash: str,
    beneficiary_identity_encrypted: str,
    internal_title: str,
    category: str,
    goal_amount: Decimal,
    goal_currency: str,
):
    organization_id = _require_organization_access(actor, organization_id)
    public_id = _require_text(public_id, "public_id")
    beneficiary_name_encrypted = _require_text(beneficiary_name_encrypted, "beneficiary_name_encrypted")
    beneficiary_identity_hash = _require_text(beneficiary_identity_hash, "beneficiary_identity_hash")
    beneficiary_identity_encrypted = _require_text(
        beneficiary_identity_encrypted,
        "beneficiary_identity_encrypted",
    )
    internal_title = _require_text(internal_title, "internal_title")
    category = _require_text(category, "category")
    goal_currency = _require_text(goal_currency, "goal_currency").upper()
    if len(beneficiary_identity_hash) != 64:
        raise HelpCaseDomainError("beneficiary_identity_hash debe ser SHA-256")
    if goal_currency not in {"VES", "USD", "EUR"}:
        raise HelpCaseDomainError("goal_currency no soportada")
    if goal_amount is None or Decimal(goal_amount) <= 0:
        raise HelpCaseDomainError("goal_amount debe ser positivo")

    beneficiary = models.BeneficiarioAyuda(
        organizacion_id=organization_id,
        nombres_apellidos_cifrado=beneficiary_name_encrypted,
        cedula_hash=beneficiary_identity_hash,
        cedula_cifrada=beneficiary_identity_encrypted,
        creado_por=actor.email,
    )
    db.add(beneficiary)
    db.flush()
    case = models.CasoAyudaV2(
        public_id=public_id,
        organizacion_id=organization_id,
        beneficiario_id=beneficiary.id,
        titulo_interno=internal_title,
        categoria=category,
        meta_monto=Decimal(goal_amount),
        meta_moneda=goal_currency,
        creado_por=actor.email,
    )
    db.add(case)
    db.flush()
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="beneficiario_creado",
        entity_type="beneficiario",
        entity_id=beneficiary.id,
    )
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="caso_creado",
        entity_type="caso",
        entity_id=case.id,
        metadata={"estado": "borrador"},
    )
    db.flush()
    return case


def submit_help_case_for_validation(db: Session, *, case_id: int, actor: ActorOrgContext):
    case = _get_managed_case(db, case_id, actor)
    if case.estado != "borrador":
        raise CaseStateError("Solo un caso en borrador puede enviarse a validacion")
    case.estado = "pendiente_validacion"
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="caso_enviado_validacion",
        entity_type="caso",
        entity_id=case.id,
        metadata={"estado_anterior": "borrador", "estado_nuevo": "pendiente_validacion"},
    )
    db.flush()
    return case


def _latest_by_key(records, key_name, version_name="version"):
    latest = {}
    for record in records:
        key = getattr(record, key_name)
        if key not in latest or getattr(record, version_name) > getattr(latest[key], version_name):
            latest[key] = record
    return list(latest.values())


def _readiness_blockers(db, case):
    blockers = []
    beneficiary = db.get(models.BeneficiarioAyuda, case.beneficiario_id)
    if not beneficiary or not beneficiary.datos_verificacion_cifrado:
        blockers.append("identidad_no_verificada")
    if beneficiary and (beneficiary.es_menor or beneficiary.representante_nombre_cifrado):
        if not (
            beneficiary.representante_nombre_cifrado
            and beneficiary.representante_relacion
            and beneficiary.representante_autoridad_verificada_en
            and beneficiary.representante_autoridad_verificada_por
        ):
            blockers.append("autoridad_representante_no_verificada")

    publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id).one_or_none()
    if publication is None:
        blockers.append("publicacion_no_configurada")

    consents = (
        db.query(models.ConsentimientoCasoAyuda)
        .filter_by(caso_id=case.id, vigente=True)
        .order_by(models.ConsentimientoCasoAyuda.version.desc())
        .all()
    )
    consent = consents[0] if consents else None
    if consent is None:
        blockers.append("consentimiento_vigente_faltante")
    elif consent.evidencia_documento_id is None:
        blockers.append("evidencia_consentimiento_faltante")
    else:
        evidence = db.get(models.DocumentoCasoAyuda, consent.evidencia_documento_id)
        if evidence is None or evidence.caso_id != case.id or evidence.clasificacion != "privado":
            blockers.append("evidencia_consentimiento_faltante")

    accounts = db.query(models.CuentaCasoAyuda).filter_by(caso_id=case.id).all()
    latest_accounts = _latest_by_key(accounts, "account_key")
    has_approved_account = bool(consent) and any(
        account.estado == "aprobada" and account.consentimiento_version == consent.version
        for account in latest_accounts
    )
    if not has_approved_account:
        blockers.append("cuenta_aprobada_faltante")

    documents = db.query(models.DocumentoCasoAyuda).filter_by(caso_id=case.id).all()
    latest_documents = _latest_by_key(documents, "document_key")
    public_documents = [document for document in latest_documents if document.clasificacion == "publico"]
    if any(
        document.estado_revision != "aprobado"
        or not consent
        or document.consentimiento_version != consent.version
        or (
            document.tipo == "informe_medico"
            and (not document.revisado_por or document.revisado_por == document.cargado_por)
        )
        for document in public_documents
    ):
        blockers.append("documento_publico_sin_aprobacion_reforzada")
    return blockers


def mark_help_case_ready(db: Session, *, case_id: int, actor: ActorOrgContext):
    case = _get_managed_case(db, case_id, actor)
    if case.estado != "pendiente_validacion":
        raise CaseStateError("Solo un caso pendiente de validacion puede quedar listo")
    blockers = _readiness_blockers(db, case)
    if blockers:
        raise CaseReadinessError(blockers)
    case.estado = "listo_publicar"
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="caso_listo_publicar",
        entity_type="caso",
        entity_id=case.id,
        metadata={"estado_anterior": "pendiente_validacion", "estado_nuevo": "listo_publicar"},
    )
    db.flush()
    return case
