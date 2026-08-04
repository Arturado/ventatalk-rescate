import json

from sqlalchemy.orm import Session

import models
import schemas
from dependencies import ActorOrgContext
from schemas import _ordered_help_case_modes
from services.help_beneficiaries import get_or_create_beneficiary
from services.help_cases import (
    HelpCaseDomainError,
    _add_audit_event,
    _require_organization_access,
)
from services.help_crypto import HelpDataCipher
from services.help_idempotency import hash_idempotency_payload


IDEMPOTENT_DRAFT_OPERATION = "create_help_case_draft"


class DraftIdempotencyConflictError(HelpCaseDomainError):
    pass


def _draft_payload_fingerprint(payload: "schemas.CasoAyudaV2DraftCreateRequest") -> str:
    return hash_idempotency_payload(payload.model_dump(mode="json"))


def _conditional_detail_json(payload):
    detail = {
        "profession": payload.profession,
        "beneficiary_access_email": payload.beneficiary_access_email,
        "direct_request": payload.direct_request,
    }
    return {key: value for key, value in detail.items() if value is not None}


def case_draft_contract_summary(case: "models.CasoAyudaV2") -> dict:
    if case.categoria == "empleo":
        aid_modes = ["oferta_laboral"]
    else:
        enabled_modes = set()
        if case.acepta_ayuda_monetaria:
            enabled_modes.add("monetaria")
        if case.acepta_ayuda_directa:
            enabled_modes.add("directa")
        aid_modes = _ordered_help_case_modes(enabled_modes)
    monetary = "monetaria" in aid_modes
    return {
        "id": case.id,
        "public_id": case.public_id,
        "organizacion_id": case.organizacion_id,
        "title": case.titulo_interno,
        "category": case.categoria,
        "subject_type": case.tipo_sujeto,
        "aid_modes": aid_modes,
        "state": case.estado,
        "goal_amount": case.meta_monto if monetary else None,
        "goal_currency": case.meta_moneda if monetary else None,
        "confirmed_amount": case.monto_confirmado if monetary else None,
        "confirmed_aids": case.ayudas_confirmadas if monetary else None,
    }


def create_help_case_draft(
    db: Session,
    *,
    actor: ActorOrgContext,
    organization_id: str,
    public_id: str,
    payload: "schemas.CasoAyudaV2DraftCreateRequest",
    idempotency_key: str,
    cipher: HelpDataCipher,
):
    organization_id = _require_organization_access(actor, organization_id)
    request_hash = _draft_payload_fingerprint(payload)

    existing_operation = db.query(models.OperacionIdempotenteAyuda).filter_by(
        actor_id=actor.email,
        operacion=IDEMPOTENT_DRAFT_OPERATION,
        idempotency_key=idempotency_key,
    ).one_or_none()
    if existing_operation is not None:
        if existing_operation.request_hash != request_hash:
            raise DraftIdempotencyConflictError("La Idempotency-Key ya se uso con un payload distinto")
        if existing_operation.estado == "completed" and existing_operation.response_body:
            return json.loads(existing_operation.response_body), False
        raise DraftIdempotencyConflictError(
            "La solicitud con esta Idempotency-Key todavia esta en proceso"
        )

    operation = models.OperacionIdempotenteAyuda(
        actor_id=actor.email,
        organizacion_id=organization_id,
        operacion=IDEMPOTENT_DRAFT_OPERATION,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        estado="processing",
    )
    db.add(operation)
    db.flush()

    beneficiary = None
    beneficiary_created = False
    if payload.subject_type == "persona":
        beneficiary, beneficiary_created = get_or_create_beneficiary(
            db,
            organization_id=organization_id,
            identity_hash=cipher.identity_hash(payload.beneficiary_identity),
            identity_encrypted=cipher.encrypt(payload.beneficiary_identity, field="beneficiary.identity"),
            name_encrypted=cipher.encrypt(payload.beneficiary_name, field="beneficiary.name"),
            actor_email=actor.email,
        )

    conditional_detail = _conditional_detail_json(payload)
    detail_encrypted = None
    detail_version = None
    if conditional_detail:
        detail_encrypted = cipher.encrypt(
            json.dumps(conditional_detail, sort_keys=True, separators=(",", ":")),
            field="case.conditional_detail",
        )
        detail_version = 1

    case = models.CasoAyudaV2(
        public_id=public_id,
        organizacion_id=organization_id,
        beneficiario_id=beneficiary.id if beneficiary else None,
        tipo_sujeto=payload.subject_type,
        titulo_interno=payload.title,
        relato_privado_cifrado=cipher.encrypt(payload.story, field="case.story"),
        categoria=payload.category,
        acepta_ayuda_monetaria="monetaria" in payload.aid_modes,
        acepta_ayuda_directa="directa" in payload.aid_modes,
        detalle_condicional_cifrado=detail_encrypted,
        detalle_condicional_version=detail_version,
        meta_monto=payload.goal_amount,
        meta_moneda=payload.goal_currency,
        creado_por=actor.email,
    )
    db.add(case)
    db.flush()

    if beneficiary is not None and beneficiary_created:
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
        action="caso_borrador_creado",
        entity_type="caso",
        entity_id=case.id,
        metadata={"categoria": case.categoria, "tipo_sujeto": case.tipo_sujeto},
    )

    summary = case_draft_contract_summary(case)
    operation.estado = "completed"
    operation.response_status = 201
    operation.response_body = json.dumps(summary, sort_keys=True, separators=(",", ":"), default=str)
    db.flush()

    return summary, True
