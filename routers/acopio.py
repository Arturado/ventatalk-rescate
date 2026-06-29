from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc, desc
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from database import get_db
from dependencies import verify_api_key
import models, schemas
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/api/acopio", tags=["acopio"])
limiter = Limiter(key_func=get_remote_address)

VE_TZ = timezone(timedelta(hours=-4))

# ── Centros ────────────────────────────────────────────────

@router.get("/centros", response_model=List[schemas.CentroAcopioResponse])
def listar_centros(
    zona: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Público — lista centros de acopio activos"""
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
    Para mostrar en venezuelarescate.com y en /acopio.
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
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(centro, campo, valor)
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
    centro.activo = False
    db.commit()
    return {"ok": True, "id": centro_id}

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
    db: Session = Depends(get_db),
):
    """Público — solicitar agregar un centro de acopio nuevo"""
    solicitud = models.CentroAcopioSolicitud(
        nombre=nombre,
        direccion=direccion or None,
        zona=zona or None,
        reportado_por=reportado_por or None,
        notas=notas or None,
    )
    db.add(solicitud)
    db.commit()
    return {"ok": True, "mensaje": "Tu solicitud fue recibida. El equipo la revisará y agregará el centro a la brevedad."}
