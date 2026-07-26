import hashlib
import json
import re
from typing import Any, Optional

from fastapi import HTTPException


IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
IDEMPOTENT_OPERATIONS = {"report_help", "confirm_help"}


def normalize_idempotency_key(value: Optional[str]) -> str:
    key = str(value or "").strip()
    if not IDEMPOTENCY_KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key debe tener entre 16 y 128 caracteres ASCII seguros",
        )
    return key


def validate_idempotency_operation(value: str) -> str:
    operation = str(value or "").strip()
    if operation not in IDEMPOTENT_OPERATIONS:
        raise ValueError("Operación idempotente no soportada")
    return operation


def hash_idempotency_payload(payload: Any) -> str:
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
