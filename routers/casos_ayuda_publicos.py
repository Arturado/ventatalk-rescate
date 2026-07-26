import hashlib
import os
import re
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

import schemas
from database import get_db
from services.help_cases import (
    build_public_help_case_response,
    get_current_public_case_document,
    get_public_help_case,
    list_public_help_cases,
)
from services.help_crypto import HelpDataCipher, HelpDataCryptoError
from services.help_files import read_encrypted_case_document
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


@router.get("", response_model=List[schemas.CasoAyudaPublicoResponse])
def list_public_help_cases_endpoint(db: Session = Depends(get_db)):
    return [
        build_public_help_case_response(db, case=case, publication=publication)
        for case, publication in list_public_help_cases(db)
    ]


@router.get("/{public_id}", response_model=schemas.CasoAyudaPublicoResponse)
def get_public_help_case_endpoint(public_id: str, db: Session = Depends(get_db)):
    result = get_public_help_case(db, public_id=public_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    case, publication = result
    return build_public_help_case_response(db, case=case, publication=publication)


@router.get("/{public_id}/documentos/{document_id}")
def download_public_help_case_document(
    public_id: str,
    document_id: int,
    db: Session = Depends(get_db),
):
    result = get_public_help_case(db, public_id=public_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    case, _publication = result
    document = get_current_public_case_document(
        db,
        case_id=case.id,
        document_id=document_id,
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    try:
        encrypted = read_encrypted_case_document(document.storage_path)
        plaintext = HelpDataCipher.from_environment().decrypt_bytes(
            encrypted,
            field="document.content",
        )
    except HelpDataCryptoError as exc:
        raise HTTPException(status_code=404, detail="Documento no encontrado") from exc
    if (
        len(plaintext) != document.size_bytes
        or hashlib.sha256(plaintext).hexdigest() != document.checksum_sha256
    ):
        raise HTTPException(status_code=404, detail="Documento no encontrado")

    extensions = {
        "application/pdf": "pdf",
        "image/jpeg": "jpg",
        "image/png": "png",
    }
    safe_type = re.sub(r"[^a-z0-9]+", "-", document.tipo.lower()).strip("-") or "documento"
    filename = f"{safe_type}-{document.id}-v{document.version}.{extensions[document.content_type]}"
    return Response(
        content=plaintext,
        media_type=document.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
