from fastapi import APIRouter, Body, Depends, Header, HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

import schemas
from database import get_db
from dependencies import ActorOrgContext, require_verified_actor_org, verify_api_key
from services.help_crypto import HelpDataCipher, HelpDataCryptoError
from services.help_idempotency import normalize_idempotency_key
from services.help_offers import (
    HelpOfferDomainError,
    OfferAccessError,
    OfferIdempotencyConflictError,
    create_direct_offer,
    create_labor_offer,
)


router = APIRouter(prefix="/api/v2/donante", tags=["casos-ayuda-ofertas-v2"])


def parse_labor_offer_payload(payload: dict = Body(...)):
    try:
        return schemas.OfertaLaboralCreateRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Payload laboral invalido") from exc


@router.post(
    "/casos-ayuda/{public_id}/ofertas",
    response_model=schemas.OfertaAyudaDirectaDonanteResponse,
    status_code=201,
)
def create_donor_direct_offer(
    public_id: str,
    payload: schemas.OfertaAyudaDirectaCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_actor_org),
):
    key = normalize_idempotency_key(idempotency_key)
    try:
        cipher = HelpDataCipher.from_environment()
        response, _created = create_direct_offer(
            db,
            actor=actor,
            public_id=public_id,
            description=payload.description,
            contact_details=payload.contact_details,
            idempotency_key=key,
            cipher=cipher,
        )
        db.commit()
        return response
    except OfferIdempotencyConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OfferAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc
    except HelpOfferDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/casos-ayuda/{public_id}/ofertas-laborales",
    response_model=schemas.OfertaLaboralDonanteResponse,
    status_code=201,
)
def create_donor_labor_offer(
    public_id: str,
    payload: schemas.OfertaLaboralCreateRequest = Depends(parse_labor_offer_payload),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_actor_org),
):
    key = normalize_idempotency_key(idempotency_key)
    try:
        cipher = HelpDataCipher.from_environment()
        response, _created = create_labor_offer(
            db,
            actor=actor,
            public_id=public_id,
            idempotency_key=key,
            cipher=cipher,
            **payload.model_dump(),
        )
        db.commit()
        return response
    except OfferIdempotencyConflictError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except OfferAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="El cifrado de datos no esta configurado") from exc
    except HelpOfferDomainError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
