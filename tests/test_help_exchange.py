import json
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
from dependencies import ActorOrgContext
import models
from services.help_exchange import (
    BcvRateUnavailableError,
    BcvRatesSnapshot,
    DolarApiBcvRateProvider,
    ManualBcvRateAccessError,
    get_current_currency_equivalents,
    get_or_create_bcv_conversion,
    parse_dolarapi_official_response,
    register_manual_bcv_rate,
)


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


class FakeBcvProvider:
    def __init__(self, rates, unavailable=False, rate_date=date(2026, 7, 22)):
        self.rates = rates
        self.unavailable = unavailable
        self.rate_date = rate_date
        self.calls = 0

    def fetch_rates(self):
        self.calls += 1
        if self.unavailable:
            raise BcvRateUnavailableError("Fuente no disponible")
        return BcvRatesSnapshot(
            rate_date=self.rate_date,
            rates={currency: Decimal(value) for currency, value in self.rates.items()},
            evidence={"transport_source": "DolarApi", "upstream_source": "BCV", "requests": {}},
        )


def test_parses_strict_official_dolarapi_response():
    quote = parse_dolarapi_official_response(
        '{"fuente":"oficial","promedio":737.2321,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"USD"}',
        expected_currency="USD",
        source_url="https://ve.dolarapi.com/v1/dolares/oficial",
    )

    assert quote.currency == "USD"
    assert quote.rate_date == date(2026, 7, 22)
    assert quote.value == Decimal("737.2321")
    assert quote.evidence["transport_source"] == "DolarApi"
    assert quote.evidence["upstream_source"] == "BCV"
    assert len(quote.evidence["response_sha256"]) == 64


@pytest.mark.parametrize("payload,currency", [
    ('{"fuente":"paralelo","promedio":800,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"USD"}', "USD"),
    ('{"fuente":"oficial","promedio":0,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"USD"}', "USD"),
    ('{"fuente":"oficial","promedio":737,"fechaActualizacion":"fecha-invalida","moneda":"USD"}', "USD"),
    ('{"fuente":"oficial","promedio":840,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"USD"}', "EUR"),
])
def test_rejects_untrusted_dolarapi_responses(payload, currency):
    with pytest.raises(BcvRateUnavailableError):
        parse_dolarapi_official_response(payload, expected_currency=currency, source_url="https://ve.dolarapi.com/test")


def test_provider_combines_current_official_usd_and_eur_quotes():
    responses = {
        "https://ve.dolarapi.com/v1/dolares/oficial": '{"fuente":"oficial","promedio":737.2321,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"USD"}',
        "https://ve.dolarapi.com/v1/euros/oficial": '{"fuente":"oficial","promedio":840.85744397,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"EUR"}',
    }
    provider = DolarApiBcvRateProvider(fetch_json=lambda url: responses[url])

    snapshot = provider.fetch_rates()

    assert snapshot.rate_date == date(2026, 7, 22)
    assert snapshot.rates == {"USD": Decimal("737.2321"), "EUR": Decimal("840.85744397")}
    assert snapshot.evidence["transport_source"] == "DolarApi"
    assert snapshot.evidence["upstream_source"] == "BCV"
    assert set(snapshot.evidence["requests"]) == {"USD", "EUR"}


def test_current_goal_equivalents_include_all_supported_currencies():
    provider = FakeBcvProvider({"USD": "36.50", "EUR": "40.00"})

    result = get_current_currency_equivalents(
        amount=Decimal("25.00"),
        source_currency="USD",
        provider=provider,
    )

    assert result.rate_date == date(2026, 7, 22)
    assert result.amounts == {
        "VES": Decimal("912.50"),
        "USD": Decimal("25.00"),
        "EUR": Decimal("22.81"),
    }
    assert result.transport_source == "DolarApi"
    assert result.upstream_source == "BCV"


def test_converts_cross_currency_through_ves_and_persists_evidence():
    provider = FakeBcvProvider({"USD": "36.50", "EUR": "40.00"})
    with TestingSessionLocal() as db:
        result = get_or_create_bcv_conversion(
            db,
            amount=Decimal("25.00"),
            source_currency="USD",
            target_currency="EUR",
            provider=provider,
        )
        db.commit()

        assert result.equivalent_amount == Decimal("22.81")
        assert result.applied_rate == Decimal("0.9125000000")
        assert result.rate_record.moneda_base == "USD"
        assert result.rate_record.moneda_cotizada == "EUR"
        evidence = json.loads(result.rate_record.evidencia_json)
        assert evidence["ves_per_currency"] == {"EUR": "40.0000000000", "USD": "36.5000000000"}
        assert evidence["conversion_path"] == ["USD", "VES", "EUR"]


def test_reuses_current_daily_snapshot_without_refetch_or_recalculation():
    provider = FakeBcvProvider({"USD": "36.50", "EUR": "40.00"})
    with TestingSessionLocal() as db:
        first = get_or_create_bcv_conversion(
            db,
            amount=Decimal("10.00"),
            source_currency="USD",
            target_currency="EUR",
            provider=provider,
        )
        db.commit()
        provider.rates = {"USD": "100.00", "EUR": "20.00"}

        second = get_or_create_bcv_conversion(
            db,
            amount=Decimal("10.00"),
            source_currency="USD",
            target_currency="EUR",
            provider=provider,
        )

        assert provider.calls == 1
        assert second.rate_record.id == first.rate_record.id
        assert second.applied_rate == Decimal("0.9125000000")
        assert second.equivalent_amount == Decimal("9.13")


def test_same_currency_needs_no_rate_snapshot():
    provider = FakeBcvProvider({})
    with TestingSessionLocal() as db:
        result = get_or_create_bcv_conversion(
            db,
            amount=Decimal("10.25"),
            source_currency="USD",
            target_currency="USD",
            provider=provider,
        )

        assert result.equivalent_amount == Decimal("10.25")
        assert result.applied_rate == Decimal("1.0000000000")
        assert result.rate_record is None
        assert provider.calls == 0
        assert db.query(models.TasaCambioAyuda).count() == 0


def test_only_super_admin_registers_audited_manual_rate():
    coordinator = ActorOrgContext("org-1", "coordinador", "coordinator@example.test", uid="coord-1")
    super_admin = ActorOrgContext("", "super_admin", "admin@example.test", uid="admin-1")
    with TestingSessionLocal() as db:
        with pytest.raises(ManualBcvRateAccessError):
            register_manual_bcv_rate(
                db,
                actor=coordinator,
                rate_date=date(2026, 7, 20),
                source_currency="USD",
                target_currency="EUR",
                value=Decimal("0.91"),
                source_reference="Boletín BCV 2026-07-20",
                reason="Contingencia local",
            )

        rate = register_manual_bcv_rate(
            db,
            actor=super_admin,
            rate_date=date(2026, 7, 20),
            source_currency="USD",
            target_currency="EUR",
            value=Decimal("0.91"),
            source_reference="Boletín BCV 2026-07-20",
            reason="Fuente oficial temporalmente inaccesible",
        )
        db.commit()

        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="tasa_bcv_manual_registrada").one()
        assert rate.fuente == "BCV-MANUAL"
        assert rate.registrado_manualmente_por == "admin-1"
        assert rate.motivo_manual == "Fuente oficial temporalmente inaccesible"
        assert audit.actor_id == "admin@example.test"
        assert "Boletín" not in audit.metadata_json


def test_manual_rate_is_reused_without_calling_unavailable_provider():
    super_admin = ActorOrgContext("", "super_admin", "admin@example.test", uid="admin-1")
    provider = FakeBcvProvider({}, unavailable=True, rate_date=date(2026, 7, 20))
    with TestingSessionLocal() as db:
        rate = register_manual_bcv_rate(
            db,
            actor=super_admin,
            rate_date=date(2026, 7, 20),
            source_currency="USD",
            target_currency="EUR",
            value=Decimal("0.9100000000"),
            source_reference="Boletín BCV 2026-07-20",
            reason="Contingencia local",
        )
        db.commit()

        result = get_or_create_bcv_conversion(
            db,
            amount=Decimal("10.00"),
            source_currency="USD",
            target_currency="EUR",
            provider=provider,
            current_date=date(2026, 7, 20),
        )

        assert result.rate_record.id == rate.id
        assert result.equivalent_amount == Decimal("9.10")
        assert provider.calls == 0
