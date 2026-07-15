from datetime import datetime, timezone

import models

CENTRO_TIPOS_VALIDOS = {"albergue", "centro_acopio", "otro"}
CENTRO_TIPOS_ALIAS = {"refugio": "albergue"}
CENTRO_ESTADOS_VALIDOS = {"activo", "inactivo", "pendiente_aprobacion", "rechazado"}
RESPONSABLE_ROLES_VALIDOS = {"coordinador", "colaborador"}
RESPONSABLE_ESTADOS_VALIDOS = {"invitado", "activo", "removido"}
DEFAULT_ORGANIZACION_ID = "venezuela-rescate"


def normalize_centro_tipo(value: str | None, default: str = "albergue") -> str:
    normalized = (value or default).strip().lower()
    normalized = CENTRO_TIPOS_ALIAS.get(normalized, normalized)
    if normalized not in CENTRO_TIPOS_VALIDOS:
        raise ValueError("tipo de centro inválido")
    return normalized


def normalize_centro_estado(value: str | None, default: str = "activo") -> str:
    normalized = (value or default).strip().lower()
    if normalized not in CENTRO_ESTADOS_VALIDOS:
        raise ValueError("estado de centro inválido")
    return normalized


def normalize_organizacion_id(value: str | None, default: str = DEFAULT_ORGANIZACION_ID) -> str:
    normalized = (value or default).strip()
    return normalized or default


def normalize_responsable_rol(value: str | None, default: str = "coordinador") -> str:
    normalized = (value or default).strip().lower()
    if normalized not in RESPONSABLE_ROLES_VALIDOS:
        raise ValueError("rol de responsable inválido")
    return normalized


def normalize_responsable_estado(value: str | None, default: str = "activo") -> str:
    normalized = (value or default).strip().lower()
    if normalized not in RESPONSABLE_ESTADOS_VALIDOS:
        raise ValueError("estado de responsable inválido")
    return normalized


def sync_centro_activation_state(centro):
    if getattr(centro, "estado", None) == "activo":
        centro.activo = True
    else:
        centro.activo = False
    return centro


def log_centro_history(
    db,
    centro_id: int,
    tipo_cambio: str,
    valor_anterior: str | None = None,
    valor_nuevo: str | None = None,
    cambiado_por: str | None = None,
    descripcion: str | None = None,
):
    db.add(
        models.CentroHistorial(
            centro_id=centro_id,
            tipo_cambio=tipo_cambio,
            valor_anterior=valor_anterior,
            valor_nuevo=valor_nuevo,
            cambiado_por=cambiado_por,
            descripcion=descripcion,
        )
    )


def utcnow():
    return datetime.now(timezone.utc)
