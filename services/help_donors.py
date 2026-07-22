import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext


DONOR_TERMS_VERSION = "donor-v1"
DONOR_ACCOUNT_ACTOR_LIMIT = 10
DONOR_ACCOUNT_IP_LIMIT = 30
DONOR_ACCOUNT_LIMIT_WINDOW_MINUTES = 5


class DonorAccessError(ValueError):
    pass


class DonorTermsRequiredError(DonorAccessError):
    pass


class DonorRateLimitError(DonorAccessError):
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
            actor_tipo="donante" if actor.role == "donor" else actor.role,
            entidad_tipo="cuenta",
            entidad_id=str(account.id),
            metadata_json=json.dumps({"cuenta_version": account.version}, separators=(",", ":")),
        ))
    db.flush()
    return case, approved
