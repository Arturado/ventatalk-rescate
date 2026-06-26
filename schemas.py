from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class PersonaResponse(BaseModel):
    id: int
    nombres_apellidos: str
    cedula: Optional[str] = None
    ultima_ubicacion: str
    foto_url: Optional[str] = None
    descripcion: Optional[str] = None
    numero_contacto: str
    quien_ayudo: Optional[str] = None
    contacto_quien_ayudo: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
