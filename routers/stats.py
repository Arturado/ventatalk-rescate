import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
import models
from database import get_db

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/stats", tags=["stats"])
public_router = APIRouter(prefix="/api/stats", tags=["stats"])

def verify_api_key(x_api_key: str = Header(...)):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="API Key invalida")
    return x_api_key

@router.get("/")
def get_stats(db: Session = Depends(get_db), _: str = Depends(verify_api_key)):
    todas = db.query(models.PersonaDesaparecida).all()
    hoy = datetime.now(timezone.utc).date()

    total = len(todas)
    hoy_count = sum(1 for p in todas if p.created_at and p.created_at.date() == hoy)
    con_foto = sum(1 for p in todas if p.foto_url)

    por_estado = {"desaparecido": 0, "encontrado": 0, "en_proceso": 0}
    for p in todas:
        if p.estado in por_estado:
            por_estado[p.estado] += 1

    por_tipo = {"desaparecido": 0, "encontrado_vivo": 0}
    for p in todas:
        t = getattr(p, "tipo_reporte", "desaparecido") or "desaparecido"
        if t in por_tipo:
            por_tipo[t] += 1

    ultimos_7 = []
    for i in range(6, -1, -1):
        dia = hoy - timedelta(days=i)
        cantidad = sum(1 for p in todas if p.created_at and p.created_at.date() == dia)
        ultimos_7.append({"fecha": dia.isoformat(), "cantidad": cantidad})

    return {
        "total": total,
        "hoy": hoy_count,
        "con_foto": con_foto,
        "por_estado": por_estado,
        "por_tipo": por_tipo,
        "ultimos_7_dias": ultimos_7,
    }


@public_router.get("/publico")
@limiter.limit("30/hour")
def get_stats_publico(request: Request, db: Session = Depends(get_db)):
    todas = db.query(models.PersonaDesaparecida).all()
    total = len(todas)
    desaparecidos = sum(1 for p in todas if p.estado == "desaparecido")
    encontrados = sum(1 for p in todas if p.estado == "encontrado")
    encontrados_vivos = sum(
        1 for p in todas if getattr(p, "tipo_reporte", None) == "encontrado_vivo"
    )
    return {
        "total": total,
        "desaparecidos": desaparecidos,
        "encontrados": encontrados,
        "encontrados_vivos": encontrados_vivos,
    }
