from fastapi import APIRouter, Depends, Header, HTTPException, Query, Form, Request
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc
from typing import List, Optional
from database import get_db
import models, schemas, os
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/api/bomberos", tags=["bomberos"])
limiter = Limiter(key_func=get_remote_address)


def verify_api_key(x_api_key: str = Header(...)):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="API Key inválida")
    return x_api_key


@router.post("/reporte", response_model=schemas.BomberoReporteResponse, status_code=201)
@limiter.limit("60/hour")
async def crear_reporte_bombero(
    request: Request,
    latitud: str = Form(...),
    longitud: str = Form(...),
    precision_metros: str = Form(None),
    status: str = Form("trabajando"),
    descripcion: str = Form(None),
    identificador: str = Form(None),
    db: Session = Depends(get_db)
):
    reporte = models.BomberoReporte(
        identificador=identificador,
        latitud=latitud,
        longitud=longitud,
        precision_metros=precision_metros,
        status=status,
        descripcion=descripcion,
    )
    db.add(reporte)
    db.commit()
    db.refresh(reporte)
    return reporte


@router.get("/ultimas-posiciones", response_model=List[schemas.BomberoReporteResponse])
def ultimas_posiciones(
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """
    Retorna la última posición de cada bombero (por identificador).
    Para bomberos sin identificador retorna todos sus reportes del día.
    Protegido por API key.
    """
    subquery = (
        db.query(
            models.BomberoReporte.identificador,
            sqlfunc.max(models.BomberoReporte.id).label("max_id")
        )
        .filter(models.BomberoReporte.identificador.isnot(None))
        .group_by(models.BomberoReporte.identificador)
        .subquery()
    )

    con_identificador = (
        db.query(models.BomberoReporte)
        .join(subquery, models.BomberoReporte.id == subquery.c.max_id)
        .all()
    )

    from datetime import datetime, timezone, timedelta
    hoy_ve = datetime.now(timezone(timedelta(hours=-4))).date()
    sin_identificador = (
        db.query(models.BomberoReporte)
        .filter(
            models.BomberoReporte.identificador.is_(None),
            sqlfunc.date(models.BomberoReporte.created_at) == hoy_ve
        )
        .order_by(models.BomberoReporte.created_at.desc())
        .all()
    )

    return con_identificador + sin_identificador


@router.get("/historial", response_model=List[schemas.BomberoReporteResponse])
def historial(
    identificador: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    """Historial completo o por identificador. Protegido por API key."""
    query = db.query(models.BomberoReporte)
    if identificador:
        query = query.filter(
            models.BomberoReporte.identificador.ilike(f"%{identificador}%")
        )
    return query.order_by(models.BomberoReporte.created_at.desc()).limit(limit).all()
