import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

import models
from services.help_config import temporary_access_limit_config


LOCAL_TEST_MARKER = "DISPOSABLE_LOCAL_TEST"
MEDICAL_DOCUMENT_TYPES = {"informe_medico", "documento_medico", "diagnostico"}


class RetentionTargetError(ValueError):
    pass


class RetentionPathError(ValueError):
    pass


@dataclass(frozen=True)
class RetentionReport:
    mode: str
    counts: dict
    mutation_count: int

    def public_dict(self):
        return {
            "mode": self.mode,
            "actions": [
                "anonymize_closed_case_data",
                "remove_expired_private_files",
                "delete_expired_temporary_sessions",
                "delete_expired_temporary_access_attempts",
                "redact_expired_notifications",
                "preserve_minimum_evidence",
                "skip_legal_holds",
            ],
            "counts": self.counts,
            "mutation_count": self.mutation_count,
        }


def _database_name(engine):
    database = str(engine.url.database or "")
    return Path(database).stem if engine.url.drivername.startswith("sqlite") else database


def _is_local_engine(engine):
    return engine.url.drivername.startswith("sqlite") or engine.url.host in {
        None,
        "localhost",
        "127.0.0.1",
        "::1",
    }


def assert_apply_target(engine: Engine, *, local_test_marker=None):
    if not _is_local_engine(engine):
        raise RetentionTargetError("Apply requires a local database target")
    if _database_name(engine).endswith("_test"):
        return
    if local_test_marker == LOCAL_TEST_MARKER:
        return
    raise RetentionTargetError(
        "Apply requires a *_test database or the exact disposable local-test marker"
    )


def _cutoff(now, years=0):
    try:
        return now.replace(year=now.year - years)
    except ValueError:
        return now.replace(year=now.year - years, day=28)


def _utc(value):
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _safe_file(root, storage_path):
    root = Path(root).resolve()
    relative = Path(str(storage_path or ""))
    if not str(storage_path or "") or relative.is_absolute() or ".." in relative.parts:
        raise RetentionPathError("Invalid retained-file path")
    candidate = root / relative
    resolved = candidate.resolve(strict=False)
    if resolved == root or root not in resolved.parents:
        raise RetentionPathError("Invalid retained-file path")
    if candidate.is_symlink() or (candidate.exists() and not candidate.is_file()):
        raise RetentionPathError("Retained-file path is not a regular file")
    return candidate


def _synthetic_hash(kind, record_id):
    return hashlib.sha256(f"retained:{kind}:{record_id}".encode()).hexdigest()


def _active_holds(db):
    holds = db.query(models.RetencionLegalAyuda).filter_by(activo=True).all()
    return {(hold.entidad_tipo, hold.entidad_id) for hold in holds}


def _is_sensitive_document(document):
    return document.clasificacion == "privado" or document.tipo in MEDICAL_DOCUMENT_TYPES


def run_retention(
    db: Session,
    *,
    upload_root,
    now=None,
    apply=False,
    local_test_marker=None,
):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Retention clock must be timezone-aware")
    if apply:
        assert_apply_target(db.get_bind(), local_test_marker=local_test_marker)

    two_year_cutoff = _cutoff(now, years=2)
    seven_year_cutoff = _cutoff(now, years=7)
    session_cutoff = now - timedelta(days=30)
    attempt_cutoff = now - timedelta(days=temporary_access_limit_config().retention_days)
    notification_cutoff = now - timedelta(days=90)
    holds = _active_holds(db)

    closed_candidates = db.query(models.CasoAyudaV2).filter(
        models.CasoAyudaV2.cerrado_at.is_not(None),
        models.CasoAyudaV2.cerrado_at <= two_year_cutoff,
        models.CasoAyudaV2.retencion_aplicada_at.is_(None),
    ).all()
    held_case_ids = {
        case.id for case in closed_candidates if ("caso", str(case.id)) in holds
    }
    cases = [case for case in closed_candidates if case.id not in held_case_ids]
    case_ids = [case.id for case in cases]

    documents = []
    accounts = []
    aids = []
    receipts = []
    if case_ids:
        documents = [
            item for item in db.query(models.DocumentoCasoAyuda).filter(
                models.DocumentoCasoAyuda.caso_id.in_(case_ids),
                models.DocumentoCasoAyuda.retencion_aplicada_at.is_(None),
            ).all() if _is_sensitive_document(item)
        ]
        accounts = db.query(models.CuentaCasoAyuda).filter(
            models.CuentaCasoAyuda.caso_id.in_(case_ids)
        ).all()
        aids = db.query(models.AyudaMonetaria).filter(
            models.AyudaMonetaria.caso_id.in_(case_ids)
        ).all()
        aid_ids = [aid.id for aid in aids]
        if aid_ids:
            receipts = db.query(models.ComprobanteAyuda).filter(
                models.ComprobanteAyuda.ayuda_id.in_(aid_ids),
                models.ComprobanteAyuda.retencion_aplicada_at.is_(None),
            ).all()

    eligible_beneficiary_ids = {case.beneficiario_id for case in cases}
    beneficiaries = []
    for beneficiary_id in eligible_beneficiary_ids:
        linked = db.query(models.CasoAyudaV2).filter_by(beneficiario_id=beneficiary_id).all()
        if all(
            item.cerrado_at is not None
            and _utc(item.cerrado_at) <= two_year_cutoff
            and ("caso", str(item.id)) not in holds
            for item in linked
        ):
            beneficiary = db.get(models.BeneficiarioAyuda, beneficiary_id)
            if beneficiary and beneficiary.nombres_apellidos_cifrado != "redacted":
                beneficiaries.append(beneficiary)

    held_scope_access_ids = {
        row[0] for row in db.query(models.AlcanceCuentaAccesoTemporal.acceso_id).filter(
            models.AlcanceCuentaAccesoTemporal.caso_id.in_([
                int(entity_id) for entity_type, entity_id in holds
                if entity_type == "caso" and entity_id.isdigit()
            ] or [-1])
        ).all()
    }
    expired_sessions = db.query(models.AccesoTemporalAyuda).filter(
        models.AccesoTemporalAyuda.created_at <= session_cutoff
    ).all()
    sessions = [
        access for access in expired_sessions
        if ("sesion_temporal", str(access.id)) not in holds
        and access.id not in held_scope_access_ids
    ]
    attempts = db.query(models.IntentoSolicitudAccesoTemporal).filter(
        models.IntentoSolicitudAccesoTemporal.created_at < attempt_cutoff
    ).all()
    expired_notifications = db.query(models.NotificacionCasoAyuda).filter(
        models.NotificacionCasoAyuda.created_at <= notification_cutoff,
        models.NotificacionCasoAyuda.retencion_redactada_at.is_(None),
    ).all()
    notifications = [
        item for item in expired_notifications
        if ("notificacion", str(item.id)) not in holds
        and (item.caso_id is None or ("caso", str(item.caso_id)) not in holds)
    ]

    file_records = [*documents, *receipts]
    safe_files = {}
    for record in file_records:
        safe_files.setdefault(record.storage_path, _safe_file(upload_root, record.storage_path))

    old_case_ids = [
        case.id for case in db.query(models.CasoAyudaV2).filter(
            models.CasoAyudaV2.cerrado_at.is_not(None),
            models.CasoAyudaV2.cerrado_at <= seven_year_cutoff,
        ).all()
        if ("caso", str(case.id)) not in holds
    ]
    minimum_evidence = 0
    if old_case_ids:
        minimum_evidence += db.query(models.AyudaMonetaria).filter(
            models.AyudaMonetaria.caso_id.in_(old_case_ids)
        ).count()
        minimum_evidence += db.query(models.ConsentimientoCasoAyuda).filter(
            models.ConsentimientoCasoAyuda.caso_id.in_(old_case_ids)
        ).count()
        minimum_evidence += db.query(models.AuditoriaCasoAyuda).filter(
            models.AuditoriaCasoAyuda.caso_id.in_(old_case_ids)
        ).count()

    counts = {
        "closed_cases": len(cases),
        "beneficiaries": len(beneficiaries),
        "bank_accounts": len(accounts),
        "private_files": len(file_records),
        "temporary_sessions": len(sessions),
        "temporary_access_attempts": len(attempts),
        "notifications": len(notifications),
        "minimum_evidence_preserved": minimum_evidence,
        "legal_hold_skipped": (
            len(held_case_ids)
            + len(expired_sessions) - len(sessions)
            + len(expired_notifications) - len(notifications)
        ),
    }
    mutation_count = sum(counts[key] for key in (
        "closed_cases",
        "beneficiaries",
        "bank_accounts",
        "private_files",
            "temporary_sessions",
            "temporary_access_attempts",
        "notifications",
    ))
    report = RetentionReport("apply" if apply else "dry-run", counts, mutation_count)
    if not apply:
        return report

    quarantine = Path(upload_root).resolve() / ".retention-quarantine" / uuid4().hex
    staged = []
    try:
        for source in safe_files.values():
            if not source.exists():
                continue
            quarantine.mkdir(parents=True, exist_ok=True)
            target = quarantine / f"{len(staged)}.enc"
            source.replace(target)
            staged.append((source, target))

        for beneficiary in beneficiaries:
            beneficiary.nombres_apellidos_cifrado = "redacted"
            beneficiary.cedula_hash = _synthetic_hash("beneficiary", beneficiary.id)
            beneficiary.cedula_cifrada = "redacted"
            beneficiary.telefono_cifrado = None
            beneficiary.correo_cifrado = None
            beneficiary.direccion_cifrada = None
            beneficiary.datos_verificacion_cifrado = None
            beneficiary.representante_nombre_cifrado = None
            beneficiary.representante_relacion = None
        for case in cases:
            case.relato_privado_cifrado = None
            case.retencion_aplicada_at = now
        for account in accounts:
            account.titular_nombre_cifrado = "redacted"
            account.identificador_cifrado = "redacted"
            account.instrucciones_cifrado = None
            account.justificacion_cifrada = None
            account.responsable_nombre_cifrado = "redacted"
            account.responsable_email_hash = _synthetic_hash("account-email", account.id)
            account.responsable_email_cifrado = "redacted"
            account.estado = "inactiva"
        for aid in aids:
            aid.donante_id = f"retained-donor-{aid.id}"
            aid.donante_email_hash = None
            aid.donante_email_cifrado = None
            aid.referencia_cifrada = None
            aid.comentario_cifrado = None
            aid.problema_detalle_cifrado = None
        for document in documents:
            document.nombre_original_cifrado = None
            document.retencion_estado = "archivo_eliminado"
            document.retencion_aplicada_at = now
        for receipt in receipts:
            receipt.nombre_original_cifrado = None
            receipt.retencion_estado = "archivo_eliminado"
            receipt.retencion_aplicada_at = now
        for notification in notifications:
            notification.destinatario_email_hash = _synthetic_hash("notification", notification.id)
            notification.destinatario_email_cifrado = "redacted"
            notification.payload_json = "{}"
            notification.retencion_redactada_at = now
            if notification.estado in {"pendiente", "procesando"}:
                notification.estado = "cancelada"
                notification.procesando_desde = None
                notification.proximo_intento_at = None
        session_ids = [access.id for access in sessions]
        if session_ids:
            db.query(models.AlcanceCuentaAccesoTemporal).filter(
                models.AlcanceCuentaAccesoTemporal.acceso_id.in_(session_ids)
            ).delete(synchronize_session=False)
            db.query(models.AccesoTemporalAyuda).filter(
                models.AccesoTemporalAyuda.id.in_(session_ids)
            ).delete(synchronize_session=False)
        attempt_ids = [attempt.id for attempt in attempts]
        if attempt_ids:
            db.query(models.IntentoSolicitudAccesoTemporal).filter(
                models.IntentoSolicitudAccesoTemporal.id.in_(attempt_ids)
            ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        for source, target in reversed(staged):
            if target.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
        shutil.rmtree(quarantine, ignore_errors=True)
        raise
    shutil.rmtree(quarantine, ignore_errors=True)
    parent = quarantine.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return report
