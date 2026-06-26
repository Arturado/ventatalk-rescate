from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import models
from database import engine
from routers import personas, reportes

# Crear tablas al arrancar
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Venezuela Rescate — API",
    description="API para reporte y búsqueda de personas desaparecidas post-sismo.",
    version="1.0.0",
)

# CORS abierto — los reportes vienen del widget embebido en cualquier dominio
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

# Archivos estáticos: fotos subidas + widget JS
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/widget", StaticFiles(directory="static"), name="widget")

# Routers
app.include_router(reportes.router)
app.include_router(personas.router)


@app.get("/health", tags=["sistema"])
def health():
    return {"status": "ok", "service": "ventatalk-rescate"}
