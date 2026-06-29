from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import func, case
from sqlalchemy.orm import Session
import models
from database import get_db
from dependencies import verify_api_key

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/stats", tags=["stats"])
public_router = APIRouter(prefix="/api/stats", tags=["stats"])

@router.get("/")
def get_stats(db: Session = Depends(get_db), _: str = Depends(verify_api_key)):
    hoy = datetime.now(timezone.utc).date()

    result = db.query(
        func.count().label("total"),
        func.count(case(
            (func.date(models.PersonaDesaparecida.created_at) == hoy.isoformat(), 1)
        )).label("hoy"),
        func.count(models.PersonaDesaparecida.foto_url).label("con_foto"),
        func.count(case(
            (models.PersonaDesaparecida.estado == "desaparecido", 1)
        )).label("estado_desaparecido"),
        func.count(case(
            (models.PersonaDesaparecida.estado == "encontrado", 1)
        )).label("estado_encontrado"),
        func.count(case(
            (models.PersonaDesaparecida.estado == "en_proceso", 1)
        )).label("estado_en_proceso"),
        func.count(case(
            (models.PersonaDesaparecida.tipo_reporte == "desaparecido", 1)
        )).label("tipo_desaparecido"),
        func.count(case(
            (models.PersonaDesaparecida.tipo_reporte == "encontrado_vivo", 1)
        )).label("tipo_encontrado_vivo"),
    ).one()

    cutoff = hoy - timedelta(days=6)
    rows = (
        db.query(
            func.date(models.PersonaDesaparecida.created_at).label("dia"),
            func.count().label("cantidad"),
        )
        .filter(func.date(models.PersonaDesaparecida.created_at) >= cutoff.isoformat())
        .group_by(func.date(models.PersonaDesaparecida.created_at))
        .all()
    )
    counts_by_day = {r.dia: r.cantidad for r in rows}
    ultimos_7 = [
        {
            "fecha": (hoy - timedelta(days=i)).isoformat(),
            "cantidad": counts_by_day.get((hoy - timedelta(days=i)).isoformat(), 0),
        }
        for i in range(6, -1, -1)
    ]

    return {
        "total": result.total,
        "hoy": result.hoy,
        "con_foto": result.con_foto,
        "por_estado": {
            "desaparecido": result.estado_desaparecido,
            "encontrado": result.estado_encontrado,
            "en_proceso": result.estado_en_proceso,
        },
        "por_tipo": {
            "desaparecido": result.tipo_desaparecido,
            "encontrado_vivo": result.tipo_encontrado_vivo,
        },
        "ultimos_7_dias": ultimos_7,
    }


@public_router.get("/publico")
@limiter.limit("30/hour")
def get_stats_publico(request: Request, db: Session = Depends(get_db)):

    # Una sola query para personas — SQLite devuelve números, no filas
    result = db.query(
        func.count().label("total"),
        func.count(case(
            (models.PersonaDesaparecida.estado == "desaparecido", 1)
        )).label("desaparecidos"),
        func.count(case(
            (models.PersonaDesaparecida.estado == "encontrado", 1)
        )).label("encontrados"),
        func.count(case(
            (models.PersonaDesaparecida.tipo_reporte == "encontrado_vivo", 1)
        )).label("encontrados_vivos"),
    ).one()

    # Count de pacientes — una sola query
    pacientes = db.query(
        func.count(models.PacienteHospitalizado.id)
    ).scalar()

    return {
        "total": result.total,
        "desaparecidos": result.desaparecidos,
        "encontrados": result.encontrados,
        "encontrados_vivos": result.encontrados_vivos,
        "pacientes_hospitalizados": pacientes,
    }
