import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext
from services.help_cases import _add_audit_event, _get_managed_case
from services.help_crypto import HelpDataCipher
from services.help_donors import _require_donor_identity
from services.help_feature_flags import enabled_help_v2_organization_ids
from services.help_idempotency import hash_idempotency_payload


IDEMPOTENT_OFFER_OPERATION = "create_help_offer"
IDEMPOTENT_LABOR_OFFER_OPERATION = "create_labor_offer"
OFFER_PAYLOAD_VERSION = 1
OFFER_STATES = {"pendiente", "contactada", "completada", "rechazada", "cancelada"}
OFFER_TRANSITIONS = {
    "contactar": ({"pendiente"}, "contactada"),
    "completar": ({"contactada"}, "completada"),
    "rechazar": ({"pendiente", "contactada"}, "rechazada"),
    "cancelar": ({"pendiente", "contactada"}, "cancelada"),
}


class HelpOfferDomainError(ValueError):
    pass


class OfferAccessError(HelpOfferDomainError):
    pass


class OfferNotFoundError(HelpOfferDomainError):
    pass


class OfferStateError(HelpOfferDomainError):
    pass


class OfferIdempotencyConflictError(HelpOfferDomainError):
    pass


def _labor_idempotency_operation(db, *, actor_id, idempotency_key):
    return db.query(models.OperacionIdempotenteAyuda).filter_by(
        actor_id=actor_id,
        operacion=IDEMPOTENT_LABOR_OFFER_OPERATION,
        idempotency_key=idempotency_key,
    ).one_or_none()


def _replay_labor_idempotency_operation(operation, request_hash):
    if operation.request_hash != request_hash:
        raise OfferIdempotencyConflictError(
            "La Idempotency-Key ya se uso con un payload distinto"
        )
    if operation.estado == "completed" and operation.response_body:
        return json.loads(operation.response_body), False
    raise OfferIdempotencyConflictError(
        "La solicitud con esta Idempotency-Key todavia esta en proceso"
    )


def _offer_donor_response(offer, case_public_id):
    return {
        "id": offer.id,
        "case_public_id": case_public_id,
        "status": offer.estado,
        "created_at": offer.created_at.isoformat(),
    }


def _offer_detail(offer, cipher):
    payload = json.loads(cipher.decrypt(offer.payload_cifrado, field="offer.payload"))
    return {
        "id": offer.id,
        "caso_id": offer.caso_id,
        "tipo": offer.tipo,
        "estado": offer.estado,
        "description": payload.get("description"),
        "actor_donante_email": cipher.decrypt(
            offer.actor_donante_email_cifrado,
            field="offer.actor_email",
        ),
        "gestionada_por": offer.gestionada_por,
        "gestionada_en": offer.gestionada_en,
        "created_at": offer.created_at,
    }


def create_direct_offer(
    db: Session,
    *,
    actor: ActorOrgContext,
    public_id: str,
    description: str,
    contact_details: str,
    idempotency_key: str,
    cipher: HelpDataCipher,
):
    _require_donor_identity(actor)
    description = str(description or "").strip()
    contact_details = str(contact_details or "").strip()
    if not description:
        raise HelpOfferDomainError("description es obligatoria")
    if not contact_details:
        raise HelpOfferDomainError("contact_details es obligatorio")

    canonical_payload = {
        "public_id": public_id,
        "description": description,
        "contact_details": contact_details,
    }
    request_hash = hash_idempotency_payload(canonical_payload)
    existing = db.query(models.OperacionIdempotenteAyuda).filter_by(
        actor_id=actor.uid,
        operacion=IDEMPOTENT_OFFER_OPERATION,
        idempotency_key=idempotency_key,
    ).one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise OfferIdempotencyConflictError("La Idempotency-Key ya se uso con un payload distinto")
        if existing.estado == "completed" and existing.response_body:
            return json.loads(existing.response_body), False
        raise OfferIdempotencyConflictError("La solicitud con esta Idempotency-Key todavia esta en proceso")

    case = db.query(models.CasoAyudaV2).filter(
        models.CasoAyudaV2.public_id == public_id,
        models.CasoAyudaV2.estado == "publicado",
        models.CasoAyudaV2.organizacion_id.in_(enabled_help_v2_organization_ids()),
    ).with_for_update().one_or_none()
    if case is None:
        raise OfferAccessError("Caso publicado no encontrado")
    if not case.acepta_ayuda_directa:
        raise OfferAccessError("El caso no acepta ayuda directa")

    operation = models.OperacionIdempotenteAyuda(
        actor_id=actor.uid,
        organizacion_id=case.organizacion_id,
        operacion=IDEMPOTENT_OFFER_OPERATION,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        estado="processing",
    )
    db.add(operation)
    db.flush()

    offer = models.OfertaAyudaDirecta(
        caso_id=case.id,
        organizacion_id=case.organizacion_id,
        tipo="directa",
        actor_donante_uid=actor.uid,
        actor_donante_email_cifrado=cipher.encrypt(actor.email, field="offer.actor_email"),
        contacto_cifrado=cipher.encrypt(contact_details, field="offer.contact"),
        payload_cifrado=cipher.encrypt(
            json.dumps({"description": description}, sort_keys=True, separators=(",", ":")),
            field="offer.payload",
        ),
        payload_version=OFFER_PAYLOAD_VERSION,
        estado="pendiente",
    )
    db.add(offer)
    db.flush()

    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()),
        organizacion_id=case.organizacion_id,
        caso_id=case.id,
        accion="oferta_creada",
        actor_id=actor.uid,
        actor_tipo="donante",
        entidad_tipo="oferta",
        entidad_id=str(offer.id),
        metadata_json=json.dumps({"tipo": offer.tipo}, separators=(",", ":")),
    ))

    response = _offer_donor_response(offer, case.public_id)
    operation.estado = "completed"
    operation.response_status = 201
    operation.response_body = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    operation.completed_at = datetime.now(timezone.utc)
    db.flush()
    return response, True


def create_labor_offer(
    db: Session,
    *,
    actor: ActorOrgContext,
    public_id: str,
    tipo_trabajo: str,
    descripcion: str,
    remuneracion_estimada: str,
    telefono: str,
    correo: str,
    idempotency_key: str,
    cipher: HelpDataCipher,
):
    _require_donor_identity(actor)
    canonical_payload = {
        "public_id": public_id,
        "tipo_trabajo": str(tipo_trabajo).strip(),
        "descripcion": str(descripcion).strip(),
        "remuneracion_estimada": str(remuneracion_estimada).strip(),
        "telefono": str(telefono).strip(),
        "correo": str(correo).strip().lower(),
    }
    if any(not value for value in canonical_payload.values()):
        raise HelpOfferDomainError("La oferta laboral contiene campos vacios")
    request_hash = hash_idempotency_payload(canonical_payload)
    existing = _labor_idempotency_operation(
        db, actor_id=actor.uid, idempotency_key=idempotency_key,
    )
    if existing is not None:
        return _replay_labor_idempotency_operation(existing, request_hash)

    case = db.query(models.CasoAyudaV2).filter(
        models.CasoAyudaV2.public_id == public_id,
        models.CasoAyudaV2.categoria == "empleo",
        models.CasoAyudaV2.estado == "publicado",
        models.CasoAyudaV2.organizacion_id.in_(enabled_help_v2_organization_ids()),
    ).with_for_update().one_or_none()
    if case is None:
        raise OfferAccessError("Caso laboral publicado no encontrado")

    operation = models.OperacionIdempotenteAyuda(
        actor_id=actor.uid,
        organizacion_id=case.organizacion_id,
        operacion=IDEMPOTENT_LABOR_OFFER_OPERATION,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        estado="processing",
    )
    try:
        # The unique constraint is the final concurrency barrier. Keeping the
        # insert inside a savepoint lets the caller transaction remain usable
        # when another request wins after our initial read.
        with db.begin_nested():
            db.add(operation)
            db.flush()
    except IntegrityError:
        winner = _labor_idempotency_operation(
            db, actor_id=actor.uid, idempotency_key=idempotency_key,
        )
        if winner is None:
            raise OfferIdempotencyConflictError(
                "No fue posible resolver la solicitud idempotente"
            )
        return _replay_labor_idempotency_operation(winner, request_hash)

    offer = models.OfertaAyudaDirecta(
        caso_id=case.id,
        organizacion_id=case.organizacion_id,
        tipo="empleo",
        actor_donante_uid=actor.uid,
        actor_donante_email_cifrado=cipher.encrypt(actor.email, field="offer.actor_email"),
        contacto_cifrado=cipher.encrypt(
            json.dumps(
                {"telefono": canonical_payload["telefono"], "correo": canonical_payload["correo"]},
                sort_keys=True,
                separators=(",", ":"),
            ),
            field="labor_offer.contact",
        ),
        payload_cifrado=cipher.encrypt(
            json.dumps(
                {
                    "tipo_trabajo": canonical_payload["tipo_trabajo"],
                    "descripcion": canonical_payload["descripcion"],
                    "remuneracion_estimada": canonical_payload["remuneracion_estimada"],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            field="labor_offer.payload",
        ),
        payload_version=OFFER_PAYLOAD_VERSION,
        estado="pendiente_respuesta",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(offer)
    db.flush()

    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()),
        organizacion_id=case.organizacion_id,
        caso_id=case.id,
        accion="oferta_laboral_creada",
        actor_id=actor.uid,
        actor_tipo="donante",
        entidad_tipo="oferta",
        entidad_id=str(offer.id),
        metadata_json=json.dumps({"tipo": "empleo"}, separators=(",", ":")),
    ))
    response = {
        "id": offer.id,
        "case_public_id": case.public_id,
        "tipo": "empleo",
        "status": "pendiente_respuesta",
        "created_at": offer.created_at.isoformat(),
    }
    operation.estado = "completed"
    operation.response_status = 201
    operation.response_body = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    operation.completed_at = datetime.now(timezone.utc)
    db.flush()
    return response, True


def list_case_offers(db: Session, *, case_id: int, actor: ActorOrgContext, status=None):
    case = _get_managed_case(db, case_id, actor)
    query = db.query(models.OfertaAyudaDirecta).filter(models.OfertaAyudaDirecta.caso_id == case.id)
    normalized_status = str(status or "").strip()
    if normalized_status:
        if normalized_status not in OFFER_STATES:
            raise HelpOfferDomainError("status no soportado")
        query = query.filter(models.OfertaAyudaDirecta.estado == normalized_status)
    offers = query.order_by(
        models.OfertaAyudaDirecta.created_at.desc(),
        models.OfertaAyudaDirecta.id.desc(),
    ).all()
    cipher = HelpDataCipher.from_environment()
    return [_offer_detail(offer, cipher) for offer in offers]


def transition_offer(
    db: Session,
    *,
    case_id: int,
    offer_id: int,
    actor: ActorOrgContext,
    action: str,
    reason_code=None,
):
    normalized_action = str(action or "").strip().lower()
    transition = OFFER_TRANSITIONS.get(normalized_action)
    if transition is None:
        raise HelpOfferDomainError("Accion de oferta no soportada")
    allowed_states, next_state = transition

    case = _get_managed_case(db, case_id, actor, lock=True)
    offer = db.query(models.OfertaAyudaDirecta).filter(
        models.OfertaAyudaDirecta.id == offer_id,
        models.OfertaAyudaDirecta.caso_id == case.id,
    ).with_for_update().one_or_none()
    if offer is None:
        raise OfferNotFoundError("Oferta no encontrada")
    if offer.estado not in allowed_states:
        raise OfferStateError("La transicion no esta permitida para el estado actual")

    previous_state = offer.estado
    offer.estado = next_state
    offer.gestionada_por = actor.email
    offer.gestionada_en = datetime.now(timezone.utc)
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action=f"oferta_{next_state}",
        entity_type="oferta",
        entity_id=offer.id,
        reason_code=str(reason_code).strip() if reason_code else None,
        metadata={"estado_anterior": previous_state, "estado_nuevo": next_state},
    )
    db.flush()
    cipher = HelpDataCipher.from_environment()
    return _offer_detail(offer, cipher)
