import re

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc
from typing import List, Optional
from datetime import datetime, timezone, timedelta
from database import get_db
from dependencies import verify_api_key
import models, schemas, json, os, uuid
from services.images import read_limited, compress_image_async
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter(prefix="/api/casos-ayuda", tags=["casos_ayuda"])
limiter = Limiter(key_func=get_remote_address)

VE_TZ = timezone(timedelta(hours=-4))

UPLOAD_DIR_ADJUNTOS = "uploads/adjuntos"
os.makedirs(UPLOAD_DIR_ADJUNTOS, exist_ok=True)

BASE_URL = os.getenv("BASE_URL", "https://rescate.ventatalk.com")

# Transiciones válidas de estado
TRANSICIONES_VALIDAS = {
    "pendiente_revision": ["publicado", "rechazado"],
    "publicado": ["necesita_actualizacion", "resuelto"],
    "necesita_actualizacion": ["publicado", "archivado"],
    "resuelto": ["archivado"],
    "rechazado": [],
    "archivado": [],
}

NIVELES_VALIDOS = {"basico", "institucional", "medico"}
NIVELES_ORDEN = {"basico": 0, "institucional": 1, "medico": 2}

DOC_EXT_POR_CONTENT_TYPE = {
    "application/pdf": "pdf",
    "application/msword": "doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
}

CEDULA_REGEX = re.compile(r'^\d{6,9}$')


class ContactoCasoAyudaCreate(BaseModel):
    telefono: Optional[str] = None
    metodo_pago: Optional[str] = None
    datos_pago: Optional[str] = None
    contacto_alterno: Optional[str] = None


class CasoAyudaCreate(BaseModel):
    cedula: str
    nombre: str
    relato: str
    condicion_resumen: str
    monto_necesario: Optional[float] = None
    moneda: Optional[str] = None
    consentimiento_publicacion: bool
    creado_por: Optional[str] = "anonimo"
    adjuntos: Optional[List[str]] = None
    contacto: Optional[ContactoCasoAyudaCreate] = None
    # Se aceptan para poder rechazarlos/ignorarlos explícitamente
    nivel_verificacion: Optional[str] = None
    estado: Optional[str] = None


class EstadoCasoAyudaUpdate(BaseModel):
    estado: str
    descripcion_cambio: Optional[str] = None
    cambiado_por: Optional[str] = None


class NivelCasoAyudaUpdate(BaseModel):
    nivel_verificacion: str
    avalado_por: Optional[str] = None
    notas: Optional[str] = None


class ConfirmarCasoAyudaBody(BaseModel):
    confirmado_por: Optional[str] = None


def _get_caso_o_404(db: Session, caso_id: int) -> models.CasoAyuda:
    caso = db.query(models.CasoAyuda).filter(models.CasoAyuda.id == caso_id).first()
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return caso


# ── Endpoints protegidos por API key ───────────────────────────────────

@router.get("/", response_model=List[schemas.CasoAyudaResponse])
def listar_casos(
    estado: Optional[str] = Query(None),
    nivel_verificacion: Optional[str] = Query(None),
    cedula: Optional[str] = Query(None),
    vencidos_antes: Optional[datetime] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    q = db.query(models.CasoAyuda)
    if estado:
        q = q.filter(models.CasoAyuda.estado == estado)
    if nivel_verificacion:
        q = q.filter(models.CasoAyuda.nivel_verificacion == nivel_verificacion)
    if cedula:
        q = q.filter(models.CasoAyuda.cedula == cedula)
    if vencidos_antes:
        q = q.filter(models.CasoAyuda.fecha_ultima_confirmacion < vencidos_antes)
    return q.order_by(desc(models.CasoAyuda.created_at)).offset(skip).limit(limit).all()


@router.get("/{caso_id}", response_model=schemas.CasoAyudaResponse)
def obtener_caso(
    caso_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    return _get_caso_o_404(db, caso_id)


@router.post("/", status_code=201)
@limiter.limit("20/hour")
async def crear_caso(
    request: Request,
    data: CasoAyudaCreate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    if data.nivel_verificacion is not None:
        raise HTTPException(status_code=400, detail="nivel_verificacion no puede asignarse en la creación")

    cedula = (data.cedula or "").strip()
    if not CEDULA_REGEX.match(cedula):
        raise HTTPException(status_code=400, detail="cedula inválida: debe contener solo números (6-9 dígitos)")

    if not data.relato or not data.relato.strip():
        raise HTTPException(status_code=400, detail="relato no puede estar vacío")

    if data.consentimiento_publicacion is not True:
        raise HTTPException(status_code=400, detail="consentimiento_publicacion debe venir explícito y ser true")

    caso = models.CasoAyuda(
        cedula=cedula,
        nombre=data.nombre,
        relato=data.relato.strip(),
        condicion_resumen=data.condicion_resumen,
        monto_necesario=data.monto_necesario,
        moneda=data.moneda,
        estado="pendiente_revision",
        creado_por=data.creado_por or "anonimo",
        adjuntos=json.dumps(data.adjuntos) if data.adjuntos else None,
        consentimiento_publicacion=True,
    )
    db.add(caso)
    db.commit()
    db.refresh(caso)

    if data.contacto:
        contacto = models.ContactoCasoAyuda(
            caso_id=caso.id,
            telefono=data.contacto.telefono,
            metodo_pago=data.contacto.metodo_pago,
            datos_pago=data.contacto.datos_pago,
            contacto_alterno=data.contacto.contacto_alterno,
        )
        db.add(contacto)
        db.commit()

    return {"id": caso.id, "estado": caso.estado}


@router.post("/{caso_id}/estado", response_model=schemas.CasoAyudaResponse)
def cambiar_estado_caso(
    caso_id: int,
    data: EstadoCasoAyudaUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    caso = _get_caso_o_404(db, caso_id)
    permitidas = TRANSICIONES_VALIDAS.get(caso.estado, [])
    if data.estado not in permitidas:
        raise HTTPException(status_code=400, detail=f"Transición no permitida: {caso.estado} → {data.estado}")

    historial = models.CasoAyudaHistorial(
        caso_id=caso.id,
        tipo_cambio="estado",
        valor_anterior=caso.estado,
        valor_nuevo=data.estado,
        cambiado_por=data.cambiado_por,
        descripcion=data.descripcion_cambio,
    )
    db.add(historial)
    caso.estado = data.estado
    db.commit()
    db.refresh(caso)
    return caso


@router.post("/{caso_id}/nivel", response_model=schemas.CasoAyudaResponse)
def cambiar_nivel_caso(
    caso_id: int,
    data: NivelCasoAyudaUpdate,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    caso = _get_caso_o_404(db, caso_id)
    if data.nivel_verificacion not in NIVELES_VALIDOS:
        raise HTTPException(status_code=400, detail="nivel_verificacion inválido")

    subiendo = NIVELES_ORDEN[data.nivel_verificacion] > NIVELES_ORDEN.get(caso.nivel_verificacion, 0)
    if subiendo and not (data.avalado_por and data.avalado_por.strip()):
        raise HTTPException(status_code=400, detail="avalado_por es requerido para subir de nivel")

    historial = models.CasoAyudaHistorial(
        caso_id=caso.id,
        tipo_cambio="nivel",
        valor_anterior=caso.nivel_verificacion,
        valor_nuevo=data.nivel_verificacion,
        cambiado_por=data.avalado_por,
        descripcion=data.notas,
    )
    db.add(historial)
    caso.nivel_verificacion = data.nivel_verificacion
    if data.avalado_por and data.avalado_por.strip():
        caso.avalado_por = data.avalado_por.strip()
        caso.avalado_en = datetime.now(timezone.utc)
    db.commit()
    db.refresh(caso)
    return caso


@router.post("/{caso_id}/confirmar", response_model=schemas.CasoAyudaResponse)
def confirmar_caso(
    caso_id: int,
    data: Optional[ConfirmarCasoAyudaBody] = Body(None),
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    caso = _get_caso_o_404(db, caso_id)
    caso.fecha_ultima_confirmacion = datetime.now(timezone.utc)

    if caso.estado == "necesita_actualizacion":
        historial = models.CasoAyudaHistorial(
            caso_id=caso.id,
            tipo_cambio="estado",
            valor_anterior=caso.estado,
            valor_nuevo="publicado",
            cambiado_por=data.confirmado_por if data else None,
            descripcion="Confirmación de vigencia reactivó el caso a publicado",
        )
        db.add(historial)
        caso.estado = "publicado"

    db.commit()
    db.refresh(caso)
    return caso


@router.get("/{caso_id}/contacto", response_model=schemas.ContactoCasoAyudaResponse)
def obtener_contacto_caso(
    caso_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    caso = _get_caso_o_404(db, caso_id)
    contacto = db.query(models.ContactoCasoAyuda).filter(
        models.ContactoCasoAyuda.caso_id == caso_id
    ).first()
    if not contacto:
        raise HTTPException(status_code=404, detail="Este caso no tiene contacto registrado")

    ip = request.client.host if request.client else "desconocida"
    historial = models.CasoAyudaHistorial(
        caso_id=caso_id,
        tipo_cambio="contacto_accedido",
        descripcion=f"Acceso a datos de contacto — IP: {ip} — {datetime.now(VE_TZ).isoformat()}",
    )
    db.add(historial)
    db.commit()
    return contacto


@router.post("/{caso_id}/adjuntos")
async def subir_adjunto_caso(
    caso_id: int,
    db: Session = Depends(get_db),
    adjunto: UploadFile = File(...),
    _: str = Depends(verify_api_key),
):
    caso = _get_caso_o_404(db, caso_id)
    content_type = adjunto.content_type or ""

    file_bytes = await read_limited(adjunto, max_bytes=10 * 1024 * 1024)
    if file_bytes is None:
        raise HTTPException(status_code=413, detail="Archivo demasiado grande (máx 10MB)")

    if content_type.startswith("image/"):
        compressed = await compress_image_async(file_bytes)
        if compressed is None:
            raise HTTPException(status_code=400, detail="No se pudo procesar la imagen")
        data_to_save = compressed
        ext = "jpg"
    elif content_type in DOC_EXT_POR_CONTENT_TYPE:
        data_to_save = file_bytes
        ext = DOC_EXT_POR_CONTENT_TYPE[content_type]
    else:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido")

    filename = f"{uuid.uuid4()}.{ext}"
    with open(os.path.join(UPLOAD_DIR_ADJUNTOS, filename), "wb") as f:
        f.write(data_to_save)

    url = f"{BASE_URL}/uploads/adjuntos/{filename}"

    try:
        adjuntos = json.loads(caso.adjuntos) if caso.adjuntos else []
    except (TypeError, ValueError):
        adjuntos = []
    adjuntos.append(url)
    caso.adjuntos = json.dumps(adjuntos)
    db.commit()

    return {"ok": True, "url": url}


@router.get("/{caso_id}/historial", response_model=List[schemas.CasoAyudaHistorialResponse])
def listar_historial_caso(
    caso_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    _get_caso_o_404(db, caso_id)
    return db.query(models.CasoAyudaHistorial).filter(
        models.CasoAyudaHistorial.caso_id == caso_id
    ).order_by(desc(models.CasoAyudaHistorial.cambiado_en)).all()


@router.delete("/{caso_id}")
def eliminar_caso(
    caso_id: int,
    db: Session = Depends(get_db),
    _: str = Depends(verify_api_key),
):
    caso = _get_caso_o_404(db, caso_id)
    if caso.estado not in ("rechazado", "archivado"):
        raise HTTPException(status_code=400, detail="Solo se pueden eliminar casos rechazados o archivados")

    db.query(models.ContactoCasoAyuda).filter(models.ContactoCasoAyuda.caso_id == caso_id).delete()
    db.query(models.CasoAyudaHistorial).filter(models.CasoAyudaHistorial.caso_id == caso_id).delete()
    db.delete(caso)
    db.commit()
    return {"ok": True}
