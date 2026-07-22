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
from services.help_exchange import BcvRateUnavailableError, get_or_create_bcv_conversion
from services.help_idempotency import hash_idempotency_payload


class HelpRateUnavailableError(ValueError):
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


def _aid_summary(aid, confirmation=None, rate_record=None):
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
        "received_amount": confirmation.monto_recibido if confirmation else None,
        "received_currency": confirmation.moneda_recibida if confirmation else None,
        "goal_equivalent_amount": confirmation.monto_meta_equivalente if confirmation else None,
        "applied_rate": rate_record.valor if rate_record else None,
        "rate_source": rate_record.fuente if rate_record else None,
        "rate_date": rate_record.fecha_tasa if rate_record else None,
        "created_at": aid.created_at,
    }


def list_help_aids(db: Session, *, case_id: int, actor: ActorOrgContext):
    case = _locked_case(db, case_id=case_id, actor=actor)
    aids = db.query(models.AyudaMonetaria).filter_by(caso_id=case.id).order_by(
        models.AyudaMonetaria.created_at.desc(),
        models.AyudaMonetaria.id.desc(),
    ).all()
    aid_ids = [aid.id for aid in aids]
    confirmations = (
        db.query(models.ConfirmacionAyuda)
        .filter(models.ConfirmacionAyuda.ayuda_id.in_(aid_ids))
        .all()
        if aid_ids
        else []
    )
    confirmations_by_aid = {confirmation.ayuda_id: confirmation for confirmation in confirmations}
    rate_ids = {confirmation.tasa_id for confirmation in confirmations if confirmation.tasa_id}
    rates_by_id = {
        rate.id: rate
        for rate in db.query(models.TasaCambioAyuda).filter(models.TasaCambioAyuda.id.in_(rate_ids)).all()
    } if rate_ids else {}
    return [
        _aid_summary(
            aid,
            confirmation=confirmations_by_aid.get(aid.id),
            rate_record=rates_by_id.get(confirmations_by_aid[aid.id].tasa_id)
            if aid.id in confirmations_by_aid
            else None,
        )
        for aid in aids
    ]


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


def _confirmation_response(*, aid, confirmation, case, rate_record=None):
    goal_reached = Decimal(case.monto_confirmado) >= Decimal(case.meta_monto)
    return {
        "aid_id": aid.id,
        "status": aid.estado,
        "received_amount": f"{Decimal(confirmation.monto_recibido):.2f}",
        "received_currency": confirmation.moneda_recibida,
        "goal_equivalent_amount": f"{Decimal(confirmation.monto_meta_equivalente):.2f}",
        "goal_amount_confirmed": f"{Decimal(case.monto_confirmado):.2f}",
        "applied_rate": f"{Decimal(rate_record.valor):.10f}" if rate_record else None,
        "rate_source": rate_record.fuente if rate_record else None,
        "rate_date": rate_record.fecha_tasa.isoformat() if rate_record else None,
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
    rate_provider,
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

    try:
        conversion = get_or_create_bcv_conversion(
            db,
            amount=amount,
            source_currency=currency,
            target_currency=case.meta_moneda,
            provider=rate_provider,
            current_date=getattr(rate_provider, "current_date", None),
        )
    except BcvRateUnavailableError as exc:
        if aid.estado != "en_revision":
            previous_status = aid.estado
            aid.estado = "en_revision"
            _add_audit_event(
                db,
                case=case,
                actor=actor,
                action="ayuda_en_revision",
                entity_type="ayuda",
                entity_id=aid.id,
                reason_code="tasa_bcv_no_disponible",
                metadata={"estado_anterior": previous_status, "estado_nuevo": "en_revision"},
            )
            db.flush()
        raise HelpRateUnavailableError(str(exc)) from exc

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
        monto_meta_equivalente=conversion.equivalent_amount,
        tasa_id=conversion.rate_record.id if conversion.rate_record else None,
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
    case.monto_confirmado = Decimal(case.monto_confirmado) + conversion.equivalent_amount
    case.ayudas_confirmadas = int(case.ayudas_confirmadas) + 1
    goal_reached = Decimal(case.monto_confirmado) >= Decimal(case.meta_monto)
    if goal_reached:
        case.estado = "meta_alcanzada"
    db.flush()

    response = _confirmation_response(
        aid=aid,
        confirmation=confirmation,
        case=case,
        rate_record=conversion.rate_record,
    )
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
