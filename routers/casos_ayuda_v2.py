from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

import schemas
from database import get_db
from dependencies import (
    ActorOrgContext,
    require_verified_case_organization_actor,
    verify_api_key,
)
from services.help_cases import HelpCaseDomainError, OrganizationAccessError, list_help_cases


router = APIRouter(prefix="/api/v2/casos-ayuda", tags=["casos-ayuda-v2"])
CaseState = Literal[
    "borrador",
    "pendiente_validacion",
    "listo_publicar",
    "publicado",
    "pausado",
    "meta_alcanzada",
    "cerrado",
    "rechazado",
    "suspendido",
    "archivado",
]


@router.get("", response_model=List[schemas.CasoAyudaV2ResumenResponse])
def list_organization_help_cases(
    organizacion_id: Optional[str] = Query(default=None),
    estado: Optional[CaseState] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    _api_actor: str = Depends(verify_api_key),
    actor: ActorOrgContext = Depends(require_verified_case_organization_actor),
):
    try:
        return list_help_cases(
            db,
            actor=actor,
            organization_id=organizacion_id,
            status=estado,
            skip=skip,
            limit=limit,
        )
    except OrganizationAccessError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HelpCaseDomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
