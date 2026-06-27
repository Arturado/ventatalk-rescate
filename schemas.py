from pydantic import BaseModel, field_serializer
from datetime import datetime, timezone, timedelta
from typing import Optional

VE_TZ = timezone(timedelta(hours=-4))

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
    numero_contacto_2: Optional[str] = None
    quien_ayudo: Optional[str] = None
    contacto_quien_ayudo: Optional[str] = None
    estado: str = "desaparecido"
    tipo_reporte: str = "desaparecido"
    created_at: datetime
    tipo_reportante: Optional[str] = None

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()
    sexo: Optional[str] = None
    edad_aproximada: Optional[str] = None
    contextura: Optional[str] = None
    cabello: Optional[str] = None
    ropa_aproximada: Optional[str] = None
    estado_clinico: Optional[str] = None
    sin_documentos: bool = False

    model_config = {"from_attributes": True}

class HospitalResponse(BaseModel):
    id: int
    nombre: str
    direccion: Optional[str] = None
    zona: str
    telefono: Optional[str] = None
    tipo: Optional[str] = None
    activo: bool
    model_config = {"from_attributes": True}

class PacienteResponse(BaseModel):
    id: int
    hospital_id: Optional[int] = None
    nombre_hospital: Optional[str] = None
    nombres_apellidos: str
    edad: Optional[str] = None
    sexo: Optional[str] = None
    cedula: Optional[str] = None
    parentesco: Optional[str] = None
    procedencia: Optional[str] = None
    observaciones: Optional[str] = None
    datos_adicionales: Optional[str] = None
    foto_captura_url: Optional[str] = None
    estado_paciente: str
    reportado_por: Optional[str] = None
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}
