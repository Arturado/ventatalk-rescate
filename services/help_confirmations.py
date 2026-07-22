import json
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext
from services.help_cases import (
    CaseNotFoundError,
    CaseStateError,
    _add_audit_event,
    _require_organization_access,
)
from services.help_crypto import HelpDataCipher
from services.help_idempotency import hash_idempotency_payload


class HelpConfirmationCurrencyError(ValueError):
    pass


class HelpConfirmationIdempotencyError(ValueError):
    pass


def _locked_case(db, *, case_id, actor):
    query = db.query(models.CasoAyudaV2).filter(models.CasoAyudaV2.id == case_id)
    if actor.is_org_scoped:
        query = query.filter(models.CasoAyudaV2.organizacion_id == actor.organizacion_id)
    case = query.with_for_update().one_or_none()
    if case is None:
        raise CaseNotFoundError("Caso no encontrado")
    _require_organization_access(actor, case.organizacion_id)
    return case


def _locked_aid_and_account(db, *, case, aid_id):
    aid = db.query(models.AyudaMonetaria).filter_by(
        id=aid_id,
        caso_id=case.id,
    ).with_for_update().one_or_none()
    if aid is None:
        raise CaseNotFoundError("Ayuda no encontrada")
    account = db.query(models.CuentaCasoAyuda).filter_by(
        id=aid.cuenta_id,
        caso_id=case.id,
        version=aid.cuenta_version,
    ).with_for_update().one_or_none()
    if account is None:
        raise CaseNotFoundError("Cuenta exacta de la ayuda no encontrada")
    return aid, account


def _problem_record(aid):
    if not aid.problema_detalle_cifrado:
        return {}
    decrypted = HelpDataCipher.from_environment().decrypt(
        aid.problema_detalle_cifrado,
        field="aid.problem_detail",
    )
    return json.loads(decrypted)


def _aid_summary(aid):
    problem = _problem_record(aid)
    return {
        "aid_id": aid.id,
        "reported_amount": aid.monto_reportado,
        "reported_currency": aid.moneda_reportada,
        "transfer_date": aid.fecha_transferencia,
        "status": aid.estado,
        "account_id": aid.cuenta_id,
        "account_version": aid.cuenta_version,
        "problem_type": aid.problema_tipo,
        "problem_detail": problem.get("detail"),
        "resolution_reason": problem.get("resolution_reason"),
        "created_at": aid.created_at,
    }


def list_help_aids(db: Session, *, case_id: int, actor: ActorOrgContext):
    case = _locked_case(db, case_id=case_id, actor=actor)
    aids = db.query(models.AyudaMonetaria).filter_by(caso_id=case.id).order_by(
        models.AyudaMonetaria.created_at.desc(),
        models.AyudaMonetaria.id.desc(),
    ).all()
    return [_aid_summary(aid) for aid in aids]


def mark_help_aid_in_review(db: Session, *, case_id: int, aid_id: int, actor: ActorOrgContext):
    case = _locked_case(db, case_id=case_id, actor=actor)
    aid, _account = _locked_aid_and_account(db, case=case, aid_id=aid_id)
    if aid.estado not in {"pendiente_confirmacion", "problema_reportado"}:
        raise CaseStateError("Solo una ayuda pendiente o con problema puede pasar a revision")
    previous_status = aid.estado
    aid.estado = "en_revision"
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="ayuda_en_revision",
        entity_type="ayuda",
        entity_id=aid.id,
        metadata={"estado_anterior": previous_status, "estado_nuevo": "en_revision"},
    )
    db.flush()
    return _aid_summary(aid)


def report_help_aid_problem(
    db: Session,
    *,
    case_id: int,
    aid_id: int,
    actor: ActorOrgContext,
    problem_type: str,
    detail: str,
):
    case = _locked_case(db, case_id=case_id, actor=actor)
    aid, _account = _locked_aid_and_account(db, case=case, aid_id=aid_id)
    if aid.estado != "pendiente_confirmacion":
        raise CaseStateError("Solo una ayuda pendiente puede reportar un problema")
    aid.estado = "problema_reportado"
    aid.problema_tipo = problem_type
    aid.problema_detalle_cifrado = HelpDataCipher.from_environment().encrypt(
        json.dumps({"detail": detail}, ensure_ascii=False, separators=(",", ":")),
        field="aid.problem_detail",
    )
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="problema_reportado",
        entity_type="ayuda",
        entity_id=aid.id,
        reason_code=problem_type,
        metadata={"estado_anterior": "pendiente_confirmacion", "estado_nuevo": "problema_reportado"},
    )
    db.flush()
    return _aid_summary(aid)


def reject_help_aid(
    db: Session,
    *,
    case_id: int,
    aid_id: int,
    actor: ActorOrgContext,
    reason: str,
):
    case = _locked_case(db, case_id=case_id, actor=actor)
    aid, _account = _locked_aid_and_account(db, case=case, aid_id=aid_id)
    if aid.estado != "en_revision":
        raise CaseStateError("Solo una ayuda en revision puede rechazarse")
    problem = _problem_record(aid)
    problem["resolution_reason"] = reason
    aid.problema_detalle_cifrado = HelpDataCipher.from_environment().encrypt(
        json.dumps(problem, ensure_ascii=False, separators=(",", ":")),
        field="aid.problem_detail",
    )
    aid.estado = "rechazada"
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="revision_resuelta",
        entity_type="ayuda",
        entity_id=aid.id,
        reason_code="rechazada",
        metadata={"estado_anterior": "en_revision", "estado_nuevo": "rechazada"},
    )
    db.flush()
    return _aid_summary(aid)


def _confirmation_response(*, aid, confirmation, case):
    goal_reached = Decimal(case.monto_confirmado) >= Decimal(case.meta_monto)
    return {
        "aid_id": aid.id,
        "status": aid.estado,
        "received_amount": f"{Decimal(confirmation.monto_recibido):.2f}",
        "received_currency": confirmation.moneda_recibida,
        "goal_amount_confirmed": f"{Decimal(case.monto_confirmado):.2f}",
        "confirmed_help_count": case.ayudas_confirmadas,
        "case_status": case.estado,
        "goal_reached": goal_reached,
    }


def confirm_help_aid(
    db: Session,
    *,
    case_id: int,
    aid_id: int,
    actor: ActorOrgContext,
    idempotency_key: str,
    received_amount,
    received_currency: str,
    effective_date,
    comment=None,
):
    amount = Decimal(received_amount)
    currency = str(received_currency or "").strip().upper()
    canonical_payload = {
        "aid_id": aid_id,
        "case_id": case_id,
        "comment": comment,
        "effective_date": effective_date.isoformat(),
        "received_amount": f"{amount:.2f}",
        "received_currency": currency,
    }
    request_hash = hash_idempotency_payload(canonical_payload)
    actor_id = actor.uid or f"{actor.email}|{actor.role}"

    case = _locked_case(db, case_id=case_id, actor=actor)
    existing = db.query(models.OperacionIdempotenteAyuda).filter_by(
        actor_id=actor_id,
        operacion="confirm_help",
        idempotency_key=idempotency_key,
    ).one_or_none()
    if existing is not None:
        if existing.request_hash != request_hash:
            raise HelpConfirmationIdempotencyError("La clave idempotente ya fue usada con otros datos")
        if existing.estado != "completed" or not existing.response_body:
            raise HelpConfirmationIdempotencyError("La operacion idempotente todavia esta en proceso")
        return json.loads(existing.response_body), True

    aid, _account = _locked_aid_and_account(db, case=case, aid_id=aid_id)
    if aid.estado not in {"pendiente_confirmacion", "en_revision"}:
        raise CaseStateError("La ayuda no esta pendiente ni en revision")

    if aid.moneda_reportada != case.meta_moneda or currency != case.meta_moneda:
        raise HelpConfirmationCurrencyError(
            "La moneda reportada y recibida debe coincidir con la moneda de la meta"
        )

    operation = models.OperacionIdempotenteAyuda(
        actor_id=actor_id,
        organizacion_id=case.organizacion_id,
        operacion="confirm_help",
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    db.add(operation)
    db.flush()

    confirmation = models.ConfirmacionAyuda(
        ayuda_id=aid.id,
        idempotency_id=operation.id,
        monto_recibido=amount,
        moneda_recibida=currency,
        monto_meta_equivalente=amount,
        confirmado_por=actor_id,
        fecha_transferencia_confirmada=effective_date,
        comentario_cifrado=(
            HelpDataCipher.from_environment().encrypt(comment, field="confirmation.comment")
            if comment
            else None
        ),
    )
    db.add(confirmation)
    aid.estado = "confirmada"
    case.monto_confirmado = Decimal(case.monto_confirmado) + amount
    case.ayudas_confirmadas = int(case.ayudas_confirmadas) + 1
    goal_reached = Decimal(case.monto_confirmado) >= Decimal(case.meta_monto)
    if goal_reached:
        case.estado = "meta_alcanzada"
    db.flush()

    response = _confirmation_response(aid=aid, confirmation=confirmation, case=case)
    operation.estado = "completed"
    operation.response_status = 200
    operation.response_body = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    operation.completed_at = datetime.now(timezone.utc)
    _add_audit_event(
        db,
        case=case,
        actor=actor,
        action="ayuda_confirmada",
        entity_type="ayuda",
        entity_id=aid.id,
        metadata={"estado_nuevo": "confirmada", "meta_alcanzada": goal_reached},
    )
    db.flush()
    return response, False
