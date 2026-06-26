import csv
import io
import os
from datetime import datetime, timezone

import bcrypt
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

import models
from database import get_db

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="templates")

COOKIE_NAME = "rescate_session"
SESSION_MAX_AGE = 8 * 3600  # 8 horas en segundos

def get_serializer():
    secret = os.getenv("SECRET_KEY", "change-me")
    return URLSafeTimedSerializer(secret)

def get_current_admin(request: Request):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    try:
        data = get_serializer().loads(token, max_age=SESSION_MAX_AGE)
        return data.get("username")
    except (SignatureExpired, BadSignature):
        return None

def require_admin(request: Request):
    user = get_current_admin(request)
    if not user:
        raise RedirectToLogin()
    return user

class RedirectToLogin(Exception):
    pass

# --- Login ---

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})

@router.post("/login")
@limiter.limit("5/15minutes")
async def login_post(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
    if user and bcrypt.checkpw(password.encode(), user.password_hash.encode()):
        token = get_serializer().dumps({"username": username})
        response = RedirectResponse(url="/admin/dashboard", status_code=303)
        response.set_cookie(
            COOKIE_NAME, token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            secure=False,  # True en producción con HTTPS
            samesite="lax",
        )
        return response
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request, "error": "Usuario o contraseña incorrectos"},
        status_code=401,
    )

@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response

# --- Dashboard ---

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)

    personas = db.query(models.PersonaDesaparecida).order_by(
        models.PersonaDesaparecida.created_at.desc()
    ).all()

    hoy = datetime.now(timezone.utc).date()
    total = len(personas)
    hoy_count = sum(1 for p in personas if p.created_at and p.created_at.date() == hoy)
    con_foto = sum(1 for p in personas if p.foto_url)
    encontrados = sum(1 for p in personas if p.estado == "encontrado")
    en_proceso = sum(1 for p in personas if p.estado == "en_proceso")

    return templates.TemplateResponse("admin/dashboard.html", {
        "request": request,
        "admin": admin,
        "personas": personas,
        "total": total,
        "hoy": hoy_count,
        "con_foto": con_foto,
        "encontrados": encontrados,
        "en_proceso": en_proceso,
    })

# --- Cambiar estado ---

@router.post("/personas/{persona_id}/estado")
async def cambiar_estado(
    persona_id: int,
    request: Request,
    estado: str = Form(...),
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)

    if estado not in ("desaparecido", "encontrado", "en_proceso"):
        return RedirectResponse(url="/admin/dashboard", status_code=303)

    persona = db.query(models.PersonaDesaparecida).filter(
        models.PersonaDesaparecida.id == persona_id
    ).first()
    if persona:
        persona.estado = estado
        db.commit()
    return RedirectResponse(url="/admin/dashboard", status_code=303)

# --- Eliminar persona ---

@router.delete("/personas/{persona_id}")
async def eliminar_persona(
    persona_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    admin = get_current_admin(request)
    if not admin:
        raise HTTPException(status_code=401, detail="No autorizado")

    persona = db.query(models.PersonaDesaparecida).filter(
        models.PersonaDesaparecida.id == persona_id
    ).first()
    if not persona:
        raise HTTPException(status_code=404, detail="No encontrado")
    db.delete(persona)
    db.commit()
    return {"ok": True}

# --- Export CSV ---

@router.get("/export/csv")
async def export_csv(request: Request, db: Session = Depends(get_db)):
    admin = get_current_admin(request)
    if not admin:
        return RedirectResponse(url="/admin/login", status_code=303)

    personas = db.query(models.PersonaDesaparecida).order_by(
        models.PersonaDesaparecida.created_at.desc()
    ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Nombres y Apellidos", "Cédula", "Última Ubicación",
        "Foto URL", "Descripción", "Número Contacto",
        "Quién Ayudó", "Contacto Quien Ayudó", "Estado", "Fecha Registro"
    ])
    for p in personas:
        writer.writerow([
            p.id, p.nombres_apellidos, p.cedula or "", p.ultima_ubicacion,
            p.foto_url or "", p.descripcion or "", p.numero_contacto,
            p.quien_ayudo or "", p.contacto_quien_ayudo or "",
            p.estado, p.created_at.strftime("%Y-%m-%d %H:%M:%S") if p.created_at else "",
        ])

    output.seek(0)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=rescate_{fecha}.csv"},
    )
