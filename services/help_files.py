import base64
import binascii
import os
from pathlib import Path
from uuid import uuid4

from services.help_crypto import HelpDataCryptoError


MAX_PRIVATE_FILE_BYTES = 5 * 1024 * 1024
MAGIC_BY_CONTENT_TYPE = {
    "application/pdf": (b"%PDF-",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


def decode_help_document(encoded, content_type):
    if content_type not in MAGIC_BY_CONTENT_TYPE:
        raise HelpDataCryptoError("Tipo de archivo no soportado")
    try:
        payload = base64.b64decode(str(encoded or ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HelpDataCryptoError("Archivo invalido") from exc
    if not payload or len(payload) > MAX_PRIVATE_FILE_BYTES:
        raise HelpDataCryptoError("El archivo supera el limite permitido")
    if not any(payload.startswith(magic) for magic in MAGIC_BY_CONTENT_TYPE[content_type]):
        raise HelpDataCryptoError("El contenido no coincide con el tipo declarado")
    return payload


def decode_private_evidence(encoded, content_type):
    return decode_help_document(encoded, content_type)


def save_encrypted_case_document(encrypted_payload, case_id, classification):
    if classification not in {"publico", "privado"}:
        raise HelpDataCryptoError("Clasificacion de archivo no soportada")
    root = Path(os.getenv("HELP_PRIVATE_UPLOAD_ROOT", "uploads"))
    directory = "public" if classification == "publico" else "private"
    relative = Path(directory) / "casos-ayuda" / str(case_id) / f"{uuid4().hex}.enc"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(encrypted_payload)
    temporary.replace(destination)
    return relative.as_posix(), destination


def save_encrypted_private_evidence(encrypted_payload, case_id):
    return save_encrypted_case_document(encrypted_payload, case_id, "privado")


def read_encrypted_case_document(storage_path):
    root = Path(os.getenv("HELP_PRIVATE_UPLOAD_ROOT", "uploads")).resolve()
    relative = Path(str(storage_path or ""))
    if relative.is_absolute() or ".." in relative.parts:
        raise HelpDataCryptoError("Ruta de archivo invalida")
    source = (root / relative).resolve()
    if source != root and root not in source.parents:
        raise HelpDataCryptoError("Ruta de archivo invalida")
    try:
        return source.read_bytes()
    except OSError as exc:
        raise HelpDataCryptoError("Archivo no disponible") from exc
