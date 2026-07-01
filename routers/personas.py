from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
import models, schemas
from database import get_db
from dependencies import verify_api_key

router = APIRouter(prefix="/api/personas", tags=["personas"])
matches_router = APIRouter(prefix="/api/matches", tags=["matches"])
limiter = Limiter(key_func=get_remote_address)

@router.get("/", response_model=List[schemas.PersonaResponse])
def listar_personas(
    nombre: Optional[str] = Query(None),
    cedula: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    tipo_reporte: Optional[str] = Query(None),
    tipo_reportante: Optional[str] = Query(None),
    sin_documentos: Optional[bool] = Query(None),
    estado_clinico: Optional[str] = Query(None),
    sexo: Optional[str] = Query(None),
    edad_aproximada: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=15000),
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
    if tipo_reportante:
        query = query.filter(models.PersonaDesaparecida.tipo_reportante == tipo_reportante)
    if sin_documentos is not None:
        query = query.filter(models.PersonaDesaparecida.sin_documentos == sin_documentos)
    if estado_clinico:
        query = query.filter(models.PersonaDesaparecida.estado_clinico == estado_clinico)
    if sexo:
        query = query.filter(models.PersonaDesaparecida.sexo == sexo)
    if edad_aproximada:
        query = query.filter(models.PersonaDesaparecida.edad_aproximada == edad_aproximada)
    return query.order_by(models.PersonaDesaparecida.created_at.desc()).offset(skip).limit(limit).all()

@router.get("/{persona_id}", response_model=schemas.PersonaResponse)
def obtener_persona(persona_id: int, db: Session = Depends(get_db), _: str = Depends(verify_api_key)):
    persona = db.query(models.PersonaDesaparecida).filter(models.PersonaDesaparecida.id == persona_id).first()
    if not persona:
        raise HTTPException(status_code=404, detail="No encontrado")
    return persona

@router.patch("/{persona_id}", response_model=schemas.PersonaResponse)
def editar_persona(
    persona_id: int,
    data: schemas.PersonaUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    persona = db.query(models.PersonaDesaparecida).filter(models.PersonaDesaparecida.id == persona_id).first()
    if not persona:
        raise HTTPException(status_code=404, detail="No encontrado")
    for campo, valor in data.model_dump(exclude_unset=True).items():
        setattr(persona, campo, valor)
    db.commit()
    db.refresh(persona)
    return persona

@router.delete("/{persona_id}")
def eliminar_persona(
    persona_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    persona = db.query(models.PersonaDesaparecida).filter(models.PersonaDesaparecida.id == persona_id).first()
    if not persona:
        raise HTTPException(status_code=404, detail="No encontrado")
    db.delete(persona)
    db.commit()
    return {"ok": True, "id": persona_id}

@router.post("/{persona_id}/estado", response_model=schemas.PersonaResponse)
def cambiar_estado_persona(
    persona_id: int,
    data: schemas.EstadoPersonaUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    estados_validos = {"desaparecido", "encontrado", "en_proceso", "localizado"}
    if data.estado not in estados_validos:
        raise HTTPException(status_code=422, detail=f"Estado inválido. Valores permitidos: {', '.join(estados_validos)}")
    persona = db.query(models.PersonaDesaparecida).filter(models.PersonaDesaparecida.id == persona_id).first()
    if not persona:
        raise HTTPException(status_code=404, detail="No encontrado")
    persona.estado = data.estado
    db.commit()
    db.refresh(persona)
    return persona


@matches_router.get("/posibles")
@limiter.limit("30/hour")
def posibles_matches(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    desaparecidos = db.query(models.PersonaDesaparecida).filter(
        models.PersonaDesaparecida.tipo_reporte == "desaparecido",
        models.PersonaDesaparecida.estado == "desaparecido",
    ).all()

    encontrados = db.query(models.PersonaDesaparecida).filter(
        models.PersonaDesaparecida.tipo_reporte == "encontrado_vivo",
        models.PersonaDesaparecida.estado != "encontrado",
    ).all()

    if not encontrados:
        return []

    results = []
    for d in desaparecidos:
        for e in encontrados:
            score = 0
            criterios = []

            if d.ultima_ubicacion and e.ultima_ubicacion:
                d_words = {w.lower() for w in d.ultima_ubicacion.split() if len(w) > 3}
                e_words = {w.lower() for w in e.ultima_ubicacion.split() if len(w) > 3}
                if d_words & e_words:
                    score += 1
                    criterios.append("zona")

            if d.sexo and e.sexo and d.sexo == e.sexo:
                score += 1
                criterios.append("sexo")

            if d.edad_aproximada and e.edad_aproximada and d.edad_aproximada == e.edad_aproximada:
                score += 1
                criterios.append("edad")

            if (d.cedula and e.cedula
                    and not d.sin_documentos and not e.sin_documentos
                    and d.cedula == e.cedula):
                score += 1
                criterios.append("cedula")

            if score >= 2:
                results.append({
                    "score": score,
                    "desaparecido": schemas.PersonaResponse.model_validate(d),
                    "encontrado": schemas.PersonaResponse.model_validate(e),
                    "criterios_match": criterios,
                })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:20]


@matches_router.get("/duplicados")
@limiter.limit("30/hour")
def posibles_duplicados(
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    todas = db.query(models.PersonaDesaparecida).all()

    results = []
    seen: set = set()

    for i, a in enumerate(todas):
        for b in todas[i + 1:]:
            if a.estado == "encontrado" and b.estado == "encontrado":
                continue

            score = 0
            criterios: list = []

            if (a.cedula and b.cedula
                    and not a.sin_documentos and not b.sin_documentos
                    and a.cedula == b.cedula):
                score += 4
                criterios.append("Cédula")

            if (a.nombres_apellidos and b.nombres_apellidos
                    and a.nombres_apellidos.lower() == b.nombres_apellidos.lower()):
                score += 3
                criterios.append("Nombre exacto")
            elif a.nombres_apellidos and b.nombres_apellidos:
                words_a = a.nombres_apellidos.lower().split()[:2]
                words_b = b.nombres_apellidos.lower().split()[:2]
                if words_a and words_a == words_b:
                    z_a = {w.lower() for w in (a.ultima_ubicacion or "").split() if len(w) > 3}
                    z_b = {w.lower() for w in (b.ultima_ubicacion or "").split() if len(w) > 3}
                    if z_a & z_b:
                        score += 2
                        criterios.append("Nombre similar · Zona")

            if score > 0:
                pair_key = (min(a.id, b.id), max(a.id, b.id))
                if pair_key not in seen:
                    seen.add(pair_key)
                    results.append({
                        "score": score,
                        "persona_a": schemas.PersonaResponse.model_validate(a),
                        "persona_b": schemas.PersonaResponse.model_validate(b),
                        "criterios_match": criterios,
                    })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:30]
