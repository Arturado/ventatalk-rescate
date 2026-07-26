import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, aliased

import models
from dependencies import ActorOrgContext
from services.help_cases import CaseNotFoundError, OrganizationAccessError, _get_managed_case
from services.help_config import temporary_access_limit_config
from services.help_crypto import HelpDataCipher
from services.help_feature_flags import enabled_help_v2_organization_ids
from services.help_notifications import (
    configured_global_admin_emails,
    decrypted_donor_email,
    enqueue_case_recipient,
    enqueue_notification,
)


CHALLENGE_TTL_SECONDS = 15 * 60
SESSION_TTL_SECONDS = 8 * 60 * 60


class TemporaryAccessDeniedError(ValueError):
    pass


class TemporaryAccessRateLimitError(ValueError):
    pass


def _utc(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _token_hash(cipher, token, purpose):
    return cipher.blind_index(token, purpose=purpose)


def _advisory_lock_id(value):
    digest = hashlib.sha256(f"temporary-access-attempt:{value}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _record_request_attempt(db, *, email_hash, ip_hash, now):
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        for lock_id in sorted({_advisory_lock_id(email_hash), _advisory_lock_id(ip_hash)}):
            db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    elif bind.dialect.name == "sqlite" and not db.in_transaction():
        db.execute(text("BEGIN IMMEDIATE"))

    config = temporary_access_limit_config()
    window_start = now - timedelta(seconds=config.window_seconds)
    email_count = db.query(models.IntentoSolicitudAccesoTemporal).filter(
        models.IntentoSolicitudAccesoTemporal.email_hash == email_hash,
        models.IntentoSolicitudAccesoTemporal.created_at >= window_start,
    ).count()
    ip_count = db.query(models.IntentoSolicitudAccesoTemporal).filter(
        models.IntentoSolicitudAccesoTemporal.ip_hash == ip_hash,
        models.IntentoSolicitudAccesoTemporal.created_at >= window_start,
    ).count()
    db.add(models.IntentoSolicitudAccesoTemporal(
        email_hash=email_hash,
        ip_hash=ip_hash,
        created_at=now,
    ))
    db.flush()
    if email_count >= config.email_limit:
        from observability import metrics

        metrics.increment("rate_limits_total", scope="temporary_access_email")
        raise TemporaryAccessRateLimitError
    if ip_count >= config.ip_limit:
        from observability import metrics

        metrics.increment("rate_limits_total", scope="temporary_access_ip")
        raise TemporaryAccessRateLimitError


def request_temporary_access(db: Session, *, email: str, public_id=None, ip_hash: str):
    cipher = HelpDataCipher.from_environment()
    now = datetime.now(timezone.utc)
    attempt_email_hash = cipher.blind_index(email, purpose="temporary-access-attempt-email")
    attempt_ip_hash = cipher.blind_index(ip_hash, purpose="temporary-access-attempt-ip")
    _record_request_attempt(
        db,
        email_hash=attempt_email_hash,
        ip_hash=attempt_ip_hash,
        now=now,
    )
    email_hash = cipher.blind_index(email, purpose="responsible-email")
    latest = aliased(models.CuentaCasoAyuda)
    query = (
        db.query(models.CuentaCasoAyuda, models.CasoAyudaV2)
        .join(models.CasoAyudaV2, models.CasoAyudaV2.id == models.CuentaCasoAyuda.caso_id)
        .filter(
            models.CuentaCasoAyuda.responsable_email_hash == email_hash,
            models.CuentaCasoAyuda.estado == "aprobada",
            models.CasoAyudaV2.organizacion_id.in_(enabled_help_v2_organization_ids()),
            ~db.query(latest.id).filter(
                latest.caso_id == models.CuentaCasoAyuda.caso_id,
                latest.account_key == models.CuentaCasoAyuda.account_key,
                latest.version > models.CuentaCasoAyuda.version,
            ).exists(),
        )
    )
    if public_id:
        query = query.filter(models.CasoAyudaV2.public_id == public_id)
    matches = query.order_by(models.CuentaCasoAyuda.id).all()
    if not matches:
        return None

    accesses = []
    organization_ids = sorted({case.organizacion_id for _, case in matches})
    for organization_id in organization_ids:
        organization_matches = [
            (account, case)
            for account, case in matches
            if case.organizacion_id == organization_id
        ]
        challenge_token = secrets.token_urlsafe(32)
        first_account, first_case = organization_matches[0]
        access = models.AccesoTemporalAyuda(
            destinatario_email_hash=email_hash,
            destinatario_email_cifrado=first_account.responsable_email_cifrado,
            challenge_hash=_token_hash(cipher, challenge_token, "temporary-access-challenge"),
            challenge_expires_at=now + timedelta(seconds=CHALLENGE_TTL_SECONDS),
            challenge_ttl_seconds=CHALLENGE_TTL_SECONDS,
            request_ip_hash=ip_hash,
            session_ttl_seconds=SESSION_TTL_SECONDS,
            solicitado_por="firebase_functions",
            created_at=now,
        )
        db.add(access)
        db.flush()
        accesses.append(access)
        for account, case in organization_matches:
            db.add(models.AlcanceCuentaAccesoTemporal(
                acceso_id=access.id,
                caso_id=case.id,
                cuenta_id=account.id,
                cuenta_version=account.version,
            ))

        enqueue_notification(
            db,
            event_type="acceso_temporal",
            recipient_type="responsable",
            recipient_email=email,
            template_key="temporary-access-v1",
            payload={"challenge_token": challenge_token},
            deduplication_key=f"temporary-access:{access.id}",
            organization_id=organization_id,
            case_id=first_case.id if len({case.id for _, case in organization_matches}) == 1 else None,
        )
    db.flush()
    return accesses


def redeem_temporary_challenge(db: Session, *, challenge_token: str):
    cipher = HelpDataCipher.from_environment()
    challenge_hash = _token_hash(cipher, challenge_token, "temporary-access-challenge")
    access = (
        db.query(models.AccesoTemporalAyuda)
        .filter(models.AccesoTemporalAyuda.challenge_hash == challenge_hash)
        .with_for_update()
        .one_or_none()
    )
    now = datetime.now(timezone.utc)
    if (
        access is None
        or access.estado != "pendiente"
        or access.challenge_consumed_at is not None
        or _utc(access.challenge_expires_at) <= now
        or access.revocado_at is not None
    ):
        raise TemporaryAccessDeniedError("El acceso temporal no es valido")

    session_token = secrets.token_urlsafe(32)
    access.challenge_consumed_at = now
    access.session_hash = _token_hash(cipher, session_token, "temporary-access-session")
    access.session_expires_at = now + timedelta(seconds=SESSION_TTL_SECONDS)
    access.session_ttl_seconds = SESSION_TTL_SECONDS
    access.estado = "activo"
    db.flush()
    return access, session_token


def authenticate_temporary_session(db: Session, *, session_token: str, lock=False):
    cipher = HelpDataCipher.from_environment()
    session_hash = _token_hash(cipher, session_token, "temporary-access-session")
    query = db.query(models.AccesoTemporalAyuda).filter(
        models.AccesoTemporalAyuda.session_hash == session_hash
    )
    if lock:
        query = query.with_for_update()
    access = query.one_or_none()
    now = datetime.now(timezone.utc)
    if (
        access is None
        or access.estado != "activo"
        or access.revocado_at is not None
        or access.session_expires_at is None
        or _utc(access.session_expires_at) <= now
    ):
        raise TemporaryAccessDeniedError("La sesion temporal no es valida")

    scopes = db.query(models.AlcanceCuentaAccesoTemporal).filter_by(acceso_id=access.id).all()
    if not scopes:
        raise TemporaryAccessDeniedError("La sesion temporal no tiene alcance")
    for scope in scopes:
        account = db.query(models.CuentaCasoAyuda).filter_by(
            id=scope.cuenta_id,
            caso_id=scope.caso_id,
            version=scope.cuenta_version,
            estado="aprobada",
        ).one_or_none()
        if account is None:
            raise TemporaryAccessDeniedError("La version de cuenta ya no esta vigente")
        newer = db.query(models.CuentaCasoAyuda.id).filter(
            models.CuentaCasoAyuda.caso_id == account.caso_id,
            models.CuentaCasoAyuda.account_key == account.account_key,
            models.CuentaCasoAyuda.version > account.version,
        ).first()
        if newer:
            raise TemporaryAccessDeniedError("La version de cuenta ya no esta vigente")
    return access, scopes


def temporary_access_context(db: Session, *, access, scopes):
    cipher = HelpDataCipher.from_environment()
    cases = []
    for case_id in sorted({scope.caso_id for scope in scopes}):
        case = db.get(models.CasoAyudaV2, case_id)
        publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case_id).one()
        accounts = []
        for scope in sorted(
            (item for item in scopes if item.caso_id == case_id),
            key=lambda item: item.cuenta_id,
        ):
            account = db.query(models.CuentaCasoAyuda).filter_by(
                id=scope.cuenta_id,
                version=scope.cuenta_version,
            ).one()
            aids = db.query(models.AyudaMonetaria).filter_by(
                caso_id=case_id,
                cuenta_id=account.id,
                cuenta_version=account.version,
            ).order_by(models.AyudaMonetaria.id).all()
            aid_ids = [aid.id for aid in aids]
            receipts_by_aid = {
                receipt.ayuda_id: receipt.id
                for receipt in db.query(models.ComprobanteAyuda).filter(
                    models.ComprobanteAyuda.ayuda_id.in_(aid_ids),
                    models.ComprobanteAyuda.estado == "vigente",
                ).order_by(models.ComprobanteAyuda.version.asc()).all()
            } if aid_ids else {}
            aid_summaries = []
            for aid in aids:
                summary = {
                    "aid_id": aid.id,
                    "reported_amount": aid.monto_reportado,
                    "reported_currency": aid.moneda_reportada,
                    "transfer_date": aid.fecha_transferencia,
                    "status": aid.estado,
                    "problem_type": aid.problema_tipo,
                }
                if aid.id in receipts_by_aid:
                    summary["receipt_id"] = receipts_by_aid[aid.id]
                aid_summaries.append(summary)
            accounts.append({
                "account_id": account.id,
                "account_version": account.version,
                "medium": account.medio,
                "currency": account.moneda,
                "identifier": cipher.decrypt(account.identificador_cifrado, field="account.identifier"),
                "instructions": cipher.decrypt(account.instrucciones_cifrado, field="account.instructions")
                if account.instrucciones_cifrado else None,
                "aids": aid_summaries,
            })
        cases.append({
            "case_id": case.id,
            "public_id": case.public_id,
            "public_name": publication.nombre_publico,
            "public_title": publication.titulo_publico,
            "status": case.estado,
            "goal_amount": case.meta_monto,
            "goal_currency": case.meta_moneda,
            "confirmed_amount": case.monto_confirmado,
            "confirmed_help_count": case.ayudas_confirmadas,
            "accounts": accounts,
        })
    return {"cases": cases}


def report_temporary_aid_problem(
    db: Session, *, access, scopes, aid_id: int, problem_type: str, detail: str
):
    scoped_versions = {
        (scope.caso_id, scope.cuenta_id, scope.cuenta_version) for scope in scopes
    }
    aid = db.query(models.AyudaMonetaria).filter_by(id=aid_id).with_for_update().one_or_none()
    if aid is None or (aid.caso_id, aid.cuenta_id, aid.cuenta_version) not in scoped_versions:
        raise CaseNotFoundError("Ayuda no encontrada")
    if aid.estado != "pendiente_confirmacion":
        raise TemporaryAccessDeniedError("Solo una ayuda pendiente puede reportar un problema")
    case = db.get(models.CasoAyudaV2, aid.caso_id)
    aid.estado = "problema_reportado"
    aid.problema_tipo = problem_type
    aid.problema_detalle_cifrado = HelpDataCipher.from_environment().encrypt(
        json.dumps({"detail": detail}, ensure_ascii=False, separators=(",", ":")),
        field="aid.problem_detail",
    )
    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()),
        organizacion_id=case.organizacion_id,
        caso_id=case.id,
        accion="problema_reportado",
        actor_id=f"temporary-access:{access.id}",
        actor_tipo="responsable_temporal",
        entidad_tipo="ayuda",
        entidad_id=str(aid.id),
        motivo_codigo=problem_type,
        metadata_json='{"estado_anterior":"pendiente_confirmacion","estado_nuevo":"problema_reportado"}',
    ))
    recipients = [("coordinador", case.creado_por)]
    donor_email = decrypted_donor_email(aid)
    if donor_email:
        recipients.insert(0, ("donante", donor_email))
    recipients.extend(("admin", email) for email in configured_global_admin_emails())
    for recipient_type, recipient_email in recipients:
        enqueue_case_recipient(
            db,
            case=case,
            event_type="problema_reportado",
            recipient_type=recipient_type,
            recipient_email=recipient_email,
            template_key="aid-problem-v1",
            payload={"case_public_id": case.public_id},
            domain_key=f"aid:{aid.id}",
        )
    db.flush()
    return {
        "aid_id": aid.id,
        "reported_amount": aid.monto_reportado,
        "reported_currency": aid.moneda_reportada,
        "transfer_date": aid.fecha_transferencia,
        "status": aid.estado,
        "problem_type": aid.problema_tipo,
    }


def list_case_temporary_accesses(db: Session, *, case_id: int, actor: ActorOrgContext):
    _get_managed_case(db, case_id, actor)
    accesses = (
        db.query(models.AccesoTemporalAyuda)
        .join(models.AlcanceCuentaAccesoTemporal)
        .filter(models.AlcanceCuentaAccesoTemporal.caso_id == case_id)
        .distinct()
        .order_by(models.AccesoTemporalAyuda.id.desc())
        .all()
    )
    return [{
        "access_id": access.id,
        "status": access.estado,
        "account_ids": sorted({
            scope.cuenta_id
            for scope in db.query(models.AlcanceCuentaAccesoTemporal).filter_by(
                acceso_id=access.id, caso_id=case_id
            ).all()
        }),
        "created_at": access.created_at,
        "expires_at": access.session_expires_at or access.challenge_expires_at,
        "revoked_at": access.revocado_at,
    } for access in accesses]


def revoke_case_temporary_access(
    db: Session, *, case_id: int, access_id: int, actor: ActorOrgContext, reason: str
):
    case = _get_managed_case(db, case_id, actor, lock=True)
    access = (
        db.query(models.AccesoTemporalAyuda)
        .join(models.AlcanceCuentaAccesoTemporal)
        .filter(
            models.AccesoTemporalAyuda.id == access_id,
            models.AlcanceCuentaAccesoTemporal.caso_id == case_id,
        )
        .with_for_update()
        .one_or_none()
    )
    if access is None:
        raise CaseNotFoundError("Acceso temporal no encontrado")
    if access.estado != "revocado":
        access.estado = "revocado"
        access.revocado_at = datetime.now(timezone.utc)
        access.revocado_por = actor.email
        access.motivo_revocacion = reason
        db.add(models.AuditoriaCasoAyuda(
            event_id=str(uuid4()),
            organizacion_id=case.organizacion_id,
            caso_id=case.id,
            accion="acceso_temporal_revocado",
            actor_id=actor.email,
            actor_tipo=actor.role,
            entidad_tipo="acceso_temporal",
            entidad_id=str(access.id),
            motivo_codigo=reason,
            metadata_json="{}",
        ))
    db.flush()
    return {"access_id": access.id, "status": access.estado}
