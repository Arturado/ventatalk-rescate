from typing import List, Literal, Optional
from uuid import uuid4
import json
import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

import schemas
import models
from database import get_db
from dependencies import (
    ActorOrgContext,
    require_verified_case_organization_actor,
    verify_api_key,
)
from services.help_cases import (
    CaseNotFoundError,
    CaseLifecycleAccessError,
    CaseReadinessError,
    CaseStateError,
    HelpCaseDomainError,
    OrganizationAccessError,
    create_help_case,
    get_help_case_detail,
    list_help_cases,
    mark_help_case_ready,
    publish_help_case,
    configure_help_case_publication,
    approve_exceptional_account,
    create_account_version,
    register_beneficiary_verification,
    register_case_document,
    register_help_case_consent,
    submit_help_case_for_validation,
    transition_help_case,
    update_help_case,
)
from services.help_crypto import HelpDataCipher, HelpDataCryptoError
from services.help_files import decode_private_evidence, save_encrypted_private_evidence
from services.help_confirmations import (
    HelpConfirmationIdempotencyError,
    HelpRateUnavailableError,
    confirm_help_aid,
    list_help_aids,
    mark_help_aid_in_review,
    reject_help_aid,
    report_help_aid_problem,
)
from services.help_exchange import (
    ManualBcvRateAccessError,
    ManualBcvRateConflictError,
    DolarApiBcvRateProvider,
    register_manual_bcv_rate,
)
from services.help_idempotency import normalize_idempotency_key


router = APIRouter(prefix="/api/v2/casos-ayuda", tags=["casos-ayuda-v2"])


def get_bcv_rate_provider():
    return DolarApiBcvRateProvider()


@router.post("/tasas/manual", response_model=schemas.TasaBcvResponse, status_code=201)
def create_manual_bcv_rate(
    payload: schemas.TasaBcvManualRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        rate = register_manual_bcv_rate(
            db,
            actor=actor,
            rate_date=payload.rate_date,
            source_currency=payload.source_currency,
            target_currency=payload.target_currency,
            value=payload.value,
            source_reference=payload.source_reference,
            reason=payload.reason,
        )
        db.commit()
        return {
            "id": rate.id,
            "source": rate.fuente,
            "rate_date": rate.fecha_tasa,
            "source_currency": rate.moneda_base,
            "target_currency": rate.moneda_cotizada,
            "value": rate.valor,
            "registered_by": rate.registrado_manualmente_por,
        }
    except ManualBcvRateAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ManualBcvRateConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
CaseState = Literal[
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
]


@router.get("/{case_id}/ayudas", response_model=List[schemas.AyudaMonetariaOrganizacionResumenResponse])
def list_organization_help_aids(
    case_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        return list_help_aids(db, case_id=case_id, actor=actor)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/{case_id}/ayudas/{aid_id}/en-revision",
    response_model=schemas.AyudaMonetariaOrganizacionResumenResponse,
)
def review_organization_help_aid(
    case_id: int,
    aid_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        aid = mark_help_aid_in_review(db, case_id=case_id, aid_id=aid_id, actor=actor)
        db.commit()
        return aid
    except (CaseNotFoundError, OrganizationAccessError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{case_id}/ayudas/{aid_id}/problema",
    response_model=schemas.AyudaMonetariaOrganizacionResumenResponse,
)
def report_organization_help_aid_problem(
    case_id: int,
    aid_id: int,
    payload: schemas.ProblemaAyudaRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        aid = report_help_aid_problem(
            db,
            case_id=case_id,
            aid_id=aid_id,
            actor=actor,
            problem_type=payload.problem_type,
            detail=payload.detail,
        )
        db.commit()
        return aid
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc


@router.post(
    "/{case_id}/ayudas/{aid_id}/rechazar",
    response_model=schemas.AyudaMonetariaOrganizacionResumenResponse,
)
def reject_organization_help_aid(
    case_id: int,
    aid_id: int,
    payload: schemas.RechazoAyudaRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        aid = reject_help_aid(
            db,
            case_id=case_id,
            aid_id=aid_id,
            actor=actor,
            reason=payload.reason,
        )
        db.commit()
        return aid
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc


@router.post(
    "/{case_id}/ayudas/{aid_id}/confirmar",
    response_model=schemas.ConfirmacionAyudaResponse,
)
def confirm_organization_help_aid(
    case_id: int,
    aid_id: int,
    payload: schemas.ConfirmacionAyudaRequest,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    rate_provider=Depends(get_bcv_rate_provider),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    key = normalize_idempotency_key(idempotency_key)
    try:
        response, replayed = confirm_help_aid(
            db,
            case_id=case_id,
            aid_id=aid_id,
            actor=actor,
            idempotency_key=key,
            received_amount=payload.received_amount,
            received_currency=payload.received_currency,
            effective_date=payload.effective_date,
            rate_provider=rate_provider,
            comment=payload.comment,
        )
        if not replayed:
            db.commit()
        return response
    except (CaseNotFoundError, OrganizationAccessError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HelpRateUnavailableError as exc:
        db.commit()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (CaseStateError, HelpConfirmationIdempotencyError) as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc


def _mask_restricted_value(value, visible=4):
    normalized = "".join(character for character in str(value or "") if character.isalnum())
    return "*" * max(len(normalized) - visible, 3) + normalized[-visible:]


def _mask_email(value):
    local, separator, domain = str(value or "").partition("@")
    if not separator:
        return _mask_restricted_value(value)
    return f"{local[:1]}***@{domain}"


def _detail_response(db, case_id, actor):
    case, blockers = get_help_case_detail(db, case_id=case_id, actor=actor)
    accounts = (
        db.query(models.CuentaCasoAyuda)
        .filter_by(caso_id=case.id)
        .order_by(models.CuentaCasoAyuda.account_key, models.CuentaCasoAyuda.version.desc())
        .all()
    )
    publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id).one_or_none()
    publication_data = None
    if publication:
        publication_data = {
            "nombre_publico": publication.nombre_publico,
            "titulo_publico": publication.titulo_publico,
            "descripcion_publica": publication.descripcion_publica,
            "localidad_general": publication.localidad_general,
            "redes_sociales": json.loads(publication.redes_sociales_json or "{}"),
            "version": publication.version,
            "activa": publication.activa,
        }
    beneficiary = db.get(models.BeneficiarioAyuda, case.beneficiario_id)
    cipher = HelpDataCipher.from_environment()
    beneficiary_name = cipher.decrypt(
        beneficiary.nombres_apellidos_cifrado,
        field="beneficiary.name",
    )
    beneficiary_identity = cipher.decrypt(
        beneficiary.cedula_cifrada,
        field="beneficiary.identity",
    )
    identity_digits = "".join(character for character in beneficiary_identity if character.isdigit())
    masked_identity = "*" * max(len(identity_digits) - 3, 3) + identity_digits[-3:]
    account_data = []
    for account in accounts:
        identifier = cipher.decrypt(account.identificador_cifrado, field="account.identifier")
        responsible_email = cipher.decrypt(account.responsable_email_cifrado, field="account.responsible_email")
        account_data.append({
            "id": account.id,
            "account_key": account.account_key,
            "version": account.version,
            "tipo_titular": account.tipo_titular,
            "medio": account.medio,
            "moneda": account.moneda,
            "estado": account.estado,
            "titular_nombre": cipher.decrypt(account.titular_nombre_cifrado, field="account.holder_name"),
            "relacion_beneficiario": account.relacion_beneficiario,
            "identificador_enmascarado": _mask_email(identifier) if account.medio == "zelle" else _mask_restricted_value(identifier),
            "instrucciones": cipher.decrypt(account.instrucciones_cifrado, field="account.instructions") if account.instrucciones_cifrado else None,
            "justificacion": cipher.decrypt(account.justificacion_cifrada, field="account.justification") if account.justificacion_cifrada else None,
            "responsable_nombre": cipher.decrypt(account.responsable_nombre_cifrado, field="account.responsible_name"),
            "responsable_email": responsible_email,
            "responsable_email_enmascarado": _mask_email(responsible_email),
        })
    consent = (
        db.query(models.ConsentimientoCasoAyuda)
        .filter_by(caso_id=case.id)
        .order_by(models.ConsentimientoCasoAyuda.version.desc())
        .first()
    )
    return {
        **schemas.CasoAyudaV2ResumenResponse.model_validate(case).model_dump(),
        "readiness_blockers": blockers,
        "accounts": account_data,
        "publicacion": publication_data,
        "verificacion": {
            "registrada": bool(beneficiary and beneficiary.datos_verificacion_cifrado),
            "es_menor": bool(beneficiary and beneficiary.es_menor),
            "tiene_representante": bool(beneficiary and beneficiary.representante_relacion),
            "relacion_representante": beneficiary.representante_relacion if beneficiary and beneficiary.representante_relacion else None,
            "autoridad_representante_verificada": bool(beneficiary and beneficiary.representante_autoridad_verificada_en),
        },
        "consentimiento": {
            "registrado": bool(consent),
            "version": consent.version if consent else None,
            "tipo_firmante": consent.firmante_tipo if consent else None,
            "evidencia_adjunta": bool(consent and consent.evidencia_documento_id),
            "vigente": bool(consent and consent.vigente),
        },
        "beneficiario": {
            "nombre_legal": beneficiary_name,
            "cedula_enmascarada": masked_identity,
        },
    }


@router.get("", response_model=List[schemas.CasoAyudaV2ResumenResponse])
def list_organization_help_cases(
    organizacion_id: Optional[str] = Query(default=None),
    estado: Optional[CaseState] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        return list_help_cases(
            db,
            actor=actor,
            organization_id=organizacion_id,
            status=estado,
            skip=skip,
            limit=limit,
        )
    except OrganizationAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HelpCaseDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=schemas.CasoAyudaV2ResumenResponse, status_code=201)
def create_organization_help_case(
    payload: schemas.CasoAyudaV2CreateRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    organization_id = str(payload.organizacion_id or actor.organizacion_id or "").strip()
    try:
        cipher = HelpDataCipher.from_environment()
        case = create_help_case(
            db,
            actor=actor,
            organization_id=organization_id,
            public_id=f"ayuda-{uuid4().hex}",
            beneficiary_name_encrypted=cipher.encrypt(
                payload.beneficiary_name,
                field="beneficiary.name",
            ),
            beneficiary_identity_hash=cipher.identity_hash(payload.beneficiary_identity),
            beneficiary_identity_encrypted=cipher.encrypt(
                payload.beneficiary_identity,
                field="beneficiary.identity",
            ),
            internal_title=payload.internal_title,
            category=payload.category,
            goal_amount=payload.goal_amount,
            goal_currency=payload.goal_currency,
        )
        db.commit()
        db.refresh(case)
        return case
    except OrganizationAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="El caso o beneficiario ya existe") from exc
    except HelpCaseDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{case_id}", response_model=schemas.CasoAyudaV2DetalleResponse)
def get_organization_help_case(
    case_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        return _detail_response(db, case_id, actor)
    except CaseNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.patch("/{case_id}", response_model=schemas.CasoAyudaV2DetalleResponse)
def update_organization_help_case(
    case_id: int,
    payload: schemas.CasoAyudaV2UpdateRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        private_story_encrypted = None
        if payload.private_story is not None:
            private_story_encrypted = HelpDataCipher.from_environment().encrypt(
                payload.private_story,
                field="case.private_story",
            )
        case = update_help_case(
            db,
            case_id=case_id,
            actor=actor,
            internal_title=payload.internal_title,
            category=payload.category,
            goal_amount=payload.goal_amount,
            goal_currency=payload.goal_currency,
            private_story_encrypted=private_story_encrypted,
        )
        db.commit()
        db.refresh(case)
        return _detail_response(db, case_id, actor)
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/verificacion", response_model=schemas.CasoAyudaV2DetalleResponse)
def verify_organization_help_case_beneficiary(
    case_id: int,
    payload: schemas.BeneficiarioAyudaVerificacionRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        cipher = HelpDataCipher.from_environment()
        register_beneficiary_verification(
            db,
            case_id=case_id,
            actor=actor,
            verification_data_encrypted=cipher.encrypt(
                payload.verification_data,
                field="beneficiary.verification",
            ),
            is_minor=payload.is_minor,
            representative_name_encrypted=(
                cipher.encrypt(payload.representative_name, field="beneficiary.representative_name")
                if payload.representative_name
                else None
            ),
            representative_relationship=payload.representative_relationship,
            representative_authority_verified=payload.representative_authority_verified,
        )
        db.commit()
        return _detail_response(db, case_id, actor)
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc
    except HelpCaseDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/{case_id}/publicacion", response_model=schemas.CasoAyudaV2DetalleResponse)
def configure_organization_help_case_publication(
    case_id: int,
    payload: schemas.PublicacionCasoAyudaRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        configure_help_case_publication(
            db,
            case_id=case_id,
            actor=actor,
            public_name=payload.public_name,
            public_title=payload.public_title,
            public_description=payload.public_description,
            general_location=payload.general_location,
            social_networks_json=json.dumps(payload.social_networks, separators=(",", ":")),
        )
        db.commit()
        return _detail_response(db, case_id, actor)
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseLifecycleAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HelpCaseDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{case_id}/consentimiento", response_model=schemas.CasoAyudaV2DetalleResponse)
def create_organization_help_case_consent(
    case_id: int,
    payload: schemas.ConsentimientoCasoAyudaCreateRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    destination = None
    try:
        cipher = HelpDataCipher.from_environment()
        document = None
        if payload.evidence_base64:
            if not payload.evidence_file_name or not payload.evidence_content_type:
                raise HTTPException(status_code=422, detail="La evidencia requiere nombre y tipo de archivo")
            evidence = decode_private_evidence(payload.evidence_base64, payload.evidence_content_type)
            encrypted_evidence = cipher.encrypt_bytes(evidence, field="consent.evidence")
            storage_path, destination = save_encrypted_private_evidence(encrypted_evidence, case_id)
            document = register_case_document(
                db,
                case_id=case_id,
                actor=actor,
                document_key=f"consent-evidence-{uuid4().hex}",
                document_type="consentimiento",
                classification="privado",
                storage_path=storage_path,
                content_type=payload.evidence_content_type,
                size_bytes=len(evidence),
                checksum_sha256=hashlib.sha256(evidence).hexdigest(),
                original_name_encrypted=cipher.encrypt(payload.evidence_file_name, field="document.original_name"),
            )
        register_help_case_consent(
            db,
            case_id=case_id,
            actor=actor,
            text_version=payload.text_version,
            scope_json=json.dumps(payload.scope, separators=(",", ":")),
            signer_name_encrypted=cipher.encrypt(payload.signer_name, field="consent.signer_name"),
            signer_type=payload.signer_type,
            evidence_document_id=document.id if document else None,
        )
        db.commit()
        return _detail_response(db, case_id, actor)
    except CaseNotFoundError as exc:
        db.rollback()
        if destination:
            destination.unlink(missing_ok=True)
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        if destination:
            destination.unlink(missing_ok=True)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (HelpDataCryptoError, HelpCaseDomainError) as exc:
        db.rollback()
        if destination:
            destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{case_id}/cuentas", response_model=schemas.CasoAyudaV2DetalleResponse)
def create_organization_help_case_account(
    case_id: int,
    payload: schemas.CuentaCasoAyudaCreateRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        cipher = HelpDataCipher.from_environment()
        create_account_version(
            db,
            case_id=case_id,
            actor=actor,
            account_key=payload.account_key,
            owner_type=payload.owner_type,
            holder_name_encrypted=cipher.encrypt(payload.holder_name, field="account.holder_name"),
            beneficiary_relationship=payload.beneficiary_relationship,
            medium=payload.medium,
            currency=payload.currency,
            identifier_encrypted=cipher.encrypt(payload.identifier, field="account.identifier"),
            instructions_encrypted=(cipher.encrypt(payload.instructions, field="account.instructions") if payload.instructions else None),
            justification_encrypted=(cipher.encrypt(payload.justification, field="account.justification") if payload.justification else None),
            responsible_name_encrypted=cipher.encrypt(payload.responsible_name, field="account.responsible_name"),
            responsible_email_hash=cipher.blind_index(payload.responsible_email, purpose="responsible-email"),
            responsible_email_encrypted=cipher.encrypt(payload.responsible_email, field="account.responsible_email"),
        )
        db.commit()
        return _detail_response(db, case_id, actor)
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (HelpDataCryptoError, HelpCaseDomainError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{case_id}/cuentas/{account_id}/aprobar", response_model=schemas.CasoAyudaV2DetalleResponse)
def approve_organization_help_case_account(
    case_id: int,
    account_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        account = db.get(models.CuentaCasoAyuda, account_id)
        if account is None or account.caso_id != case_id:
            raise CaseNotFoundError("Cuenta no encontrada")
        approve_exceptional_account(db, account_id=account_id, actor=actor)
        db.commit()
        return _detail_response(db, case_id, actor)
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HelpCaseDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc
    except HelpCaseDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{case_id}/enviar-validacion", response_model=schemas.CasoAyudaV2ResumenResponse)
def submit_organization_help_case(
    case_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        case = submit_help_case_for_validation(db, case_id=case_id, actor=actor)
        db.commit()
        db.refresh(case)
        return case
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/marcar-listo", response_model=schemas.CasoAyudaV2ResumenResponse)
def ready_organization_help_case(
    case_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        case = mark_help_case_ready(db, case_id=case_id, actor=actor)
        db.commit()
        db.refresh(case)
        return case
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseReadinessError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"message": str(exc), "blockers": exc.blockers}) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{case_id}/publicar", response_model=schemas.CasoAyudaV2ResumenResponse)
def publish_organization_help_case(
    case_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        case = publish_help_case(db, case_id=case_id, actor=actor)
        db.commit()
        db.refresh(case)
        return case
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseReadinessError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail={"message": str(exc), "blockers": exc.blockers}) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/{case_id}/{action}",
    response_model=schemas.CasoAyudaV2ResumenResponse,
)
def transition_organization_help_case(
    case_id: int,
    action: Literal["pausar", "reanudar", "cerrar", "rechazar", "suspender", "reactivar", "archivar"],
    payload: schemas.TransicionCasoAyudaRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        case = transition_help_case(
            db,
            case_id=case_id,
            actor=actor,
            action=action,
            reason_code=payload.reason_code,
        )
        db.commit()
        db.refresh(case)
        return case
    except (CaseNotFoundError, OrganizationAccessError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except CaseLifecycleAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except CaseStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except HelpCaseDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
