from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import schemas
from database import get_db
from dependencies import ActorOrgContext, require_verified_actor_org, verify_api_key
from services.help_crypto import HelpDataCipher, HelpDataCryptoError
from services.help_donors import (
    DONOR_TERMS_VERSION,
    DonorAccessError,
    DonorRateLimitError,
    DonorTermsRequiredError,
    accept_terms,
    disclose_donor_accounts,
    get_terms_acceptance,
)


router = APIRouter(prefix="/api/v2/donante", tags=["casos-ayuda-donante-v2"])


@router.get("/terminos", response_model=schemas.EstadoTerminosDonanteResponse)
def get_donor_terms_status(
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_actor_org),
):
    try:
        acceptance = get_terms_acceptance(db, actor=actor)
        return {
            "required_version": DONOR_TERMS_VERSION,
            "accepted": acceptance is not None,
            "accepted_at": acceptance.accepted_at if acceptance else None,
        }
    except DonorAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/terminos/aceptar", response_model=schemas.EstadoTerminosDonanteResponse)
def accept_donor_terms(
    payload: schemas.AceptacionTerminosDonanteRequest,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_actor_org),
):
    try:
        acceptance = accept_terms(db, actor=actor, terms_version=payload.terms_version)
        db.commit()
        db.refresh(acceptance)
        return {
            "required_version": DONOR_TERMS_VERSION,
            "accepted": True,
            "accepted_at": acceptance.accepted_at,
        }
    except IntegrityError:
        db.rollback()
        acceptance = get_terms_acceptance(db, actor=actor)
        return {
            "required_version": DONOR_TERMS_VERSION,
            "accepted": acceptance is not None,
            "accepted_at": acceptance.accepted_at if acceptance else None,
        }
    except DonorAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/casos-ayuda/{public_id}/cuentas", response_model=schemas.CuentasCasoDonanteResponse)
def get_donor_case_accounts(
    public_id: str,
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_actor_org),
):
    try:
        case, accounts = disclose_donor_accounts(db, actor=actor, public_id=public_id)
        cipher = HelpDataCipher.from_environment()
        response = {
            "case_public_id": case.public_id,
            "accounts": [{
                "id": account.id,
                "version": account.version,
                "holder_name": cipher.decrypt(account.titular_nombre_cifrado, field="account.holder_name"),
                "beneficiary_relationship": account.relacion_beneficiario,
                "medium": account.medio,
                "currency": account.moneda,
                "identifier": cipher.decrypt(account.identificador_cifrado, field="account.identifier"),
                "instructions": cipher.decrypt(account.instrucciones_cifrado, field="account.instructions") if account.instrucciones_cifrado else None,
            } for account in accounts],
        }
        db.commit()
        return response
    except DonorTermsRequiredError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except DonorRateLimitError as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except DonorAccessError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HelpDataCryptoError as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="No fue posible consultar las cuentas") from exc
