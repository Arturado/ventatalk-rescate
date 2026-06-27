import os
import bcrypt
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

import models
from database import engine, SessionLocal
from routers import personas, reportes
from routers.admin import router as admin_router
from routers.stats import router as stats_router, public_router as stats_public_router

templates = Jinja2Templates(directory="templates")
limiter = Limiter(key_func=get_remote_address)


def seed_admin_users(db: Session):
    pairs = [
        (os.getenv("ADMIN_1_USER"), os.getenv("ADMIN_1_PASS")),
        (os.getenv("ADMIN_2_USER"), os.getenv("ADMIN_2_PASS")),
        (os.getenv("ADMIN_3_USER"), os.getenv("ADMIN_3_PASS")),
    ]
    for username, password in pairs:
        if not username or not password:
            continue
        exists = db.query(models.AdminUser).filter(models.AdminUser.username == username).first()
        if not exists:
            hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            db.add(models.AdminUser(username=username, password_hash=hashed))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_admin_users(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="Venezuela Rescate API",
    description="API de emergencias post-sismo Venezuela 2026",
    version="1.0.0",
    docs_url="/cowboy",
    redoc_url=None,
    openapi_url="/cowboy/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://venezuelarescate.com",
        "https://www.venezuelarescate.com",
        "https://rescate.ventatalk.com",
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:4173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")
app.mount("/widget", StaticFiles(directory="static"), name="widget")
app.mount("/static", StaticFiles(directory="static"), name="static-files")

app.include_router(reportes.router)
app.include_router(reportes.check_router)
app.include_router(personas.router)
app.include_router(personas.matches_router)
app.include_router(stats_router)
app.include_router(stats_public_router)
app.include_router(admin_router)


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/chatbot", response_class=HTMLResponse)
async def chatbot(request: Request):
    return templates.TemplateResponse("chatbot.html", {"request": request})


@app.get("/health", tags=["sistema"])
def health():
    return {"status": "ok", "service": "ventatalk-rescate"}
