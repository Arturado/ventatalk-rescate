import os
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from sqlalchemy.orm import Session

import schemas
from database import get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
from services.help_cases import CaseNotFoundError, OrganizationAccessError
from services.help_crypto import HelpDataCryptoError
from services.help_notifications import ResendProvider
from services.help_receipts import ReceiptNotFoundError, download_help_receipt
from services.help_temporary_access import (
    TemporaryAccessDeniedError,
    TemporaryAccessRateLimitError,
    authenticate_temporary_session,
    list_case_temporary_accesses,
    redeem_temporary_challenge,
    report_temporary_aid_problem,
    request_temporary_access,
    revoke_case_temporary_access,
    temporary_access_context,
)


router = APIRouter(tags=["casos-ayuda-accesos-temporales"])


def _temporary_session(db, token, *, lock=False):
    if not token:
        raise HTTPException(status_code=401, detail="La sesion temporal es obligatoria")
    try:
        return authenticate_temporary_session(db, session_token=token, lock=lock)
    except (TemporaryAccessDeniedError, HelpDataCryptoError) as exc:
        raise HTTPException(status_code=401, detail="La sesion temporal no es valida") from exc


@router.post("/api/v2/accesos-temporales/solicitar", status_code=202)
def request_access(
    payload: schemas.SolicitudAccesoTemporalRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
):
    if not ResendProvider().configured or not str(os.getenv("HELP_FRONTEND_BASE_URL") or "").strip():
        raise HTTPException(status_code=503, detail="El acceso temporal no esta disponible")
    try:
        request_temporary_access(
            db,
            email=payload.email,
            public_id=payload.public_id,
            ip_hash=payload.ip_hash,
        )
        db.commit()
    except TemporaryAccessRateLimitError as exc:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Demasiadas solicitudes; intenta nuevamente mas tarde",
        ) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc
    return {"status": "accepted"}


@router.post(
    "/api/v2/accesos-temporales/canjear",
    response_model=schemas.CanjeAccesoTemporalResponse,
)
def redeem_access(
    payload: schemas.CanjeAccesoTemporalRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
):
    try:
        access, session_token = redeem_temporary_challenge(
            db, challenge_token=payload.challenge_token
        )
        db.commit()
        return {"session_token": session_token, "expires_at": access.session_expires_at}
    except (TemporaryAccessDeniedError, HelpDataCryptoError) as exc:
        db.rollback()
        raise HTTPException(status_code=401, detail="El acceso temporal no es valido") from exc


@router.get(
    "/api/v2/accesos-temporales/contexto",
    response_model=schemas.ContextoAccesoTemporalResponse,
    response_model_exclude_unset=True,
)
def get_access_context(
    x_temporary_session: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
):
    access, scopes = _temporary_session(db, x_temporary_session)
    return temporary_access_context(db, access=access, scopes=scopes)


@router.get(
    "/api/v2/accesos-temporales/ayudas/{aid_id}/comprobantes/{receipt_id}/descargar"
)
def download_temporary_help_receipt(
    aid_id: int,
    receipt_id: int,
    x_temporary_session: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
):
    access, scopes = _temporary_session(db, x_temporary_session)
    try:
        download = download_help_receipt(
            db,
            receipt_id=receipt_id,
            aid_id=aid_id,
            access="temporary",
            temporary_access=access,
            scopes=scopes,
        )
        db.commit()
        return Response(
            content=download.content,
            media_type=download.content_type,
            headers=download.headers,
        )
    except ReceiptNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/api/v2/accesos-temporales/ayudas/{aid_id}/problema",
    response_model=schemas.AyudaAccesoTemporalResponse,
)
def report_access_problem(
    aid_id: int,
    payload: schemas.ProblemaAyudaRequest,
    x_temporary_session: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
):
    access, scopes = _temporary_session(db, x_temporary_session, lock=True)
    try:
        response = report_temporary_aid_problem(
            db,
            access=access,
            scopes=scopes,
            aid_id=aid_id,
            problem_type=payload.problem_type,
            detail=payload.detail,
        )
        db.commit()
        return response
    except CaseNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="Ayuda no encontrada") from exc
    except TemporaryAccessDeniedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get(
    "/api/v2/casos-ayuda/{case_id}/accesos-temporales",
    response_model=list[schemas.AccesoTemporalOrganizacionResponse],
)
def list_accesses(
    case_id: int,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        return list_case_temporary_accesses(db, case_id=case_id, actor=actor)
    except (CaseNotFoundError, OrganizationAccessError) as exc:
        raise HTTPException(status_code=404, detail="Caso no encontrado") from exc


@router.post("/api/v2/casos-ayuda/{case_id}/accesos-temporales/{access_id}/revocar")
def revoke_access(
    case_id: int,
    access_id: int,
    payload: schemas.RevocacionAccesoTemporalRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        response = revoke_case_temporary_access(
            db,
            case_id=case_id,
            access_id=access_id,
            actor=actor,
            reason=payload.reason,
        )
        db.commit()
        return response
    except (CaseNotFoundError, OrganizationAccessError) as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail="Acceso temporal no encontrado") from exc
