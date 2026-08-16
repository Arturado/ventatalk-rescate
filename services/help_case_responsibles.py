from datetime import datetime, timezone

from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext, DelegateAffirmation
from services.help_cases import (
    HelpCaseDomainError,
    _add_audit_event,
    _get_managed_case,
)


class ResponsibleNotFoundError(HelpCaseDomainError):
    pass


class DelegateAffirmationMismatchError(HelpCaseDomainError):
    pass


class DuplicateResponsibleError(HelpCaseDomainError):
    pass


def seed_default_responsible(db: Session, *, case: "models.CasoAyudaV2", actor: ActorOrgContext):
    responsible = models.CasoAyudaResponsable(
        caso_id=case.id,
        organizacion_id=case.organizacion_id,
        usuario_id=actor.email,
        rol="registrador",
        estado="activo",
        asignado_por_id=actor.email,
    )
    db.add(responsible)
    db.flush()
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="responsable_registrador_sembrado",
        entity_type="responsable",
        entity_id=responsible.id,
        metadata={"usuario_id": responsible.usuario_id},
    )
    return responsible


def list_case_responsibles(db: Session, *, case_id: int, actor: ActorOrgContext, include_removed: bool = False):
    case = _get_managed_case(db, case_id, actor)
    query = db.query(models.CasoAyudaResponsable).filter(models.CasoAyudaResponsable.caso_id == case.id)
    if not include_removed:
        query = query.filter(models.CasoAyudaResponsable.estado != "removido")
    return query.order_by(models.CasoAyudaResponsable.asignado_en.desc()).all()


def _require_matching_affirmation(
    *,
    affirmation: DelegateAffirmation,
    operation: str,
    case: "models.CasoAyudaV2",
    target_email: str,
    target_uid: str | None = None,
):
    if affirmation.operation != operation:
        raise DelegateAffirmationMismatchError("La afirmación de delegado no corresponde a esta operación")
    if affirmation.case_id != case.id:
        raise DelegateAffirmationMismatchError("La afirmación de delegado no corresponde a este caso")
    if affirmation.organization_id != case.organizacion_id:
        raise DelegateAffirmationMismatchError("La afirmación de delegado no corresponde a esta organización")
    if affirmation.target_email != target_email:
        raise DelegateAffirmationMismatchError("La afirmación de delegado no corresponde al objetivo indicado")
    if target_uid is not None and affirmation.target_uid != target_uid:
        raise DelegateAffirmationMismatchError("La afirmación de delegado no corresponde al objetivo indicado")


def assign_case_responsible(
    db: Session,
    *,
    case_id: int,
    actor: ActorOrgContext,
    affirmation: DelegateAffirmation,
    target_uid: str,
    target_email: str,
):
    case = _get_managed_case(db, case_id, actor, lock=True)
    normalized_uid = str(target_uid or "").strip()
    normalized_email = str(target_email or "").strip().lower()
    if not normalized_uid:
        raise HelpCaseDomainError("target_uid es obligatorio")
    if not normalized_email or "@" not in normalized_email:
        raise HelpCaseDomainError("target_email es obligatorio")
    _require_matching_affirmation(
        affirmation=affirmation,
        operation="assign_responsible",
        case=case,
        target_email=normalized_email,
        target_uid=normalized_uid,
    )

    existing = db.query(models.CasoAyudaResponsable).filter(
        models.CasoAyudaResponsable.caso_id == case.id,
        models.CasoAyudaResponsable.usuario_id == normalized_email,
        models.CasoAyudaResponsable.estado == "activo",
    ).one_or_none()
    if existing is not None:
        raise DuplicateResponsibleError("Ese usuario ya es responsable activo de este caso")

    responsible = models.CasoAyudaResponsable(
        caso_id=case.id,
        organizacion_id=case.organizacion_id,
        usuario_id=normalized_email,
        rol="delegado",
        estado="activo",
        asignado_por_id=actor.email,
    )
    db.add(responsible)
    db.flush()
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="responsable_asignado",
        entity_type="responsable",
        entity_id=responsible.id,
        metadata={"usuario_id": responsible.usuario_id},
    )
    db.flush()
    return responsible


def revoke_case_responsible(
    db: Session,
    *,
    case_id: int,
    responsible_id: int,
    actor: ActorOrgContext,
    affirmation: DelegateAffirmation,
):
    case = _get_managed_case(db, case_id, actor, lock=True)
    responsible = db.query(models.CasoAyudaResponsable).filter(
        models.CasoAyudaResponsable.id == responsible_id,
        models.CasoAyudaResponsable.caso_id == case.id,
    ).one_or_none()
    if responsible is None:
        raise ResponsibleNotFoundError("Responsable no encontrado")
    if responsible.estado == "removido":
        raise HelpCaseDomainError("Ese responsable ya fue removido")
    _require_matching_affirmation(
        affirmation=affirmation,
        operation="revoke_responsible",
        case=case,
        target_email=responsible.usuario_id,
    )

    responsible.estado = "removido"
    responsible.removido_por_id = actor.email
    responsible.removido_en = datetime.now(timezone.utc)
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="responsable_revocado",
        entity_type="responsable",
        entity_id=responsible.id,
        metadata={"usuario_id": responsible.usuario_id},
    )
    db.flush()
    return responsible


def is_active_responsible(db: Session, *, case_id: int, actor: ActorOrgContext) -> bool:
    return (
        db.query(models.CasoAyudaResponsable)
        .filter(
            models.CasoAyudaResponsable.caso_id == case_id,
            models.CasoAyudaResponsable.usuario_id == actor.email,
            models.CasoAyudaResponsable.estado == "activo",
        )
        .first()
        is not None
    )
