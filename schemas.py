from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class PersonaResponse(BaseModel):
    id: int
    nombres_apellidos: str
    cedula: Optional[str] = None
    ultima_ubicacion: str
    foto_url: Optional[str] = None
    foto_url_2: Optional[str] = None
    foto_url_3: Optional[str] = None
    descripcion: Optional[str] = None
    numero_contacto: str
    quien_ayudo: Optional[str] = None
    contacto_quien_ayudo: Optional[str] = None
    estado: str = "desaparecido"
    tipo_reporte: str = "desaparecido"
    created_at: datetime
    tipo_reportante: Optional[str] = None
    sexo: Optional[str] = None
    edad_aproximada: Optional[str] = None
    contextura: Optional[str] = None
    cabello: Optional[str] = None
    ropa_aproximada: Optional[str] = None
    estado_clinico: Optional[str] = None
    sin_documentos: bool = False

    model_config = {"from_attributes": True}
