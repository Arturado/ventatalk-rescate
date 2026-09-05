"""Transactional primitives for protected labor offers (no HTTP or cookies)."""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import models
from services.help_crypto import HelpDataCipher
from services.help_idempotency import hash_idempotency_payload, normalize_idempotency_key


LINK_TTL = timedelta(days=7)
SESSION_TTL = timedelta(hours=24)
RATE_WINDOW = timedelta(minutes=15)
RATE_LIMIT = 10
MODERATION_ROLES = frozenset({"coordinador", "admin", "super_admin"})
MODERATION_REASONS = frozenset({"contenido_inapropiado", "fraude_sospechado", "riesgo_privacidad"})


class LaborAccessError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _now(value=None):
    return value or datetime.now(timezone.utc)


def _hash(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _derived_token(link_token, context_hash, session_id, purpose):
    digest = hashlib.sha256(
        f"labor:{purpose}:{link_token}:{context_hash}:{session_id}".encode()
    ).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _audit(db, *, offer, action, actor_id, actor_type, reason=None, metadata=None):
    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()), organizacion_id=offer.organizacion_id,
        caso_id=offer.caso_id, accion=action, actor_id=actor_id,
        actor_tipo=actor_type, entidad_tipo="oferta_laboral",
        entidad_id=str(offer.id), motivo_codigo=reason,
        metadata_json=json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
    ))


def _locked_offer(db, offer_id):
    offer = db.query(models.OfertaAyudaDirecta).filter(
        models.OfertaAyudaDirecta.id == offer_id,
        models.OfertaAyudaDirecta.tipo == "empleo",
    ).with_for_update().one_or_none()
    if offer is None:
        raise LaborAccessError("labor_offer_unavailable")
    return offer


def issue_labor_access_link(db, *, offer_id, subject_type, subject_uid, issuer, now=None, token=None):
    moment = _now(now)
    if subject_type not in {"beneficiario", "representante"} or not str(subject_uid or "").strip():
        raise LaborAccessError("labor_access_subject_invalid")
    if issuer.role not in MODERATION_ROLES or not issuer.uid:
        raise LaborAccessError("labor_access_forbidden")
    offer = _locked_offer(db, offer_id)
    if offer.estado != "pendiente_respuesta" or moment >= offer.expires_at:
        raise LaborAccessError("labor_access_unavailable")
    if issuer.role != "super_admin" and issuer.organizacion_id != offer.organizacion_id:
        raise LaborAccessError("labor_access_forbidden")
    # A newly emitted link owns a fresh seven-day window. Redemption never
    # extends it; only explicit replacement can establish another window.
    offer.expires_at = moment + LINK_TTL
    prior_ids = [row[0] for row in db.query(models.DesafioAccesoOfertaLaboral.id).filter(
        models.DesafioAccesoOfertaLaboral.oferta_id == offer.id,
        models.DesafioAccesoOfertaLaboral.sujeto_uid == subject_uid,
        models.DesafioAccesoOfertaLaboral.revoked_at.is_(None),
    ).all()]
    if prior_ids:
        db.query(models.DesafioAccesoOfertaLaboral).filter(
            models.DesafioAccesoOfertaLaboral.id.in_(prior_ids)
        ).update({models.DesafioAccesoOfertaLaboral.revoked_at: moment}, synchronize_session=False)
        db.query(models.SesionOfertaLaboral).filter(
            models.SesionOfertaLaboral.desafio_id.in_(prior_ids),
            models.SesionOfertaLaboral.revoked_at.is_(None),
        ).update({models.SesionOfertaLaboral.revoked_at: moment}, synchronize_session=False)
    raw_token = token or secrets.token_urlsafe(32)
    case = db.get(models.CasoAyudaV2, offer.caso_id)
    challenge = models.DesafioAccesoOfertaLaboral(
        oferta_id=offer.id, caso_id=offer.caso_id,
        beneficiario_id=case.beneficiario_id,
        sujeto_tipo=subject_type, sujeto_uid=subject_uid,
        token_hash=_hash(raw_token), expires_at=offer.expires_at,
    )
    db.add(challenge)
    db.flush()
    _audit(db, offer=offer, action="enlace_laboral_emitido", actor_id=issuer.uid,
           actor_type="super_admin" if issuer.role == "super_admin" else issuer.role)
    return {"offer_id": offer.id, "link_token": raw_token,
            "expires_at": challenge.expires_at, "cache_control": "no-store"}


def redeem_labor_access_link(db, *, token, client_context, now=None):
    moment = _now(now)
    challenge = db.query(models.DesafioAccesoOfertaLaboral).filter_by(
        token_hash=_hash(token)
    ).with_for_update().one_or_none()
    if challenge is None:
        raise LaborAccessError("labor_access_unavailable")
    offer = _locked_offer(db, challenge.oferta_id)
    if (challenge.revoked_at is not None or moment >= challenge.expires_at
            or offer.estado != "pendiente_respuesta" or moment >= offer.expires_at):
        raise LaborAccessError("labor_access_unavailable")
    if challenge.rate_window_started_at is None or moment >= challenge.rate_window_started_at + RATE_WINDOW:
        challenge.rate_window_started_at = moment
        challenge.rate_window_count = 0
    if challenge.rate_window_count >= RATE_LIMIT:
        raise LaborAccessError("labor_access_rate_limited")
    challenge.rate_window_count += 1
    challenge.redeem_count += 1
    challenge.last_redeemed_at = moment
    context_hash = _hash(f"labor-client-context:{client_context}")
    session = db.query(models.SesionOfertaLaboral).filter(
        models.SesionOfertaLaboral.desafio_id == challenge.id,
        models.SesionOfertaLaboral.client_context_hash == context_hash,
        models.SesionOfertaLaboral.revoked_at.is_(None),
        models.SesionOfertaLaboral.expires_at > moment,
    ).order_by(models.SesionOfertaLaboral.id.desc()).first()
    if session is None:
        session = models.SesionOfertaLaboral(
            desafio_id=challenge.id, oferta_id=challenge.oferta_id, caso_id=challenge.caso_id,
            beneficiario_id=challenge.beneficiario_id, sujeto_tipo=challenge.sujeto_tipo,
            sujeto_uid=challenge.sujeto_uid, client_context_hash=context_hash,
            sesion_hash="0" * 64, csrf_hash="0" * 64,
            expires_at=min(moment + SESSION_TTL, challenge.expires_at, offer.expires_at),
        )
        db.add(session)
        db.flush()
    session_token = _derived_token(token, context_hash, session.id, "session")
    csrf_token = _derived_token(token, context_hash, session.id, "csrf")
    session.sesion_hash = _hash(session_token)
    session.csrf_hash = _hash(csrf_token)
    _audit(db, offer=offer, action="enlace_laboral_canjeado",
           actor_id=challenge.sujeto_uid, actor_type=challenge.sujeto_tipo,
           metadata={"rate_key_hash": _hash(f"{challenge.token_hash}:{context_hash}")})
    db.flush()
    return {"session_id": session.id, "session_token": session_token,
            "csrf_token": csrf_token, "expires_at": session.expires_at,
            "cache_control": "no-store"}


def _session_by_token(db, session_token, *, allow_revoked=False):
    session = db.query(models.SesionOfertaLaboral).filter_by(
        sesion_hash=_hash(session_token)
    ).one_or_none()
    if session is None or (session.revoked_at is not None and not allow_revoked):
        raise LaborAccessError("labor_session_unavailable")
    return session


def validate_labor_session_csrf(db, *, session_token, csrf_token):
    session = _session_by_token(db, session_token)
    if not csrf_token or not hmac.compare_digest(session.csrf_hash, _hash(csrf_token)):
        raise LaborAccessError("labor_csrf_invalid")
    return session


def close_labor_session(db, *, session_token, csrf_token, now=None):
    session = validate_labor_session_csrf(
        db, session_token=session_token, csrf_token=csrf_token,
    )
    session.revoked_at = _now(now)
    db.flush()
    return {"status": "closed"}


def get_labor_offer_context(db, *, session_token, now=None):
    moment = _now(now)
    session = _session_by_token(db, session_token)
    offer = db.get(models.OfertaAyudaDirecta, session.oferta_id)
    if moment >= session.expires_at or offer.estado != "pendiente_respuesta":
        raise LaborAccessError("labor_session_unavailable")
    payload = json.loads(HelpDataCipher.from_environment().decrypt(
        offer.payload_cifrado, field="labor_offer.payload"
    ))
    return {"offer_id": offer.id, "state": offer.estado,
            "tipo_trabajo": payload["tipo_trabajo"], "descripcion": payload["descripcion"],
            "remuneracion_estimada": payload["remuneracion_estimada"],
            "cache_control": "no-store"}


def list_labor_offers_for_moderation(db, *, actor):
    if actor.role not in MODERATION_ROLES or not actor.uid:
        raise LaborAccessError("labor_moderation_forbidden")
    query = db.query(models.OfertaAyudaDirecta).filter_by(tipo="empleo")
    if actor.role != "super_admin":
        query = query.filter_by(organizacion_id=actor.organizacion_id)
    rows = []
    for offer in query.order_by(models.OfertaAyudaDirecta.id).all():
        rows.append({"offer_id": offer.id, "case_id": offer.caso_id,
                     "state": offer.estado, "created_at": offer.created_at,
                     "cache_control": "no-store"})
        _audit(db, offer=offer, action="oferta_laboral_listada_redactada",
               actor_id=actor.uid,
               actor_type="admin" if actor.role == "admin" else actor.role)
    return rows


def get_donor_labor_offer_status(db, *, offer_id, actor):
    if actor.role != "donor" or not actor.uid:
        raise LaborAccessError("labor_offer_forbidden")
    offer = db.query(models.OfertaAyudaDirecta).filter_by(
        id=offer_id, tipo="empleo"
    ).one_or_none()
    if offer is None or offer.actor_donante_uid != actor.uid:
        raise LaborAccessError("labor_offer_forbidden")
    return {"offer_id": offer.id, "state": offer.estado,
            "created_at": offer.created_at, "expires_at": offer.expires_at}


def _idempotency(db, *, actor_id, operation, key, request, offer_id):
    key = normalize_idempotency_key(key)
    request_hash = hash_idempotency_payload(request)
    existing = db.query(models.OperacionIdempotenteAyuda).filter_by(
        actor_id=actor_id, operacion=operation, idempotency_key=key,
    ).one_or_none()
    if existing:
        if existing.request_hash != request_hash:
            raise LaborAccessError("idempotency_conflict")
        if existing.estado == "completed":
            return existing, json.loads(existing.response_body)
        raise LaborAccessError("idempotency_in_progress")
    record = models.OperacionIdempotenteAyuda(
        actor_id=actor_id, operacion=operation, idempotency_key=key,
        request_hash=request_hash, estado="processing", oferta_id=offer_id,
    )
    db.add(record)
    db.flush()
    return record, None


def _outbox(db, *, offer, state, cipher):
    key = f"labor-offer:{offer.id}:{state}:donante"
    existing = db.query(models.NotificacionCasoAyuda).filter_by(
        deduplication_key=key
    ).one_or_none()
    if existing:
        return existing
    row = models.NotificacionCasoAyuda(
        deduplication_key=key, evento_tipo=f"oferta_laboral_{state}",
        organizacion_id=offer.organizacion_id, caso_id=offer.caso_id, oferta_id=offer.id,
        destinatario_tipo="donante", destinatario_email_hash=None,
        destinatario_email_cifrado=offer.actor_donante_email_cifrado,
        template_key=f"labor-offer-{state}-v1",
        payload_json=cipher.encrypt(json.dumps({"offer_id": offer.id, "state": state},
            separators=(",", ":"), sort_keys=True), field="notification.payload"),
    )
    db.add(row)
    return row


def _revoke_access(db, offer_id, moment):
    db.query(models.DesafioAccesoOfertaLaboral).filter(
        models.DesafioAccesoOfertaLaboral.oferta_id == offer_id,
        models.DesafioAccesoOfertaLaboral.revoked_at.is_(None),
    ).update({models.DesafioAccesoOfertaLaboral.revoked_at: moment}, synchronize_session=False)
    db.query(models.SesionOfertaLaboral).filter(
        models.SesionOfertaLaboral.oferta_id == offer_id,
        models.SesionOfertaLaboral.revoked_at.is_(None),
    ).update({models.SesionOfertaLaboral.revoked_at: moment}, synchronize_session=False)


def _complete_terminal(db, *, offer, state, actor_type, actor_uid, reason, session,
                       operation, moment, cipher):
    if offer.estado != "pendiente_respuesta":
        raise LaborAccessError("terminal_conflict")
    previous_version = offer.lock_version
    offer.estado = state
    offer.terminal_at = moment
    offer.lock_version += 1
    offer.gestionada_por = actor_uid or "worker"
    offer.gestionada_en = moment
    transition = models.TransicionTerminalOfertaLaboral(
        oferta_id=offer.id, caso_id=offer.caso_id, estado_terminal=state,
        actor_tipo=actor_type, actor_uid=actor_uid, razon_codigo=reason,
        sesion_id=session.id if session else None, idempotencia_id=operation.id,
        oferta_version_anterior=previous_version,
    )
    db.add(transition)
    db.flush()
    if state == "aceptada":
        db.add(models.LiberacionContactoOfertaLaboral(
            oferta_id=offer.id, caso_id=offer.caso_id, transicion_id=transition.id,
            sesion_id=session.id, autorizado_uid=session.sujeto_uid,
            autorizado_tipo=session.sujeto_tipo,
        ))
    _revoke_access(db, offer.id, moment)
    _outbox(db, offer=offer, state=state, cipher=cipher)
    audit_type = {"donante": "donante", "worker": "sistema",
                  "admin_organizacion": "admin"}.get(actor_type, actor_type)
    _audit(db, offer=offer, action=f"oferta_laboral_{state}",
           actor_id=actor_uid or "labor-expiry-worker", actor_type=audit_type, reason=reason)


def decide_labor_offer(db, *, session_token, decision, idempotency_key, now=None):
    if decision not in {"aceptada", "rechazada"}:
        raise LaborAccessError("labor_decision_invalid")
    moment = _now(now)
    session = _session_by_token(db, session_token, allow_revoked=True)
    operation, replay = _idempotency(
        db, actor_id=session.sujeto_uid, operation="labor_decision", key=idempotency_key,
        request={"offer_id": session.oferta_id, "decision": decision}, offer_id=session.oferta_id,
    )
    if replay is not None:
        return replay
    if session.revoked_at is not None or moment >= session.expires_at:
        raise LaborAccessError("labor_session_unavailable")
    offer = _locked_offer(db, session.oferta_id)
    cipher = HelpDataCipher.from_environment()
    _complete_terminal(db, offer=offer, state=decision, actor_type=session.sujeto_tipo,
                       actor_uid=session.sujeto_uid, reason=None, session=session,
                       operation=operation, moment=moment, cipher=cipher)
    response = {"offer_id": offer.id, "state": decision, "cache_control": "no-store"}
    if decision == "aceptada":
        response["contact"] = json.loads(cipher.decrypt(
            offer.contacto_cifrado, field="labor_offer.contact"
        ))
    persisted = {key: value for key, value in response.items() if key != "contact"}
    operation.estado = "completed"
    operation.response_status = 200
    operation.response_body = json.dumps(persisted, separators=(",", ":"), sort_keys=True)
    operation.completed_at = moment
    db.flush()
    return response


def cancel_labor_offer(db, *, offer_id, actor, reason, idempotency_key, now=None):
    moment = _now(now)
    if not actor.uid:
        raise LaborAccessError("labor_cancel_forbidden")
    offer = _locked_offer(db, offer_id)
    if actor.role == "donor":
        allowed = actor.uid == offer.actor_donante_uid and reason == "donor_voluntaria"
        actor_type = "donante"
    else:
        allowed = actor.role in MODERATION_ROLES and reason in MODERATION_REASONS and (
            actor.role == "super_admin" or actor.organizacion_id == offer.organizacion_id)
        actor_type = "admin_organizacion" if actor.role == "admin" else actor.role
    if not allowed:
        raise LaborAccessError("labor_cancel_forbidden")
    operation, replay = _idempotency(
        db, actor_id=actor.uid, operation="labor_cancel", key=idempotency_key,
        request={"offer_id": offer.id, "reason": reason}, offer_id=offer.id,
    )
    if replay is not None:
        return replay
    _complete_terminal(db, offer=offer, state="cancelada", actor_type=actor_type,
                       actor_uid=actor.uid, reason=reason, session=None,
                       operation=operation, moment=moment, cipher=HelpDataCipher.from_environment())
    response = {"offer_id": offer.id, "state": "cancelada", "cache_control": "no-store"}
    operation.estado = "completed"
    operation.response_status = 200
    operation.response_body = json.dumps(response, separators=(",", ":"), sort_keys=True)
    operation.completed_at = moment
    db.flush()
    return response


def expire_labor_offer(db, *, offer_id, worker_authorized, now=None):
    if not worker_authorized:
        raise LaborAccessError("labor_expire_forbidden")
    moment = _now(now)
    offer = _locked_offer(db, offer_id)
    actor_id = "labor-expiry-worker"
    key = f"labor-expire-{_hash(f'{offer.id}:{offer.expires_at.isoformat()}')}"
    operation, replay = _idempotency(
        db, actor_id=actor_id, operation="labor_expire", key=key,
        request={"offer_id": offer.id, "expires_at": offer.expires_at.isoformat()},
        offer_id=offer.id,
    )
    if replay is not None:
        return replay
    if moment < offer.expires_at:
        raise LaborAccessError("labor_offer_not_expired")
    _complete_terminal(db, offer=offer, state="vencida", actor_type="worker",
                       actor_uid=None, reason=None, session=None, operation=operation,
                       moment=moment, cipher=HelpDataCipher.from_environment())
    response = {"offer_id": offer.id, "state": "vencida", "cache_control": "no-store"}
    operation.estado = "completed"
    operation.response_status = 200
    operation.response_body = json.dumps(response, separators=(",", ":"), sort_keys=True)
    operation.completed_at = moment
    db.flush()
    return response
