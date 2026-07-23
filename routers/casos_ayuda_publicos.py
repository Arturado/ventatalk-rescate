import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
import schemas
from database import get_db
from services.help_cases import get_public_help_case, list_public_help_cases, CaseNotFoundError
from services.help_exchange import (
    BcvRateUnavailableError,
    CachedBcvRateProvider,
    DolarApiBcvRateProvider,
    get_current_currency_equivalents,
)


router = APIRouter(prefix="/api/v2/public/casos-ayuda", tags=["casos-ayuda-publicos-v2"])
public_rate_provider = CachedBcvRateProvider(
    DolarApiBcvRateProvider(),
    ttl_seconds=float(os.getenv("DOLARAPI_PUBLIC_CACHE_TTL_SECONDS", "600")),
)


def get_public_rate_provider():
    return public_rate_provider


def _public_response(case, publication, db):
    # Public medical documents expose metadata only; private storage paths never leave the backend.
    documents = db.query(models.DocumentoCasoAyuda).filter(
        models.DocumentoCasoAyuda.caso_id == case.id,
        models.DocumentoCasoAyuda.clasificacion == "publico",
        models.DocumentoCasoAyuda.estado_revision == "aprobado",
    ).all()
    return {
        "id": case.id,
        "public_id": case.public_id,
        "nombre_publico": publication.nombre_publico,
        "titulo_publico": publication.titulo_publico,
        "descripcion_publica": publication.descripcion_publica,
        "categoria": case.categoria,
        "localidad_general": publication.localidad_general,
        "meta_monto": case.meta_monto,
        "meta_moneda": case.meta_moneda,
        "monto_confirmado": case.monto_confirmado,
        "ayudas_confirmadas": case.ayudas_confirmadas,
        "estado": case.estado,
        "prioridad_especial": case.prioridad_especial,
        "publicado_at": case.publicado_at,
        "documentos": [
            {"id": document.id, "tipo": document.tipo, "content_type": document.content_type}
            for document in documents
        ],
    }


@router.get("", response_model=List[schemas.CasoAyudaPublicoResponse])
def list_public_help_cases_endpoint(db: Session = Depends(get_db)):
    return [_public_response(case, publication, db) for case, publication in list_public_help_cases(db)]


@router.get("/{public_id}", response_model=schemas.CasoAyudaPublicoResponse)
def get_public_help_case_endpoint(public_id: str, db: Session = Depends(get_db)):
    result = get_public_help_case(db, public_id=public_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    case, publication = result
    return _public_response(case, publication, db)


@router.get("/{public_id}/equivalencias", response_model=schemas.EquivalenciasCasoPublicoResponse)
def get_public_help_case_equivalents_endpoint(
    public_id: str,
    db: Session = Depends(get_db),
    rate_provider=Depends(get_public_rate_provider),
):
    result = get_public_help_case(db, public_id=public_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    case, _publication = result
    try:
        equivalents = get_current_currency_equivalents(
            amount=case.meta_monto,
            source_currency=case.meta_moneda,
            provider=rate_provider,
        )
    except BcvRateUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "case_public_id": case.public_id,
        "goal_amount": case.meta_monto,
        "goal_currency": case.meta_moneda,
        "equivalents": equivalents.amounts,
        "rate_date": equivalents.rate_date,
        "transport_source": equivalents.transport_source,
        "upstream_source": equivalents.upstream_source,
        "referential": True,
    }
