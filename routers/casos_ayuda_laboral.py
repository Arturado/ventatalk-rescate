from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request, Response
from fastapi.routing import APIRoute
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.orm import Session

from database import get_db
from dependencies import ActorOrgContext, require_verified_actor_org, verify_api_key
from services.help_labor_access import (
    LaborAccessError,
    cancel_labor_offer,
    close_labor_session,
    decide_labor_offer,
    get_donor_labor_offer_status,
    get_labor_offer_context,
    issue_labor_access_link,
    list_labor_offers_for_moderation,
    pending_labor_notification_recipients,
    enqueue_labor_new_offer_notification,
    record_labor_recipient_failure,
    redeem_labor_access_link,
    rotate_labor_session_csrf,
    validate_labor_session_csrf,
)

LABOR_ALLOWED_ORIGINS = frozenset({
    "https://venezuelarescate.com", "https://www.venezuelarescate.com",
    "https://sos-ve-20fa3.web.app", "https://sos-ve-20fa3.firebaseapp.com",
    "http://localhost:5173", "http://127.0.0.1:5173",
})


class NoStoreLaborRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            response = await original(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response

        return handler


class LaborNoStoreMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v2/ofertas-laborales"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response


router = APIRouter(
    prefix="/api/v2/ofertas-laborales", tags=["ofertas-laborales-v2"],
    route_class=NoStoreLaborRoute,
)


def _secure(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _error(exc):
    forbidden = exc.code.endswith("forbidden") or exc.code == "labor_csrf_invalid"
    rate = exc.code == "labor_access_rate_limited"
    invalid = exc.code.endswith("invalid")
    status = 429 if rate else 403 if forbidden else 400 if invalid else 409 if exc.code == "terminal_conflict" else 401
    public = "Solicitud laboral no autorizada." if forbidden else (
        "Demasiados intentos. Intenta más tarde." if rate else "Acceso laboral no disponible."
    )
    raise HTTPException(status_code=status, detail=public, headers={"Cache-Control": "no-store"}) from exc


def _closed(payload, fields):
    if not isinstance(payload, dict) or set(payload) != set(fields):
        raise LaborAccessError("labor_payload_invalid")


def _trusted_origin(request):
    origin = request.headers.get("origin", "").rstrip("/")
    referer = request.headers.get("referer", "")
    if origin not in LABOR_ALLOWED_ORIGINS or (referer and not referer.startswith(f"{origin}/")):
        raise LaborAccessError("labor_csrf_forbidden")


def _notification_worker(actor):
    if actor.role != "super_admin" or actor.uid != "labor-notification-worker":
        raise LaborAccessError("recipient_not_authorized")


@router.post("/{offer_id}/enlaces", status_code=201)
def issue_link(offer_id: int, response: Response, payload: dict = Body(...),
               db: Session = Depends(get_db), _api=Depends(verify_api_key),
               actor: ActorOrgContext = Depends(require_verified_actor_org)):
    _secure(response)
    try:
        _closed(payload, {"subject_type", "subject_uid"})
        result = issue_labor_access_link(
            db, offer_id=offer_id, subject_type=payload.get("subject_type"),
            subject_uid=payload.get("subject_uid"), issuer=actor,
        )
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.post("/acceso/canjear")
def redeem_link(response: Response, payload: dict = Body(...), db: Session = Depends(get_db),
                _api=Depends(verify_api_key)):
    _secure(response)
    try:
        _closed(payload, {"link_token", "client_context"})
        result = redeem_labor_access_link(
            db, token=payload.get("link_token", ""),
            client_context=payload.get("client_context", ""),
        )
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.get("/acceso/contexto")
def labor_context(response: Response, x_labor_session: str = Header(default=""),
                  db: Session = Depends(get_db), _api=Depends(verify_api_key)):
    _secure(response)
    try:
        return get_labor_offer_context(db, session_token=x_labor_session)
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.post("/acceso/csrf")
def rotate_csrf(request: Request, response: Response,
                x_labor_session: str = Header(default=""),
                db: Session = Depends(get_db), _api=Depends(verify_api_key)):
    _secure(response)
    try:
        _trusted_origin(request)
        result = rotate_labor_session_csrf(db, session_token=x_labor_session)
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.post("/acceso/decision")
def labor_decision(response: Response, payload: dict = Body(...),
                   x_labor_session: str = Header(default=""),
                   x_labor_csrf: str = Header(default=""),
                   db: Session = Depends(get_db), _api=Depends(verify_api_key)):
    _secure(response)
    try:
        _closed(payload, {"decision", "idempotency_key"})
        validate_labor_session_csrf(
            db, session_token=x_labor_session, csrf_token=x_labor_csrf,
        )
        result = decide_labor_offer(
            db, session_token=x_labor_session, decision=payload.get("decision"),
            idempotency_key=payload.get("idempotency_key"),
        )
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.post("/acceso/cerrar")
def close_session(response: Response, x_labor_session: str = Header(default=""),
                  x_labor_csrf: str = Header(default=""),
                  db: Session = Depends(get_db), _api=Depends(verify_api_key)):
    _secure(response)
    try:
        result = close_labor_session(
            db, session_token=x_labor_session, csrf_token=x_labor_csrf,
        )
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.get("/moderacion")
def moderation_list(response: Response, db: Session = Depends(get_db),
                    _api=Depends(verify_api_key),
                    actor: ActorOrgContext = Depends(require_verified_actor_org)):
    _secure(response)
    try:
        result = list_labor_offers_for_moderation(db, actor=actor)
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.get("/{offer_id}/estado")
def donor_status(offer_id: int, response: Response, db: Session = Depends(get_db),
                 _api=Depends(verify_api_key),
                 actor: ActorOrgContext = Depends(require_verified_actor_org)):
    _secure(response)
    try:
        return get_donor_labor_offer_status(db, offer_id=offer_id, actor=actor)
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.post("/{offer_id}/cancelar")
def cancel_offer(offer_id: int, response: Response, payload: dict = Body(...),
                 db: Session = Depends(get_db), _api=Depends(verify_api_key),
                 actor: ActorOrgContext = Depends(require_verified_actor_org)):
    _secure(response)
    try:
        _closed(payload, {"reason", "idempotency_key"})
        result = cancel_labor_offer(
            db, offer_id=offer_id, actor=actor, reason=payload.get("reason"),
            idempotency_key=payload.get("idempotency_key"),
        )
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.get("/interno/notificaciones/pendientes")
def pending_notifications(response: Response, db: Session = Depends(get_db),
                          _api=Depends(verify_api_key),
                          actor: ActorOrgContext = Depends(require_verified_actor_org)):
    _secure(response)
    try:
        _notification_worker(actor)
        return pending_labor_notification_recipients(db)
    except LaborAccessError as exc:
        _error(exc)


@router.post("/interno/notificaciones/{challenge_id}")
def create_notification(challenge_id: int, response: Response, payload: dict = Body(...),
                        db: Session = Depends(get_db), _api=Depends(verify_api_key),
                        actor: ActorOrgContext = Depends(require_verified_actor_org)):
    _secure(response)
    try:
        _notification_worker(actor)
        _closed(payload, {"recipient_uid", "recipient_email"})
        result = enqueue_labor_new_offer_notification(
            db, challenge_id=challenge_id,
            recipient_uid=payload.get("recipient_uid"),
            recipient_email=payload.get("recipient_email"),
        )
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)


@router.post("/interno/notificaciones/{challenge_id}/fallo")
def recipient_failure(challenge_id: int, response: Response, payload: dict = Body(...),
                      db: Session = Depends(get_db), _api=Depends(verify_api_key),
                      actor: ActorOrgContext = Depends(require_verified_actor_org)):
    _secure(response)
    try:
        _notification_worker(actor)
        _closed(payload, {"recipient_uid", "code"})
        result = record_labor_recipient_failure(db, challenge_id=challenge_id,
            recipient_uid=payload.get("recipient_uid"), code=payload.get("code"))
        db.commit()
        return result
    except LaborAccessError as exc:
        db.rollback(); _error(exc)
