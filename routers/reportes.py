import io, os, uuid
from fastapi import APIRouter, Depends, File, Form, UploadFile
from PIL import Image
from sqlalchemy.orm import Session
import models, schemas
from database import get_db

router = APIRouter(prefix="/api/reportes", tags=["reportes"])
UPLOAD_DIR = "uploads/fotos"
MAX_SIZE_KB = 500
os.makedirs(UPLOAD_DIR, exist_ok=True)
BASE_URL = os.getenv("BASE_URL", "https://rescate.ventatalk.com")

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
async def crear_reporte(
    nombres_apellidos: str = Form(...),
    cedula: str = Form(None),
    ultima_ubicacion: str = Form(...),
    descripcion: str = Form(None),
    numero_contacto: str = Form(...),
    quien_ayudo: str = Form(None),
    contacto_quien_ayudo: str = Form(None),
    foto: UploadFile = File(None),
    db: Session = Depends(get_db),
):
    foto_url = None
    if foto and foto.filename:
        file_bytes = await foto.read()
        try:
            compressed = compress_image(file_bytes)
        except Exception:
            compressed = None
        if compressed:
            filename = f"{uuid.uuid4()}.jpg"
            with open(os.path.join(UPLOAD_DIR, filename), "wb") as f:
                f.write(compressed)
            foto_url = f"{BASE_URL}/uploads/fotos/{filename}"
    persona = models.PersonaDesaparecida(
        nombres_apellidos=nombres_apellidos, cedula=cedula,
        ultima_ubicacion=ultima_ubicacion, descripcion=descripcion,
        numero_contacto=numero_contacto, quien_ayudo=quien_ayudo,
        contacto_quien_ayudo=contacto_quien_ayudo, foto_url=foto_url,
    )
    db.add(persona)
    db.commit()
    db.refresh(persona)
    return persona
