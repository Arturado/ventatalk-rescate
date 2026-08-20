import os
import bcrypt
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse, JSONResponse
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
from routers.hospitales import router as hospitales_router, pacientes_router
from routers.bomberos import router as bomberos_router
from routers.albergues import router as albergues_router
from routers.casos_ayuda_publicos import router as casos_ayuda_publicos_router
from routers.casos_ayuda_v2 import router as casos_ayuda_v2_router
from routers.casos_ayuda_donantes import router as casos_ayuda_donantes_router
from routers.casos_ayuda_ofertas import router as casos_ayuda_ofertas_router
from routers.casos_ayuda_accesos import router as casos_ayuda_accesos_router
from sqlalchemy import text as sql_text
from sqlalchemy import inspect
from services.shelter_centers import DEFAULT_ORGANIZACION_ID
from services.help_config import donor_limit_config
from services.help_retention_schema import ensure_retention_schema
from services.help_spec004_schema import is_help_spec004_ready
from services.help_offers_schema import is_help_offers_ready
from observability import install_observability, router as observability_router

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


def seed_centros(db: Session):
    count = db.execute(sql_text("SELECT COUNT(*) FROM centros")).scalar()
    if count > 0:
        return

    centros_data = [
        # Chacao / Sucre
        {"nombre": "Parque Alí Primera / Parque del Oeste", "direccion": "Catia / Gato Negro", "zona": "Caracas - Libertador"},
        {"nombre": "Parque Francisco de Miranda / Parque del Este", "direccion": "Municipio Sucre", "zona": "Caracas - Sucre"},
        {"nombre": "Plaza Altamira", "direccion": "Municipio Chacao", "zona": "Caracas - Chacao"},
        {"nombre": "Plaza Bolívar de Chacao", "direccion": "Municipio Chacao", "zona": "Caracas - Chacao"},
        {"nombre": "El Coliseo de Petare / Coliseo La Urbina", "direccion": "Prolongación Av. El Samán, Urb. La Urbina", "zona": "Caracas - Sucre"},
        {"nombre": "Plaza los Museos", "direccion": "Caracas", "zona": "Caracas - Libertador"},
        # Municipio Libertador
        {"nombre": "Complejo Cultural Guayana Esequiba", "direccion": "Parroquia San Bernardino", "zona": "Caracas - Libertador"},
        {"nombre": "Estadio Chato Candela", "direccion": "Parroquia 23 de Enero", "zona": "Caracas - Libertador"},
        {"nombre": "Liceo Andrés Bello", "direccion": "Av. México", "zona": "Caracas - Libertador"},
        {"nombre": "Sede Instituto Nacional de Deportes (IND)", "direccion": "Parroquia El Paraíso", "zona": "Caracas - Libertador"},
        {"nombre": "Sede Ipostel (Centro Postal de Caracas)", "direccion": "Parroquia San Juan", "zona": "Caracas - Libertador"},
        {"nombre": "Liceo Miguel Antonio Caro", "direccion": "Av. Sucre esq. Calle Real de Los Frailes, Catia", "zona": "Caracas - Libertador"},
        {"nombre": "Instalaciones adyacentes Parque Alí Primera", "direccion": "Catia", "zona": "Caracas - Libertador"},
        {"nombre": "U.E.N. Francisco Pimentel", "direccion": "Av. Sur 4, esq. Mamey a Dolores, Parroquia Santa Teresa", "zona": "Caracas - Libertador"},
        {"nombre": "U.E.N. Gran Colombia", "direccion": "Av. Roosevelt con Av. Ayacucho, Los Cármenes, Parroquia Santa Rosalía", "zona": "Caracas - Libertador"},
        {"nombre": "U.E.N. Luís Hurtado Higuera", "direccion": "Calle El Colegio, Urb. Luis Hurtado Higuera, El Junquito Km 12", "zona": "Caracas - Libertador"},
        {"nombre": "Plaza Mausoleo", "direccion": "Final Av. Panteón", "zona": "Caracas - Libertador"},
        {"nombre": "Escuela República de Bolivia", "direccion": "La Pastora", "zona": "Caracas - Libertador"},
        {"nombre": "Polideportivo La Trinidad", "direccion": "La Trinidad, Caracas", "zona": "Caracas - Baruta"},
    ]

    for c in centros_data:
        db.add(models.Centro(**c))
    db.commit()
    print(f"Seed: {len(centros_data)} centros insertados")


def seed_hospitales(db: Session):
    count = db.execute(sql_text("SELECT COUNT(*) FROM hospitales")).scalar()
    if count > 0:
        return

    hospitales_data = [
        # CARACAS / DISTRITO CAPITAL
        {"nombre": "Hospital Universitario de Caracas", "direccion": "Ciudad Universitaria, Caracas", "zona": "Caracas", "telefono": "0212-6053111", "tipo": "publico"},
        {"nombre": "Hospital Vargas de Caracas", "direccion": "Av. Vargas, San José, Caracas", "zona": "Caracas", "telefono": "0212-4083020", "tipo": "publico"},
        {"nombre": "Hospital Dr. José María Vargas", "direccion": "Calle Real de Carballo, San José, Caracas", "zona": "Caracas", "telefono": "0212-4083000", "tipo": "publico"},
        {"nombre": "Hospital de Niños J.M. de los Ríos", "direccion": "San Bernardino, Caracas", "zona": "Caracas", "telefono": "0212-5749111", "tipo": "publico"},
        {"nombre": "Maternidad Concepción Palacios", "direccion": "Av. Vollmer, San Bernardino, Caracas", "zona": "Caracas", "telefono": "0212-5742122", "tipo": "publico"},
        {"nombre": "Hospital Pérez Carreño (IVSS)", "direccion": "Av. Principal de Los Ruices, Caracas", "zona": "Caracas", "telefono": "0212-2395911", "tipo": "publico"},
        {"nombre": "Hospital Dr. José Ignacio Baldó (El Algodonal)", "direccion": "El Algodonal, Carapita, Caracas", "zona": "Caracas", "telefono": "0212-4435111", "tipo": "publico"},
        {"nombre": "Clínica El Ávila", "direccion": "Av. San Juan Bosco, Altamira, Caracas", "zona": "Caracas", "telefono": "0212-2760100", "tipo": "privado"},
        {"nombre": "Centro Médico de Caracas", "direccion": "Av. Eraso, San Bernardino, Caracas", "zona": "Caracas", "telefono": "0212-5554111", "tipo": "privado"},
        {"nombre": "Clínica La Floresta", "direccion": "Av. Principal de La Floresta, Caracas", "zona": "Caracas", "telefono": "0212-2095511", "tipo": "privado"},
        {"nombre": "Hospital Domingo Luciani (IVSS)", "direccion": "El Llanito, Caracas", "zona": "Caracas", "telefono": "0212-2561111", "tipo": "publico"},
        {"nombre": "Hospital Dr. Enrique Tejera (Cotiza)", "direccion": "Cotiza, Caracas", "zona": "Caracas", "telefono": "0212-8625511", "tipo": "publico"},
        # VARGAS / LA GUAIRA
        {"nombre": "Hospital Dr. Raúl Leoni (La Guaira)", "direccion": "Av. La Armada, La Guaira, Vargas", "zona": "Vargas", "telefono": "0212-3521111", "tipo": "publico"},
        {"nombre": "Hospital Tipo II Dr. Patrocinio Peñuela Ruiz", "direccion": "Macuto, Vargas", "zona": "Vargas", "telefono": None, "tipo": "publico"},
        {"nombre": "Ambulatorio Urbano III Caraballeda", "direccion": "Caraballeda, Vargas", "zona": "Vargas", "telefono": None, "tipo": "ambulatorio"},
        {"nombre": "Ambulatorio Los Caracas", "direccion": "Los Caracas, Vargas", "zona": "Vargas", "telefono": None, "tipo": "ambulatorio"},
        # MIRANDA
        {"nombre": "Hospital Dr. Victorino Santaella Ruiz", "direccion": "Los Teques, Miranda", "zona": "Miranda", "telefono": "0212-3214111", "tipo": "publico"},
        {"nombre": "Hospital General del Este Dr. Domingo Luciani", "direccion": "El Llanito, Miranda", "zona": "Miranda", "telefono": "0212-2561111", "tipo": "publico"},
        {"nombre": "Hospital Gervasio Vera Custodio", "direccion": "Ocumare del Tuy, Miranda", "zona": "Miranda", "telefono": None, "tipo": "publico"},
        {"nombre": "Hospital Dr. Jesús Yerena (Lídice)", "direccion": "Lídice, Miranda", "zona": "Miranda", "telefono": None, "tipo": "publico"},
        {"nombre": "Clínica El Ávila Guarenas", "direccion": "Guarenas, Miranda", "zona": "Miranda", "telefono": None, "tipo": "privado"},
        # CARABOBO
        {"nombre": "Ciudad Hospitalaria Dr. Enrique Tejera (CHET)", "direccion": "Valencia, Carabobo", "zona": "Carabobo", "telefono": "0241-8576111", "tipo": "publico"},
        {"nombre": "Hospital General de Valencia (IVSS)", "direccion": "Av. Bolívar Norte, Valencia, Carabobo", "zona": "Carabobo", "telefono": "0241-8232111", "tipo": "publico"},
        {"nombre": "Hospital de Niños Angel Larralde", "direccion": "Urb. La Viña, Valencia, Carabobo", "zona": "Carabobo", "telefono": "0241-8243111", "tipo": "publico"},
        {"nombre": "Clínica Razetti de Valencia", "direccion": "Av. Bolívar, Valencia, Carabobo", "zona": "Carabobo", "telefono": "0241-8222111", "tipo": "privado"},
        {"nombre": "Hospital General Dr. Adolfo Prince Lara (Puerto Cabello)", "direccion": "Puerto Cabello, Carabobo", "zona": "Carabobo", "telefono": None, "tipo": "publico"},
        # ARAGUA
        {"nombre": "Hospital Central de Maracay (Dr. Carlos Arvelo)", "direccion": "Av. Bolívar, Maracay, Aragua", "zona": "Aragua", "telefono": "0243-2325111", "tipo": "publico"},
        {"nombre": "Hospital de Niños Rafael Tobías Guevara", "direccion": "Maracay, Aragua", "zona": "Aragua", "telefono": "0243-2335111", "tipo": "publico"},
        {"nombre": "Hospital Militar Dr. Carlos Arvelo", "direccion": "Av. Las Delicias, Maracay, Aragua", "zona": "Aragua", "telefono": None, "tipo": "publico"},
        {"nombre": "Clínica Razetti de Maracay", "direccion": "Maracay, Aragua", "zona": "Aragua", "telefono": "0243-2422111", "tipo": "privado"},
        {"nombre": "Hospital Dr. Domingo Guzmán Lander (La Victoria)", "direccion": "La Victoria, Aragua", "zona": "Aragua", "telefono": None, "tipo": "publico"},
        # FALCON
        {"nombre": "Hospital Universitario Dr. Alfredo Van Grieken", "direccion": "Coro, Falcón", "zona": "Falcon", "telefono": "0268-2525111", "tipo": "publico"},
        {"nombre": "Hospital Dr. Rafael Calles Sierra (Punto Fijo)", "direccion": "Punto Fijo, Falcón", "zona": "Falcon", "telefono": "0269-2454111", "tipo": "publico"},
        {"nombre": "Ambulatorio La Vela de Coro", "direccion": "La Vela de Coro, Falcón", "zona": "Falcon", "telefono": None, "tipo": "ambulatorio"},
        # YARACUY
        {"nombre": "Hospital Plácido Daniel Rodríguez Rivero", "direccion": "San Felipe, Yaracuy", "zona": "Yaracuy", "telefono": "0254-2312111", "tipo": "publico"},
        {"nombre": "Hospital Dr. Manuel Núñez Tovar (Nirgua)", "direccion": "Nirgua, Yaracuy", "zona": "Yaracuy", "telefono": None, "tipo": "publico"},
        {"nombre": "Ambulatorio Urbano Tipo III San Felipe", "direccion": "San Felipe, Yaracuy", "zona": "Yaracuy", "telefono": None, "tipo": "ambulatorio"},
    ]

    for h in hospitales_data:
        hospital = models.Hospital(**h)
        db.add(hospital)
    db.commit()
    print(f"Seed: {len(hospitales_data)} hospitales insertados")


NOTIFICATION_EVENT_TYPES = (
    "ayuda_reportada",
    "ayuda_confirmada",
    "problema_reportado",
    "revision_resuelta",
    "meta_alcanzada",
    "acceso_temporal",
    "account_changed",
)

AID_STATE_TYPES = (
    "pendiente_confirmacion",
    "confirmada",
    "problema_reportado",
    "en_revision",
    "rechazada",
    "cancelada",
)


def ensure_notification_outbox_schema(target_engine=engine):
    inspector = inspect(target_engine)
    columns_by_table = {
        "casos_ayuda_ayudas": {
            "donante_email_hash": "VARCHAR(64)",
            "donante_email_cifrado": "TEXT",
        },
        "casos_ayuda_notificaciones": {
            "procesando_desde": "TIMESTAMP WITH TIME ZONE"
            if target_engine.dialect.name == "postgresql"
            else "DATETIME",
        },
    }
    with target_engine.begin() as conn:
        for table_name, columns in columns_by_table.items():
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name not in existing_columns:
                    conn.execute(sql_text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    ))

        if target_engine.dialect.name != "postgresql":
            return
        allowed_sql = ", ".join(f"'{value}'" for value in NOTIFICATION_EVENT_TYPES)
        incompatible_count = conn.execute(sql_text(
            "SELECT COUNT(*) FROM casos_ayuda_notificaciones "
            f"WHERE evento_tipo NOT IN ({allowed_sql})"
        )).scalar_one()
        if incompatible_count:
            raise RuntimeError(
                "La migracion del outbox encontro eventos incompatibles; no se modifico el constraint"
            )
        constraints = {
            constraint["name"]: constraint.get("sqltext", "")
            for constraint in inspect(conn).get_check_constraints("casos_ayuda_notificaciones")
        }
        event_constraint = constraints.get("ck_casos_ayuda_notificacion_evento", "")
        if "account_changed" not in event_constraint:
            if "ck_casos_ayuda_notificacion_evento" in constraints:
                conn.execute(sql_text(
                    "ALTER TABLE casos_ayuda_notificaciones "
                    "DROP CONSTRAINT ck_casos_ayuda_notificacion_evento"
                ))
            conn.execute(sql_text(
                "ALTER TABLE casos_ayuda_notificaciones "
                "ADD CONSTRAINT ck_casos_ayuda_notificacion_evento "
                f"CHECK (evento_tipo IN ({allowed_sql}))"
            ))


def ensure_help_aid_state_schema(target_engine=engine):
    if target_engine.dialect.name != "postgresql":
        return
    allowed_sql = ", ".join(f"'{value}'" for value in AID_STATE_TYPES)
    with target_engine.begin() as conn:
        constraints = {
            constraint["name"]: constraint.get("sqltext", "")
            for constraint in inspect(conn).get_check_constraints("casos_ayuda_ayudas")
        }
        state_constraint = constraints.get("ck_casos_ayuda_ayuda_estado", "")
        if "cancelada" in state_constraint:
            return
        incompatible_count = conn.execute(sql_text(
            "SELECT COUNT(*) FROM casos_ayuda_ayudas "
            f"WHERE estado NOT IN ({allowed_sql})"
        )).scalar_one()
        if incompatible_count:
            raise RuntimeError(
                "La migracion de estados de ayuda encontro datos incompatibles; no se modifico el constraint"
            )
        if "ck_casos_ayuda_ayuda_estado" in constraints:
            conn.execute(sql_text(
                "ALTER TABLE casos_ayuda_ayudas DROP CONSTRAINT ck_casos_ayuda_ayuda_estado"
            ))
        conn.execute(sql_text(
            "ALTER TABLE casos_ayuda_ayudas ADD CONSTRAINT ck_casos_ayuda_ayuda_estado "
            f"CHECK (estado IN ({allowed_sql}))"
        ))


def ensure_optional_columns():
    columns_by_table = {
        "centros": {
            "tipo": "VARCHAR(30) DEFAULT 'albergue'",
            "organizacion_id": f"VARCHAR(100) DEFAULT '{DEFAULT_ORGANIZACION_ID}'",
            "estado": "VARCHAR(30) DEFAULT 'activo'",
            "reportado_por": "VARCHAR(200)",
            "notas": "TEXT",
            "before_submit_version": "VARCHAR(100)",
            "before_submit_title": "VARCHAR(200)",
            "before_submit_description": "TEXT",
            "before_submit_items_json": "TEXT",
            "before_submit_confirmations_json": "TEXT",
            "before_submit_acknowledged_at": "VARCHAR(40)",
            "before_submit_source": "VARCHAR(50)",
        },
        "centros_solicitudes": {
            "organizacion_id": f"VARCHAR(100) DEFAULT '{DEFAULT_ORGANIZACION_ID}'",
            "before_submit_version": "VARCHAR(100)",
            "before_submit_title": "VARCHAR(200)",
            "before_submit_description": "TEXT",
            "before_submit_items_json": "TEXT",
            "before_submit_confirmations_json": "TEXT",
            "before_submit_acknowledged_at": "VARCHAR(40)",
            "before_submit_source": "VARCHAR(50)",
        },
        "casos_ayuda_accesos": {
            "request_ip_hash": "VARCHAR(64)",
        },
    }

    inspector = inspect(engine)
    access_constraints = {
        constraint["name"]: constraint.get("sqltext", "")
        for constraint in inspector.get_check_constraints("casos_ayuda_accesos")
    }
    session_constraint = access_constraints.get("ck_casos_ayuda_acceso_session_ttl", "")
    tighten_session_constraint = (
        engine.dialect.name == "postgresql" and "28800" not in session_constraint
    )

    with engine.begin() as conn:
        for table_name, columns in columns_by_table.items():
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue

                conn.execute(
                    sql_text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    )
                )

        if tighten_session_constraint:
            conn.execute(sql_text(
                "ALTER TABLE casos_ayuda_accesos "
                "DROP CONSTRAINT ck_casos_ayuda_acceso_session_ttl"
            ))
            conn.execute(sql_text(
                "ALTER TABLE casos_ayuda_accesos "
                "ADD CONSTRAINT ck_casos_ayuda_acceso_session_ttl "
                "CHECK (session_ttl_seconds > 0 AND session_ttl_seconds <= 28800)"
            ))

        conn.execute(
            sql_text(
                "UPDATE centros "
                "SET tipo = 'albergue' "
                "WHERE tipo IS NULL OR tipo = ''"
            )
        )
        conn.execute(
            sql_text(
                "UPDATE centros "
                "SET tipo = 'albergue' "
                "WHERE tipo = 'refugio'"
            )
        )
        conn.execute(
            sql_text(
                "UPDATE centros "
                "SET organizacion_id = :organizacion_id "
                "WHERE organizacion_id IS NULL OR organizacion_id = ''"
            ),
            {"organizacion_id": DEFAULT_ORGANIZACION_ID},
        )
        conn.execute(
            sql_text(
                "UPDATE centros "
                "SET estado = CASE WHEN COALESCE(activo, true) THEN 'activo' ELSE 'inactivo' END "
                "WHERE estado IS NULL OR estado = ''"
            )
        )
        conn.execute(
            sql_text(
                "UPDATE centros_solicitudes "
                "SET organizacion_id = :organizacion_id "
                "WHERE organizacion_id IS NULL OR organizacion_id = ''"
            ),
            {"organizacion_id": DEFAULT_ORGANIZACION_ID},
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    donor_limit_config()
    models.Base.metadata.create_all(bind=engine)
    ensure_notification_outbox_schema()
    ensure_help_aid_state_schema()
    ensure_optional_columns()
    ensure_retention_schema(engine)
    if not is_help_spec004_ready(engine):
        print(
            "help-spec004: schema not ready yet - run "
            "'python -m scripts.migrate_help_spec004 --apply' against a disposable database"
        )
    db = SessionLocal()
    try:
        seed_admin_users(db)
        seed_hospitales(db)
        seed_centros(db)
    finally:
        db.close()
    yield


ENV = os.getenv("ENVIRONMENT", "production")

app = FastAPI(
    title="Venezuela Rescate API",
    description="API de emergencias post-sismo Venezuela 2026",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

# Docs protegidos por API key en /cowboy-bebop
@app.get("/cowboy-bebop", include_in_schema=False)
async def custom_swagger(x_api_key: str = Header(None)):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    response = get_swagger_ui_html(
        openapi_url="/cowboy-bebop/openapi.json",
        title="Venezuela Rescate API"
    )
    schema = get_openapi(title="Venezuela Rescate API", version="1.0.0", routes=app.routes)
    schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = response.body.decode().replace(
        "url: '/cowboy-bebop/openapi.json',",
        f"spec: {schema_json},",
    )
    return HTMLResponse(html)

@app.get("/cowboy-bebop/openapi.json", include_in_schema=False)
async def custom_openapi(x_api_key: str = Header(None)):
    expected = os.getenv("API_KEY")
    if not expected or x_api_key != expected:
        raise HTTPException(status_code=403, detail="Acceso denegado")
    return get_openapi(
        title="Venezuela Rescate API",
        version="1.0.0",
        routes=app.routes,
    )

MAX_BODY_BYTES = 15 * 1024 * 1024  # 15MB absoluto

@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_BODY_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": "Cuerpo de la petición demasiado grande (máx 15MB)"}
        )
    return await call_next(request)

app.state.limiter = limiter
install_observability(app)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://venezuelarescate.com",
        "https://www.venezuelarescate.com",
        "https://sos-ve-20fa3.web.app",
        "https://sos-ve-20fa3.firebaseapp.com",
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

app.mount("/uploads/capturas", StaticFiles(directory="uploads/capturas"), name="capturas")
app.mount("/uploads/adjuntos", StaticFiles(directory="uploads/adjuntos"), name="adjuntos")
app.mount("/uploads/fotos", StaticFiles(directory="uploads/fotos"), name="fotos")
app.mount("/uploads/notificaciones", StaticFiles(directory="uploads/notificaciones"), name="notificaciones")
app.mount("/uploads/albergue_entregas", StaticFiles(directory="uploads/albergue_entregas", check_dir=False), name="albergue-entregas")
app.mount("/widget", StaticFiles(directory="static"), name="widget")
app.mount("/static", StaticFiles(directory="static"), name="static-files")

app.include_router(reportes.router)
app.include_router(reportes.check_router)
app.include_router(personas.router)
app.include_router(personas.matches_router)
app.include_router(stats_router)
app.include_router(stats_public_router)
app.include_router(hospitales_router)
app.include_router(pacientes_router)
app.include_router(bomberos_router)
app.include_router(albergues_router)
app.include_router(casos_ayuda_v2_router)
app.include_router(casos_ayuda_publicos_router)
app.include_router(casos_ayuda_donantes_router)
app.include_router(casos_ayuda_ofertas_router)
app.include_router(casos_ayuda_accesos_router)
app.include_router(admin_router)
app.include_router(observability_router)


@app.get("/", response_class=HTMLResponse)
async def landing(request: Request):
    return templates.TemplateResponse("landing.html", {"request": request})


@app.get("/chatbot", response_class=HTMLResponse)
async def chatbot(request: Request):
    return templates.TemplateResponse("chatbot.html", {"request": request})


@app.get("/hospitales", response_class=HTMLResponse)
async def hospitales_page(request: Request):
    return templates.TemplateResponse("hospitales.html", {"request": request})


@app.get("/hospitales/reportar", response_class=HTMLResponse)
async def hospitales_reportar_page(request: Request):
    return templates.TemplateResponse("hospitales_reportar.html", {"request": request})


@app.get("/bombero", response_class=HTMLResponse)
async def bombero_page(request: Request):
    return templates.TemplateResponse("bombero.html", {"request": request})


@app.get("/albergues")
async def albergues_page(request: Request):
    return templates.TemplateResponse("albergues.html", {"request": request})


@app.get("/albergues/reportar/{centro_id}")
async def albergues_reportar_page(centro_id: int, request: Request):
    return templates.TemplateResponse("albergues_reportar.html",
        {"request": request, "centro_id": centro_id})


@app.get("/albergues/pedido/{orden_id}")
async def albergues_pedido_page(orden_id: int, request: Request):
    return templates.TemplateResponse("albergues_pedido.html",
        {"request": request, "orden_id": orden_id})


@app.get("/albergues/directorio")
async def albergues_directorio_page(request: Request):
    return templates.TemplateResponse("albergues_directorio.html",
        {"request": request})


@app.get("/personas/{persona_id}")
async def persona_ficha_page(persona_id: int, request: Request):
    return templates.TemplateResponse("persona_ficha.html",
        {"request": request, "persona_id": persona_id})


@app.get("/health", tags=["sistema"])
def health():
    return {
        "status": "ok",
        "service": "ventatalk-rescate",
        "help_spec004_ready": is_help_spec004_ready(engine),
        "help_offers_ready": is_help_offers_ready(engine),
    }
