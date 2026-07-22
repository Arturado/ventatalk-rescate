import hashlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

import models


DOLARAPI_BCV_SOURCE = "DOLARAPI-BCV"
BCV_MANUAL_SOURCE = "BCV-MANUAL"
DOLARAPI_BASE_URL = "https://ve.dolarapi.com"
DOLARAPI_CURRENT_URLS = {
    "USD": f"{DOLARAPI_BASE_URL}/v1/dolares/oficial",
    "EUR": f"{DOLARAPI_BASE_URL}/v1/euros/oficial",
}
DOLARAPI_LEGAL_URL = "https://dolarapi.com/docs/legal.html"
SUPPORTED_CURRENCIES = {"VES", "USD", "EUR"}
RATE_QUANTUM = Decimal("0.0000000001")
AMOUNT_QUANTUM = Decimal("0.01")


class BcvRateUnavailableError(ValueError):
    pass


class ManualBcvRateAccessError(ValueError):
    pass


class ManualBcvRateConflictError(ValueError):
    pass


@dataclass(frozen=True)
class BcvRatesSnapshot:
    rate_date: date
    rates: dict[str, Decimal]
    evidence: dict


@dataclass(frozen=True)
class DolarApiQuote:
    currency: str
    rate_date: date
    value: Decimal
    evidence: dict


@dataclass(frozen=True)
class ConversionResult:
    equivalent_amount: Decimal
    applied_rate: Decimal
    rate_record: models.TasaCambioAyuda | None


@dataclass(frozen=True)
class CurrentCurrencyEquivalents:
    rate_date: date
    amounts: dict[str, Decimal]
    transport_source: str
    upstream_source: str


def parse_dolarapi_official_response(raw_content, *, expected_currency: str, source_url: str):
    content = str(raw_content or "")
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise BcvRateUnavailableError("DolarApi devolvio una respuesta JSON invalida") from exc
    if not isinstance(payload, dict) or str(payload.get("fuente") or "").strip().lower() != "oficial":
        raise BcvRateUnavailableError("DolarApi no devolvio una cotizacion oficial")
    currency = _normalize_currency(expected_currency)
    response_currency = str(payload.get("moneda") or currency).strip().upper()
    if response_currency != currency:
        raise BcvRateUnavailableError("DolarApi devolvio una moneda inesperada")
    try:
        value = Decimal(str(payload.get("promedio")))
    except Exception as exc:
        raise BcvRateUnavailableError("DolarApi devolvio una cotizacion invalida") from exc
    if value <= 0:
        raise BcvRateUnavailableError("DolarApi devolvio una cotizacion no positiva")
    timestamp = str(payload.get("fechaActualizacion") or payload.get("fecha") or "").strip()
    try:
        quote_date = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
    except ValueError as exc:
        raise BcvRateUnavailableError("DolarApi devolvio una fecha invalida") from exc
    return DolarApiQuote(
        currency=currency,
        rate_date=quote_date,
        value=value,
        evidence={
            "url": source_url,
            "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "transport_source": "DolarApi",
            "upstream_source": "BCV",
            "provider_legal_url": DOLARAPI_LEGAL_URL,
            "quote_date": quote_date.isoformat(),
        },
    )


class DolarApiBcvRateProvider:
    def __init__(self, fetch_json=None):
        self._fetch_json = fetch_json or self._request_json

    @property
    def current_date(self):
        return datetime.now(ZoneInfo("America/Caracas")).date()

    @staticmethod
    def _request_json(url):
        timeout = float(os.getenv("DOLARAPI_REQUEST_TIMEOUT_SECONDS", "10"))
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "VentatalkRescate/1.0"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="strict")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise BcvRateUnavailableError("No fue posible consultar DolarApi") from exc

    def fetch_rates(self):
        quotes = {
            currency: parse_dolarapi_official_response(
                self._fetch_json(url),
                expected_currency=currency,
                source_url=url,
            )
            for currency, url in DOLARAPI_CURRENT_URLS.items()
        }
        quote_dates = {quote.rate_date for quote in quotes.values()}
        if len(quote_dates) != 1:
            raise BcvRateUnavailableError("Las cotizaciones oficiales de DolarApi tienen fechas diferentes")
        quote_date = quote_dates.pop()
        return BcvRatesSnapshot(
            rate_date=quote_date,
            rates={currency: quote.value for currency, quote in quotes.items()},
            evidence={
                "transport_source": "DolarApi",
                "upstream_source": "BCV",
                "provider_legal_url": DOLARAPI_LEGAL_URL,
                "requests": {currency: quote.evidence for currency, quote in quotes.items()},
            },
        )


def _normalize_currency(value):
    currency = str(value or "").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise BcvRateUnavailableError("Moneda no soportada para conversion BCV")
    return currency


def _convert_amount(amount, source, target, rates):
    source_in_ves = Decimal("1") if source == "VES" else Decimal(rates[source])
    target_in_ves = Decimal("1") if target == "VES" else Decimal(rates[target])
    applied_rate = (source_in_ves / target_in_ves).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    equivalent = (Decimal(amount) * applied_rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP)
    return equivalent, applied_rate


def get_current_currency_equivalents(*, amount, source_currency: str, provider):
    source = _normalize_currency(source_currency)
    snapshot = provider.fetch_rates()
    amounts = {}
    for target in ("VES", "USD", "EUR"):
        required = {currency for currency in (source, target) if currency != "VES"}
        if not required.issubset(snapshot.rates):
            raise BcvRateUnavailableError("DolarApi no contiene todas las cotizaciones requeridas")
        equivalent, _rate = _convert_amount(amount, source, target, snapshot.rates)
        amounts[target] = equivalent
    return CurrentCurrencyEquivalents(
        rate_date=snapshot.rate_date,
        amounts=amounts,
        transport_source=snapshot.evidence.get("transport_source", "DolarApi"),
        upstream_source=snapshot.evidence.get("upstream_source", "BCV"),
    )


def get_or_create_bcv_conversion(
    db: Session,
    *,
    amount,
    source_currency: str,
    target_currency: str,
    provider,
    current_date=None,
):
    source = _normalize_currency(source_currency)
    target = _normalize_currency(target_currency)
    decimal_amount = Decimal(amount)
    if source == target:
        return ConversionResult(
            equivalent_amount=decimal_amount.quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
            applied_rate=Decimal("1").quantize(RATE_QUANTUM),
            rate_record=None,
        )

    today = current_date or getattr(provider, "current_date", None) or datetime.now(ZoneInfo("America/Caracas")).date()
    existing_rates = db.query(models.TasaCambioAyuda).filter(
        models.TasaCambioAyuda.fuente.in_([DOLARAPI_BCV_SOURCE, BCV_MANUAL_SOURCE]),
        models.TasaCambioAyuda.fecha_tasa == today,
        models.TasaCambioAyuda.moneda_base == source,
        models.TasaCambioAyuda.moneda_cotizada == target,
    ).all()
    existing = next((rate for rate in existing_rates if rate.fuente == DOLARAPI_BCV_SOURCE), None)
    if existing is None:
        existing = next((rate for rate in existing_rates if rate.fuente == BCV_MANUAL_SOURCE), None)
    if existing is not None:
        applied_rate = Decimal(existing.valor).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        return ConversionResult(
            equivalent_amount=(decimal_amount * applied_rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
            applied_rate=applied_rate,
            rate_record=existing,
        )

    snapshot = provider.fetch_rates()
    required = {currency for currency in (source, target) if currency != "VES"}
    if not required.issubset(snapshot.rates):
        raise BcvRateUnavailableError("DolarApi no contiene todas las cotizaciones requeridas")
    snapshot_existing = db.query(models.TasaCambioAyuda).filter_by(
        fuente=DOLARAPI_BCV_SOURCE,
        fecha_tasa=snapshot.rate_date,
        moneda_base=source,
        moneda_cotizada=target,
    ).one_or_none()
    if snapshot_existing is not None:
        applied_rate = Decimal(snapshot_existing.valor).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
        return ConversionResult(
            equivalent_amount=(decimal_amount * applied_rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
            applied_rate=applied_rate,
            rate_record=snapshot_existing,
        )
    ves_rates = {currency: Decimal(snapshot.rates[currency]) for currency in required}
    if any(value <= 0 for value in ves_rates.values()):
        raise BcvRateUnavailableError("La fuente BCV contiene tasas invalidas")

    _equivalent, applied_rate = _convert_amount(decimal_amount, source, target, ves_rates)
    evidence = {
        **snapshot.evidence,
        "rate_date": snapshot.rate_date.isoformat(),
        "queried_at": datetime.now(ZoneInfo("America/Caracas")).isoformat(),
        "source_currency": source,
        "target_currency": target,
        "ves_per_currency": {
            currency: f"{ves_rates[currency].quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP):.10f}"
            for currency in sorted(ves_rates)
        },
        "conversion_path": [source, "VES", target],
    }
    rate_record = models.TasaCambioAyuda(
        fuente=DOLARAPI_BCV_SOURCE,
        fecha_tasa=snapshot.rate_date,
        moneda_base=source,
        moneda_cotizada=target,
        valor=applied_rate,
        evidencia_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )
    db.add(rate_record)
    db.flush()
    return ConversionResult(
        equivalent_amount=(decimal_amount * applied_rate).quantize(AMOUNT_QUANTUM, rounding=ROUND_HALF_UP),
        applied_rate=applied_rate,
        rate_record=rate_record,
    )


def register_manual_bcv_rate(
    db: Session,
    *,
    actor,
    rate_date: date,
    source_currency: str,
    target_currency: str,
    value,
    source_reference: str,
    reason: str,
):
    if actor.role != "super_admin":
        raise ManualBcvRateAccessError("Solo un super_admin puede registrar una tasa manual")
    source = _normalize_currency(source_currency)
    target = _normalize_currency(target_currency)
    if source == target:
        raise ManualBcvRateConflictError("Una tasa manual requiere monedas diferentes")
    decimal_value = Decimal(value).quantize(RATE_QUANTUM, rounding=ROUND_HALF_UP)
    if decimal_value <= 0:
        raise ManualBcvRateConflictError("La tasa manual debe ser positiva")
    existing = db.query(models.TasaCambioAyuda).filter_by(
        fuente=BCV_MANUAL_SOURCE,
        fecha_tasa=rate_date,
        moneda_base=source,
        moneda_cotizada=target,
    ).one_or_none()
    if existing is not None:
        raise ManualBcvRateConflictError("Ya existe una tasa manual para la fecha y par de monedas")

    actor_id = actor.uid or actor.email
    evidence = {
        "source_reference": str(source_reference).strip(),
        "rate_date": rate_date.isoformat(),
        "source_currency": source,
        "target_currency": target,
        "manual": True,
    }
    rate = models.TasaCambioAyuda(
        fuente=BCV_MANUAL_SOURCE,
        fecha_tasa=rate_date,
        moneda_base=source,
        moneda_cotizada=target,
        valor=decimal_value,
        evidencia_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        registrado_manualmente_por=actor_id,
        motivo_manual=str(reason).strip(),
    )
    db.add(rate)
    db.flush()
    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()),
        organizacion_id="global",
        caso_id=None,
        accion="tasa_bcv_manual_registrada",
        actor_id=actor.email,
        actor_tipo=actor.role,
        entidad_tipo="tasa_bcv",
        entidad_id=str(rate.id),
        motivo_codigo="contingencia_manual",
        metadata_json=json.dumps({
            "fecha_tasa": rate_date.isoformat(),
            "moneda_base": source,
            "moneda_cotizada": target,
        }, sort_keys=True, separators=(",", ":")),
    ))
    db.flush()
    return rate
