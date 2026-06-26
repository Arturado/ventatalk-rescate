from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from database import Base

class PersonaDesaparecida(Base):
    __tablename__ = "personas_desaparecidas"
    id = Column(Integer, primary_key=True, index=True)
    nombres_apellidos = Column(String(200), nullable=False, index=True)
    cedula = Column(String(30), nullable=True, index=True)
    ultima_ubicacion = Column(String(500), nullable=False)
    foto_url = Column(String(600), nullable=True)
    descripcion = Column(Text, nullable=True)
    numero_contacto = Column(String(30), nullable=False)
    quien_ayudo = Column(String(200), nullable=True)
    contacto_quien_ayudo = Column(String(30), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
