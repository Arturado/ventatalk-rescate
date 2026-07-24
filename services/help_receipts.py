import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext
from services.help_crypto import HelpDataCipher, HelpDataCryptoError
from services.help_files import read_encrypted_case_document


GLOBAL_RECEIPT_REASON_CODES = {
    "revision_incidencia",
    "auditoria",
    "soporte_autorizado",
}
RECEIPT_EXTENSIONS = {
    "application/pdf": "pdf",
    "image/jpeg": "jpg",
    "image/png": "png",
}


class ReceiptNotFoundError(ValueError):
    pass


class ReceiptReasonRequiredError(ValueError):
    pass


class ReceiptReasonForbiddenError(ValueError):
    pass


@dataclass(frozen=True)
class ReceiptDownload:
    content: bytes
    content_type: str
    filename: str

    @property
    def headers(self):
        return {
            "Content-Disposition": f'attachment; filename="{self.filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        }


def _receipt_record(db, *, receipt_id, aid_id, case_id=None):
    query = (
        db.query(models.ComprobanteAyuda, models.AyudaMonetaria, models.CasoAyudaV2)
        .join(models.AyudaMonetaria, models.AyudaMonetaria.id == models.ComprobanteAyuda.ayuda_id)
        .join(models.CasoAyudaV2, models.CasoAyudaV2.id == models.AyudaMonetaria.caso_id)
        .filter(
            models.ComprobanteAyuda.id == receipt_id,
            models.ComprobanteAyuda.ayuda_id == aid_id,
        )
    )
    if case_id is not None:
        query = query.filter(models.CasoAyudaV2.id == case_id)
    return query.one_or_none()


def _authorize_receipt(record, *, actor, access, reason_code, scopes):
    receipt, aid, case = record
    if access == "temporary":
        scoped_versions = {
            (scope.caso_id, scope.cuenta_id, scope.cuenta_version) for scope in scopes
        }
        if (aid.caso_id, aid.cuenta_id, aid.cuenta_version) not in scoped_versions:
            raise ReceiptNotFoundError("Comprobante no encontrado")
        return None

    if access == "donor":
        if actor.role != "donor" or not actor.uid or aid.donante_id != actor.uid:
            raise ReceiptNotFoundError("Comprobante no encontrado")
        return None

    if access != "organization" or actor.role not in {"coordinador", "admin", "super_admin"}:
        raise ReceiptNotFoundError("Comprobante no encontrado")

    is_global = actor.role == "super_admin" or (actor.role == "admin" and not actor.organizacion_id)
    if is_global:
        normalized_reason = str(reason_code or "").strip().lower()
        if not normalized_reason:
            raise ReceiptReasonRequiredError("La descarga global requiere un motivo")
        if normalized_reason not in GLOBAL_RECEIPT_REASON_CODES:
            raise ReceiptReasonForbiddenError("El motivo de descarga global no esta permitido")
        return normalized_reason

    if not actor.organizacion_id or actor.organizacion_id != case.organizacion_id:
        raise ReceiptNotFoundError("Comprobante no encontrado")
    return None


def download_help_receipt(
    db: Session,
    *,
    receipt_id: int,
    aid_id: int,
    access: str,
    actor: ActorOrgContext = None,
    case_id: int = None,
    reason_code: str = None,
    temporary_access=None,
    scopes=(),
):
    if access == "organization":
        is_global = actor.role == "super_admin" or (actor.role == "admin" and not actor.organizacion_id)
        if is_global and not str(reason_code or "").strip():
            raise ReceiptReasonRequiredError("La descarga global requiere un motivo")
        if is_global and str(reason_code).strip().lower() not in GLOBAL_RECEIPT_REASON_CODES:
            raise ReceiptReasonForbiddenError("El motivo de descarga global no esta permitido")

    record = _receipt_record(db, receipt_id=receipt_id, aid_id=aid_id, case_id=case_id)
    if record is None:
        raise ReceiptNotFoundError("Comprobante no encontrado")
    receipt, aid, case = record
    audit_reason = _authorize_receipt(
        record,
        actor=actor,
        access=access,
        reason_code=reason_code,
        scopes=scopes,
    )

    try:
        encrypted = read_encrypted_case_document(receipt.storage_path)
        plaintext = HelpDataCipher.from_environment().decrypt_bytes(encrypted, field="aid.receipt")
    except HelpDataCryptoError as exc:
        raise ReceiptNotFoundError("Comprobante no encontrado") from exc

    if (
        len(plaintext) != receipt.size_bytes
        or hashlib.sha256(plaintext).hexdigest() != receipt.checksum_sha256
    ):
        raise ReceiptNotFoundError("Comprobante no encontrado")
    extension = RECEIPT_EXTENSIONS.get(receipt.content_type)
    if extension is None:
        raise ReceiptNotFoundError("Comprobante no encontrado")

    if access == "temporary":
        actor_type = "responsable_temporal"
        actor_id = f"temporary-access:{temporary_access.id}"
    else:
        actor_type = "donante" if access == "donor" else actor.role
        actor_id = actor.uid if access == "donor" else actor.email
    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()),
        organizacion_id=case.organizacion_id,
        caso_id=case.id,
        accion="comprobante_descargado",
        actor_id=actor_id,
        actor_tipo=actor_type,
        entidad_tipo="comprobante",
        entidad_id=str(receipt.id),
        motivo_codigo=audit_reason,
        metadata_json=json.dumps(
            {"comprobante_id": receipt.id, "version": receipt.version},
            sort_keys=True,
            separators=(",", ":"),
        ),
    ))
    db.flush()
    return ReceiptDownload(
        content=plaintext,
        content_type=receipt.content_type,
        filename=f"comprobante-ayuda-{aid.id}-v{receipt.version}.{extension}",
    )
