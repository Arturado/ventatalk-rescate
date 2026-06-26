import io, os, uuid
from typing import Optional
from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import or_
from sqlalchemy.orm import Session
import models, schemas
from database import get_db

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/reportes", tags=["reportes"])
UPLOAD_DIR = "uploads/fotos"
MAX_SIZE_KB = 500
os.makedirs(UPLOAD_DIR, exist_ok=True)
BASE_URL = os.getenv("BASE_URL", "https://rescate.ventatalk.com")

def normalize_cedula(value: str) -> str:
    if not value:
        return value
    v = value.strip()
    if len(v) >= 2 and v[1] == '-' and v[0].upper() in ('V', 'E'):
        v = v[0].upper() + v[1:]
    return v[:15]

def normalize_contacto(value: str) -> str:
    if not value:
        return value
    return value.strip()[:15]

async def save_photo(foto: UploadFile):
    if not foto or not foto.filename:
        return None
    file_bytes = await foto.read()
    try:
        compressed = compress_image(file_bytes)
    except Exception:
        return None
    if not compressed:
        return None
    filename = f"{uuid.uuid4()}.jpg"
    with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
        f.write(compressed)
    return f"{BASE_URL}/uploads/fotos/{filename}"

def compress_image(file_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(file_bytes))
    if img.mode in ("RGBA", "P", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((2000, 2000), Image.LANCZOS)
    quality = 85
    while quality >= 20:
        output = io.BytesIO()
        img.save(output, format="JPEG", quality=quality, optimize=True)
        if output.tell() <= MAX_SIZE_KB * 1024:
            break
        quality -= 10
    return output.getvalue()

@router.post("/", response_model=schemas.PersonaResponse, status_code=201)
@limiter.limit("10/hour")
async def crear_reporte(
    request: Request,
    nombres_apellidos: str = Form(...),
    cedula: str = Form(None),
    ultima_ubicacion: str = Form(...),
    descripcion: str = Form(None),
    numero_contacto: str = Form(...),
    quien_ayudo: str = Form(None),
    contacto_quien_ayudo: str = Form(None),
    tipo_reporte: str = Form("desaparecido"),
    tipo_reportante: str = Form(None),
    sexo: str = Form(None),
    edad_aproximada: str = Form(None),
    contextura: str = Form(None),
    cabello: str = Form(None),
    ropa_aproximada: str = Form(None),
    estado_clinico: str = Form(None),
    sin_documentos: bool = Form(False),
    website: str = Form(None),
    foto: UploadFile = File(None),
    foto_2: UploadFile = File(None),
    foto_3: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    # Honeypot: campo oculto — si viene con valor es un bot
    if website:
        return JSONResponse(status_code=200, content={"id": 0, "mensaje": "ok"})

    if tipo_reporte not in ("desaparecido", "encontrado_vivo"):
        tipo_reporte = "desaparecido"

    if cedula:
        cedula = normalize_cedula(cedula)
    numero_contacto = normalize_contacto(numero_contacto)
    if contacto_quien_ayudo:
        contacto_quien_ayudo = normalize_contacto(contacto_quien_ayudo)

    foto_url = await save_photo(foto)
    foto_url_2 = await save_photo(foto_2)
    foto_url_3 = await save_photo(foto_3)

    persona = models.PersonaDesaparecida(
        nombres_apellidos=nombres_apellidos, cedula=cedula,
        ultima_ubicacion=ultima_ubicacion, descripcion=descripcion,
        numero_contacto=numero_contacto, quien_ayudo=quien_ayudo,
        contacto_quien_ayudo=contacto_quien_ayudo, foto_url=foto_url,
        foto_url_2=foto_url_2, foto_url_3=foto_url_3,
        estado="desaparecido", tipo_reporte=tipo_reporte,
        tipo_reportante=tipo_reportante, sexo=sexo,
        edad_aproximada=edad_aproximada, contextura=contextura,
        cabello=cabello, ropa_aproximada=ropa_aproximada,
        estado_clinico=estado_clinico, sin_documentos=sin_documentos,
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona


check_router = APIRouter(prefix="/api", tags=["utils"])

# Auxiliar decorada: aplica rate limit 60/hour al IP cuando no hay API key válida.
# Se llama manualmente desde buscar_personas en lugar de usar @limiter.limit en el endpoint.
async def _buscar_noop(request: Request): pass
_buscar_noop = limiter.limit("60/hour")(_buscar_noop)

@check_router.get("/buscar")
async def buscar_personas(
    request: Request,
    x_api_key: Optional[str] = Header(None),
    nombre: str = None,
    cedula: str = None,
    db: Session = Depends(get_db),
):
    api_key_valid = x_api_key and x_api_key == os.getenv("API_KEY")
    if not api_key_valid:
        await _buscar_noop(request)
    nombre = nombre.strip() if nombre else ""
    cedula = normalize_cedula(cedula) if cedula else ""
    if not nombre and not cedula:
        return JSONResponse(
            status_code=400,
            content={"detail": "Debe proporcionar al menos un parámetro: nombre o cedula"},
        )
    q = db.query(models.PersonaDesaparecida)
    if cedula:
        if cedula.isdigit():
            q = q.filter(or_(
                models.PersonaDesaparecida.cedula == cedula,
                models.PersonaDesaparecida.cedula == f"V-{cedula}",
            ))
        else:
            q = q.filter(models.PersonaDesaparecida.cedula == cedula)
    elif nombre:
        q = q.filter(models.PersonaDesaparecida.nombres_apellidos.ilike(f"%{nombre}%"))
    personas = q.order_by(models.PersonaDesaparecida.created_at.desc()).limit(10).all()
    return [
        {
            "id": p.id,
            "nombres_apellidos": p.nombres_apellidos,
            "cedula": p.cedula,
            "ultima_ubicacion": p.ultima_ubicacion,
            "estado": p.estado,
            "tipo_reporte": p.tipo_reporte,
            "foto_url": p.foto_url,
            "created_at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in personas
    ]


@check_router.get("/check-cedula")
@limiter.limit("20/hour")
async def check_cedula(
    request: Request,
    cedula: str,
    db: Session = Depends(get_db),
):
    if not cedula or not cedula.strip():
        return {"existe": False}
    persona = db.query(models.PersonaDesaparecida).filter(
        models.PersonaDesaparecida.cedula == cedula
    ).first()
    if persona:
        return {"existe": True, "id": persona.id}
    return {"existe": False}


@check_router.get("/feed")
@limiter.limit("30/hour")
async def feed_publico(
    request: Request,
    estado: str = None,
    tipo_reporte: str = None,
    skip: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(models.PersonaDesaparecida)
    q = q.filter(or_(
        models.PersonaDesaparecida.estado_clinico != "fallecido",
        models.PersonaDesaparecida.estado_clinico == None,
    ))
    if estado:
        q = q.filter(models.PersonaDesaparecida.estado == estado)
    if tipo_reporte:
        q = q.filter(models.PersonaDesaparecida.tipo_reporte == tipo_reporte)
    personas = q.order_by(models.PersonaDesaparecida.created_at.desc()).offset(skip).limit(50).all()
    return [
        {
            "id": p.id,
            "nombres_apellidos": p.nombres_apellidos,
            "cedula": p.cedula,
            "ultima_ubicacion": p.ultima_ubicacion,
            "descripcion": p.descripcion,
            "numero_contacto": p.numero_contacto,
            "foto_url": p.foto_url,
            "estado": p.estado,
            "tipo_reporte": p.tipo_reporte,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "tipo_reportante": p.tipo_reportante,
            "sexo": p.sexo,
            "edad_aproximada": p.edad_aproximada,
            "contextura": p.contextura,
            "cabello": p.cabello,
            "ropa_aproximada": p.ropa_aproximada,
            "estado_clinico": p.estado_clinico,
            "sin_documentos": p.sin_documentos,
        }
        for p in personas
    ]
