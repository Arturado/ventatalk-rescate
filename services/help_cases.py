import json
from datetime import datetime, timezone
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


CASE_STATES = {
    "borrador",
    "pendiente_validacion",
    "listo_publicar",
    "publicado",
    "pausado",
    "meta_alcanzada",
    "cerrado",
    "rechazado",
    "suspendido",
    "archivado",
}


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


def list_help_cases(
    db: Session,
    *,
    actor: ActorOrgContext,
    organization_id=None,
    status=None,
    skip=0,
    limit=50,
):
    requested_organization = str(organization_id or "").strip()
    if actor.is_org_scoped:
        if requested_organization and requested_organization != actor.organizacion_id:
            raise OrganizationAccessError("El actor no pertenece a la organizacion solicitada")
        target_organization = actor.organizacion_id
    else:
        target_organization = _require_text(requested_organization, "organization_id")
    _require_organization_access(actor, target_organization)

    normalized_status = str(status or "").strip()
    if normalized_status and normalized_status not in CASE_STATES:
        raise HelpCaseDomainError("status no soportado")
    query = db.query(models.CasoAyudaV2).filter(
        models.CasoAyudaV2.organizacion_id == target_organization,
    )
    if normalized_status:
        query = query.filter(models.CasoAyudaV2.estado == normalized_status)
    return (
        query.order_by(models.CasoAyudaV2.created_at.desc(), models.CasoAyudaV2.id.desc())
        .offset(max(0, int(skip)))
        .limit(min(100, max(1, int(limit))))
        .all()
    )


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


def _require_editable_case(db, case_id, actor):
    case = _get_managed_case(db, case_id, actor)
    if case.estado not in {"borrador", "pendiente_validacion"}:
        raise CaseStateError("El caso no admite cambios durante este estado")
    return case


def _normalize_json(value, field_name):
    value = _require_text(value, field_name)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise HelpCaseDomainError(f"{field_name} debe contener JSON valido") from exc
    if not isinstance(parsed, (dict, list)):
        raise HelpCaseDomainError(f"{field_name} debe contener un objeto o lista JSON")
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def update_help_case(
    db: Session,
    *,
    case_id: int,
    actor: ActorOrgContext,
    internal_title=None,
    category=None,
    goal_amount=None,
    goal_currency=None,
    private_story_encrypted=None,
):
    case = _require_editable_case(db, case_id, actor)
    changed_fields = []
    if internal_title is not None:
        case.titulo_interno = _require_text(internal_title, "internal_title")
        changed_fields.append("titulo_interno")
    if category is not None:
        case.categoria = _require_text(category, "category")
        changed_fields.append("categoria")
    if goal_amount is not None:
        normalized_amount = Decimal(goal_amount)
        if normalized_amount <= 0:
            raise HelpCaseDomainError("goal_amount debe ser positivo")
        case.meta_monto = normalized_amount
        changed_fields.append("meta_monto")
    if goal_currency is not None:
        normalized_currency = _require_text(goal_currency, "goal_currency").upper()
        if normalized_currency not in {"VES", "USD", "EUR"}:
            raise HelpCaseDomainError("goal_currency no soportada")
        case.meta_moneda = normalized_currency
        changed_fields.append("meta_moneda")
    if private_story_encrypted is not None:
        case.relato_privado_cifrado = _require_text(private_story_encrypted, "private_story_encrypted")
        changed_fields.append("relato_privado_cifrado")
    if not changed_fields:
        raise HelpCaseDomainError("Debe indicar al menos un campo para actualizar")
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="caso_actualizado",
        entity_type="caso",
        entity_id=case.id,
        metadata={"campos": sorted(changed_fields)},
    )
    db.flush()
    return case


def register_beneficiary_verification(
    db: Session,
    *,
    case_id: int,
    actor: ActorOrgContext,
    verification_data_encrypted: str,
    is_minor: bool = False,
    representative_name_encrypted=None,
    representative_relationship=None,
    representative_authority_verified: bool = False,
):
    case = _require_editable_case(db, case_id, actor)
    verification_data_encrypted = _require_text(
        verification_data_encrypted,
        "verification_data_encrypted",
    )
    has_representative = bool(representative_name_encrypted or representative_relationship or is_minor)
    if has_representative and not (
        representative_name_encrypted
        and representative_relationship
        and representative_authority_verified
    ):
        raise HelpCaseDomainError("La representacion requiere identidad, relacion y autoridad verificadas")

    beneficiary = db.get(models.BeneficiarioAyuda, case.beneficiario_id)
    beneficiary.datos_verificacion_cifrado = verification_data_encrypted
    beneficiary.es_menor = bool(is_minor)
    beneficiary.representante_nombre_cifrado = (
        _require_text(representative_name_encrypted, "representative_name_encrypted")
        if has_representative
        else None
    )
    beneficiary.representante_relacion = (
        _require_text(representative_relationship, "representative_relationship")
        if has_representative
        else None
    )
    beneficiary.representante_autoridad_verificada_en = (
        datetime.now(timezone.utc) if has_representative else None
    )
    beneficiary.representante_autoridad_verificada_por = actor.email if has_representative else None
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="beneficiario_verificado",
        entity_type="beneficiario",
        entity_id=beneficiary.id,
        metadata={"es_menor": bool(is_minor), "tiene_representante": has_representative},
    )
    db.flush()
    return beneficiary


def configure_help_case_publication(
    db: Session,
    *,
    case_id: int,
    actor: ActorOrgContext,
    public_name: str,
    public_title: str,
    public_description: str,
    general_location=None,
    social_networks_json=None,
):
    case = _require_editable_case(db, case_id, actor)
    publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id).one_or_none()
    if publication is None:
        publication = models.PublicacionCasoAyuda(caso_id=case.id, version=1)
        db.add(publication)
    else:
        publication.version += 1
    publication.nombre_publico = _require_text(public_name, "public_name")
    publication.titulo_publico = _require_text(public_title, "public_title")
    publication.descripcion_publica = _require_text(public_description, "public_description")
    publication.localidad_general = str(general_location).strip() if general_location is not None else None
    publication.redes_sociales_json = (
        _normalize_json(social_networks_json, "social_networks_json")
        if social_networks_json is not None
        else None
    )
    publication.configurado_por = actor.email
    db.flush()
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="publicacion_configurada",
        entity_type="publicacion",
        entity_id=publication.id,
        metadata={"version": publication.version},
    )
    db.flush()
    return publication


def register_help_case_consent(
    db: Session,
    *,
    case_id: int,
    actor: ActorOrgContext,
    text_version: str,
    scope_json: str,
    signer_name_encrypted: str,
    signer_type: str,
    evidence_document_id: int,
):
    case = _require_editable_case(db, case_id, actor)
    text_version = _require_text(text_version, "text_version")
    scope_json = _normalize_json(scope_json, "scope_json")
    signer_name_encrypted = _require_text(signer_name_encrypted, "signer_name_encrypted")
    signer_type = _require_text(signer_type, "signer_type")
    if signer_type not in {"beneficiario", "representante"}:
        raise HelpCaseDomainError("signer_type no soportado")
    beneficiary = db.get(models.BeneficiarioAyuda, case.beneficiario_id)
    if signer_type == "representante" and not (
        beneficiary.representante_nombre_cifrado
        and beneficiary.representante_relacion
        and beneficiary.representante_autoridad_verificada_en
        and beneficiary.representante_autoridad_verificada_por
    ):
        raise HelpCaseDomainError("El representante no tiene autoridad verificada")
    evidence = db.get(models.DocumentoCasoAyuda, evidence_document_id)
    if not evidence or (
        evidence.caso_id != case.id
        or evidence.clasificacion != "privado"
        or evidence.tipo != "consentimiento"
    ):
        raise HelpCaseDomainError("La evidencia debe ser un documento privado del caso")

    existing_consents = (
        db.query(models.ConsentimientoCasoAyuda)
        .filter_by(caso_id=case.id)
        .order_by(models.ConsentimientoCasoAyuda.version.desc())
        .all()
    )
    next_version = existing_consents[0].version + 1 if existing_consents else 1
    now = datetime.now(timezone.utc)
    for existing in existing_consents:
        if existing.vigente:
            existing.vigente = False
            existing.retirado_at = now
            existing.retirado_por = actor.email
            existing.motivo_retiro = "reemplazado_por_nueva_version"
    consent = models.ConsentimientoCasoAyuda(
        caso_id=case.id,
        version=next_version,
        texto_version=text_version,
        alcance_json=scope_json,
        firmante_nombre_cifrado=signer_name_encrypted,
        firmante_tipo=signer_type,
        evidencia_documento_id=evidence.id,
        registrado_por=actor.email,
    )
    db.add(consent)
    db.flush()
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="consentimiento_registrado",
        entity_type="consentimiento",
        entity_id=consent.id,
        metadata={"firmante_tipo": signer_type, "version": next_version},
    )
    db.flush()
    return consent


def _current_consent(db, case_id):
    consent = (
        db.query(models.ConsentimientoCasoAyuda)
        .filter_by(caso_id=case_id, vigente=True)
        .order_by(models.ConsentimientoCasoAyuda.version.desc())
        .first()
    )
    if consent is None:
        raise HelpCaseDomainError("El caso requiere un consentimiento vigente")
    return consent


def register_case_document(
    db: Session,
    *,
    case_id: int,
    actor: ActorOrgContext,
    document_key: str,
    document_type: str,
    classification: str,
    storage_path: str,
    content_type: str,
    size_bytes: int,
    checksum_sha256: str,
    original_name_encrypted=None,
):
    case = _require_editable_case(db, case_id, actor)
    document_key = _require_text(document_key, "document_key")
    document_type = _require_text(document_type, "document_type")
    classification = _require_text(classification, "classification")
    storage_path = _require_text(storage_path, "storage_path")
    content_type = _require_text(content_type, "content_type")
    checksum_sha256 = _require_text(checksum_sha256, "checksum_sha256")
    if classification not in {"publico", "privado"}:
        raise HelpCaseDomainError("classification no soportada")
    expected_prefix = "public/" if classification == "publico" else "private/"
    if not storage_path.startswith(expected_prefix):
        raise HelpCaseDomainError("La ruta no corresponde con la clasificacion del documento")
    if int(size_bytes) <= 0:
        raise HelpCaseDomainError("size_bytes debe ser positivo")
    if len(checksum_sha256) != 64:
        raise HelpCaseDomainError("checksum_sha256 debe tener 64 caracteres")

    consent_version = None
    if classification == "publico":
        consent_version = _current_consent(db, case.id).version
    latest = (
        db.query(models.DocumentoCasoAyuda)
        .filter_by(caso_id=case.id, document_key=document_key)
        .order_by(models.DocumentoCasoAyuda.version.desc())
        .first()
    )
    document = models.DocumentoCasoAyuda(
        caso_id=case.id,
        document_key=document_key,
        version=(latest.version + 1) if latest else 1,
        tipo=document_type,
        clasificacion=classification,
        storage_path=storage_path,
        nombre_original_cifrado=original_name_encrypted,
        content_type=content_type,
        size_bytes=int(size_bytes),
        checksum_sha256=checksum_sha256,
        consentimiento_version=consent_version,
        cargado_por=actor.email,
    )
    db.add(document)
    db.flush()
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="documento_registrado",
        entity_type="documento",
        entity_id=document.id,
        metadata={
            "clasificacion": classification,
            "tipo": document_type,
            "version": document.version,
        },
    )
    db.flush()
    return document


def review_case_document(
    db: Session,
    *,
    document_id: int,
    actor: ActorOrgContext,
    approve: bool,
    reason=None,
):
    document = db.get(models.DocumentoCasoAyuda, document_id)
    if document is None:
        raise CaseNotFoundError("Documento no encontrado")
    case = _require_editable_case(db, document.caso_id, actor)
    if document.estado_revision != "pendiente":
        raise CaseStateError("El documento ya fue revisado")
    if not approve and not str(reason or "").strip():
        raise HelpCaseDomainError("El rechazo requiere un motivo")
    if document.clasificacion == "publico":
        consent = _current_consent(db, case.id)
        if document.consentimiento_version != consent.version:
            raise HelpCaseDomainError("El documento no corresponde al consentimiento vigente")
    if (
        approve
        and document.clasificacion == "publico"
        and document.tipo == "informe_medico"
        and document.cargado_por == actor.email
    ):
        raise HelpCaseDomainError("El informe medico requiere un revisor distinto")

    document.estado_revision = "aprobado" if approve else "rechazado"
    document.revisado_por = actor.email
    document.revisado_at = datetime.now(timezone.utc)
    document.motivo_revision = str(reason).strip() if reason is not None else None
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="documento_revisado",
        entity_type="documento",
        entity_id=document.id,
        metadata={"estado": document.estado_revision, "version": document.version},
    )
    db.flush()
    return document


def _revoke_account_accesses(db, *, account, actor):
    scopes = (
        db.query(models.AlcanceCuentaAccesoTemporal)
        .filter_by(cuenta_id=account.id, cuenta_version=account.version)
        .all()
    )
    revoked = 0
    now = datetime.now(timezone.utc)
    for scope in scopes:
        access = db.get(models.AccesoTemporalAyuda, scope.acceso_id)
        if access and access.estado != "revocado":
            access.estado = "revocado"
            access.revocado_at = now
            access.revocado_por = actor.email
            access.motivo_revocacion = "cuenta_versionada"
            revoked += 1
    return revoked


def create_account_version(
    db: Session,
    *,
    case_id: int,
    actor: ActorOrgContext,
    account_key: str,
    owner_type: str,
    holder_name_encrypted: str,
    beneficiary_relationship: str,
    medium: str,
    currency: str,
    identifier_encrypted: str,
    responsible_name_encrypted: str,
    responsible_email_hash: str,
    responsible_email_encrypted: str,
    instructions_encrypted=None,
    justification_encrypted=None,
):
    case = _require_editable_case(db, case_id, actor)
    consent = _current_consent(db, case.id)
    account_key = _require_text(account_key, "account_key")
    owner_type = _require_text(owner_type, "owner_type")
    holder_name_encrypted = _require_text(holder_name_encrypted, "holder_name_encrypted")
    beneficiary_relationship = _require_text(beneficiary_relationship, "beneficiary_relationship")
    medium = _require_text(medium, "medium")
    currency = _require_text(currency, "currency").upper()
    identifier_encrypted = _require_text(identifier_encrypted, "identifier_encrypted")
    responsible_name_encrypted = _require_text(responsible_name_encrypted, "responsible_name_encrypted")
    responsible_email_hash = _require_text(responsible_email_hash, "responsible_email_hash")
    responsible_email_encrypted = _require_text(responsible_email_encrypted, "responsible_email_encrypted")
    if owner_type not in {"beneficiario", "organizacion", "tercero", "coordinador"}:
        raise HelpCaseDomainError("owner_type no soportado")
    if medium not in {"banco_venezolano", "zelle", "banco_internacional"}:
        raise HelpCaseDomainError("medium no soportado")
    if currency not in {"VES", "USD", "EUR"}:
        raise HelpCaseDomainError("currency no soportada")
    if len(responsible_email_hash) != 64:
        raise HelpCaseDomainError("responsible_email_hash debe ser SHA-256")
    exceptional = owner_type in {"tercero", "coordinador"}
    if exceptional and not str(justification_encrypted or "").strip():
        raise HelpCaseDomainError("La cuenta excepcional requiere justificacion")

    previous = (
        db.query(models.CuentaCasoAyuda)
        .filter_by(caso_id=case.id, account_key=account_key)
        .order_by(models.CuentaCasoAyuda.version.desc())
        .first()
    )
    revoked_accesses = 0
    if previous:
        previous.estado = "inactiva"
        revoked_accesses = _revoke_account_accesses(db, account=previous, actor=actor)
    now = datetime.now(timezone.utc)
    account = models.CuentaCasoAyuda(
        caso_id=case.id,
        account_key=account_key,
        version=(previous.version + 1) if previous else 1,
        tipo_titular=owner_type,
        titular_nombre_cifrado=holder_name_encrypted,
        relacion_beneficiario=beneficiary_relationship,
        medio=medium,
        moneda=currency,
        identificador_cifrado=identifier_encrypted,
        instrucciones_cifrado=instructions_encrypted,
        justificacion_cifrada=justification_encrypted,
        responsable_nombre_cifrado=responsible_name_encrypted,
        responsable_email_hash=responsible_email_hash,
        responsable_email_cifrado=responsible_email_encrypted,
        consentimiento_version=consent.version,
        estado="pendiente" if exceptional else "aprobada",
        creado_por=actor.email,
        aprobado_por=None if exceptional else actor.email,
        aprobado_at=None if exceptional else now,
    )
    db.add(account)
    db.flush()
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="cuenta_versionada" if previous else "cuenta_creada",
        entity_type="cuenta",
        entity_id=account.id,
        metadata={
            "accesos_revocados": revoked_accesses,
            "tipo_titular": owner_type,
            "version": account.version,
        },
    )
    db.flush()
    return account


def approve_exceptional_account(
    db: Session,
    *,
    account_id: int,
    actor: ActorOrgContext,
):
    account = db.get(models.CuentaCasoAyuda, account_id)
    if account is None:
        raise CaseNotFoundError("Cuenta no encontrada")
    case = _require_editable_case(db, account.caso_id, actor)
    if account.tipo_titular not in {"tercero", "coordinador"}:
        raise HelpCaseDomainError("La cuenta no requiere aprobacion excepcional")
    if account.estado != "pendiente":
        raise CaseStateError("La cuenta no esta pendiente de aprobacion")
    if account.creado_por == actor.email:
        raise HelpCaseDomainError("La cuenta requiere un segundo actor")
    account.estado = "aprobada"
    account.aprobado_por = actor.email
    account.aprobado_at = datetime.now(timezone.utc)
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="cuenta_excepcional_aprobada",
        entity_type="cuenta",
        entity_id=account.id,
        metadata={"tipo_titular": account.tipo_titular, "version": account.version},
    )
    db.flush()
    return account


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
