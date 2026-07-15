import os
from typing import Optional

from fastapi import Header, HTTPException


def normalize_actor_id(value: Optional[str]) -> Optional[str]:
    actor_id = str(value or "").strip().lower()
    return actor_id or None


def verify_api_key(
    x_api_key: str = Header(...),
    x_actor_id: Optional[str] = Header(None),
) -> str:
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="API Key inválida")
    return normalize_actor_id(x_actor_id) or "api_key"
