import io, os, uuid
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from PIL import Image
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session
import models, schemas
from database import get_db

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/api/reportes", tags=["reportes"])
UPLOAD_DIR = "uploads/fotos"
MAX_SIZE_KB = 500
os.makedirs(UPLOAD_DIR, exist_ok=True)
BASE_URL = os.getenv("BASE_URL", "https://rescate.ventatalk.com")

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
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona


check_router = APIRouter(prefix="/api", tags=["utils"])

@check_router.get("/buscar")
@limiter.limit("20/hour")
async def buscar_personas(
    request: Request,
    nombre: str = None,
    cedula: str = None,
    db: Session = Depends(get_db),
):
    nombre = nombre.strip() if nombre else ""
    cedula = cedula.strip() if cedula else ""
    if not nombre and not cedula:
        return JSONResponse(
            status_code=400,
            content={"detail": "Debe proporcionar al menos un parámetro: nombre o cedula"},
        )
    q = db.query(models.PersonaDesaparecida)
    if nombre:
        q = q.filter(models.PersonaDesaparecida.nombres_apellidos.ilike(f"%{nombre}%"))
    if cedula:
        q = q.filter(models.PersonaDesaparecida.cedula == cedula)
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
