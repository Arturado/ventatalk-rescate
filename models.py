from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.sql import func
from database import Base

class PersonaDesaparecida(Base):
    __tablename__ = "personas_desaparecidas"
    id = Column(Integer, primary_key=True, index=True)
    nombres_apellidos = Column(String(500), nullable=False, index=True)
    cedula = Column(String(30), nullable=True, index=True)
    ultima_ubicacion = Column(String(500), nullable=False)
    foto_url = Column(String(600), nullable=True)
    foto_url_2 = Column(String(600), nullable=True)
    foto_url_3 = Column(String(600), nullable=True)
    descripcion = Column(Text, nullable=True)
    numero_contacto = Column(String(30), nullable=False)
    numero_contacto_2 = Column(String(30), nullable=True)
    quien_ayudo = Column(String(500), nullable=True)
    contacto_quien_ayudo = Column(String(30), nullable=True)
    estado = Column(String(20), nullable=False, default="desaparecido")
    tipo_reporte = Column(String(20), nullable=False, default="desaparecido")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    tipo_reportante = Column(String(30), nullable=True)
    sexo = Column(String(20), nullable=True)
    edad_aproximada = Column(String(20), nullable=True)
    contextura = Column(String(20), nullable=True)
    cabello = Column(String(20), nullable=True)
    ropa_aproximada = Column(String(500), nullable=True)
    estado_clinico = Column(String(30), nullable=True)
    sin_documentos = Column(Boolean, nullable=False, default=False, server_default='false')
    email_reportante = Column(String(200), nullable=True)
    es_menor = Column(Boolean, nullable=False, default=False, server_default='false')

class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    password_hash = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Hospital(Base):
    __tablename__ = "hospitales"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(200), nullable=False, index=True)
    direccion = Column(String(500), nullable=True)
    zona = Column(String(100), nullable=False, index=True)
    # zona valores: "Caracas", "Vargas", "Miranda", "Carabobo", "Falcon", "Yaracuy", "Aragua"
    telefono = Column(String(50), nullable=True)
    tipo = Column(String(50), nullable=True)
    # tipo valores: "publico", "privado", "clinica", "ambulatorio", "refugio_medico"
    activo = Column(Boolean, nullable=False, default=True, server_default='true')
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PacienteHospitalizado(Base):
    __tablename__ = "pacientes_hospitalizados"
    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, nullable=True, index=True)
    # nullable porque a veces no saben en qué hospital exacto
    nombre_hospital = Column(String(300), nullable=True)
    # Campo libre por si no está en el directorio
    nombres_apellidos = Column(String(300), nullable=False, index=True)
    edad = Column(String(20), nullable=True)
    sexo = Column(String(20), nullable=True)
    # valores: "masculino", "femenino", "no_determinado"
    cedula = Column(String(50), nullable=True, index=True)
    parentesco = Column(String(100), nullable=True)
    # Quién reporta y su relación con el paciente
    procedencia = Column(String(300), nullable=True)
    # De dónde viene el paciente
    observaciones = Column(Text, nullable=True)
    datos_adicionales = Column(Text, nullable=True)
    # JSON string con campos extra según el hospital
    foto_captura_url = Column(String(600), nullable=True)
    # Foto de lista/captura para transcripción manual
    estado_paciente = Column(String(30), nullable=False, default="ingresado")
    # valores: "ingresado", "estable", "grave", "critico", "alta", "trasladado", "fallecido"
    reportado_por = Column(String(200), nullable=True)
    # Nombre del médico/enfermero que carga (sin auth, voluntario)
    necesita_ayuda = Column(Boolean, nullable=False, default=False, server_default='false')
    tipo_ayuda = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    telefono = Column(String(100), nullable=True)
    nombre_variantes = Column(Text, nullable=True)
    batch_date = Column(String(20), nullable=True)
    fuente = Column(String(30), nullable=True, default="manual")
    es_menor = Column(Boolean, nullable=False, default=False, server_default='false')

class BomberoReporte(Base):
    __tablename__ = "bombero_reportes"
    id = Column(Integer, primary_key=True, index=True)
    identificador = Column(String(100), nullable=True)
    latitud = Column(String(20), nullable=False)
    longitud = Column(String(20), nullable=False)
    precision_metros = Column(String(20), nullable=True)
    status = Column(String(30), nullable=False, default="trabajando")
    descripcion = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PacienteMovimiento(Base):
    __tablename__ = "paciente_movimientos"
    id = Column(Integer, primary_key=True, index=True)
    paciente_id = Column(Integer, nullable=False, index=True)
    hospital_anterior = Column(String(200), nullable=True)
    hospital_nuevo = Column(String(200), nullable=True)
    estado_anterior = Column(String(30), nullable=True)
    estado_nuevo = Column(String(30), nullable=True)
    batch_date_anterior = Column(String(20), nullable=True)
    batch_date_nuevo = Column(String(20), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class Centro(Base):
    __tablename__ = "centros"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(200), nullable=False, index=True)
    tipo = Column(String(30), nullable=False, default="albergue", server_default="albergue")
    organizacion_id = Column(String(100), nullable=False, default="venezuela-rescate", server_default="venezuela-rescate")
    estado = Column(String(30), nullable=False, default="activo", server_default="activo")
    direccion = Column(String(500), nullable=True)
    zona = Column(String(100), nullable=True)
    reportado_por = Column(String(200), nullable=True)
    notas = Column(Text, nullable=True)
    activo = Column(Boolean, nullable=False, default=True, server_default='true')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    latitud = Column(String(30), nullable=True)
    longitud = Column(String(30), nullable=True)
    before_submit_version = Column(String(100), nullable=True)
    before_submit_title = Column(String(200), nullable=True)
    before_submit_description = Column(Text, nullable=True)
    before_submit_items_json = Column(Text, nullable=True)
    before_submit_confirmations_json = Column(Text, nullable=True)
    before_submit_acknowledged_at = Column(String(40), nullable=True)
    before_submit_source = Column(String(50), nullable=True)

class CentroReporte(Base):
    __tablename__ = "centro_reportes"
    id = Column(Integer, primary_key=True, index=True)
    centro_id = Column(Integer, nullable=False, index=True)
    # Población
    hombres = Column(Integer, nullable=True, default=0)
    mujeres = Column(Integer, nullable=True, default=0)
    ninos = Column(Integer, nullable=True, default=0)
    lactantes = Column(Integer, nullable=True, default=0)
    # Necesidades — lista separada por comas de items seleccionados
    necesitan = Column(Text, nullable=True)
    no_necesitan = Column(Text, nullable=True)
    # Items adicionales escritos manualmente
    necesitan_extra = Column(Text, nullable=True)
    no_necesitan_extra = Column(Text, nullable=True)
    # Notas libres
    notas = Column(Text, nullable=True)
    # Quien reporta
    reportado_por = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PersonaNotificacionLocalizado(Base):
    __tablename__ = "persona_notificaciones_localizado"
    id = Column(Integer, primary_key=True, index=True)
    persona_id = Column(Integer, nullable=False, index=True)
    nombre_reportante = Column(String(200), nullable=False)
    numero_contacto = Column(String(30), nullable=False)
    descripcion = Column(Text, nullable=True)
    foto_url = Column(String(600), nullable=True)
    estado = Column(String(20), nullable=False, default="pendiente")
    # valores: "pendiente", "confirmado", "rechazado"
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CentroSolicitud(Base):
    __tablename__ = "centros_solicitudes"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(200), nullable=False)
    organizacion_id = Column(String(100), nullable=False, default="venezuela-rescate", server_default="venezuela-rescate")
    direccion = Column(String(500), nullable=True)
    zona = Column(String(100), nullable=True)
    reportado_por = Column(String(200), nullable=True)
    notas = Column(Text, nullable=True)
    estado = Column(String(20), nullable=False, default="pendiente")
    # valores: "pendiente", "aprobado", "rechazado"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    latitud = Column(String(30), nullable=True)
    longitud = Column(String(30), nullable=True)
    before_submit_version = Column(String(100), nullable=True)
    before_submit_title = Column(String(200), nullable=True)
    before_submit_description = Column(Text, nullable=True)
    before_submit_items_json = Column(Text, nullable=True)
    before_submit_confirmations_json = Column(Text, nullable=True)
    before_submit_acknowledged_at = Column(String(40), nullable=True)
    before_submit_source = Column(String(50), nullable=True)

class CentroResponsable(Base):
    __tablename__ = "centro_responsables"
    id = Column(Integer, primary_key=True, index=True)
    centro_id = Column(Integer, nullable=False, index=True)
    usuario_id = Column(String(200), nullable=False, index=True)
    rol_en_centro = Column(String(30), nullable=False, default="coordinador", server_default="coordinador")
    estado = Column(String(20), nullable=False, default="activo", server_default="activo")
    asignado_por_id = Column(String(200), nullable=True)
    asignado_en = Column(DateTime(timezone=True), server_default=func.now())
    removido_por_id = Column(String(200), nullable=True)
    removido_en = Column(DateTime(timezone=True), nullable=True)

class CentroHistorial(Base):
    __tablename__ = "centro_historial"
    id = Column(Integer, primary_key=True, index=True)
    centro_id = Column(Integer, nullable=False, index=True)
    tipo_cambio = Column(String(50), nullable=False)
    valor_anterior = Column(String(200), nullable=True)
    valor_nuevo = Column(String(200), nullable=True)
    cambiado_por = Column(String(200), nullable=True)
    descripcion = Column(Text, nullable=True)
    cambiado_en = Column(DateTime(timezone=True), server_default=func.now())

class CentroOrden(Base):
    __tablename__ = "centro_ordenes"
    id = Column(Integer, primary_key=True, index=True)
    reporte_id = Column(Integer, nullable=False, index=True)
    centro_id = Column(Integer, nullable=False, index=True)
    items_ordenados = Column(Text, nullable=False)
    # JSON string: [{"item": "Agua potable", "cantidad": 20}, ...]
    nota_repartidor = Column(Text, nullable=True)
    estado = Column(String(20), nullable=False, default="preparando")
    # valores: "preparando", "en_camino", "entregado"
    creado_por = Column(String(200), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CentroEntrega(Base):
    __tablename__ = "centro_entregas"
    id = Column(Integer, primary_key=True, index=True)
    orden_id = Column(Integer, nullable=False, index=True, unique=True)
    nombre_receptor = Column(String(200), nullable=False)
    foto_entrega_url = Column(String(600), nullable=True)
    notas_entrega = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class CasoAyuda(Base):
    __tablename__ = "casos_ayuda"
    id = Column(Integer, primary_key=True, index=True)
    cedula = Column(String(20), nullable=False, index=True)
    nombre = Column(String(200), nullable=False)
    relato = Column(Text, nullable=False)
    condicion_resumen = Column(String(500), nullable=False)
    monto_necesario = Column(Float, nullable=True)
    moneda = Column(String(10), nullable=True)
    estado = Column(String(30), nullable=False, default="pendiente_revision")
    # Estados: pendiente_revision | publicado | rechazado |
    # necesita_actualizacion | resuelto | archivado
    nivel_verificacion = Column(String(20), nullable=False, default="basico")
    # Niveles: basico | institucional | medico
    avalado_por = Column(String(200), nullable=True)
    avalado_en = Column(DateTime(timezone=True), nullable=True)
    adjuntos = Column(Text, nullable=True)
    # JSON array de URLs: ["https://...", "https://..."]
    consentimiento_publicacion = Column(Boolean, nullable=False, default=False, server_default='false')
    fecha_ultima_confirmacion = Column(DateTime(timezone=True),
        server_default=func.now())
    creado_por = Column(String(200), nullable=False, default="anonimo")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now())

class ContactoCasoAyuda(Base):
    __tablename__ = "contacto_casos_ayuda"
    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, nullable=False, unique=True, index=True)
    telefono = Column(String(30), nullable=True)
    metodo_pago = Column(String(100), nullable=True)
    datos_pago = Column(Text, nullable=True)
    contacto_alterno = Column(String(200), nullable=True)

class CasoAyudaHistorial(Base):
    __tablename__ = "casos_ayuda_historial"
    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, nullable=False, index=True)
    tipo_cambio = Column(String(20), nullable=False)
    # valores: "estado" | "nivel"
    valor_anterior = Column(String(50), nullable=True)
    valor_nuevo = Column(String(50), nullable=True)
    cambiado_por = Column(String(200), nullable=True)
    descripcion = Column(Text, nullable=True)
    cambiado_en = Column(DateTime(timezone=True), server_default=func.now())


class BeneficiarioAyuda(Base):
    __tablename__ = "beneficiarios_ayuda"
    __table_args__ = (
        UniqueConstraint(
            "organizacion_id",
            "cedula_hash",
            name="uq_beneficiarios_ayuda_org_cedula_hash",
        ),
        UniqueConstraint(
            "id",
            "organizacion_id",
            name="uq_beneficiarios_ayuda_id_org",
        ),
        CheckConstraint("organizacion_id <> ''", name="ck_beneficiarios_ayuda_org"),
        CheckConstraint("length(cedula_hash) = 64", name="ck_beneficiarios_ayuda_cedula_hash"),
    )

    id = Column(Integer, primary_key=True, index=True)
    organizacion_id = Column(String(100), nullable=False, index=True)
    nombres_apellidos_cifrado = Column(Text, nullable=False)
    cedula_hash = Column(String(64), nullable=False, index=True)
    cedula_cifrada = Column(Text, nullable=False)
    telefono_cifrado = Column(Text, nullable=True)
    correo_cifrado = Column(Text, nullable=True)
    direccion_cifrada = Column(Text, nullable=True)
    datos_verificacion_cifrado = Column(Text, nullable=True)
    es_menor = Column(Boolean, nullable=False, default=False, server_default="false")
    representante_nombre_cifrado = Column(Text, nullable=True)
    representante_relacion = Column(String(100), nullable=True)
    representante_autoridad_verificada_en = Column(DateTime(timezone=True), nullable=True)
    representante_autoridad_verificada_por = Column(String(200), nullable=True)
    creado_por = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CasoAyudaV2(Base):
    __tablename__ = "casos_ayuda_v2"
    __table_args__ = (
        ForeignKeyConstraint(
            ["beneficiario_id", "organizacion_id"],
            ["beneficiarios_ayuda.id", "beneficiarios_ayuda.organizacion_id"],
            name="fk_casos_ayuda_v2_beneficiario_org",
            ondelete="RESTRICT",
        ),
        CheckConstraint("organizacion_id <> ''", name="ck_casos_ayuda_v2_org"),
        CheckConstraint("meta_monto > 0", name="ck_casos_ayuda_v2_meta_positiva"),
        CheckConstraint("meta_moneda IN ('VES', 'USD', 'EUR')", name="ck_casos_ayuda_v2_moneda"),
        CheckConstraint(
            "estado IN ('borrador', 'pendiente_validacion', 'listo_publicar', 'publicado', "
            "'pausado', 'meta_alcanzada', 'cerrado', 'rechazado', 'suspendido', 'archivado')",
            name="ck_casos_ayuda_v2_estado",
        ),
        CheckConstraint("monto_confirmado >= 0", name="ck_casos_ayuda_v2_confirmado"),
        CheckConstraint("ayudas_confirmadas >= 0", name="ck_casos_ayuda_v2_contador"),
    )

    id = Column(Integer, primary_key=True, index=True)
    public_id = Column(String(100), nullable=False, unique=True, index=True)
    organizacion_id = Column(String(100), nullable=False, index=True)
    beneficiario_id = Column(Integer, nullable=False, index=True)
    titulo_interno = Column(String(250), nullable=False)
    relato_privado_cifrado = Column(Text, nullable=True)
    categoria = Column(String(80), nullable=False, index=True)
    meta_monto = Column(Numeric(18, 2), nullable=False)
    meta_moneda = Column(String(3), nullable=False)
    monto_confirmado = Column(Numeric(18, 2), nullable=False, default=0, server_default="0")
    ayudas_confirmadas = Column(Integer, nullable=False, default=0, server_default="0")
    estado = Column(String(30), nullable=False, default="borrador", server_default="borrador", index=True)
    prioridad_especial = Column(Boolean, nullable=False, default=False, server_default="false")
    estado_anterior_suspension = Column(String(30), nullable=True)
    creado_por = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    publicado_at = Column(DateTime(timezone=True), nullable=True)
    cerrado_at = Column(DateTime(timezone=True), nullable=True)


class PublicacionCasoAyuda(Base):
    __tablename__ = "casos_ayuda_publicaciones"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_casos_ayuda_publicaciones_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=False, unique=True, index=True)
    nombre_publico = Column(String(200), nullable=False)
    titulo_publico = Column(String(250), nullable=False)
    descripcion_publica = Column(Text, nullable=False)
    localidad_general = Column(String(200), nullable=True)
    redes_sociales_json = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default="1")
    activa = Column(Boolean, nullable=False, default=False, server_default="false")
    configurado_por = Column(String(200), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConsentimientoCasoAyuda(Base):
    __tablename__ = "casos_ayuda_consentimientos"
    __table_args__ = (
        UniqueConstraint("caso_id", "version", name="uq_casos_ayuda_consentimiento_version"),
        CheckConstraint("version > 0", name="ck_casos_ayuda_consentimiento_version"),
    )

    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    texto_version = Column(String(100), nullable=False)
    alcance_json = Column(Text, nullable=False)
    firmante_nombre_cifrado = Column(Text, nullable=False)
    firmante_tipo = Column(String(30), nullable=False, default="beneficiario", server_default="beneficiario")
    evidencia_documento_id = Column(Integer, nullable=True, index=True)
    fecha_consentimiento = Column(DateTime(timezone=True), server_default=func.now())
    registrado_por = Column(String(200), nullable=False)
    vigente = Column(Boolean, nullable=False, default=True, server_default="true")
    retirado_at = Column(DateTime(timezone=True), nullable=True)
    retirado_por = Column(String(200), nullable=True)
    motivo_retiro = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DocumentoCasoAyuda(Base):
    __tablename__ = "casos_ayuda_documentos"
    __table_args__ = (
        UniqueConstraint(
            "caso_id",
            "document_key",
            "version",
            name="uq_casos_ayuda_documento_version",
        ),
        CheckConstraint("version > 0", name="ck_casos_ayuda_documento_version"),
        CheckConstraint("clasificacion IN ('publico', 'privado')", name="ck_casos_ayuda_documento_clasificacion"),
        CheckConstraint(
            "estado_revision IN ('pendiente', 'aprobado', 'rechazado', 'retirado')",
            name="ck_casos_ayuda_documento_estado",
        ),
        CheckConstraint("size_bytes > 0", name="ck_casos_ayuda_documento_size"),
        CheckConstraint("length(checksum_sha256) = 64", name="ck_casos_ayuda_documento_checksum"),
    )

    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=False, index=True)
    document_key = Column(String(100), nullable=False)
    version = Column(Integer, nullable=False)
    tipo = Column(String(80), nullable=False, index=True)
    clasificacion = Column(String(20), nullable=False, index=True)
    estado_revision = Column(String(20), nullable=False, default="pendiente", server_default="pendiente")
    storage_path = Column(String(600), nullable=False, unique=True)
    nombre_original_cifrado = Column(Text, nullable=True)
    content_type = Column(String(150), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    checksum_sha256 = Column(String(64), nullable=False)
    consentimiento_version = Column(Integer, nullable=True)
    cargado_por = Column(String(200), nullable=False)
    revisado_por = Column(String(200), nullable=True)
    revisado_at = Column(DateTime(timezone=True), nullable=True)
    motivo_revision = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class CuentaCasoAyuda(Base):
    __tablename__ = "casos_ayuda_cuentas"
    __table_args__ = (
        UniqueConstraint(
            "caso_id",
            "account_key",
            "version",
            name="uq_casos_ayuda_cuenta_version",
        ),
        UniqueConstraint(
            "id",
            "caso_id",
            "version",
            name="uq_casos_ayuda_cuenta_id_caso_version",
        ),
        CheckConstraint("version > 0", name="ck_casos_ayuda_cuenta_version"),
        CheckConstraint(
            "tipo_titular IN ('beneficiario', 'organizacion', 'tercero', 'coordinador')",
            name="ck_casos_ayuda_cuenta_tipo_titular",
        ),
        CheckConstraint(
            "medio IN ('banco_venezolano', 'zelle', 'banco_internacional')",
            name="ck_casos_ayuda_cuenta_medio",
        ),
        CheckConstraint("moneda IN ('VES', 'USD', 'EUR')", name="ck_casos_ayuda_cuenta_moneda"),
        CheckConstraint(
            "estado IN ('pendiente', 'aprobada', 'inactiva')",
            name="ck_casos_ayuda_cuenta_estado",
        ),
        CheckConstraint("consentimiento_version > 0", name="ck_casos_ayuda_cuenta_consentimiento"),
        CheckConstraint(
            "length(responsable_email_hash) = 64",
            name="ck_casos_ayuda_cuenta_responsable_hash",
        ),
        CheckConstraint(
            "estado <> 'aprobada' OR tipo_titular NOT IN ('tercero', 'coordinador') OR "
            "(justificacion_cifrada IS NOT NULL AND aprobado_por IS NOT NULL AND aprobado_por <> creado_por)",
            name="ck_casos_ayuda_cuenta_aprobacion_excepcional",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_key = Column(String(100), nullable=False)
    version = Column(Integer, nullable=False)
    tipo_titular = Column(String(20), nullable=False)
    titular_nombre_cifrado = Column(Text, nullable=False)
    relacion_beneficiario = Column(String(150), nullable=False)
    medio = Column(String(30), nullable=False)
    moneda = Column(String(3), nullable=False)
    identificador_cifrado = Column(Text, nullable=False)
    instrucciones_cifrado = Column(Text, nullable=True)
    justificacion_cifrada = Column(Text, nullable=True)
    responsable_nombre_cifrado = Column(Text, nullable=False)
    responsable_email_hash = Column(String(64), nullable=False, index=True)
    responsable_email_cifrado = Column(Text, nullable=False)
    consentimiento_version = Column(Integer, nullable=False)
    estado = Column(String(20), nullable=False, default="pendiente", server_default="pendiente", index=True)
    creado_por = Column(String(200), nullable=False)
    aprobado_por = Column(String(200), nullable=True)
    aprobado_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AyudaMonetaria(Base):
    __tablename__ = "casos_ayuda_ayudas"
    __table_args__ = (
        ForeignKeyConstraint(
            ["cuenta_id", "caso_id", "cuenta_version"],
            ["casos_ayuda_cuentas.id", "casos_ayuda_cuentas.caso_id", "casos_ayuda_cuentas.version"],
            name="fk_casos_ayuda_ayuda_cuenta_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint("monto_reportado > 0", name="ck_casos_ayuda_ayuda_monto"),
        CheckConstraint(
            "moneda_reportada IN ('VES', 'USD', 'EUR')",
            name="ck_casos_ayuda_ayuda_moneda",
        ),
        CheckConstraint(
            "estado IN ('pendiente_confirmacion', 'confirmada', 'problema_reportado', "
            "'en_revision', 'rechazada', 'cancelada')",
            name="ck_casos_ayuda_ayuda_estado",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=False, index=True)
    cuenta_id = Column(Integer, nullable=False, index=True)
    cuenta_version = Column(Integer, nullable=False)
    donante_id = Column(String(200), nullable=False, index=True)
    monto_reportado = Column(Numeric(18, 2), nullable=False)
    moneda_reportada = Column(String(3), nullable=False)
    fecha_transferencia = Column(Date, nullable=False)
    referencia_cifrada = Column(Text, nullable=True)
    comentario_cifrado = Column(Text, nullable=True)
    estado = Column(
        String(30),
        nullable=False,
        default="pendiente_confirmacion",
        server_default="pendiente_confirmacion",
        index=True,
    )
    idempotency_id = Column(
        Integer,
        ForeignKey("casos_ayuda_idempotencia.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    problema_tipo = Column(String(50), nullable=True)
    problema_detalle_cifrado = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ComprobanteAyuda(Base):
    __tablename__ = "casos_ayuda_comprobantes"
    __table_args__ = (
        UniqueConstraint("ayuda_id", "version", name="uq_casos_ayuda_comprobante_version"),
        CheckConstraint("version > 0", name="ck_casos_ayuda_comprobante_version"),
        CheckConstraint("storage_path LIKE 'private/%'", name="ck_casos_ayuda_comprobante_privado"),
        CheckConstraint("size_bytes > 0", name="ck_casos_ayuda_comprobante_size"),
        CheckConstraint("length(checksum_sha256) = 64", name="ck_casos_ayuda_comprobante_checksum"),
        CheckConstraint(
            "estado IN ('vigente', 'reemplazado', 'rechazado')",
            name="ck_casos_ayuda_comprobante_estado",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    ayuda_id = Column(Integer, ForeignKey("casos_ayuda_ayudas.id", ondelete="RESTRICT"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    storage_path = Column(String(600), nullable=False, unique=True)
    nombre_original_cifrado = Column(Text, nullable=True)
    content_type = Column(String(150), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    checksum_sha256 = Column(String(64), nullable=False)
    estado = Column(String(20), nullable=False, default="vigente", server_default="vigente")
    cargado_por = Column(String(200), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TasaCambioAyuda(Base):
    __tablename__ = "casos_ayuda_tasas"
    __table_args__ = (
        UniqueConstraint(
            "fuente",
            "fecha_tasa",
            "moneda_base",
            "moneda_cotizada",
            name="uq_casos_ayuda_tasa_snapshot",
        ),
        CheckConstraint("valor > 0", name="ck_casos_ayuda_tasa_valor"),
        CheckConstraint(
            "moneda_base IN ('VES', 'USD', 'EUR') AND moneda_cotizada IN ('VES', 'USD', 'EUR')",
            name="ck_casos_ayuda_tasa_monedas",
        ),
        CheckConstraint("moneda_base <> moneda_cotizada", name="ck_casos_ayuda_tasa_par"),
    )

    id = Column(Integer, primary_key=True, index=True)
    fuente = Column(String(50), nullable=False)
    fecha_tasa = Column(Date, nullable=False, index=True)
    moneda_base = Column(String(3), nullable=False)
    moneda_cotizada = Column(String(3), nullable=False)
    valor = Column(Numeric(24, 10), nullable=False)
    evidencia_json = Column(Text, nullable=False)
    consultado_at = Column(DateTime(timezone=True), server_default=func.now())
    registrado_manualmente_por = Column(String(200), nullable=True)
    motivo_manual = Column(Text, nullable=True)


class ConfirmacionAyuda(Base):
    __tablename__ = "casos_ayuda_confirmaciones"
    __table_args__ = (
        CheckConstraint("monto_recibido > 0", name="ck_casos_ayuda_confirmacion_monto"),
        CheckConstraint("monto_meta_equivalente > 0", name="ck_casos_ayuda_confirmacion_equivalente"),
        CheckConstraint(
            "moneda_recibida IN ('VES', 'USD', 'EUR')",
            name="ck_casos_ayuda_confirmacion_moneda",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    ayuda_id = Column(
        Integer,
        ForeignKey("casos_ayuda_ayudas.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    idempotency_id = Column(
        Integer,
        ForeignKey("casos_ayuda_idempotencia.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    monto_recibido = Column(Numeric(18, 2), nullable=False)
    moneda_recibida = Column(String(3), nullable=False)
    monto_meta_equivalente = Column(Numeric(18, 2), nullable=False)
    tasa_id = Column(Integer, ForeignKey("casos_ayuda_tasas.id", ondelete="RESTRICT"), nullable=True, index=True)
    confirmado_por = Column(String(200), nullable=False)
    fecha_transferencia_confirmada = Column(Date, nullable=False)
    comentario_cifrado = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AccesoTemporalAyuda(Base):
    __tablename__ = "casos_ayuda_accesos"
    __table_args__ = (
        CheckConstraint(
            "challenge_ttl_seconds > 0 AND challenge_ttl_seconds <= 900",
            name="ck_casos_ayuda_acceso_challenge_ttl",
        ),
        CheckConstraint(
            "session_ttl_seconds > 0 AND session_ttl_seconds <= 86400",
            name="ck_casos_ayuda_acceso_session_ttl",
        ),
        CheckConstraint(
            "length(destinatario_email_hash) = 64 AND length(challenge_hash) = 64",
            name="ck_casos_ayuda_acceso_hashes",
        ),
        CheckConstraint(
            "session_hash IS NULL OR length(session_hash) = 64",
            name="ck_casos_ayuda_acceso_session_hash",
        ),
        CheckConstraint(
            "estado IN ('pendiente', 'activo', 'consumido', 'expirado', 'revocado')",
            name="ck_casos_ayuda_acceso_estado",
        ),
        CheckConstraint(
            "estado <> 'activo' OR (session_hash IS NOT NULL AND session_expires_at IS NOT NULL "
            "AND challenge_consumed_at IS NOT NULL)",
            name="ck_casos_ayuda_acceso_sesion_activa",
        ),
        CheckConstraint(
            "estado <> 'revocado' OR (revocado_at IS NOT NULL AND revocado_por IS NOT NULL "
            "AND motivo_revocacion IS NOT NULL)",
            name="ck_casos_ayuda_acceso_revocacion",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    destinatario_email_hash = Column(String(64), nullable=False, index=True)
    destinatario_email_cifrado = Column(Text, nullable=False)
    challenge_hash = Column(String(64), nullable=False, unique=True)
    challenge_expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    challenge_ttl_seconds = Column(Integer, nullable=False, default=900, server_default="900")
    challenge_consumed_at = Column(DateTime(timezone=True), nullable=True)
    session_hash = Column(String(64), nullable=True, unique=True)
    session_expires_at = Column(DateTime(timezone=True), nullable=True, index=True)
    session_ttl_seconds = Column(Integer, nullable=False, default=86400, server_default="86400")
    estado = Column(String(20), nullable=False, default="pendiente", server_default="pendiente", index=True)
    solicitado_por = Column(String(200), nullable=False)
    intentos_fallidos = Column(Integer, nullable=False, default=0, server_default="0")
    revocado_at = Column(DateTime(timezone=True), nullable=True)
    revocado_por = Column(String(200), nullable=True)
    motivo_revocacion = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AlcanceCuentaAccesoTemporal(Base):
    __tablename__ = "casos_ayuda_acceso_cuentas"
    __table_args__ = (
        UniqueConstraint(
            "acceso_id",
            "cuenta_id",
            "cuenta_version",
            name="uq_casos_ayuda_acceso_cuenta_version",
        ),
        ForeignKeyConstraint(
            ["cuenta_id", "caso_id", "cuenta_version"],
            ["casos_ayuda_cuentas.id", "casos_ayuda_cuentas.caso_id", "casos_ayuda_cuentas.version"],
            name="fk_casos_ayuda_acceso_cuenta_version",
            ondelete="RESTRICT",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    acceso_id = Column(
        Integer,
        ForeignKey("casos_ayuda_accesos.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=False, index=True)
    cuenta_id = Column(Integer, nullable=False, index=True)
    cuenta_version = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditoriaCasoAyuda(Base):
    __tablename__ = "casos_ayuda_auditoria"
    __table_args__ = (
        CheckConstraint(
            "actor_tipo IN ('donante', 'responsable_temporal', 'coordinador', 'admin', 'super_admin', 'sistema')",
            name="ck_casos_ayuda_auditoria_actor_tipo",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    event_id = Column(String(100), nullable=False, unique=True)
    organizacion_id = Column(String(100), nullable=False, index=True)
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=True, index=True)
    accion = Column(String(100), nullable=False, index=True)
    actor_id = Column(String(200), nullable=False, index=True)
    actor_tipo = Column(String(30), nullable=False)
    entidad_tipo = Column(String(50), nullable=False, index=True)
    entidad_id = Column(String(100), nullable=False, index=True)
    motivo_codigo = Column(String(100), nullable=True)
    metadata_json = Column(Text, nullable=False, default="{}", server_default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


@event.listens_for(AuditoriaCasoAyuda, "before_update")
@event.listens_for(AuditoriaCasoAyuda, "before_delete")
def _prevent_audit_mutation(*_):
    raise ValueError("Los eventos de auditoria son inmutables")


class NotificacionCasoAyuda(Base):
    __tablename__ = "casos_ayuda_notificaciones"
    __table_args__ = (
        CheckConstraint(
            "evento_tipo IN ('ayuda_reportada', 'ayuda_confirmada', 'problema_reportado', "
            "'revision_resuelta', 'meta_alcanzada', 'acceso_temporal')",
            name="ck_casos_ayuda_notificacion_evento",
        ),
        CheckConstraint(
            "destinatario_tipo IN ('beneficiario', 'responsable', 'donante', 'coordinador', 'admin')",
            name="ck_casos_ayuda_notificacion_destinatario",
        ),
        CheckConstraint(
            "estado IN ('pendiente', 'procesando', 'enviada', 'fallida', 'cancelada')",
            name="ck_casos_ayuda_notificacion_estado",
        ),
        CheckConstraint("intentos >= 0", name="ck_casos_ayuda_notificacion_intentos"),
        CheckConstraint(
            "length(destinatario_email_hash) = 64",
            name="ck_casos_ayuda_notificacion_email_hash",
        ),
        CheckConstraint(
            "estado <> 'enviada' OR (proveedor IS NOT NULL AND proveedor_referencia IS NOT NULL "
            "AND enviada_at IS NOT NULL)",
            name="ck_casos_ayuda_notificacion_enviada",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    deduplication_key = Column(String(200), nullable=False, unique=True)
    evento_tipo = Column(String(40), nullable=False, index=True)
    organizacion_id = Column(String(100), nullable=True, index=True)
    caso_id = Column(Integer, ForeignKey("casos_ayuda_v2.id", ondelete="RESTRICT"), nullable=True, index=True)
    destinatario_tipo = Column(String(30), nullable=False)
    destinatario_email_hash = Column(String(64), nullable=False, index=True)
    destinatario_email_cifrado = Column(Text, nullable=False)
    template_key = Column(String(100), nullable=False)
    payload_json = Column(Text, nullable=False)
    estado = Column(String(20), nullable=False, default="pendiente", server_default="pendiente", index=True)
    intentos = Column(Integer, nullable=False, default=0, server_default="0")
    proximo_intento_at = Column(DateTime(timezone=True), nullable=True, index=True)
    proveedor = Column(String(100), nullable=True)
    proveedor_referencia = Column(String(200), nullable=True)
    ultimo_error_codigo = Column(String(100), nullable=True)
    enviada_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OperacionIdempotenteAyuda(Base):
    __tablename__ = "casos_ayuda_idempotencia"
    __table_args__ = (
        UniqueConstraint(
            "actor_id",
            "operacion",
            "idempotency_key",
            name="uq_casos_ayuda_idempotencia_scope",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(String(200), nullable=False, index=True)
    organizacion_id = Column(String(100), nullable=True, index=True)
    operacion = Column(String(40), nullable=False, index=True)
    idempotency_key = Column(String(128), nullable=False)
    request_hash = Column(String(64), nullable=False)
    estado = Column(String(20), nullable=False, default="processing", server_default="processing")
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
