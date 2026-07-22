from pydantic import BaseModel, Field, field_serializer
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Literal, Optional

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

class CentroResponse(BaseModel):
    id: int
    nombre: str
    tipo: str = "albergue"
    organizacion_id: str = "venezuela-rescate"
    estado: str = "activo"
    direccion: Optional[str] = None
    zona: Optional[str] = None
    reportado_por: Optional[str] = None
    notas: Optional[str] = None
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

class CentroReporteResponse(BaseModel):
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

class CentroConReporteResponse(BaseModel):
    centro: CentroResponse
    ultimo_reporte: Optional[CentroReporteResponse] = None
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

class CentroUpdate(BaseModel):
    nombre: Optional[str] = None
    tipo: Optional[str] = None
    organizacion_id: Optional[str] = None
    estado: Optional[str] = None
    direccion: Optional[str] = None
    zona: Optional[str] = None
    activo: Optional[bool] = None
    reportado_por: Optional[str] = None
    notas: Optional[str] = None
    latitud: Optional[str] = None
    longitud: Optional[str] = None

class CentroReporteUpdate(BaseModel):
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
    organizacion_id: Optional[str] = None
    direccion: Optional[str] = None
    zona: Optional[str] = None
    reportado_por: Optional[str] = None
    notas: Optional[str] = None
    before_submit_version: Optional[str] = None
    before_submit_title: Optional[str] = None
    before_submit_description: Optional[str] = None
    before_submit_items_json: Optional[str] = None
    before_submit_confirmations_json: Optional[str] = None
    before_submit_acknowledged_at: Optional[str] = None
    before_submit_source: Optional[str] = None
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

class CentroResponsableResponse(BaseModel):
    id: int
    centro_id: int
    usuario_id: str
    rol_en_centro: str
    estado: str
    asignado_por_id: Optional[str] = None
    asignado_en: datetime
    removido_por_id: Optional[str] = None
    removido_en: Optional[datetime] = None

    @field_serializer('asignado_en', 'removido_en')
    def serialize_dt(self, value: Optional[datetime]) -> Optional[str]:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat() if value else None

    model_config = {"from_attributes": True}

class CentroHistorialResponse(BaseModel):
    id: int
    centro_id: int
    tipo_cambio: str
    valor_anterior: Optional[str] = None
    valor_nuevo: Optional[str] = None
    cambiado_por: Optional[str] = None
    descripcion: Optional[str] = None
    cambiado_en: datetime

    @field_serializer('cambiado_en')
    def serialize_historial_dt(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat() if value else None

    model_config = {"from_attributes": True}

class CentroOrdenResponse(BaseModel):
    id: int
    reporte_id: int
    centro_id: int
    items_ordenados: str  # JSON string
    nota_repartidor: Optional[str] = None
    estado: str
    creado_por: Optional[str] = None
    created_at: datetime
    entrega: Optional["CentroEntregaResponse"] = None

    @field_serializer('created_at')
    def serialize_created_at(self, value: datetime) -> str:
        from datetime import timezone, timedelta
        VE_TZ = timezone(timedelta(hours=-4))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat()

    model_config = {"from_attributes": True}

class CentroEntregaResponse(BaseModel):
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

CentroOrdenResponse.model_rebuild()

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
    adjuntos: Optional[List[str]] = []
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


class CasoAyudaV2ResumenResponse(BaseModel):
    id: int
    public_id: str
    organizacion_id: str
    titulo_interno: str
    categoria: str
    meta_monto: Decimal
    meta_moneda: str
    monto_confirmado: Decimal
    ayudas_confirmadas: int
    estado: str
    prioridad_especial: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CasoAyudaV2CreateRequest(BaseModel):
    organizacion_id: Optional[str] = None
    beneficiary_name: str = Field(min_length=2, max_length=500)
    beneficiary_identity: str = Field(min_length=4, max_length=30)
    internal_title: str = Field(min_length=3, max_length=250)
    category: str = Field(min_length=2, max_length=80)
    goal_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    goal_currency: Literal["VES", "USD", "EUR"]


class CasoAyudaV2UpdateRequest(BaseModel):
    internal_title: Optional[str] = Field(default=None, min_length=3, max_length=250)
    category: Optional[str] = Field(default=None, min_length=2, max_length=80)
    goal_amount: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    goal_currency: Optional[Literal["VES", "USD", "EUR"]] = None
    private_story: Optional[str] = Field(default=None, min_length=2, max_length=10000)


class CuentaCasoAyudaResumenResponse(BaseModel):
    id: int
    account_key: str
    version: int
    tipo_titular: str
    medio: str
    moneda: str
    estado: str
    titular_nombre: str
    relacion_beneficiario: str
    identificador_enmascarado: str
    instrucciones: Optional[str] = None
    justificacion: Optional[str] = None
    responsable_nombre: str
    responsable_email_enmascarado: str

    model_config = {"from_attributes": True}


class PublicacionCasoAyudaResumenResponse(BaseModel):
    nombre_publico: str
    titulo_publico: str
    descripcion_publica: str
    localidad_general: Optional[str] = None
    redes_sociales: dict[str, str] = Field(default_factory=dict)
    version: int
    activa: bool


class VerificacionCasoAyudaResumenResponse(BaseModel):
    registrada: bool
    es_menor: bool
    tiene_representante: bool
    relacion_representante: Optional[str] = None
    autoridad_representante_verificada: bool


class ConsentimientoCasoAyudaResumenResponse(BaseModel):
    registrado: bool
    version: Optional[int] = None
    tipo_firmante: Optional[str] = None
    evidencia_adjunta: bool
    vigente: bool


class BeneficiarioCasoAyudaResumenResponse(BaseModel):
    nombre_legal: str
    cedula_enmascarada: str


class CasoAyudaV2DetalleResponse(CasoAyudaV2ResumenResponse):
    readiness_blockers: List[str]
    accounts: List[CuentaCasoAyudaResumenResponse] = []
    publicacion: Optional[PublicacionCasoAyudaResumenResponse] = None
    verificacion: VerificacionCasoAyudaResumenResponse
    consentimiento: ConsentimientoCasoAyudaResumenResponse
    beneficiario: BeneficiarioCasoAyudaResumenResponse


class BeneficiarioAyudaVerificacionRequest(BaseModel):
    verification_data: str = Field(min_length=2, max_length=10000)
    is_minor: bool = False
    representative_name: Optional[str] = Field(default=None, min_length=2, max_length=500)
    representative_relationship: Optional[str] = Field(default=None, min_length=2, max_length=100)
    representative_authority_verified: bool = False


class PublicacionCasoAyudaRequest(BaseModel):
    public_name: str = Field(min_length=1, max_length=200)
    public_title: str = Field(min_length=3, max_length=250)
    public_description: str = Field(min_length=10, max_length=10000)
    general_location: Optional[str] = Field(default=None, max_length=200)
    social_networks: dict[str, str] = Field(default_factory=dict)


class ConsentimientoCasoAyudaCreateRequest(BaseModel):
    text_version: str = Field(min_length=2, max_length=100)
    scope: dict[str, bool]
    signer_name: str = Field(min_length=2, max_length=500)
    signer_type: Literal["beneficiario", "representante"]
    evidence_file_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    evidence_content_type: Optional[Literal["application/pdf", "image/jpeg", "image/png"]] = None
    evidence_base64: Optional[str] = Field(default=None, min_length=8, max_length=7_100_000)


class CuentaCasoAyudaCreateRequest(BaseModel):
    account_key: str = Field(min_length=2, max_length=100)
    owner_type: Literal["beneficiario", "organizacion", "tercero", "coordinador"]
    holder_name: str = Field(min_length=2, max_length=500)
    beneficiary_relationship: str = Field(min_length=2, max_length=150)
    medium: Literal["banco_venezolano", "zelle", "banco_internacional"]
    currency: Literal["VES", "USD", "EUR"]
    identifier: str = Field(min_length=2, max_length=1000)
    instructions: Optional[str] = Field(default=None, max_length=5000)
    justification: Optional[str] = Field(default=None, max_length=5000)
    responsible_name: str = Field(min_length=2, max_length=500)
    responsible_email: str = Field(min_length=5, max_length=320)


class CasoAyudaPublicoResponse(BaseModel):
    id: int
    public_id: str
    nombre_publico: str
    titulo_publico: str
    descripcion_publica: str
    categoria: str
    localidad_general: Optional[str] = None
    meta_monto: Decimal
    meta_moneda: str
    monto_confirmado: Decimal
    ayudas_confirmadas: int
    estado: str
    prioridad_especial: bool
    publicado_at: Optional[datetime] = None
    documentos: List[dict] = []


class AceptacionTerminosDonanteRequest(BaseModel):
    terms_version: Literal["donor-v1"]


class EstadoTerminosDonanteResponse(BaseModel):
    required_version: str
    accepted: bool
    accepted_at: Optional[datetime] = None


class CuentaDonanteResponse(BaseModel):
    id: int
    version: int
    holder_name: str
    beneficiary_relationship: str
    medium: str
    currency: str
    identifier: str
    instructions: Optional[str] = None


class CuentasCasoDonanteResponse(BaseModel):
    case_public_id: str
    accounts: List[CuentaDonanteResponse]
