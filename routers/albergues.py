import json
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc, desc
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from database import get_db
from dependencies import verify_api_key
from services.images import read_limited, compress_image_async
from services.before_submit import build_before_submit_metadata
from services.shelter_centers import (
    log_centro_history,
    normalize_centro_estado,
    normalize_centro_tipo,
    normalize_organizacion_id,
    sync_centro_activation_state,
)
import models, schemas
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/api/albergues", tags=["albergues"])
limiter = Limiter(key_func=get_remote_address)

VE_TZ = timezone(timedelta(hours=-4))
BASE_URL = os.getenv("BASE_URL", "https://rescate.ventatalk.com")
UPLOAD_DIR_ENTREGAS = "uploads/albergue_entregas"
ESTADOS_ORDEN_VALIDOS = {"preparando", "en_camino", "entregado"}


class OrdenEstadoUpdate(BaseModel):
    estado: str


def _crear_reporte_en_centro(
    *,
    db: Session,
    centro_id: int,
    hombres: int = 0,
    mujeres: int = 0,
    ninos: int = 0,
    lactantes: int = 0,
    necesitan: Optional[str] = None,
    no_necesitan: Optional[str] = None,
    necesitan_extra: Optional[str] = None,
    no_necesitan_extra: Optional[str] = None,
    notas: Optional[str] = None,
    reportado_por: Optional[str] = None,
):
    centro = db.query(models.Centro).filter(
        models.Centro.id == centro_id,
        models.Centro.activo == True
    ).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")

    reporte = models.CentroReporte(
        centro_id=centro_id,
        hombres=hombres,
        mujeres=mujeres,
        ninos=ninos,
        lactantes=lactantes,
        necesitan=necesitan,
        no_necesitan=no_necesitan,
        necesitan_extra=necesitan_extra,
        no_necesitan_extra=no_necesitan_extra,
        notas=notas,
        reportado_por=reportado_por,
    )
    db.add(reporte)
    db.commit()
    db.refresh(reporte)
    return reporte

# ── Centros ────────────────────────────────────────────────

@router.get("/centros", response_model=List[schemas.CentroResponse])
def listar_centros(
    zona: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Público — lista centros de albergue activos"""
    q = db.query(models.Centro).filter(models.Centro.activo == True)
    if zona:
        q = q.filter(models.Centro.zona.ilike(f"%{zona}%"))
    return q.order_by(models.Centro.zona, models.Centro.nombre).all()

@router.get("/centros/{centro_id}", response_model=schemas.CentroResponse)
def obtener_centro(centro_id: int, db: Session = Depends(get_db)):
    centro = db.query(models.Centro).filter(
        models.Centro.id == centro_id
    ).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")
    return centro

# ── Reportes públicos ──────────────────────────────────────

@router.post("/reportes", response_model=schemas.CentroReporteResponse, status_code=201)
@limiter.limit("30/hour")
async def crear_reporte(
    request: Request,
    centro_id: int = Form(...),
    hombres: int = Form(0),
    mujeres: int = Form(0),
    ninos: int = Form(0),
    lactantes: int = Form(0),
    necesitan: str = Form(None),
    no_necesitan: str = Form(None),
    necesitan_extra: str = Form(None),
    no_necesitan_extra: str = Form(None),
    notas: str = Form(None),
    reportado_por: str = Form(None),
    db: Session = Depends(get_db)
):
    """Público — personal de campo crea reporte sin auth"""
    return _crear_reporte_en_centro(
        db=db,
        centro_id=centro_id,
        hombres=hombres,
        mujeres=mujeres,
        ninos=ninos,
        lactantes=lactantes,
        necesitan=necesitan,
        no_necesitan=no_necesitan,
        necesitan_extra=necesitan_extra,
        no_necesitan_extra=no_necesitan_extra,
        notas=notas,
        reportado_por=reportado_por,
    )


@router.post("/reportes/secure", response_model=schemas.CentroReporteResponse, status_code=201)
async def crear_reporte_seguro(
    centro_id: int = Form(...),
    hombres: int = Form(0),
    mujeres: int = Form(0),
    ninos: int = Form(0),
    lactantes: int = Form(0),
    necesitan: str = Form(None),
    no_necesitan: str = Form(None),
    necesitan_extra: str = Form(None),
    no_necesitan_extra: str = Form(None),
    notas: str = Form(None),
    reportado_por: str = Form(None),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Protegido — reporte autenticado vía Firebase Functions."""
    return _crear_reporte_en_centro(
        db=db,
        centro_id=centro_id,
        hombres=hombres,
        mujeres=mujeres,
        ninos=ninos,
        lactantes=lactantes,
        necesitan=necesitan,
        no_necesitan=no_necesitan,
        necesitan_extra=necesitan_extra,
        no_necesitan_extra=no_necesitan_extra,
        notas=notas,
        reportado_por=reportado_por,
    )

@router.get("/estado", response_model=List[schemas.CentroConReporteResponse])
def estado_general(db: Session = Depends(get_db)):
    """
    Público — estado actual de todos los centros con su último reporte.
    Para mostrar en venezuelarescate.com y en /albergues.
    """
    centros = db.query(models.Centro).filter(
        models.Centro.activo == True
    ).order_by(models.Centro.zona, models.Centro.nombre).all()

    hoy = datetime.now(VE_TZ).date()
    resultado = []

    for centro in centros:
        ultimo = db.query(models.CentroReporte).filter(
            models.CentroReporte.centro_id == centro.id
        ).order_by(desc(models.CentroReporte.created_at)).first()

        total_hoy = db.query(sqlfunc.count(models.CentroReporte.id)).filter(
            models.CentroReporte.centro_id == centro.id,
            sqlfunc.date(models.CentroReporte.created_at) == hoy
        ).scalar()

        resultado.append(schemas.CentroConReporteResponse(
            centro=centro,
            ultimo_reporte=ultimo,
            total_reportes_hoy=total_hoy or 0
        ))

    return resultado

@router.get("/reportes/{centro_id}/historial",
            response_model=List[schemas.CentroReporteResponse])
def historial_centro(
    centro_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Protegido — historial de reportes de un centro. Para el colega."""
    return db.query(models.CentroReporte).filter(
        models.CentroReporte.centro_id == centro_id
    ).order_by(desc(models.CentroReporte.created_at)).limit(limit).all()

@router.patch("/centros/{centro_id}", response_model=schemas.CentroResponse)
def editar_centro(
    centro_id: int,
    data: schemas.CentroUpdate,
    db: Session = Depends(get_db),
    actor_id: str = Depends(verify_api_key),
):
    centro = db.query(models.Centro).filter(models.Centro.id == centro_id).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")
    updates = data.model_dump(exclude_unset=True)

    if "tipo" in updates:
        try:
            updates["tipo"] = normalize_centro_tipo(updates["tipo"], default=centro.tipo)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if "organizacion_id" in updates:
        updates["organizacion_id"] = normalize_organizacion_id(
            updates["organizacion_id"],
            default=centro.organizacion_id,
        )

    if "estado" in updates:
        try:
            updates["estado"] = normalize_centro_estado(updates["estado"], default=centro.estado)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    if "activo" in updates and "estado" not in updates:
        updates["estado"] = "activo" if updates["activo"] else "inactivo"

    for campo, valor in updates.items():
        anterior = getattr(centro, campo)
        if anterior == valor:
            continue
        setattr(centro, campo, valor)
        log_centro_history(
            db,
            centro_id=centro.id,
            tipo_cambio=campo,
            valor_anterior=str(anterior) if anterior is not None else None,
            valor_nuevo=str(valor) if valor is not None else None,
            cambiado_por=actor_id,
        )

    sync_centro_activation_state(centro)
    db.commit()
    db.refresh(centro)
    return centro

@router.delete("/centros/{centro_id}")
def desactivar_centro(
    centro_id: int,
    db: Session = Depends(get_db),
    actor_id: str = Depends(verify_api_key),
):
    centro = db.query(models.Centro).filter(models.Centro.id == centro_id).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")
    estado_anterior = centro.estado
    centro.activo = False
    centro.estado = "inactivo"
    log_centro_history(
        db,
        centro_id=centro.id,
        tipo_cambio="estado",
        valor_anterior=estado_anterior,
        valor_nuevo=centro.estado,
        cambiado_por=actor_id,
    )
    db.commit()
    return {"ok": True, "id": centro_id}


@router.get("/centros/{centro_id}/responsables", response_model=List[schemas.CentroResponsableResponse])
def listar_responsables_centro(
    centro_id: int,
    incluir_removidos: bool = Query(False),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    centro = db.query(models.Centro).filter(models.Centro.id == centro_id).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")

    q = db.query(models.CentroResponsable).filter(
        models.CentroResponsable.centro_id == centro_id
    )
    if not incluir_removidos:
        q = q.filter(models.CentroResponsable.estado != "removido")
    return q.order_by(desc(models.CentroResponsable.asignado_en)).all()


@router.get("/responsables", response_model=List[schemas.CentroResponsableResponse])
def listar_responsabilidades_usuario(
    usuario_id: str = Query(...),
    incluir_removidos: bool = Query(False),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    usuario_normalizado = (usuario_id or "").strip().lower()
    if not usuario_normalizado:
        raise HTTPException(status_code=400, detail="usuario_id es obligatorio")

    q = db.query(models.CentroResponsable).filter(
        models.CentroResponsable.usuario_id == usuario_normalizado
    )
    if not incluir_removidos:
        q = q.filter(models.CentroResponsable.estado != "removido")
    return q.order_by(desc(models.CentroResponsable.asignado_en)).all()


@router.post("/responsables/activar")
def activar_responsabilidades_usuario(
    usuario_id: str = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    usuario_normalizado = (usuario_id or "").strip().lower()
    if not usuario_normalizado:
        raise HTTPException(status_code=400, detail="usuario_id es obligatorio")

    responsabilidades = db.query(models.CentroResponsable).filter(
        models.CentroResponsable.usuario_id == usuario_normalizado,
        models.CentroResponsable.estado == "invitado",
    ).all()

    activados = []
    for responsable in responsabilidades:
        responsable.estado = "activo"
        activados.append({
            "responsable_id": responsable.id,
            "centro_id": responsable.centro_id,
        })
        log_centro_history(
            db,
            centro_id=responsable.centro_id,
            tipo_cambio="responsable",
            valor_anterior=f"{responsable.usuario_id}:{responsable.rol_en_centro}:invitado",
            valor_nuevo=f"{responsable.usuario_id}:{responsable.rol_en_centro}:activo",
            cambiado_por=usuario_normalizado,
            descripcion="Invitación aceptada por el responsable al iniciar sesión.",
        )

    db.commit()
    return {
        "ok": True,
        "activados": len(activados),
        "items": activados,
    }


@router.get("/centros/{centro_id}/historial", response_model=List[schemas.CentroHistorialResponse])
def listar_historial_centro(
    centro_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    centro = db.query(models.Centro).filter(models.Centro.id == centro_id).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")

    return db.query(models.CentroHistorial).filter(
        models.CentroHistorial.centro_id == centro_id
    ).order_by(desc(models.CentroHistorial.cambiado_en)).limit(limit).all()

@router.patch("/reportes/{reporte_id}", response_model=schemas.CentroReporteResponse)
def editar_reporte(
    reporte_id: int,
    data: schemas.CentroReporteUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    reporte = db.query(models.CentroReporte).filter(models.CentroReporte.id == reporte_id).first()
    if not reporte:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(reporte, campo, valor)
    db.commit()
    db.refresh(reporte)
    return reporte

@router.delete("/reportes/{reporte_id}")
def eliminar_reporte(
    reporte_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    reporte = db.query(models.CentroReporte).filter(models.CentroReporte.id == reporte_id).first()
    if not reporte:
        raise HTTPException(status_code=404, detail="Reporte no encontrado")
    db.delete(reporte)
    db.commit()
    return {"ok": True, "id": reporte_id}

@router.post("/solicitar-centro", status_code=201)
@limiter.limit("10/hour")
async def solicitar_centro(
    request: Request,
    nombre: str = Form(...),
    organizacion_id: Optional[str] = Form(None),
    direccion: Optional[str] = Form(None),
    zona: Optional[str] = Form(None),
    reportado_por: Optional[str] = Form(None),
    notas: Optional[str] = Form(None),
    latitud: Optional[str] = Form(None),
    longitud: Optional[str] = Form(None),
    before_submit_version: Optional[str] = Form(None),
    before_submit_title: Optional[str] = Form(None),
    before_submit_description: Optional[str] = Form(None),
    before_submit_items: Optional[str] = Form(None),
    before_submit_confirmations: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Público — solicitar agregar un centro de albergue nuevo"""
    try:
        before_submit = build_before_submit_metadata(
            version=before_submit_version,
            title=before_submit_title,
            description=before_submit_description,
            items=before_submit_items,
            confirmations=before_submit_confirmations,
            source="public_shelter_form",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    solicitud = models.CentroSolicitud(
        nombre=nombre,
        organizacion_id=normalize_organizacion_id(organizacion_id),
        direccion=direccion or None,
        zona=zona or None,
        reportado_por=reportado_por or None,
        notas=notas or None,
        latitud=latitud or None,
        longitud=longitud or None,
        **before_submit,
    )
    db.add(solicitud)
    db.commit()
    return {"ok": True, "mensaje": "Tu solicitud fue recibida. El equipo la revisará y agregará el centro a la brevedad."}

# ── Órdenes de albergues ──────────────────────────────────────

@router.get("/ordenes", response_model=List[schemas.CentroOrdenResponse])
def listar_ordenes(
    centro_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(None),
    reporte_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Protegido — lista órdenes de albergues con filtros opcionales"""
    q = db.query(models.CentroOrden)
    if centro_id is not None:
        q = q.filter(models.CentroOrden.centro_id == centro_id)
    if estado is not None:
        q = q.filter(models.CentroOrden.estado == estado)
    if reporte_id is not None:
        q = q.filter(models.CentroOrden.reporte_id == reporte_id)
    ordenes = q.order_by(desc(models.CentroOrden.created_at)).all()

    resultado = []
    for orden in ordenes:
        resp = schemas.CentroOrdenResponse.model_validate(orden)
        entrega = db.query(models.CentroEntrega).filter(
            models.CentroEntrega.orden_id == orden.id
        ).first()
        if entrega:
            resp.entrega = schemas.CentroEntregaResponse.model_validate(entrega)
        resultado.append(resp)
    return resultado


@router.get("/ordenes/{orden_id}")
@limiter.limit("60/hour")
async def obtener_orden(
    request: Request,
    orden_id: int,
    db: Session = Depends(get_db),
):
    """Público — el repartidor consulta el detalle del pedido, sin API key"""
    orden = db.query(models.CentroOrden).filter(models.CentroOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    try:
        items = json.loads(orden.items_ordenados)
    except (TypeError, ValueError):
        items = []

    entrega = db.query(models.CentroEntrega).filter(
        models.CentroEntrega.orden_id == orden.id
    ).first()

    centro = db.query(models.Centro).filter(
        models.Centro.id == orden.centro_id
    ).first()

    reporte = db.query(models.CentroReporte).filter(
        models.CentroReporte.id == orden.reporte_id
    ).first()

    created_at = orden.created_at
    if created_at and created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)

    return {
        "id": orden.id,
        "reporte_id": orden.reporte_id,
        "centro_id": orden.centro_id,
        "items_ordenados": items,
        "nota_repartidor": orden.nota_repartidor,
        "estado": orden.estado,
        "creado_por": orden.creado_por,
        "created_at": created_at.astimezone(VE_TZ).isoformat() if created_at else None,
        "entrega": schemas.CentroEntregaResponse.model_validate(entrega) if entrega else None,
        "centro": {
            "nombre": centro.nombre,
            "direccion": centro.direccion,
        } if centro else None,
        "reporte": {
            "necesitan": reporte.necesitan,
            "no_necesitan": reporte.no_necesitan,
            "notas": reporte.notas,
        } if reporte else None,
    }


@router.post("/ordenes/{orden_id}/confirmar-entrega",
             response_model=schemas.CentroEntregaResponse, status_code=201)
@limiter.limit("10/hour")
async def confirmar_entrega(
    request: Request,
    orden_id: int,
    nombre_receptor: str = Form(...),
    notas_entrega: str = Form(None),
    foto_entrega: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    """Público — el repartidor confirma la entrega del pedido, sin API key"""
    orden = db.query(models.CentroOrden).filter(models.CentroOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    existente = db.query(models.CentroEntrega).filter(
        models.CentroEntrega.orden_id == orden_id
    ).first()
    if existente:
        raise HTTPException(status_code=400, detail="Esta orden ya fue confirmada")

    foto_url = None
    if foto_entrega and foto_entrega.filename:
        file_bytes = await read_limited(foto_entrega)
        if file_bytes is None:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 10MB)")
        compressed = await compress_image_async(file_bytes)
        if compressed:
            filename = f"entrega_{uuid.uuid4()}.jpg"
            with open(os.path.join(UPLOAD_DIR_ENTREGAS, filename), "wb") as f:
                f.write(compressed)
            foto_url = f"{BASE_URL}/uploads/albergue_entregas/{filename}"

    entrega = models.CentroEntrega(
        orden_id=orden_id,
        nombre_receptor=nombre_receptor,
        foto_entrega_url=foto_url,
        notas_entrega=notas_entrega,
    )
    db.add(entrega)
    estado_anterior = orden.estado
    orden.estado = "entregado"
    log_centro_history(
        db,
        centro_id=orden.centro_id,
        tipo_cambio="orden_entrega",
        valor_anterior=f"{orden.id}:{estado_anterior}",
        valor_nuevo=f"{orden.id}:entregado",
        cambiado_por=(nombre_receptor or "").strip() or "entrega_publica",
        descripcion=f"Entrega confirmada vía link público por {nombre_receptor}.",
    )
    db.commit()
    db.refresh(entrega)
    return entrega


@router.patch("/ordenes/{orden_id}/estado", response_model=schemas.CentroOrdenResponse)
def cambiar_estado_orden(
    orden_id: int,
    data: OrdenEstadoUpdate,
    db: Session = Depends(get_db),
    actor_id: str = Depends(verify_api_key),
):
    orden = db.query(models.CentroOrden).filter(models.CentroOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    if data.estado not in ESTADOS_ORDEN_VALIDOS:
        raise HTTPException(status_code=422, detail="Estado inválido")

    estado_anterior = orden.estado
    orden.estado = data.estado
    log_centro_history(
        db,
        centro_id=orden.centro_id,
        tipo_cambio="orden_estado",
        valor_anterior=f"{orden.id}:{estado_anterior}",
        valor_nuevo=f"{orden.id}:{orden.estado}",
        cambiado_por=actor_id,
        descripcion=f"Estado del pedido #{orden.id} actualizado.",
    )
    db.commit()
    db.refresh(orden)

    resp = schemas.CentroOrdenResponse.model_validate(orden)
    entrega = db.query(models.CentroEntrega).filter(
        models.CentroEntrega.orden_id == orden.id
    ).first()
    if entrega:
        resp.entrega = schemas.CentroEntregaResponse.model_validate(entrega)
    return resp


@router.delete("/ordenes/{orden_id}")
def eliminar_orden(
    orden_id: int,
    db: Session = Depends(get_db),
    actor_id: str = Depends(verify_api_key),
):
    orden = db.query(models.CentroOrden).filter(models.CentroOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    estado_anterior = orden.estado
    centro_id = orden.centro_id
    entrega_existente = db.query(models.CentroEntrega).filter(models.CentroEntrega.orden_id == orden_id).first()
    db.query(models.CentroEntrega).filter(models.CentroEntrega.orden_id == orden_id).delete()
    db.delete(orden)
    log_centro_history(
        db,
        centro_id=centro_id,
        tipo_cambio="orden_eliminada",
        valor_anterior=f"{orden_id}:{estado_anterior}",
        cambiado_por=actor_id,
        descripcion=(
            f"Pedido #{orden_id} eliminado."
            + (" También se removió la confirmación de entrega asociada." if entrega_existente else "")
        ),
    )
    db.commit()
    return {"ok": True}
