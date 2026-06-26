import os
from typing import List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session
import models, schemas
from database import get_db

router = APIRouter(prefix="/api/personas", tags=["personas"])

def verify_api_key(x_api_key: str = Header(...)):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="API Key invalida")
    return x_api_key

@router.get("/", response_model=List[schemas.PersonaResponse])
def listar_personas(
    nombre: Optional[str] = Query(None),
    cedula: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    tipo_reporte: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    query = db.query(models.PersonaDesaparecida)
    if nombre:
        query = query.filter(models.PersonaDesaparecida.nombres_apellidos.ilike(f"%{nombre}%"))
    if cedula:
        query = query.filter(models.PersonaDesaparecida.cedula == cedula)
    if estado:
        query = query.filter(models.PersonaDesaparecida.estado == estado)
    if tipo_reporte:
        query = query.filter(models.PersonaDesaparecida.tipo_reporte == tipo_reporte)
    return query.order_by(models.PersonaDesaparecida.created_at.desc()).offset(skip).limit(limit).all()

@router.get("/{persona_id}", response_model=schemas.PersonaResponse)
def obtener_persona(persona_id: int, db: Session = Depends(get_db), _: str = Depends(verify_api_key)):
    persona = db.query(models.PersonaDesaparecida).filter(models.PersonaDesaparecida.id == persona_id).first()
    if not persona:
        raise HTTPException(status_code=404, detail="No encontrado")
    return persona
