from sqlalchemy import Boolean, Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from database import Base

class PersonaDesaparecida(Base):
    __tablename__ = "personas_desaparecidas"
    id = Column(Integer, primary_key=True, index=True)
    nombres_apellidos = Column(String(200), nullable=False, index=True)
    cedula = Column(String(30), nullable=True, index=True)
    ultima_ubicacion = Column(String(500), nullable=False)
    foto_url = Column(String(600), nullable=True)
    foto_url_2 = Column(String(600), nullable=True)
    foto_url_3 = Column(String(600), nullable=True)
    descripcion = Column(Text, nullable=True)
    numero_contacto = Column(String(30), nullable=False)
    numero_contacto_2 = Column(String(30), nullable=True)
    quien_ayudo = Column(String(200), nullable=True)
    contacto_quien_ayudo = Column(String(30), nullable=True)
    estado = Column(String(20), nullable=False, default="desaparecido")
    tipo_reporte = Column(String(20), nullable=False, default="desaparecido")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    tipo_reportante = Column(String(30), nullable=True)
    sexo = Column(String(20), nullable=True)
    edad_aproximada = Column(String(20), nullable=True)
    contextura = Column(String(20), nullable=True)
    cabello = Column(String(20), nullable=True)
    ropa_aproximada = Column(String(200), nullable=True)
    estado_clinico = Column(String(30), nullable=True)
    sin_documentos = Column(Boolean, nullable=False, default=False)

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
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class PacienteHospitalizado(Base):
    __tablename__ = "pacientes_hospitalizados"
    id = Column(Integer, primary_key=True, index=True)
    hospital_id = Column(Integer, nullable=True, index=True)
    # nullable porque a veces no saben en qué hospital exacto
    nombre_hospital = Column(String(200), nullable=True)
    # Campo libre por si no está en el directorio
    nombres_apellidos = Column(String(200), nullable=False, index=True)
    edad = Column(String(20), nullable=True)
    sexo = Column(String(20), nullable=True)
    # valores: "masculino", "femenino", "no_determinado"
    cedula = Column(String(30), nullable=True, index=True)
    parentesco = Column(String(100), nullable=True)
    # Quién reporta y su relación con el paciente
    procedencia = Column(String(200), nullable=True)
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
    necesita_ayuda = Column(Boolean, nullable=False, default=False)
    tipo_ayuda = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    telefono = Column(String(30), nullable=True)
    nombre_variantes = Column(Text, nullable=True)
    batch_date = Column(String(20), nullable=True)
    fuente = Column(String(30), nullable=True, default="manual")

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
    activo = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

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
