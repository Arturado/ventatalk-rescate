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

# ── Centros ────────────────────────────────────────────────

@router.get("/centros", response_model=List[schemas.CentroAcopioResponse])
def listar_centros(
    zona: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Público — lista centros de albergue activos"""
    q = db.query(models.CentroAcopio).filter(models.CentroAcopio.activo == True)
    if zona:
        q = q.filter(models.CentroAcopio.zona.ilike(f"%{zona}%"))
    return q.order_by(models.CentroAcopio.zona, models.CentroAcopio.nombre).all()

@router.get("/centros/{centro_id}", response_model=schemas.CentroAcopioResponse)
def obtener_centro(centro_id: int, db: Session = Depends(get_db)):
    centro = db.query(models.CentroAcopio).filter(
        models.CentroAcopio.id == centro_id
    ).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")
    return centro

# ── Reportes públicos ──────────────────────────────────────

@router.post("/reportes", response_model=schemas.AcopioReporteResponse, status_code=201)
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
    centro = db.query(models.CentroAcopio).filter(
        models.CentroAcopio.id == centro_id,
        models.CentroAcopio.activo == True
    ).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")

    reporte = models.AcopioReporte(
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

@router.get("/estado", response_model=List[schemas.CentroAcopioConReporteResponse])
def estado_general(db: Session = Depends(get_db)):
    """
    Público — estado actual de todos los centros con su último reporte.
    Para mostrar en venezuelarescate.com y en /albergues.
    """
    centros = db.query(models.CentroAcopio).filter(
        models.CentroAcopio.activo == True
    ).order_by(models.CentroAcopio.zona, models.CentroAcopio.nombre).all()

    hoy = datetime.now(VE_TZ).date()
    resultado = []

    for centro in centros:
        ultimo = db.query(models.AcopioReporte).filter(
            models.AcopioReporte.centro_id == centro.id
        ).order_by(desc(models.AcopioReporte.created_at)).first()

        total_hoy = db.query(sqlfunc.count(models.AcopioReporte.id)).filter(
            models.AcopioReporte.centro_id == centro.id,
            sqlfunc.date(models.AcopioReporte.created_at) == hoy
        ).scalar()

        resultado.append(schemas.CentroAcopioConReporteResponse(
            centro=centro,
            ultimo_reporte=ultimo,
            total_reportes_hoy=total_hoy or 0
        ))

    return resultado

@router.get("/reportes/{centro_id}/historial",
            response_model=List[schemas.AcopioReporteResponse])
def historial_centro(
    centro_id: int,
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Protegido — historial de reportes de un centro. Para el colega."""
    return db.query(models.AcopioReporte).filter(
        models.AcopioReporte.centro_id == centro_id
    ).order_by(desc(models.AcopioReporte.created_at)).limit(limit).all()

@router.patch("/centros/{centro_id}", response_model=schemas.CentroAcopioResponse)
def editar_centro(
    centro_id: int,
    data: schemas.CentroAcopioUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    centro = db.query(models.CentroAcopio).filter(models.CentroAcopio.id == centro_id).first()
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
            cambiado_por="api_key",
        )

    sync_centro_activation_state(centro)
    db.commit()
    db.refresh(centro)
    return centro

@router.delete("/centros/{centro_id}")
def desactivar_centro(
    centro_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    centro = db.query(models.CentroAcopio).filter(models.CentroAcopio.id == centro_id).first()
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
        cambiado_por="api_key",
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
    centro = db.query(models.CentroAcopio).filter(models.CentroAcopio.id == centro_id).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")

    q = db.query(models.CentroResponsable).filter(
        models.CentroResponsable.centro_id == centro_id
    )
    if not incluir_removidos:
        q = q.filter(models.CentroResponsable.estado != "removido")
    return q.order_by(desc(models.CentroResponsable.asignado_en)).all()


@router.get("/centros/{centro_id}/historial", response_model=List[schemas.CentroHistorialResponse])
def listar_historial_centro(
    centro_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    centro = db.query(models.CentroAcopio).filter(models.CentroAcopio.id == centro_id).first()
    if not centro:
        raise HTTPException(status_code=404, detail="Centro no encontrado")

    return db.query(models.CentroHistorial).filter(
        models.CentroHistorial.centro_id == centro_id
    ).order_by(desc(models.CentroHistorial.cambiado_en)).limit(limit).all()

@router.patch("/reportes/{reporte_id}", response_model=schemas.AcopioReporteResponse)
def editar_reporte(
    reporte_id: int,
    data: schemas.AcopioReporteUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    reporte = db.query(models.AcopioReporte).filter(models.AcopioReporte.id == reporte_id).first()
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
    reporte = db.query(models.AcopioReporte).filter(models.AcopioReporte.id == reporte_id).first()
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

    solicitud = models.CentroAcopioSolicitud(
        nombre=nombre,
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

@router.get("/ordenes", response_model=List[schemas.AcopioOrdenResponse])
def listar_ordenes(
    centro_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(None),
    reporte_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    """Protegido — lista órdenes de albergues con filtros opcionales"""
    q = db.query(models.AcopioOrden)
    if centro_id is not None:
        q = q.filter(models.AcopioOrden.centro_id == centro_id)
    if estado is not None:
        q = q.filter(models.AcopioOrden.estado == estado)
    if reporte_id is not None:
        q = q.filter(models.AcopioOrden.reporte_id == reporte_id)
    ordenes = q.order_by(desc(models.AcopioOrden.created_at)).all()

    resultado = []
    for orden in ordenes:
        resp = schemas.AcopioOrdenResponse.model_validate(orden)
        entrega = db.query(models.AcopioEntrega).filter(
            models.AcopioEntrega.orden_id == orden.id
        ).first()
        if entrega:
            resp.entrega = schemas.AcopioEntregaResponse.model_validate(entrega)
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
    orden = db.query(models.AcopioOrden).filter(models.AcopioOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    try:
        items = json.loads(orden.items_ordenados)
    except (TypeError, ValueError):
        items = []

    entrega = db.query(models.AcopioEntrega).filter(
        models.AcopioEntrega.orden_id == orden.id
    ).first()

    centro = db.query(models.CentroAcopio).filter(
        models.CentroAcopio.id == orden.centro_id
    ).first()

    reporte = db.query(models.AcopioReporte).filter(
        models.AcopioReporte.id == orden.reporte_id
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
        "entrega": schemas.AcopioEntregaResponse.model_validate(entrega) if entrega else None,
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
             response_model=schemas.AcopioEntregaResponse, status_code=201)
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
    orden = db.query(models.AcopioOrden).filter(models.AcopioOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")

    existente = db.query(models.AcopioEntrega).filter(
        models.AcopioEntrega.orden_id == orden_id
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

    entrega = models.AcopioEntrega(
        orden_id=orden_id,
        nombre_receptor=nombre_receptor,
        foto_entrega_url=foto_url,
        notas_entrega=notas_entrega,
    )
    db.add(entrega)
    orden.estado = "entregado"
    db.commit()
    db.refresh(entrega)
    return entrega


@router.patch("/ordenes/{orden_id}/estado", response_model=schemas.AcopioOrdenResponse)
def cambiar_estado_orden(
    orden_id: int,
    data: OrdenEstadoUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    orden = db.query(models.AcopioOrden).filter(models.AcopioOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    if data.estado not in ESTADOS_ORDEN_VALIDOS:
        raise HTTPException(status_code=422, detail="Estado inválido")

    orden.estado = data.estado
    db.commit()
    db.refresh(orden)

    resp = schemas.AcopioOrdenResponse.model_validate(orden)
    entrega = db.query(models.AcopioEntrega).filter(
        models.AcopioEntrega.orden_id == orden.id
    ).first()
    if entrega:
        resp.entrega = schemas.AcopioEntregaResponse.model_validate(entrega)
    return resp


@router.delete("/ordenes/{orden_id}")
def eliminar_orden(
    orden_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    orden = db.query(models.AcopioOrden).filter(models.AcopioOrden.id == orden_id).first()
    if not orden:
        raise HTTPException(status_code=404, detail="Orden no encontrada")
    db.query(models.AcopioEntrega).filter(models.AcopioEntrega.orden_id == orden_id).delete()
    db.delete(orden)
    db.commit()
    return {"ok": True}
