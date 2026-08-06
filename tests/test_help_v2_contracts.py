from decimal import Decimal

import pytest
from pydantic import ValidationError

from schemas import (
    CasoAyudaPublicContractResponse,
    CasoAyudaV2ContractSummaryResponse,
    CasoAyudaV2DraftCreateRequest,
    CasoAyudaV2DraftUpdateRequest,
)


def test_person_health_draft_accepts_money_and_direct_aid():
    request = CasoAyudaV2DraftCreateRequest(
        category="salud",
        subject_type="persona",
        aid_modes=["directa", "monetaria", "directa"],
        beneficiary_name="Ana Perez",
        beneficiary_identity="11.111.111",
        title="Tratamiento medico",
        story="Necesita apoyo para completar su tratamiento.",
        goal_amount="250.50",
        goal_currency="USD",
    )

    assert request.beneficiary_identity == "V-11111111"
    assert request.aid_modes == ["monetaria", "directa"]
    assert request.goal_amount == Decimal("250.50")


def test_organization_campaign_does_not_require_a_beneficiary():
    request = CasoAyudaV2DraftCreateRequest(
        category="insumo_recurso",
        subject_type="campana_organizacion",
        aid_modes=["directa"],
        title="Colchones para el refugio",
        story="Campana para equipar el area de descanso.",
    )

    assert request.beneficiary_name is None
    assert request.beneficiary_identity is None


def test_employment_requires_person_profession_and_access_email_without_financial_fields():
    request = CasoAyudaV2DraftCreateRequest(
        category="empleo",
        subject_type="persona",
        aid_modes=["oferta_laboral"],
        beneficiary_name="Ana Perez",
        beneficiary_identity="V-11111111",
        title="Disenadora busca empleo",
        story="Cuenta con experiencia en diseno editorial.",
        profession="Disenadora grafica",
        beneficiary_access_email="ANA@example.com",
    )

    assert request.beneficiary_access_email == "ana@example.com"
    assert request.goal_amount is None
    assert request.goal_currency is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "category": "salud",
            "subject_type": "persona",
            "aid_modes": [],
            "beneficiary_name": "Ana Perez",
            "beneficiary_identity": "V-11111111",
            "title": "Tratamiento medico",
            "story": "Descripcion publica suficientemente extensa.",
        },
        {
            "category": "empleo",
            "subject_type": "campana_organizacion",
            "aid_modes": ["oferta_laboral"],
            "title": "Oferta de empleo",
            "story": "Descripcion publica suficientemente extensa.",
            "profession": "Diseno",
            "beneficiary_access_email": "ana@example.com",
        },
        {
            "category": "insumo_recurso",
            "subject_type": "campana_organizacion",
            "aid_modes": ["directa"],
            "beneficiary_name": "Dato prohibido",
            "title": "Campana de insumos",
            "story": "Descripcion publica suficientemente extensa.",
        },
        {
            "category": "salud",
            "subject_type": "persona",
            "aid_modes": ["monetaria"],
            "beneficiary_name": "Ana Perez",
            "beneficiary_identity": "V-11111111",
            "title": "Tratamiento medico",
            "story": "Descripcion publica suficientemente extensa.",
            "goal_amount": "10",
        },
    ],
)
def test_rejects_incompatible_draft_contracts(payload):
    with pytest.raises(ValidationError):
        CasoAyudaV2DraftCreateRequest(**payload)


def test_update_requires_at_least_one_field_and_complete_financial_pair():
    with pytest.raises(ValidationError):
        CasoAyudaV2DraftUpdateRequest()
    with pytest.raises(ValidationError):
        CasoAyudaV2DraftUpdateRequest(goal_amount="10")
    with pytest.raises(ValidationError):
        CasoAyudaV2DraftUpdateRequest(beneficiary_access_email="correo-invalido")

    update = CasoAyudaV2DraftUpdateRequest(
        goal_amount="10",
        goal_currency="VES",
        beneficiary_access_email="ANA@example.com",
    )
    assert update.goal_amount == Decimal("10")
    assert update.beneficiary_access_email == "ana@example.com"


def test_nonmonetary_summary_and_public_contract_omit_financial_values():
    summary = CasoAyudaV2ContractSummaryResponse(
        id=1,
        public_id="case-public-id",
        organizacion_id="org-1",
        title="Disenadora busca empleo",
        category="empleo",
        subject_type="persona",
        aid_modes=["oferta_laboral"],
        state="publicado",
    )
    public = CasoAyudaPublicContractResponse(
        public_id="case-public-id",
        title="Disenadora busca empleo",
        description="Cuenta con experiencia en diseno editorial.",
        category="empleo",
        subject_type="persona",
        aid_modes=["oferta_laboral"],
        state="publicado",
        primary_photo_url="/api/v2/public/casos-ayuda/case-public-id/foto",
    )

    assert summary.goal_amount is None
    assert public.goal_amount is None
    assert public.confirmed_amount is None


def test_monetary_public_contract_requires_all_financial_values():
    with pytest.raises(ValidationError):
        CasoAyudaPublicContractResponse(
            public_id="case-public-id",
            title="Tratamiento medico",
            description="Necesita apoyo para completar su tratamiento.",
            category="salud",
            subject_type="persona",
            aid_modes=["monetaria"],
            state="publicado",
            primary_photo_url="/api/v2/public/casos-ayuda/case-public-id/foto",
            goal_amount="250",
            goal_currency="USD",
        )

    public = CasoAyudaPublicContractResponse(
        public_id="case-public-id",
        title="Tratamiento medico",
        description="Necesita apoyo para completar su tratamiento.",
        category="salud",
        subject_type="persona",
        aid_modes=["monetaria"],
        state="publicado",
        primary_photo_url="/api/v2/public/casos-ayuda/case-public-id/foto",
        goal_amount="250",
        goal_currency="USD",
        confirmed_amount="0",
        confirmed_aids=0,
    )
    assert public.goal_amount == Decimal("250")
