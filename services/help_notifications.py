import json
import os
import re
import secrets
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

import models
from services.help_crypto import HelpDataCipher


class NotificationPayloadError(ValueError):
    pass


class ProviderConfigurationError(RuntimeError):
    pass


class NotificationProviderError(RuntimeError):
    def __init__(self, message, *, code):
        self.code = str(code or "provider_error")[:100]
        super().__init__(message)


MAX_NOTIFICATION_ATTEMPTS = 5
MAX_NOTIFICATION_BACKOFF_SECONDS = 60 * 60
PROCESSING_LEASE_SECONDS = 15 * 60
RESEND_URL = "https://api.resend.com/emails"
ALLOWED_RECIPIENT_TYPES = frozenset({
    "beneficiario",
    "responsable",
    "donante",
    "coordinador",
    "admin",
})


TEMPLATE_PAYLOAD_FIELDS = {
    "aid-reported-v1": frozenset({"case_public_id"}),
    "aid-confirmed-v1": frozenset({"case_public_id"}),
    "aid-problem-v1": frozenset({"case_public_id"}),
    "aid-review-resolution-v1": frozenset({"case_public_id", "resolution"}),
    "goal-reached-v1": frozenset({"case_public_id"}),
    "account-changed-v1": frozenset({"case_public_id", "account_version"}),
    "temporary-access-v1": frozenset({"challenge_token"}),
    "labor-offer-access-v1": frozenset({"challenge_id", "offer_id"}),
    "labor-offer-aceptada-v1": frozenset({"offer_id", "state"}),
    "labor-offer-rechazada-v1": frozenset({"offer_id", "state"}),
    "labor-offer-vencida-v1": frozenset({"offer_id", "state"}),
    "labor-offer-cancelada-v1": frozenset({"offer_id", "state", "public_reason"}),
}
TEMPLATE_EVENT_TYPES = {
    "aid-reported-v1": "ayuda_reportada",
    "aid-confirmed-v1": "ayuda_confirmada",
    "aid-problem-v1": "problema_reportado",
    "aid-review-resolution-v1": "revision_resuelta",
    "goal-reached-v1": "meta_alcanzada",
    "account-changed-v1": "account_changed",
    "temporary-access-v1": "acceso_temporal",
    "labor-offer-access-v1": "acceso_temporal",
    "labor-offer-aceptada-v1": "oferta_laboral_aceptada",
    "labor-offer-rechazada-v1": "oferta_laboral_rechazada",
    "labor-offer-vencida-v1": "oferta_laboral_vencida",
    "labor-offer-cancelada-v1": "oferta_laboral_cancelada",
}

TEMPLATE_CONTENT = {
    "aid-reported-v1": (
        "Ayuda reportada",
        "Se reporto una ayuda para el caso {case_public_id}. Ingresa a la plataforma para revisarla.",
    ),
    "aid-confirmed-v1": (
        "Ayuda confirmada",
        "La ayuda para el caso {case_public_id} fue confirmada.",
    ),
    "aid-problem-v1": (
        "Problema reportado",
        "Se reporto un problema con una ayuda del caso {case_public_id}. Ingresa a la plataforma para revisarlo.",
    ),
    "aid-review-resolution-v1": (
        "Revision resuelta",
        "La revision de una ayuda del caso {case_public_id} fue resuelta con estado {resolution}.",
    ),
    "goal-reached-v1": (
        "Meta alcanzada",
        "El caso {case_public_id} alcanzo su meta.",
    ),
    "account-changed-v1": (
        "Cuenta actualizada",
        "La cuenta version {account_version} del caso {case_public_id} fue registrada. Ingresa a la plataforma para revisarla.",
    ),
    "labor-offer-aceptada-v1": ("Oferta laboral aceptada", "La persona autorizada acepto la oferta y podra iniciar el contacto."),
    "labor-offer-rechazada-v1": ("Oferta laboral rechazada", "La oferta laboral fue rechazada."),
    "labor-offer-vencida-v1": ("Oferta laboral vencida", "La oferta laboral vencio sin una decision."),
    "labor-offer-cancelada-v1": ("Oferta laboral cancelada", "La oferta laboral fue cancelada: {public_reason}."),
}


def normalize_notification_email(value):
    email = str(value or "").strip().lower()
    if (
        len(email) > 254
        or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email)
    ):
        raise NotificationPayloadError("El correo destinatario no es valido")
    return email


def _validated_payload(template_key, payload):
    allowed_fields = TEMPLATE_PAYLOAD_FIELDS.get(template_key)
    if allowed_fields is None:
        raise NotificationPayloadError("La plantilla de notificacion no esta permitida")
    if not isinstance(payload, dict) or set(payload) != allowed_fields:
        raise NotificationPayloadError("El payload no coincide con la plantilla versionada")
    if any(not isinstance(value, (str, int, bool)) for value in payload.values()):
        raise NotificationPayloadError("El payload contiene valores no permitidos")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def enqueue_notification(
    db,
    *,
    event_type,
    recipient_type,
    recipient_email,
    template_key,
    payload,
    deduplication_key,
    organization_id=None,
    case_id=None,
):
    if TEMPLATE_EVENT_TYPES.get(template_key) != event_type:
        raise NotificationPayloadError("La plantilla no corresponde al evento de notificacion")
    if recipient_type not in ALLOWED_RECIPIENT_TYPES:
        raise NotificationPayloadError("El tipo de destinatario no esta permitido")
    normalized_email = normalize_notification_email(recipient_email)
    cipher = HelpDataCipher.from_environment()
    email_hash = cipher.blind_index(normalized_email, purpose="notification-email")
    scoped_key = f"{deduplication_key}:{email_hash}"
    existing = db.query(models.NotificacionCasoAyuda).filter_by(
        deduplication_key=scoped_key
    ).one_or_none()
    if existing is not None:
        return existing

    notification = models.NotificacionCasoAyuda(
        deduplication_key=scoped_key,
        evento_tipo=event_type,
        organizacion_id=organization_id,
        caso_id=case_id,
        destinatario_tipo=recipient_type,
        destinatario_email_hash=email_hash,
        destinatario_email_cifrado=cipher.encrypt(
            normalized_email,
            field="notification.recipient_email",
        ),
        template_key=template_key,
        payload_json=cipher.encrypt(
            _validated_payload(template_key, payload),
            field="notification.payload",
        ),
    )
    try:
        with db.begin_nested():
            db.add(notification)
            db.flush()
    except IntegrityError:
        return db.query(models.NotificacionCasoAyuda).filter_by(
            deduplication_key=scoped_key
        ).one()
    return notification


def enqueue_case_recipient(
    db,
    *,
    case,
    event_type,
    recipient_type,
    recipient_email,
    template_key,
    payload,
    domain_key,
):
    return enqueue_notification(
        db,
        event_type=event_type,
        recipient_type=recipient_type,
        recipient_email=recipient_email,
        template_key=template_key,
        payload=payload,
        deduplication_key=f"{event_type}:{domain_key}",
        organization_id=case.organizacion_id,
        case_id=case.id,
    )


def decrypted_donor_email(aid):
    if not aid.donante_email_hash or not aid.donante_email_cifrado:
        return None
    return HelpDataCipher.from_environment().decrypt(
        aid.donante_email_cifrado,
        field="aid.donor_email",
    )


def configured_global_admin_emails():
    raw_value = os.getenv("HELP_ADMIN_NOTIFICATION_EMAILS", "")
    emails = []
    seen = set()
    for value in raw_value.split(","):
        if not value.strip():
            continue
        email = normalize_notification_email(value)
        if email not in seen:
            seen.add(email)
            emails.append(email)
    return emails


def _default_resend_transport(url, body, headers, timeout):
    request = Request(url, data=body.encode(), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except HTTPError as exc:
        exc.read()
        raise NotificationProviderError(
            "Resend rechazo la solicitud",
            code=f"http_{exc.code}",
        ) from exc
    except (TimeoutError, URLError, OSError) as exc:
        raise NotificationProviderError("No fue posible contactar Resend", code="transport") from exc


class ResendProvider:
    name = "resend"

    def __init__(self, *, transport=None):
        self.transport = transport or _default_resend_transport

    @property
    def configured(self):
        return bool(
            str(os.getenv("RESEND_API_KEY") or "").strip()
            and str(os.getenv("RESEND_FROM_EMAIL") or "").strip()
        )

    def _configuration(self):
        token = str(os.getenv("RESEND_API_KEY") or "").strip()
        from_email = str(os.getenv("RESEND_FROM_EMAIL") or "").strip()
        if not token or not from_email:
            raise ProviderConfigurationError("Resend no esta configurado")
        return token, normalize_notification_email(from_email)

    def send(self, notification, *, delivery_context=None):
        token, from_email = self._configuration()
        cipher = HelpDataCipher.from_environment()
        recipient = cipher.decrypt(
            notification.destinatario_email_cifrado,
            field="notification.recipient_email",
        )
        payload = json.loads(cipher.decrypt(
            notification.payload_json,
            field="notification.payload",
        ))
        _validated_payload(notification.template_key, payload)
        if notification.template_key in {"temporary-access-v1", "labor-offer-access-v1"}:
            frontend_base = str(os.getenv("HELP_FRONTEND_BASE_URL") or "").strip().rstrip("/")
            if not frontend_base:
                raise ProviderConfigurationError("HELP_FRONTEND_BASE_URL no esta configurado")
            subject = "Acceso temporal"
            token = payload.get("challenge_token") or (delivery_context or {}).get("challenge_token")
            route = "revisar-ayuda" if notification.template_key == "temporary-access-v1" else "acceso-oferta-laboral"
            subject = "Acceso a oferta laboral" if notification.template_key == "labor-offer-access-v1" else "Acceso temporal"
            link = f"{frontend_base}/{route}?token={quote(token, safe='')}"
            text_body = (f"Usa este enlace para revisar la oferta laboral: {link}" if route == "acceso-oferta-laboral"
                         else f"Usa este enlace temporal para revisar tus ayudas: {link}")
        else:
            subject, body_template = TEMPLATE_CONTENT[notification.template_key]
            text_body = body_template.format(**payload)
        request_body = json.dumps({
            "from": from_email,
            "to": [recipient],
            "subject": subject,
            "text": text_body,
        }, ensure_ascii=False, separators=(",", ":"))
        status, response_body = self.transport(
            RESEND_URL,
            request_body,
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "ventatalk-rescate/1.0",
            },
            10,
        )
        if not 200 <= int(status) < 300:
            raise NotificationProviderError("Resend rechazo la solicitud", code=f"http_{status}")
        try:
            reference = str(json.loads(response_body)["id"]).strip()
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise NotificationProviderError("Respuesta invalida de Resend", code="invalid_response") from exc
        if not reference:
            raise NotificationProviderError("Respuesta invalida de Resend", code="invalid_response")
        return reference[:200]


def _claim_notifications(db, *, limit, now):
    stale_before = now - timedelta(seconds=PROCESSING_LEASE_SECONDS)
    eligible = or_(
        models.NotificacionCasoAyuda.estado == "pendiente",
        (
            (models.NotificacionCasoAyuda.estado == "fallida")
            & (models.NotificacionCasoAyuda.intentos < MAX_NOTIFICATION_ATTEMPTS)
            & (models.NotificacionCasoAyuda.proximo_intento_at <= now)
        ),
        (
            (models.NotificacionCasoAyuda.estado == "procesando")
            & (models.NotificacionCasoAyuda.procesando_desde <= stale_before)
        ),
    )
    query = (
        db.query(models.NotificacionCasoAyuda)
        .filter(eligible)
        .order_by(models.NotificacionCasoAyuda.id)
        .limit(max(1, min(int(limit), 100)))
    )
    if db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    claimed = query.all()
    for notification in claimed:
        notification.estado = "procesando"
        notification.procesando_desde = now
        notification.proximo_intento_at = None
    db.commit()
    return claimed


def claim_notification_batch(db, *, limit=25, now=None):
    return _claim_notifications(
        db,
        limit=limit,
        now=now or datetime.now(timezone.utc),
    )


def process_notification_batch(db, *, provider=None, limit=25, now=None):
    provider = provider or ResendProvider()
    if not provider.configured:
        raise ProviderConfigurationError("El proveedor de notificaciones no esta configurado")
    now = now or datetime.now(timezone.utc)
    claimed = claim_notification_batch(db, limit=limit, now=now)
    result = {"claimed": len(claimed), "sent": 0, "failed": 0}
    for claimed_notification in claimed:
        notification = db.get(models.NotificacionCasoAyuda, claimed_notification.id)
        notification.intentos += 1
        try:
            delivery_context = None
            if notification.template_key == "labor-offer-access-v1":
                payload = json.loads(HelpDataCipher.from_environment().decrypt(
                    notification.payload_json, field="notification.payload"
                ))
                challenge = db.get(models.DesafioAccesoOfertaLaboral, payload["challenge_id"])
                offer = db.get(models.OfertaAyudaDirecta, payload["offer_id"])
                if (challenge is None or offer is None or challenge.revoked_at is not None
                        or now >= challenge.expires_at or offer.estado != "pendiente_respuesta"):
                    raise NotificationProviderError("Destinatario no autorizado", code="recipient_not_authorized")
                raw_token = secrets.token_urlsafe(32)
                challenge.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
                delivery_context = {"challenge_token": raw_token}
            reference = (provider.send(notification, delivery_context=delivery_context)
                         if delivery_context else provider.send(notification))
        except ProviderConfigurationError:
            db.rollback()
            claimed_ids = [item.id for item in claimed]
            db.query(models.NotificacionCasoAyuda).filter(
                models.NotificacionCasoAyuda.id.in_(claimed_ids),
                models.NotificacionCasoAyuda.estado == "procesando",
            ).update({
                "estado": "pendiente",
                "procesando_desde": None,
            }, synchronize_session=False)
            db.commit()
            raise
        except NotificationProviderError as exc:
            terminal_recipient = exc.code in {"recipient_not_found", "recipient_email_missing", "recipient_email_unverified", "recipient_not_authorized"}
            notification.estado = "cancelada" if terminal_recipient else "fallida"
            notification.ultimo_error_codigo = exc.code
            notification.procesando_desde = None
            if not terminal_recipient and notification.intentos < MAX_NOTIFICATION_ATTEMPTS:
                delay = min(60 * (2 ** (notification.intentos - 1)), MAX_NOTIFICATION_BACKOFF_SECONDS)
                notification.proximo_intento_at = now + timedelta(seconds=delay)
            else:
                notification.proximo_intento_at = None
            result["failed"] += 1
        else:
            notification.estado = "enviada"
            notification.proveedor = getattr(provider, "name", "resend")
            notification.proveedor_referencia = reference
            notification.enviada_at = now
            notification.procesando_desde = None
            notification.proximo_intento_at = None
            notification.ultimo_error_codigo = None
            result["sent"] += 1
        db.commit()
    return result


def notification_readiness(db):
    return {
        "provider_configured": ResendProvider().configured,
        "pending_count": db.query(models.NotificacionCasoAyuda).filter(
            models.NotificacionCasoAyuda.estado.in_({"pendiente", "procesando"})
        ).count(),
        "failing_count": db.query(models.NotificacionCasoAyuda).filter_by(estado="fallida").count(),
        "global_admin_recipient_count": len(configured_global_admin_emails()),
        "global_admin_recipients_configured": bool(configured_global_admin_emails()),
        "global_admin_recipient_source": (
            "HELP_ADMIN_NOTIFICATION_EMAILS_interim_until_002_memberships"
        ),
    }
