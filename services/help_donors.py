import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext
from services.help_crypto import HelpDataCipher, HelpDataCryptoError
from services.help_files import decode_private_evidence, save_encrypted_private_evidence
from services.help_idempotency import hash_idempotency_payload


DONOR_TERMS_VERSION = "donor-v1"
DONOR_ACCOUNT_ACTOR_LIMIT = 10
DONOR_ACCOUNT_IP_LIMIT = 30
DONOR_ACCOUNT_LIMIT_WINDOW_MINUTES = 5
DONOR_REPORT_LIMIT_PER_HOUR = 20


class DonorAccessError(ValueError):
    pass


class DonorTermsRequiredError(DonorAccessError):
    pass


class DonorRateLimitError(DonorAccessError):
    pass


class DonorIdempotencyConflictError(DonorAccessError):
    pass


class DonorPayloadError(DonorAccessError):
    pass


def _require_donor_identity(actor: ActorOrgContext):
    if not actor.can_access_public_cases or not actor.uid:
        raise DonorAccessError("La identidad verificada del donante es obligatoria")
    if len(actor.ip_hash) != 64:
        raise DonorAccessError("La huella de origen del donante es obligatoria")


def get_terms_acceptance(db: Session, *, actor: ActorOrgContext):
    _require_donor_identity(actor)
    return db.query(models.AceptacionTerminosDonante).filter_by(
        actor_uid=actor.uid,
        terms_version=DONOR_TERMS_VERSION,
    ).one_or_none()


def accept_terms(db: Session, *, actor: ActorOrgContext, terms_version: str):
    _require_donor_identity(actor)
    if terms_version != DONOR_TERMS_VERSION:
        raise DonorAccessError("La versión de condiciones no está vigente")
    acceptance = get_terms_acceptance(db, actor=actor)
    if acceptance is not None:
        return acceptance
    acceptance = models.AceptacionTerminosDonante(
        actor_uid=actor.uid,
        terms_version=terms_version,
        ip_hash=actor.ip_hash,
    )
    db.add(acceptance)
    db.flush()
    return acceptance


def _latest_accounts(accounts):
    latest = {}
    for account in sorted(accounts, key=lambda item: item.version, reverse=True):
        latest.setdefault(account.account_key, account)
    return list(latest.values())


def disclose_donor_accounts(db: Session, *, actor: ActorOrgContext, public_id: str):
    acceptance = get_terms_acceptance(db, actor=actor)
    if acceptance is None:
        raise DonorTermsRequiredError("Debes aceptar las condiciones vigentes antes de consultar cuentas")

    case = db.query(models.CasoAyudaV2).filter_by(public_id=public_id, estado="publicado").one_or_none()
    if case is None:
        raise DonorAccessError("Caso publicado no encontrado")
    publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id, activa=True).one_or_none()
    if publication is None:
        raise DonorAccessError("Caso publicado no encontrado")
    consent = db.query(models.ConsentimientoCasoAyuda).filter_by(caso_id=case.id, vigente=True).one_or_none()
    if consent is None:
        raise DonorAccessError("El caso no tiene consentimiento vigente")

    window_start = datetime.now(timezone.utc) - timedelta(minutes=DONOR_ACCOUNT_LIMIT_WINDOW_MINUTES)
    actor_count = db.query(models.AccesoCuentaDonante).filter(
        models.AccesoCuentaDonante.actor_uid == actor.uid,
        models.AccesoCuentaDonante.caso_id == case.id,
        models.AccesoCuentaDonante.accessed_at >= window_start,
    ).count()
    ip_count = db.query(models.AccesoCuentaDonante).filter(
        models.AccesoCuentaDonante.ip_hash == actor.ip_hash,
        models.AccesoCuentaDonante.caso_id == case.id,
        models.AccesoCuentaDonante.accessed_at >= window_start,
    ).count()
    if actor_count >= DONOR_ACCOUNT_ACTOR_LIMIT or ip_count >= DONOR_ACCOUNT_IP_LIMIT:
        raise DonorRateLimitError("Demasiadas consultas de cuentas; intenta nuevamente más tarde")

    accounts = _latest_accounts(db.query(models.CuentaCasoAyuda).filter_by(caso_id=case.id).all())
    approved = [
        account for account in accounts
        if account.estado == "aprobada" and account.consentimiento_version == consent.version
    ]
    for account in approved:
        db.add(models.AccesoCuentaDonante(
            actor_uid=actor.uid,
            ip_hash=actor.ip_hash,
            caso_id=case.id,
            cuenta_id=account.id,
            cuenta_version=account.version,
            terms_version=acceptance.terms_version,
        ))
        db.add(models.AuditoriaCasoAyuda(
            event_id=str(uuid4()),
            organizacion_id=case.organizacion_id,
            caso_id=case.id,
            accion="cuenta_consultada_donante",
            actor_id=actor.uid,
            actor_tipo="donante",
            entidad_tipo="cuenta",
            entidad_id=str(account.id),
            metadata_json=json.dumps({"cuenta_version": account.version}, separators=(",", ":")),
        ))
    db.flush()
    return case, approved


def _reported_aid_response(aid, case_public_id, receipt_attached):
    return {
        "id": aid.id,
        "case_public_id": case_public_id,
        "account_version": aid.cuenta_version,
        "amount": f"{aid.monto_reportado:.2f}",
        "currency": aid.moneda_reportada,
        "transfer_date": aid.fecha_transferencia.isoformat(),
        "status": aid.estado,
        "receipt_attached": receipt_attached,
        "created_at": aid.created_at.isoformat(),
    }


def report_monetary_aid(
    db: Session,
    *,
    actor: ActorOrgContext,
    public_id: str,
    idempotency_key: str,
    account_id: int,
    amount,
    currency: str,
    transfer_date,
    reference=None,
    comment=None,
    receipt_file_name=None,
    receipt_content_type=None,
    receipt_base64=None,
):
    acceptance = get_terms_acceptance(db, actor=actor)
    if acceptance is None:
        raise DonorTermsRequiredError("Debes aceptar las condiciones vigentes antes de reportar una ayuda")

    receipt_bytes = None
    receipt_checksum = None
    if receipt_base64 is not None:
        try:
            receipt_bytes = decode_private_evidence(receipt_base64, receipt_content_type)
        except HelpDataCryptoError as exc:
            raise DonorPayloadError(str(exc)) from exc
        receipt_checksum = hashlib.sha256(receipt_bytes).hexdigest()

    canonical_payload = {
        "public_id": public_id,
        "account_id": account_id,
        "amount": f"{amount:.2f}",
        "currency": currency,
        "transfer_date": transfer_date.isoformat(),
        "reference": reference,
        "comment": comment,
        "receipt_file_name": receipt_file_name,
        "receipt_content_type": receipt_content_type,
        "receipt_checksum": receipt_checksum,
    }
    request_hash = hash_idempotency_payload(canonical_payload)
    existing = db.query(models.OperacionIdempotenteAyuda).filter_by(
        actor_id=actor.uid,
        operacion="report_help",
        idempotency_key=idempotency_key,
    ).one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise DonorIdempotencyConflictError("La clave idempotente ya fue usada con otros datos")
        if existing.estado != "completed" or not existing.response_body:
            raise DonorIdempotencyConflictError("La operación idempotente todavía está en proceso")
        return json.loads(existing.response_body), None

    case = db.query(models.CasoAyudaV2).filter_by(public_id=public_id, estado="publicado").with_for_update().one_or_none()
    if case is None:
        raise DonorAccessError("Caso publicado no encontrado")
    publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id, activa=True).one_or_none()
    if publication is None:
        raise DonorAccessError("Caso publicado no encontrado")
    consent = db.query(models.ConsentimientoCasoAyuda).filter_by(caso_id=case.id, vigente=True).with_for_update().one_or_none()
    if consent is None:
        raise DonorAccessError("El caso no tiene consentimiento vigente")

    account = db.query(models.CuentaCasoAyuda).filter_by(id=account_id, caso_id=case.id).with_for_update().one_or_none()
    if account is None:
        raise DonorAccessError("Cuenta aprobada vigente no encontrada")
    if currency != account.moneda:
        raise DonorPayloadError("La moneda reportada debe coincidir con la cuenta seleccionada")
    recent_reports = db.query(models.AyudaMonetaria).filter(
        models.AyudaMonetaria.donante_id == actor.uid,
        models.AyudaMonetaria.created_at >= datetime.now(timezone.utc) - timedelta(hours=1),
    ).count()
    if recent_reports >= DONOR_REPORT_LIMIT_PER_HOUR:
        raise DonorRateLimitError("Demasiadas ayudas registradas; intenta nuevamente más tarde")
    latest_version = db.query(models.CuentaCasoAyuda.version).filter_by(
        caso_id=case.id,
        account_key=account.account_key,
    ).order_by(models.CuentaCasoAyuda.version.desc()).first()
    if (
        latest_version is None
        or account.version != latest_version[0]
        or account.estado != "aprobada"
        or account.consentimiento_version != consent.version
    ):
        raise DonorAccessError("Cuenta aprobada vigente no encontrada")

    operation = models.OperacionIdempotenteAyuda(
        actor_id=actor.uid,
        organizacion_id=case.organizacion_id,
        operacion="report_help",
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    db.add(operation)
    db.flush()

    cipher = HelpDataCipher.from_environment()
    aid = models.AyudaMonetaria(
        caso_id=case.id,
        cuenta_id=account.id,
        cuenta_version=account.version,
        donante_id=actor.uid,
        monto_reportado=amount,
        moneda_reportada=currency,
        fecha_transferencia=transfer_date,
        referencia_cifrada=cipher.encrypt(reference, field="aid.reference") if reference else None,
        comentario_cifrado=cipher.encrypt(comment, field="aid.comment") if comment else None,
        estado="pendiente_confirmacion",
        idempotency_id=operation.id,
    )
    db.add(aid)
    db.flush()

    saved_path = None
    try:
        if receipt_bytes is not None:
            encrypted_receipt = cipher.encrypt_bytes(receipt_bytes, field="aid.receipt")
            storage_path, saved_path = save_encrypted_private_evidence(encrypted_receipt, case.id)
            db.add(models.ComprobanteAyuda(
                ayuda_id=aid.id,
                version=1,
                storage_path=storage_path,
                nombre_original_cifrado=cipher.encrypt(receipt_file_name, field="receipt.original_name"),
                content_type=receipt_content_type,
                size_bytes=len(receipt_bytes),
                checksum_sha256=receipt_checksum,
                estado="vigente",
                cargado_por=actor.uid,
            ))

        db.refresh(aid)
        response = _reported_aid_response(aid, case.public_id, receipt_bytes is not None)
        operation.estado = "completed"
        operation.response_status = 201
        operation.response_body = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
        operation.completed_at = datetime.now(timezone.utc)
        db.add(models.AuditoriaCasoAyuda(
            event_id=str(uuid4()),
            organizacion_id=case.organizacion_id,
            caso_id=case.id,
            accion="ayuda_reportada_donante",
            actor_id=actor.uid,
            actor_tipo="donante",
            entidad_tipo="ayuda",
            entidad_id=str(aid.id),
            metadata_json=json.dumps({
                "cuenta_version": account.version,
                "moneda": currency,
                "comprobante_adjunto": receipt_bytes is not None,
            }, separators=(",", ":")),
        ))
        db.flush()
        return response, saved_path
    except Exception:
        if saved_path is not None:
            Path(saved_path).unlink(missing_ok=True)
        raise


def list_donor_monetary_aids(db: Session, *, actor: ActorOrgContext):
    acceptance = get_terms_acceptance(db, actor=actor)
    if acceptance is None:
        raise DonorTermsRequiredError("Debes aceptar las condiciones vigentes antes de consultar ayudas")
    rows = db.query(
        models.AyudaMonetaria,
        models.CasoAyudaV2.public_id,
        models.PublicacionCasoAyuda.titulo_publico,
    ).join(
        models.CasoAyudaV2,
        models.CasoAyudaV2.id == models.AyudaMonetaria.caso_id,
    ).join(
        models.PublicacionCasoAyuda,
        models.PublicacionCasoAyuda.caso_id == models.CasoAyudaV2.id,
    ).filter(
        models.AyudaMonetaria.donante_id == actor.uid,
    ).order_by(models.AyudaMonetaria.created_at.desc(), models.AyudaMonetaria.id.desc()).all()

    summaries = []
    for aid, public_id, public_title in rows:
        receipt_attached = db.query(models.ComprobanteAyuda.id).filter_by(
            ayuda_id=aid.id,
            estado="vigente",
        ).first() is not None
        summaries.append({
            "public_title": public_title,
            **_reported_aid_response(aid, public_id, receipt_attached),
        })
    return summaries
