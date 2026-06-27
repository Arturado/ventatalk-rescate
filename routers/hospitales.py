from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from typing import List, Optional
from database import get_db
import models, schemas, os, uuid
from PIL import Image
import io

router = APIRouter(prefix="/api/hospitales", tags=["hospitales"])
pacientes_router = APIRouter(prefix="/api/pacientes", tags=["pacientes"])

BASE_URL = os.getenv("BASE_URL", "https://rescate.ventatalk.com")
UPLOAD_DIR = "uploads/capturas"
os.makedirs(UPLOAD_DIR, exist_ok=True)

def verify_api_key(x_api_key: str = Header(...)):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=401, detail="API Key inválida")
    return x_api_key

# ── Hospitales ────────────────────────────────────────────────

@router.get("/", response_model=List[schemas.HospitalResponse])
def listar_hospitales(
    zona: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    # Público, sin API key
    query = db.query(models.Hospital).filter(models.Hospital.activo == True)
    if zona:
        query = query.filter(models.Hospital.zona.ilike(f"%{zona}%"))
    if tipo:
        query = query.filter(models.Hospital.tipo == tipo)
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
        file_bytes = await foto_captura.read()
        try:
            img = Image.open(io.BytesIO(file_bytes))
            if img.mode not in ("RGB",):
                img = img.convert("RGB")
            img.thumbnail((2000, 2000), Image.LANCZOS)
            output = io.BytesIO()
            img.save(output, format="JPEG", quality=85, optimize=True)
            filename = f"cap_{uuid.uuid4()}.jpg"
            with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
                f.write(output.getvalue())
            foto_url = f"{BASE_URL}/uploads/capturas/{filename}"
        except Exception:
            pass

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

@pacientes_router.get("/", response_model=List[schemas.PacienteResponse])
def listar_pacientes(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=5000),
    nombre: Optional[str] = Query(None),
    cedula: Optional[str] = Query(None),
    estado: Optional[str] = Query(None),
    hospital_id: Optional[int] = Query(None),
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
    return query.order_by(models.PacienteHospitalizado.created_at.desc()).offset(skip).limit(limit).all()

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
