import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import schemas
import services.help_cases as help_cases
import services.help_offers as help_offers


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "spec005_phase0.json").read_text(encoding="utf-8")
)


def _required_contract(name):
    contract = getattr(schemas, name, None)
    assert contract is not None, f"RED: falta definir schemas.{name}"
    return contract


def test_red_employment_public_contract_has_labor_mode_without_financial_or_org_fields():
    contract = _required_contract("CasoAyudaPublicoResponse")
    payload = {
        **FIXTURES["employment_public_case"],
        "modalidades": ["oferta_laboral"],
    }
    serialized = contract(**payload).model_dump(mode="json", exclude_none=True)

    assert serialized["modalidades"] == ["oferta_laboral"]
    for forbidden in {
        "organizacion_id",
        "organizacion_nombre",
        "prioridad_especial",
        "meta_monto",
        "meta_moneda",
        "monto_confirmado",
        "ayudas_confirmadas",
        "equivalencias",
    }:
        assert forbidden not in serialized


def test_red_labor_offer_payload_accepts_only_the_five_closed_fields():
    contract = _required_contract("OfertaLaboralCreateRequest")
    payload = FIXTURES["labor_offer"]
    parsed = contract(**payload)

    assert parsed.model_dump() == payload

    for extra in (
        {"organizacion_id": "org-atacante"},
        {"actor_uid": "uid-atacante"},
        {"role": "admin"},
    ):
        with pytest.raises(ValidationError):
            contract(**payload, **extra)


@pytest.mark.parametrize(
    "missing_field",
    [
        "tipo_trabajo",
        "descripcion",
        "remuneracion_estimada",
        "telefono",
        "correo",
    ],
)
def test_red_labor_offer_payload_rejects_each_missing_required_field(missing_field):
    contract = _required_contract("OfertaLaboralCreateRequest")
    payload = {**FIXTURES["labor_offer"]}
    payload.pop(missing_field)

    with pytest.raises(ValidationError):
        contract(**payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("tipo_trabajo", "x"),
        ("tipo_trabajo", "x" * 121),
        ("descripcion", "corta"),
        ("descripcion", "x" * 2001),
        ("remuneracion_estimada", "x"),
        ("remuneracion_estimada", "x" * 251),
        ("telefono", "1234"),
        ("telefono", "x" * 81),
        ("correo", "correo-invalido"),
    ],
)
def test_red_labor_offer_payload_rejects_each_invalid_value(field, value):
    contract = _required_contract("OfertaLaboralCreateRequest")
    payload = {**FIXTURES["labor_offer"], field: value}

    with pytest.raises(ValidationError):
        contract(**payload)


def test_red_labor_offer_has_a_service_separate_from_direct_offer():
    create_labor_offer = getattr(help_offers, "create_labor_offer", None)
    assert create_labor_offer is not None, "RED: falta el servicio laboral separado"
    assert create_labor_offer is not help_offers.create_direct_offer


def test_red_labor_response_never_reflects_private_offer_fields():
    contract = _required_contract("OfertaLaboralDonanteResponse")
    response = contract(**FIXTURES["labor_offer_response"]).model_dump(mode="json")

    assert response["tipo"] == "empleo"
    assert response["status"] == "pendiente_respuesta"
    for private_field in FIXTURES["labor_offer"]:
        assert private_field not in response


@pytest.mark.parametrize(
    "payload",
    [
        {
            "document_key": "foto_galeria:taller",
            "document_type": "foto_galeria",
            "classification": "publico",
            "file_name": "taller.jpg",
            "content_type": "image/jpeg",
            "content_base64": "Zm90by1maWN0aWNpYQ==",
        },
        {
            "document_key": "foto_galeria:herramientas",
            "document_type": "foto_galeria",
            "classification": "publico",
            "file_name": "herramientas.png",
            "content_type": "image/png",
            "content_base64": "Zm90by1maWN0aWNpYQ==",
        },
    ],
)
def test_red_gallery_photo_contract_accepts_stable_public_image_keys(payload):
    parsed = schemas.DocumentoCasoAyudaCreateRequest(**payload)
    assert parsed.document_key == payload["document_key"]


@pytest.mark.parametrize(
    "override",
    [
        {"document_key": "foto_galeria:Clave Invalida"},
        {"document_key": "foto-galeria-sin-prefijo"},
        {"classification": "privado"},
        {"content_type": "application/pdf", "file_name": "galeria.pdf"},
        {"content_type": "image/png", "file_name": "galeria.jpg"},
    ],
)
def test_red_gallery_photo_contract_rejects_invalid_key_privacy_or_mime(override):
    payload = {
        "document_key": "foto_galeria:taller",
        "document_type": "foto_galeria",
        "classification": "publico",
        "file_name": "taller.jpg",
        "content_type": "image/jpeg",
        "content_base64": "Zm90by1maWN0aWNpYQ==",
        **override,
    }

    with pytest.raises(ValidationError):
        schemas.DocumentoCasoAyudaCreateRequest(**payload)


def test_red_public_photo_order_places_cover_before_gallery_sorted_by_stable_key():
    order_documents = getattr(help_cases, "order_public_case_documents", None)
    assert order_documents is not None, "RED: falta explicitar el orden documental público"
    documents = [
        {"id": 3, "tipo": "foto_galeria", "document_key": "foto_galeria:taller"},
        {"id": 1, "tipo": "foto_principal", "document_key": "foto_principal"},
        {"id": 2, "tipo": "foto_galeria", "document_key": "foto_galeria:herramientas"},
    ]
    assert [item["id"] for item in order_documents(documents)] == [1, 2, 3]
