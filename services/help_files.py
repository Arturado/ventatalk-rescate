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


def decode_private_evidence(encoded, content_type):
    if content_type not in MAGIC_BY_CONTENT_TYPE:
        raise HelpDataCryptoError("Tipo de archivo privado no soportado")
    try:
        payload = base64.b64decode(str(encoded or ""), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HelpDataCryptoError("Archivo privado invalido") from exc
    if not payload or len(payload) > MAX_PRIVATE_FILE_BYTES:
        raise HelpDataCryptoError("El archivo privado supera el limite permitido")
    if not any(payload.startswith(magic) for magic in MAGIC_BY_CONTENT_TYPE[content_type]):
        raise HelpDataCryptoError("El contenido no coincide con el tipo declarado")
    return payload


def save_encrypted_private_evidence(encrypted_payload, case_id):
    root = Path(os.getenv("HELP_PRIVATE_UPLOAD_ROOT", "uploads"))
    relative = Path("private") / "casos-ayuda" / str(case_id) / f"{uuid4().hex}.enc"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(encrypted_payload)
    temporary.replace(destination)
    return relative.as_posix(), destination
