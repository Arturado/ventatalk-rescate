from pydantic import BaseModel, Field, field_serializer, field_validator, model_serializer, model_validator
import re
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import List, Literal, Optional

from services.help_identity import HelpIdentityError, normalize_venezuelan_identity

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

class CasoAyudaV2ResumenResponse(BaseModel):
    id: int
    public_id: str
    organizacion_id: str
    titulo_interno: str
    categoria: str
    tipo_sujeto: str
    acepta_ayuda_monetaria: bool
    acepta_ayuda_directa: bool
    meta_monto: Optional[Decimal] = None
    meta_moneda: Optional[str] = None
    monto_confirmado: Decimal
    ayudas_confirmadas: int
    estado: str
    prioridad_especial: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CasoAyudaV2UpdateRequest(BaseModel):
    internal_title: Optional[str] = Field(default=None, min_length=3, max_length=250)
    category: Optional[str] = Field(default=None, min_length=2, max_length=80)
    goal_amount: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    goal_currency: Optional[Literal["VES", "USD", "EUR"]] = None
    private_story: Optional[str] = Field(default=None, min_length=2, max_length=10000)
    profession: Optional[str] = Field(default=None, min_length=2, max_length=250)


HelpCaseCategory = Literal["salud", "empleo", "insumo_recurso"]
HelpCaseSubjectType = Literal["persona", "campana_organizacion"]
HelpCaseAidMode = Literal["monetaria", "directa", "oferta_laboral"]
HelpCaseState = Literal[
    "borrador",
    "publicado",
    "pausado",
    "meta_alcanzada",
    "cerrado",
    "suspendido",
    "archivado",
]


def _ordered_help_case_modes(values):
    selected = set(values or [])
    return [mode for mode in ("monetaria", "directa", "oferta_laboral") if mode in selected]


class CasoAyudaV2DraftCreateRequest(BaseModel):
    organizacion_id: Optional[str] = None
    category: HelpCaseCategory
    subject_type: HelpCaseSubjectType
    aid_modes: List[HelpCaseAidMode] = Field(min_length=1)
    beneficiary_name: Optional[str] = Field(default=None, min_length=2, max_length=500)
    beneficiary_identity: Optional[str] = Field(default=None, min_length=6, max_length=30)
    title: str = Field(min_length=3, max_length=250)
    story: str = Field(min_length=10, max_length=10000)
    goal_amount: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    goal_currency: Optional[Literal["VES", "USD", "EUR"]] = None
    profession: Optional[str] = Field(default=None, min_length=2, max_length=250)
    beneficiary_access_email: Optional[str] = Field(default=None, min_length=5, max_length=320)

    @field_validator("aid_modes")
    @classmethod
    def normalize_modes(cls, value):
        return _ordered_help_case_modes(value)

    @field_validator("beneficiary_identity")
    @classmethod
    def normalize_identity(cls, value):
        if value is None:
            return None
        try:
            return normalize_venezuelan_identity(value)
        except HelpIdentityError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("beneficiary_access_email")
    @classmethod
    def normalize_access_email(cls, value):
        normalized = str(value or "").strip().lower()
        if value is not None and (
            normalized.count("@") != 1
            or "." not in normalized.rsplit("@", 1)[-1]
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("El correo de acceso no es valido")
        return normalized or None

    @model_validator(mode="after")
    def validate_contract_matrix(self):
        modes = set(self.aid_modes)
        if self.subject_type == "persona":
            if not self.beneficiary_name or not self.beneficiary_identity:
                raise ValueError("Los casos de persona requieren beneficiario y cedula")
        elif any((
            self.beneficiary_name,
            self.beneficiary_identity,
            self.profession,
            self.beneficiary_access_email,
        )):
            raise ValueError("Las campanas no admiten datos personales")

        if self.category == "empleo":
            if (
                self.subject_type != "persona"
                or modes != {"oferta_laboral"}
                or not self.profession
                or not self.beneficiary_access_email
                or any((self.goal_amount, self.goal_currency))
            ):
                raise ValueError("El contrato de Empleo no es valido")
            return self

        if not modes or not modes.issubset({"monetaria", "directa"}):
            raise ValueError("La modalidad del caso no es valida")
        has_money = "monetaria" in modes
        if has_money != (self.goal_amount is not None and self.goal_currency is not None):
            raise ValueError("La modalidad monetaria requiere meta y moneda")
        if self.profession or self.beneficiary_access_email:
            raise ValueError("Los campos laborales solo aplican a Empleo")
        return self


class CasoAyudaV2DraftUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=3, max_length=250)
    story: Optional[str] = Field(default=None, min_length=10, max_length=10000)
    goal_amount: Optional[Decimal] = Field(default=None, gt=0, max_digits=18, decimal_places=2)
    goal_currency: Optional[Literal["VES", "USD", "EUR"]] = None
    profession: Optional[str] = Field(default=None, min_length=2, max_length=250)
    beneficiary_access_email: Optional[str] = Field(default=None, min_length=5, max_length=320)

    @field_validator("beneficiary_access_email")
    @classmethod
    def normalize_access_email(cls, value):
        normalized = str(value or "").strip().lower()
        if value is not None and (
            normalized.count("@") != 1
            or "." not in normalized.rsplit("@", 1)[-1]
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("El correo de acceso no es valido")
        return normalized or None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("La actualizacion no contiene cambios")
        goal_fields = {"goal_amount", "goal_currency"}
        if self.model_fields_set.intersection(goal_fields) and not goal_fields.issubset(self.model_fields_set):
            raise ValueError("Meta y moneda deben actualizarse juntas")
        return self


class CasoAyudaV2ContractSummaryResponse(BaseModel):
    id: int
    public_id: str
    organizacion_id: str
    title: str
    category: HelpCaseCategory
    subject_type: HelpCaseSubjectType
    aid_modes: List[HelpCaseAidMode]
    state: HelpCaseState
    goal_amount: Optional[Decimal] = None
    goal_currency: Optional[Literal["VES", "USD", "EUR"]] = None
    confirmed_amount: Optional[Decimal] = None
    confirmed_aids: Optional[int] = None

    @model_validator(mode="after")
    def validate_financial_projection(self):
        monetary = "monetaria" in self.aid_modes
        financial_values = (
            self.goal_amount,
            self.goal_currency,
            self.confirmed_amount,
            self.confirmed_aids,
        )
        if monetary and any(value is None for value in financial_values):
            raise ValueError("El resumen monetario requiere valores financieros completos")
        if not monetary and any(value is not None for value in financial_values):
            raise ValueError("Un caso no monetario no expone valores financieros")
        return self


class CasoAyudaPublicContractResponse(BaseModel):
    public_id: str
    title: str
    description: str
    category: HelpCaseCategory
    subject_type: HelpCaseSubjectType
    aid_modes: List[HelpCaseAidMode]
    state: HelpCaseState
    primary_photo_url: str
    goal_amount: Optional[Decimal] = None
    goal_currency: Optional[Literal["VES", "USD", "EUR"]] = None
    confirmed_amount: Optional[Decimal] = None
    confirmed_aids: Optional[int] = None

    @model_validator(mode="after")
    def validate_financial_projection(self):
        monetary = "monetaria" in self.aid_modes
        financial_values = (
            self.goal_amount,
            self.goal_currency,
            self.confirmed_amount,
            self.confirmed_aids,
        )
        if monetary and any(value is None for value in financial_values):
            raise ValueError("El contrato monetario requiere valores financieros completos")
        if not monetary and any(value is not None for value in financial_values):
            raise ValueError("Un caso no monetario no expone valores financieros")
        return self


class TransicionCasoAyudaRequest(BaseModel):
    reason_code: Optional[Literal[
        "criterios_no_cumplidos",
        "documentacion_invalida",
        "duplicado",
        "riesgo_operativo",
        "revision_administrativa",
        "solicitud_organizacion",
    ]] = None


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
    responsable_email: str
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


class DocumentoCasoAyudaResumenResponse(BaseModel):
    id: int
    document_key: str
    version: int
    tipo: str
    clasificacion: str
    estado_revision: str
    content_type: str
    size_bytes: int
    cargado_por: str
    revisado_por: Optional[str] = None

    model_config = {"from_attributes": True}


class CasoAyudaV2DetalleResponse(CasoAyudaV2ResumenResponse):
    readiness_blockers: List[str]
    accounts: List[CuentaCasoAyudaResumenResponse] = []
    relato_privado: Optional[str] = None
    profesion: Optional[str] = None
    publicacion: Optional[PublicacionCasoAyudaResumenResponse] = None
    verificacion: VerificacionCasoAyudaResumenResponse
    consentimiento: ConsentimientoCasoAyudaResumenResponse
    beneficiario: Optional[BeneficiarioCasoAyudaResumenResponse] = None
    documentos: List[DocumentoCasoAyudaResumenResponse] = []


class CasoAyudaResponsableResponse(BaseModel):
    id: int
    caso_id: int
    organizacion_id: str
    usuario_id: str
    rol: str
    estado: str
    asignado_por_id: Optional[str] = None
    asignado_en: datetime
    removido_por_id: Optional[str] = None
    removido_en: Optional[datetime] = None

    @field_serializer('asignado_en', 'removido_en')
    def serialize_dt(self, value: Optional[datetime]) -> Optional[str]:
        if value and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(VE_TZ).isoformat() if value else None

    model_config = {"from_attributes": True}


class AsignarResponsableCasoAyudaRequest(BaseModel):
    target_uid: str = Field(min_length=1, max_length=128)
    target_email: str = Field(min_length=3, max_length=320)

    @field_validator("target_email")
    @classmethod
    def normalize_target_email(cls, value):
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("target_email invalido")
        return normalized


class BeneficiarioAyudaVerificacionRequest(BaseModel):
    is_minor: bool = False
    representative_name: Optional[str] = Field(default=None, min_length=2, max_length=500)
    representative_relationship: Optional[str] = Field(default=None, min_length=2, max_length=100)
    representative_authority_verified: bool = False


class PublicacionCasoAyudaRequest(BaseModel):
    public_name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    public_title: str = Field(min_length=3, max_length=250)
    public_description: str = Field(min_length=10, max_length=10000)
    general_location: Optional[str] = Field(default=None, max_length=200)
    social_networks: dict[str, str] = Field(default_factory=dict)
    within_current_consent_scope: Optional[bool] = None


class ConsentimientoCasoAyudaCreateRequest(BaseModel):
    text_version: str = Field(min_length=2, max_length=100)
    scope: dict[str, bool]
    signer_name: str = Field(min_length=2, max_length=500)
    signer_type: Literal["beneficiario", "representante"]
    evidence_file_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    evidence_content_type: Optional[Literal["application/pdf", "image/jpeg", "image/png"]] = None
    evidence_base64: Optional[str] = Field(default=None, min_length=8, max_length=7_100_000)


class DocumentoCasoAyudaCreateRequest(BaseModel):
    document_key: str = Field(min_length=1, max_length=100)
    document_type: str = Field(min_length=1, max_length=80)
    classification: Literal["publico", "privado"]
    file_name: str = Field(min_length=1, max_length=255)
    content_type: Literal["application/pdf", "image/jpeg", "image/png"]
    content_base64: str = Field(min_length=8, max_length=7_100_000)

    @model_validator(mode="after")
    def validate_public_photo_contract(self):
        if self.document_type not in {"foto_principal", "foto_galeria"}:
            return self
        if self.classification != "publico":
            raise ValueError("Las fotografias deben ser publicas")
        if self.content_type not in {"image/jpeg", "image/png"}:
            raise ValueError("Las fotografias solo admiten JPEG o PNG")
        lower_name = self.file_name.lower()
        valid_extension = (
            self.content_type == "image/jpeg" and lower_name.endswith((".jpg", ".jpeg"))
        ) or (self.content_type == "image/png" and lower_name.endswith(".png"))
        if not valid_extension:
            raise ValueError("La extension no corresponde con el MIME de la fotografia")
        if self.document_type == "foto_principal" and self.document_key != "foto_principal":
            raise ValueError("La foto principal requiere su clave estable")
        if self.document_type == "foto_galeria" and not re.fullmatch(
            r"foto_galeria:[a-z0-9][a-z0-9-]{0,63}", self.document_key,
        ):
            raise ValueError("La foto de galeria requiere una clave estable")
        return self


class DocumentoCasoAyudaReviewRequest(BaseModel):
    approve: bool
    reason: Optional[str] = Field(default=None, max_length=2000)


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
    acepta_ayuda_monetaria: bool
    acepta_ayuda_directa: bool
    modalidades: List[HelpCaseAidMode] = Field(default_factory=list)
    meta_monto: Optional[Decimal] = None
    meta_moneda: Optional[str] = None
    monto_confirmado: Optional[Decimal] = None
    ayudas_confirmadas: Optional[int] = None
    estado: str
    publicado_at: Optional[datetime] = None
    documentos: List[dict] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def omit_employment_financial_fields(self, handler):
        serialized = handler(self)
        if self.categoria == "empleo":
            for field in (
                "meta_monto",
                "meta_moneda",
                "monto_confirmado",
                "ayudas_confirmadas",
            ):
                serialized.pop(field, None)
        return serialized


class EquivalenciasCasoPublicoResponse(BaseModel):
    case_public_id: str
    goal_amount: Decimal
    goal_currency: Literal["VES", "USD", "EUR"]
    equivalents: dict[Literal["VES", "USD", "EUR"], Decimal]
    rate_date: date
    transport_source: str
    upstream_source: str
    referential: bool = True


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


class AyudaMonetariaDonanteRequest(BaseModel):
    account_id: int = Field(gt=0)
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: Literal["VES", "USD", "EUR"]
    transfer_date: date
    reference: Optional[str] = Field(default=None, max_length=500)
    comment: Optional[str] = Field(default=None, max_length=5000)
    receipt_file_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    receipt_content_type: Optional[Literal["application/pdf", "image/jpeg", "image/png"]] = None
    receipt_base64: Optional[str] = Field(default=None, min_length=8, max_length=7_100_000)

    @model_validator(mode="after")
    def validate_receipt_fields(self):
        receipt_fields = (
            self.receipt_file_name,
            self.receipt_content_type,
            self.receipt_base64,
        )
        if any(value is not None for value in receipt_fields) and not all(
            value is not None for value in receipt_fields
        ):
            raise ValueError("Los datos del comprobante deben enviarse completos")
        return self


class AyudaMonetariaDonanteResponse(BaseModel):
    id: int
    case_public_id: str
    account_version: int
    amount: Decimal
    currency: Literal["VES", "USD", "EUR"]
    transfer_date: date
    status: str
    receipt_attached: bool
    receipt_id: Optional[int] = None
    created_at: datetime


class AyudaMonetariaDonanteResumenResponse(AyudaMonetariaDonanteResponse):
    public_title: str


class OfertaAyudaDirectaCreateRequest(BaseModel):
    description: str = Field(min_length=10, max_length=2000)
    contact_details: str = Field(min_length=5, max_length=500)


class OfertaAyudaDirectaDonanteResponse(BaseModel):
    id: int
    case_public_id: str
    status: str
    created_at: datetime


class OfertaLaboralCreateRequest(BaseModel):
    tipo_trabajo: str = Field(min_length=2, max_length=120)
    descripcion: str = Field(min_length=10, max_length=2000)
    remuneracion_estimada: str = Field(min_length=2, max_length=250)
    telefono: str = Field(min_length=5, max_length=80)
    correo: str = Field(min_length=5, max_length=320)

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    @field_validator("correo")
    @classmethod
    def normalize_labor_email(cls, value):
        normalized = value.lower()
        if (
            normalized.count("@") != 1
            or "." not in normalized.rsplit("@", 1)[-1]
            or any(character.isspace() for character in normalized)
        ):
            raise ValueError("El correo no es valido")
        return normalized


class OfertaLaboralDonanteResponse(BaseModel):
    id: int
    case_public_id: str
    tipo: Literal["empleo"]
    status: Literal["pendiente_respuesta"]
    created_at: datetime


class OfertaAyudaDirectaOrganizacionResponse(BaseModel):
    id: int
    caso_id: int
    tipo: str
    estado: str
    description: str
    actor_donante_email: str
    gestionada_por: Optional[str] = None
    gestionada_en: Optional[datetime] = None
    created_at: datetime


class TransicionOfertaAyudaRequest(BaseModel):
    reason_code: Optional[str] = Field(default=None, max_length=100)


class AyudaMonetariaOrganizacionResumenResponse(BaseModel):
    aid_id: int
    reported_amount: Decimal
    reported_currency: Literal["VES", "USD", "EUR"]
    transfer_date: date
    status: str
    account_id: int
    account_version: int
    receipt_id: Optional[int] = None
    problem_type: Optional[str] = None
    problem_detail: Optional[str] = None
    resolution_reason: Optional[str] = None
    received_amount: Optional[Decimal] = None
    received_currency: Optional[Literal["VES", "USD", "EUR"]] = None
    goal_equivalent_amount: Optional[Decimal] = None
    applied_rate: Optional[Decimal] = None
    rate_source: Optional[str] = None
    rate_date: Optional[date] = None
    created_at: datetime


class ProblemaAyudaRequest(BaseModel):
    problem_type: Literal[
        "transferencia_no_recibida",
        "monto_diferente",
        "moneda_diferente",
        "cuenta_incorrecta",
        "comprobante_ilegible",
        "duplicado",
        "otro",
    ]
    detail: str = Field(min_length=3, max_length=5000)


class RechazoAyudaRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=5000)


class TasaBcvManualRequest(BaseModel):
    rate_date: date
    source_currency: Literal["VES", "USD", "EUR"]
    target_currency: Literal["VES", "USD", "EUR"]
    value: Decimal = Field(gt=0, max_digits=24, decimal_places=10)
    source_reference: str = Field(min_length=3, max_length=500)
    reason: str = Field(min_length=3, max_length=5000)


class TasaBcvResponse(BaseModel):
    id: int
    source: str
    rate_date: date
    source_currency: Literal["VES", "USD", "EUR"]
    target_currency: Literal["VES", "USD", "EUR"]
    value: Decimal
    registered_by: str


class ConfirmacionAyudaRequest(BaseModel):
    received_amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    received_currency: Literal["VES", "USD", "EUR"]
    effective_date: date
    comment: Optional[str] = Field(default=None, max_length=5000)


class ConfirmacionAyudaResponse(BaseModel):
    aid_id: int
    status: str
    received_amount: Decimal
    received_currency: Literal["VES", "USD", "EUR"]
    goal_equivalent_amount: Decimal
    goal_amount_confirmed: Decimal
    applied_rate: Optional[Decimal] = None
    rate_source: Optional[str] = None
    rate_date: Optional[date] = None
    confirmed_help_count: int
    case_status: str
    goal_reached: bool


class SolicitudAccesoTemporalRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    public_id: Optional[str] = Field(default=None, min_length=1, max_length=100)
    ip_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        normalized = value.strip().lower()
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("email invalido")
        return normalized


class CanjeAccesoTemporalRequest(BaseModel):
    challenge_token: str = Field(min_length=43, max_length=500)


class CanjeAccesoTemporalResponse(BaseModel):
    session_token: str
    expires_at: datetime


class AyudaAccesoTemporalResponse(BaseModel):
    aid_id: int
    reported_amount: Decimal
    reported_currency: Literal["VES", "USD", "EUR"]
    transfer_date: date
    status: str
    problem_type: Optional[str] = None
    receipt_id: Optional[int] = None


class CuentaAccesoTemporalResponse(BaseModel):
    account_id: int
    account_version: int
    medium: str
    currency: Literal["VES", "USD", "EUR"]
    identifier: str
    instructions: Optional[str] = None
    aids: List[AyudaAccesoTemporalResponse]


class CasoAccesoTemporalResponse(BaseModel):
    case_id: int
    public_id: str
    public_name: str
    public_title: str
    status: str
    goal_amount: Decimal
    goal_currency: Literal["VES", "USD", "EUR"]
    confirmed_amount: Decimal
    confirmed_help_count: int
    accounts: List[CuentaAccesoTemporalResponse]


class ContextoAccesoTemporalResponse(BaseModel):
    cases: List[CasoAccesoTemporalResponse]


class AccesoTemporalOrganizacionResponse(BaseModel):
    access_id: int
    status: str
    account_ids: List[int]
    created_at: datetime
    expires_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class RevocacionAccesoTemporalRequest(BaseModel):
    reason: Literal[
        "solicitud_responsable",
        "riesgo_seguridad",
        "cuenta_actualizada",
        "caso_cerrado",
        "otro",
    ]
