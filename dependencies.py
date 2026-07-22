import hashlib
import hmac
import os
import time
from typing import Optional

from fastapi import Depends, Header, HTTPException


def normalize_actor_id(value: Optional[str]) -> Optional[str]:
    actor_id = str(value or "").strip().lower()
    return actor_id or None


def verify_api_key(
    x_api_key: Optional[str] = Header(None),
    x_actor_id: Optional[str] = Header(None),
) -> str:
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="API Key inválida")
    return normalize_actor_id(x_actor_id) or "api_key"


ACTOR_ORG_SIGNATURE_MAX_AGE_SECONDS = 60
SIGNED_ACTOR_ROLES = {
    "donor",
    "coordinador",
    "authority",
    "admin",
    "super_admin",
    "acopio",
    "acopios",
    "albergues_refugios",
}
CASE_ORGANIZATION_ROLES = {"coordinador", "admin", "super_admin"}


class ActorOrgContext:
    """Identidad y organización del actor real, verificadas por firma HMAC.

    La firma la calcula Firebase Functions con el organizacion_id/rol/email
    que ya resolvió desde `authorized_users` — nunca se toma del form/query,
    que el llamador podría manipular libremente.
    """

    def __init__(self, organizacion_id: str, role: str, email: str, uid: str = "", ip_hash: str = ""):
        self.organizacion_id = organizacion_id
        self.role = role
        self.email = email
        self.uid = uid
        self.ip_hash = ip_hash

    @property
    def is_org_scoped(self) -> bool:
        return bool(self.organizacion_id) and self.role != "super_admin"

    @property
    def can_access_public_cases(self) -> bool:
        return self.role in SIGNED_ACTOR_ROLES

    @property
    def can_manage_organization_cases(self) -> bool:
        return self.role in CASE_ORGANIZATION_ROLES


def _actor_org_signature(
    organizacion_id: str,
    role: str,
    email: str,
    uid: str,
    ip_hash: str,
    timestamp: str,
    secret: str,
) -> str:
    message = f"{organizacion_id}|{role}|{email}|{uid}|{ip_hash}|{timestamp}".encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def require_verified_actor_org(
    x_actor_org_id: Optional[str] = Header(None),
    x_actor_role: Optional[str] = Header(None),
    x_actor_email: Optional[str] = Header(None),
    x_actor_uid: Optional[str] = Header(None),
    x_actor_ip_hash: Optional[str] = Header(None),
    x_actor_org_signature: Optional[str] = Header(None),
    x_actor_org_timestamp: Optional[str] = Header(None),
) -> ActorOrgContext:
    secret = os.getenv("ACTOR_SIGNING_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="ACTOR_SIGNING_SECRET no configurado")

    if not x_actor_org_signature or not x_actor_org_timestamp:
        raise HTTPException(status_code=401, detail="Falta la firma de organización del actor")

    try:
        timestamp = int(x_actor_org_timestamp)
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Timestamp de firma inválido")

    if abs(time.time() - timestamp) > ACTOR_ORG_SIGNATURE_MAX_AGE_SECONDS:
        raise HTTPException(status_code=401, detail="La firma de organización expiró")

    organizacion_id = (x_actor_org_id or "").strip()
    role = (x_actor_role or "").strip()
    email = (x_actor_email or "").strip().lower()
    uid = (x_actor_uid or "").strip()
    ip_hash = (x_actor_ip_hash or "").strip().lower()
    expected = _actor_org_signature(
        organizacion_id,
        role,
        email,
        uid,
        ip_hash,
        x_actor_org_timestamp,
        secret,
    )

    if not hmac.compare_digest(expected, x_actor_org_signature):
        raise HTTPException(status_code=401, detail="Firma de organización inválida")

    if not email or "@" not in email:
        raise HTTPException(status_code=401, detail="El actor firmado requiere un correo válido")
    if role not in SIGNED_ACTOR_ROLES:
        raise HTTPException(status_code=401, detail="Rol de actor firmado inválido")
    if role == "donor" and organizacion_id:
        raise HTTPException(status_code=401, detail="Un donante no puede declarar una organización administrativa")
    if role == "coordinador" and not organizacion_id:
        raise HTTPException(status_code=401, detail="Un coordinador requiere una organización")
    if len(uid) > 128:
        raise HTTPException(status_code=401, detail="UID de actor firmado inválido")
    if ip_hash and (len(ip_hash) != 64 or any(character not in "0123456789abcdef" for character in ip_hash)):
        raise HTTPException(status_code=401, detail="Huella IP de actor firmado inválida")

    return ActorOrgContext(
        organizacion_id=organizacion_id,
        role=role,
        email=email,
        uid=uid,
        ip_hash=ip_hash,
    )


def require_verified_case_organization_actor(
    actor: ActorOrgContext = Depends(require_verified_actor_org),
) -> ActorOrgContext:
    if not actor.can_manage_organization_cases:
        raise HTTPException(status_code=403, detail="El actor no puede administrar casos de una organización")
    return actor


def get_actor_org_scope(
    x_api_key: Optional[str] = Header(None),
    x_actor_org_id: Optional[str] = Header(None),
    x_actor_role: Optional[str] = Header(None),
    x_actor_email: Optional[str] = Header(None),
    x_actor_uid: Optional[str] = Header(None),
    x_actor_ip_hash: Optional[str] = Header(None),
    x_actor_org_signature: Optional[str] = Header(None),
    x_actor_org_timestamp: Optional[str] = Header(None),
) -> Optional[ActorOrgContext]:
    """Devuelve el contexto de organización SOLO si la autenticación fue por x-api-key.

    Si la autenticación fue por sesión de cookie (admin del dashboard, sin
    concepto de organización), devuelve None y el llamador no debe restringir
    nada — mantiene el comportamiento actual para ese camino.
    """
    expected_api_key = os.getenv("API_KEY")
    if not (x_api_key and expected_api_key and x_api_key == expected_api_key):
        return None

    return require_verified_actor_org(
        x_actor_org_id=x_actor_org_id,
        x_actor_role=x_actor_role,
        x_actor_email=x_actor_email,
        x_actor_uid=x_actor_uid,
        x_actor_ip_hash=x_actor_ip_hash,
        x_actor_org_signature=x_actor_org_signature,
        x_actor_org_timestamp=x_actor_org_timestamp,
    )
