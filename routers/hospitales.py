from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
import models, schemas, os, uuid
from services.images import read_limited, compress_image_async
from dependencies import verify_api_key
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api/hospitales", tags=["hospitales"])
pacientes_router = APIRouter(prefix="/api/pacientes", tags=["pacientes"])

BASE_URL = os.getenv("BASE_URL", "https://rescate.ventatalk.com")
UPLOAD_DIR = "uploads/capturas"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── Hospitales ────────────────────────────────────────────────

@router.get("/", response_model=List[schemas.HospitalResponse])
def listar_hospitales(
    zona: Optional[str] = None,
    tipo: Optional[str] = None,
    db: Session = Depends(get_db),
    response: Response = None,
):
    # Público, sin API key
    query = db.query(models.Hospital).filter(models.Hospital.activo == True)
    if zona:
        query = query.filter(models.Hospital.zona.ilike(f"%{zona}%"))
    if tipo:
        query = query.filter(models.Hospital.tipo == tipo)
    response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"
    return query.order_by(models.Hospital.zona, models.Hospital.nombre).all()

@router.get("/{hospital_id}", response_model=schemas.HospitalResponse)
def obtener_hospital(hospital_id: int, db: Session = Depends(get_db)):
    h = db.query(models.Hospital).filter(models.Hospital.id == hospital_id).first()
    if not h:
        raise HTTPException(status_code=404, detail="Hospital no encontrado")
    return h

# ── Pacientes ─────────────────────────────────────────────────

@pacientes_router.post("/", response_model=schemas.PacienteResponse, status_code=201)
async def crear_paciente(
    nombres_apellidos: str = Form(...),
    hospital_id: int = Form(None),
    nombre_hospital: str = Form(None),
    edad: str = Form(None),
    sexo: str = Form(None),
    cedula: str = Form(None),
    parentesco: str = Form(None),
    procedencia: str = Form(None),
    observaciones: str = Form(None),
    datos_adicionales: str = Form(None),
    estado_paciente: str = Form("ingresado"),
    reportado_por: str = Form(None),
    necesita_ayuda: bool = Form(False),
    tipo_ayuda: str = Form(None),
    foto_captura: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    foto_url = None
    if foto_captura and foto_captura.filename:
        file_bytes = await read_limited(foto_captura)
        if file_bytes is None:
            raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 10MB)")
        compressed = await compress_image_async(file_bytes)
        if compressed:
            filename = f"cap_{uuid.uuid4()}.jpg"
            with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
                f.write(compressed)
            foto_url = f"{BASE_URL}/uploads/capturas/{filename}"

    paciente = models.PacienteHospitalizado(
        hospital_id=hospital_id,
        nombre_hospital=nombre_hospital,
        nombres_apellidos=nombres_apellidos,
        edad=edad, sexo=sexo, cedula=cedula,
        parentesco=parentesco, procedencia=procedencia,
        observaciones=observaciones,
        datos_adicionales=datos_adicionales,
        estado_paciente=estado_paciente,
        reportado_por=reportado_por,
        necesita_ayuda=necesita_ayuda,
        tipo_ayuda=tipo_ayuda or None,
        foto_captura_url=foto_url
    )
    db.add(paciente)
    db.commit()
    db.refresh(paciente)
    return paciente

@pacientes_router.get("/recientes")
def pacientes_recientes(db: Session = Depends(get_db)):
    from datetime import timezone
    rows = (
        db.query(models.PacienteHospitalizado)
        .filter(models.PacienteHospitalizado.estado_paciente != "fallecido")
        .order_by(models.PacienteHospitalizado.created_at.desc())
        .limit(20)
        .all()
    )
    out = []
    for p in rows:
        dt = p.created_at
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        out.append({
            "id": p.id,
            "nombre_hospital": p.nombre_hospital,
            "nombres_apellidos": p.nombres_apellidos,
            "edad": p.edad,
            "sexo": p.sexo,
            "estado_paciente": p.estado_paciente,
            "procedencia": p.procedencia,
            "created_at": dt.isoformat() if dt else None,
        })
    return out

@pacientes_router.get("/buscar", response_model=List[schemas.PacienteResponse])
def buscar_pacientes(
    nombre: Optional[str] = Query(None),
    cedula: Optional[str] = Query(None),
    hospital_id: Optional[int] = Query(None),
    zona: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    # Público sin API key, rate limit en main
    if not nombre and not cedula and not hospital_id:
        raise HTTPException(status_code=400, detail="Indica al menos un criterio de búsqueda")
    query = db.query(models.PacienteHospitalizado)
    if nombre:
        query = query.filter(models.PacienteHospitalizado.nombres_apellidos.ilike(f"%{nombre}%"))
    if cedula:
        # Normalizar cédula igual que en personas
        cedula_norm = cedula.strip().upper()
        from sqlalchemy import or_
        query = query.filter(or_(
            models.PacienteHospitalizado.cedula == cedula_norm,
            models.PacienteHospitalizado.cedula == cedula_norm.lstrip("V-").lstrip("E-"),
        ))
    if hospital_id:
        query = query.filter(models.PacienteHospitalizado.hospital_id == hospital_id)
    return query.order_by(models.PacienteHospitalizado.created_at.desc()).limit(50).all()

@pacientes_router.get("/count")
def contar_pacientes(
    nombre: Optional[str] = Query(None),
    cedula: Optional[str] = Query(None),
    hospital_id: Optional[int] = Query(None),
    estado: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    query = db.query(models.PacienteHospitalizado)
    if nombre:
        query = query.filter(models.PacienteHospitalizado.nombres_apellidos.ilike(f"%{nombre}%"))
    if cedula:
        query = query.filter(models.PacienteHospitalizado.cedula.ilike(f"%{cedula}%"))
    if estado:
        query = query.filter(models.PacienteHospitalizado.estado_paciente == estado)
    if hospital_id:
        query = query.filter(models.PacienteHospitalizado.hospital_id == hospital_id)
    return {"total": query.count()}

@pacientes_router.get("/matches/duplicados")
def duplicados_pacientes(
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    todos = db.query(models.PacienteHospitalizado).all()
    results = []
    seen: set = set()

    for i, a in enumerate(todos):
        for b in todos[i + 1:]:
            score = 0
            criterios: list = []

            if (a.cedula and b.cedula
                    and a.cedula.strip().upper() == b.cedula.strip().upper()):
                score += 4
                criterios.append("Cédula")

            if (a.nombres_apellidos and b.nombres_apellidos
                    and a.nombres_apellidos.lower() == b.nombres_apellidos.lower()):
                score += 3
                criterios.append("Nombre exacto")
            elif a.nombres_apellidos and b.nombres_apellidos:
                words_a = a.nombres_apellidos.lower().split()[:2]
                words_b = b.nombres_apellidos.lower().split()[:2]
                if (words_a and words_a == words_b
                        and a.hospital_id and a.hospital_id == b.hospital_id):
                    score += 2
                    criterios.append("Nombre similar · Hospital")

            if (a.sexo and b.sexo and a.sexo == b.sexo
                    and a.edad and b.edad and a.edad.lower() == b.edad.lower()
                    and a.hospital_id and a.hospital_id == b.hospital_id):
                score += 2
                criterios.append("Sexo · Edad · Hospital")

            if score > 0:
                pair_key = (min(a.id, b.id), max(a.id, b.id))
                if pair_key not in seen:
                    seen.add(pair_key)
                    results.append({
                        "score": score,
                        "paciente_a": schemas.PacienteResponse.model_validate(a),
                        "paciente_b": schemas.PacienteResponse.model_validate(b),
                        "criterios_match": criterios,
                    })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:30]

@pacientes_router.get("/", response_model=List[schemas.PacienteResponse])
def listar_pacientes(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=15000),
    nombre: Optional[str] = Query(None),
    cedula: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    hospital_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    query = db.query(models.PacienteHospitalizado).filter(
        models.PacienteHospitalizado.estado_paciente != "fallecido"
    )
    if nombre:
        query = query.filter(models.PacienteHospitalizado.nombres_apellidos.ilike(f"%{nombre}%"))
    if cedula:
        query = query.filter(models.PacienteHospitalizado.cedula.ilike(f"%{cedula}%"))
    if estado and estado != "fallecido":
        query = query.filter(models.PacienteHospitalizado.estado_paciente == estado)
    if hospital_id:
        query = query.filter(models.PacienteHospitalizado.hospital_id == hospital_id)
    return query.order_by(models.PacienteHospitalizado.created_at.desc()).offset(skip).limit(limit).all()

@pacientes_router.get("/{paciente_id}/historial", response_model=List[schemas.MovimientoResponse])
@limiter.limit("30/hour")
def historial_paciente(
    paciente_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    movimientos = (
        db.query(models.PacienteMovimiento)
        .filter(models.PacienteMovimiento.paciente_id == paciente_id)
        .order_by(models.PacienteMovimiento.created_at.asc())
        .all()
    )
    return movimientos

@pacientes_router.patch("/{paciente_id}/estado")
def actualizar_estado_paciente(
    paciente_id: int,
    estado: str = Form(...),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key)
):
    p = db.query(models.PacienteHospitalizado).filter(
        models.PacienteHospitalizado.id == paciente_id
    ).first()
    if not p:
        raise HTTPException(status_code=404, detail="No encontrado")
    p.estado_paciente = estado
    db.commit()
    return {"ok": True}
