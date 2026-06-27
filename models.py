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
