from sqlalchemy import Boolean, Column, Integer, String, DateTime, Text, Float
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

class CentroAcopio(Base):
    __tablename__ = "centros_acopio"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(200), nullable=False, index=True)
    direccion = Column(String(500), nullable=True)
    zona = Column(String(100), nullable=True)
    activo = Column(Boolean, nullable=False, default=True, server_default='true')
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    latitud = Column(String(30), nullable=True)
    longitud = Column(String(30), nullable=True)

class AcopioReporte(Base):
    __tablename__ = "acopio_reportes"
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

class CentroAcopioSolicitud(Base):
    __tablename__ = "centros_acopio_solicitudes"
    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(200), nullable=False)
    direccion = Column(String(500), nullable=True)
    zona = Column(String(100), nullable=True)
    reportado_por = Column(String(200), nullable=True)
    notas = Column(Text, nullable=True)
    estado = Column(String(20), nullable=False, default="pendiente")
    # valores: "pendiente", "aprobado", "rechazado"
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    latitud = Column(String(30), nullable=True)
    longitud = Column(String(30), nullable=True)

class AcopioOrden(Base):
    __tablename__ = "acopio_ordenes"
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

class AcopioEntrega(Base):
    __tablename__ = "acopio_entregas"
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
