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
    email_reportante: Optional[str] = None
    es_menor: bool = False

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
    necesita_ayuda: bool = False
    tipo_ayuda: Optional[str] = None
    created_at: datetime
    telefono: Optional[str] = None
    nombre_variantes: Optional[str] = None
    batch_date: Optional[str] = None
    fuente: Optional[str] = None
    es_menor: bool = False

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class BomberoReporteResponse(BaseModel):
    id: int
    identificador: Optional[str] = None
    latitud: str
    longitud: str
    precision_metros: Optional[str] = None
    status: str
    descripcion: Optional[str] = None
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class MovimientoResponse(BaseModel):
    id: int
    paciente_id: int
    hospital_anterior: Optional[str] = None
    hospital_nuevo: Optional[str] = None
    estado_anterior: Optional[str] = None
    estado_nuevo: Optional[str] = None
    batch_date_anterior: Optional[str] = None
    batch_date_nuevo: Optional[str] = None
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class CentroAcopioResponse(BaseModel):
    id: int
    nombre: str
    direccion: Optional[str] = None
    zona: Optional[str] = None
    activo: bool
    created_at: datetime
    latitud: Optional[str] = None
    longitud: Optional[str] = None

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class AcopioReporteResponse(BaseModel):
    id: int
    centro_id: int
    hombres: Optional[int] = 0
    mujeres: Optional[int] = 0
    ninos: Optional[int] = 0
    lactantes: Optional[int] = 0
    necesitan: Optional[str] = None
    no_necesitan: Optional[str] = None
    necesitan_extra: Optional[str] = None
    no_necesitan_extra: Optional[str] = None
    notas: Optional[str] = None
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

class CentroAcopioConReporteResponse(BaseModel):
    centro: CentroAcopioResponse
    ultimo_reporte: Optional[AcopioReporteResponse] = None
    total_reportes_hoy: int = 0
    model_config = {"from_attributes": True}

class PersonaUpdate(BaseModel):
    nombres_apellidos: Optional[str] = None
    cedula: Optional[str] = None
    ultima_ubicacion: Optional[str] = None
    descripcion: Optional[str] = None
    numero_contacto: Optional[str] = None
    estado: Optional[str] = None
    tipo_reporte: Optional[str] = None
    estado_clinico: Optional[str] = None
    sexo: Optional[str] = None
    edad_aproximada: Optional[str] = None
    contextura: Optional[str] = None
    cabello: Optional[str] = None
    ropa_aproximada: Optional[str] = None
    quien_ayudo: Optional[str] = None
    contacto_quien_ayudo: Optional[str] = None
    numero_contacto_2: Optional[str] = None
    sin_documentos: Optional[bool] = None
    tipo_reportante: Optional[str] = None

class PacienteUpdate(BaseModel):
    nombres_apellidos: Optional[str] = None
    cedula: Optional[str] = None
    nombre_hospital: Optional[str] = None
    hospital_id: Optional[int] = None
    edad: Optional[str] = None
    sexo: Optional[str] = None
    procedencia: Optional[str] = None
    parentesco: Optional[str] = None
    observaciones: Optional[str] = None
    datos_adicionales: Optional[str] = None
    estado_paciente: Optional[str] = None
    necesita_ayuda: Optional[bool] = None
    tipo_ayuda: Optional[str] = None
    reportado_por: Optional[str] = None

class EstadoPersonaUpdate(BaseModel):
    estado: str
    descripcion_cambio: Optional[str] = None

class CentroAcopioUpdate(BaseModel):
    nombre: Optional[str] = None
    direccion: Optional[str] = None
    zona: Optional[str] = None
    activo: Optional[bool] = None

class AcopioReporteUpdate(BaseModel):
    hombres: Optional[int] = None
    mujeres: Optional[int] = None
    ninos: Optional[int] = None
    lactantes: Optional[int] = None
    necesitan: Optional[str] = None
    no_necesitan: Optional[str] = None
    necesitan_extra: Optional[str] = None
    no_necesitan_extra: Optional[str] = None
    notas: Optional[str] = None
    reportado_por: Optional[str] = None

class NotificacionLocalizadoResponse(BaseModel):
    id: int
    persona_id: int
    nombre_reportante: str
    numero_contacto: str
    descripcion: Optional[str] = None
    foto_url: Optional[str] = None
    estado: str
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class CentroSolicitudResponse(BaseModel):
    id: int
    nombre: str
    direccion: Optional[str] = None
    zona: Optional[str] = None
    reportado_por: Optional[str] = None
    notas: Optional[str] = None
    estado: str
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class AcopioOrdenResponse(BaseModel):
    id: int
    reporte_id: int
    centro_id: int
    items_ordenados: str  # JSON string
    nota_repartidor: Optional[str] = None
    estado: str
    creado_por: Optional[str] = None
    created_at: datetime
    entrega: Optional["AcopioEntregaResponse"] = None

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class AcopioEntregaResponse(BaseModel):
    id: int
    orden_id: int
    nombre_receptor: str
    foto_entrega_url: Optional[str] = None
    notas_entrega: Optional[str] = None
    created_at: datetime

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

AcopioOrdenResponse.model_rebuild()

class CasoAyudaResponse(BaseModel):
    id: int
    cedula: str
    nombre: str
    relato: str
    condicion_resumen: str
    monto_necesario: Optional[float] = None
    moneda: Optional[str] = None
    estado: str
    nivel_verificacion: str
    avalado_por: Optional[str] = None
    avalado_en: Optional[datetime] = None
    adjuntos: Optional[list] = None
    consentimiento_publicacion: bool
    fecha_ultima_confirmacion: datetime
    creado_por: str
    created_at: datetime
    updated_at: datetime

    @field_serializer('created_at', 'updated_at', 'fecha_ultima_confirmacion')
    def serialize_dt(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat() if value else None

    model_config = {"from_attributes": True}

class ContactoCasoAyudaResponse(BaseModel):
    caso_id: int
    telefono: Optional[str] = None
    metodo_pago: Optional[str] = None
    datos_pago: Optional[str] = None
    contacto_alterno: Optional[str] = None
    model_config = {"from_attributes": True}

class CasoAyudaHistorialResponse(BaseModel):
    id: int
    caso_id: int
    tipo_cambio: str
    valor_anterior: Optional[str] = None
    valor_nuevo: Optional[str] = None
    cambiado_por: Optional[str] = None
    descripcion: Optional[str] = None
    cambiado_en: datetime

    @field_serializer('cambiado_en')
    def serialize_dt(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat() if value else None

    model_config = {"from_attributes": True}
