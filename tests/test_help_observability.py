import base64
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from observability import install_observability, metrics, router as observability_router, safe_log
from services.help_config import donor_limit_config
from services.help_exchange import BcvRateUnavailableError, DolarApiBcvRateProvider


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
app = FastAPI()
app.include_router(observability_router)


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    metrics.clear()
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setenv("RESEND_API_KEY", "resend-test-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "sender@example.test")
    yield
    Base.metadata.drop_all(bind=engine)


def test_donor_limits_have_validated_environment_defaults(monkeypatch):
    monkeypatch.delenv("DONOR_ACCOUNT_ACTOR_LIMIT", raising=False)
    monkeypatch.delenv("DONOR_ACCOUNT_IP_LIMIT", raising=False)
    monkeypatch.delenv("DONOR_ACCOUNT_LIMIT_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("DONOR_REPORT_LIMIT", raising=False)
    monkeypatch.delenv("DONOR_REPORT_LIMIT_WINDOW_SECONDS", raising=False)

    config = donor_limit_config()

    assert config.account_actor_limit == 10
    assert config.account_ip_limit == 30
    assert config.account_window_seconds == 300
    assert config.report_limit == 20
    assert config.report_window_seconds == 3600


@pytest.mark.parametrize("name,value", [
    ("DONOR_ACCOUNT_ACTOR_LIMIT", "0"),
    ("DONOR_ACCOUNT_IP_LIMIT", "not-an-integer"),
    ("DONOR_ACCOUNT_LIMIT_WINDOW_SECONDS", "86401"),
    ("DONOR_REPORT_LIMIT", "-1"),
])
def test_donor_limits_reject_unsafe_values(monkeypatch, name, value):
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        donor_limit_config()


def test_safe_log_redacts_forbidden_keys_and_sensitive_values(caplog):
    logger = logging.getLogger("test.security")
    with caplog.at_level(logging.INFO, logger="test.security"):
        safe_log(
            logger,
            logging.WARNING,
            "access_denied",
            reason="actor_limit",
            case="case-family",
            email="private@example.test",
            token="secret-token",
            note="enc:v1:ciphertext",
        )

    marker = json.loads(caplog.records[-1].message)
    assert marker["event"] == "access_denied"
    assert marker["reason"] == "actor_limit"
    assert marker["email"] == "[REDACTED]"
    assert marker["token"] == "[REDACTED]"
    assert marker["note"] == "[REDACTED]"
    assert "private@example.test" not in caplog.text
    assert "secret-token" not in caplog.text
    assert "ciphertext" not in caplog.text


def test_safe_log_redacts_forbidden_keys_in_nested_mappings(caplog):
    logger = logging.getLogger("test.security.nested")
    with caplog.at_level(logging.INFO, logger="test.security.nested"):
        safe_log(
            logger,
            logging.ERROR,
            "request_error",
            context={"api_key": "nested-secret", "safe_reason": "dependency"},
        )

    assert "nested-secret" not in caplog.text
    assert json.loads(caplog.records[-1].message)["context"] == {
        "api_key": "[REDACTED]",
        "safe_reason": "dependency",
    }


def test_liveness_and_readiness_are_minimal_and_fail_closed(monkeypatch):
    assert client.get("/live").json() == {"status": "alive"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    monkeypatch.delenv("RESEND_API_KEY")
    failed = client.get("/ready")

    assert failed.status_code == 503
    assert failed.json() == {"status": "not_ready"}
    assert "resend" not in failed.text.lower()


def test_readiness_rejects_invalid_resend_sender(monkeypatch):
    monkeypatch.setenv("RESEND_FROM_EMAIL", "not-an-email")

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "not_ready"}


def test_metrics_are_openmetrics_and_have_no_sensitive_or_id_labels():
    metrics.increment("access_denials_total", reason="rate_limit")
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/openmetrics-text; version=1.0.0")
    assert 'rescate_access_denials_total{reason="rate_limit"} 1' in response.text
    assert "rescate_aid_pending_count 0" in response.text
    assert "rescate_notification_queue_count" in response.text
    assert response.text.endswith("# EOF\n")
    assert not any(label in response.text for label in ("case_id=", "email=", "ip_hash=", "account_id="))


def test_bcv_provider_records_success_failure_and_latency():
    success = DolarApiBcvRateProvider(fetch_json=lambda url: (
        '{"fuente":"oficial","promedio":10,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"USD"}'
        if "dolares" in url else
        '{"fuente":"oficial","promedio":12,"fechaActualizacion":"2026-07-22T00:00:00-04:00","moneda":"EUR"}'
    ))
    success.fetch_rates()
    failure = DolarApiBcvRateProvider(fetch_json=lambda _url: "not-json")
    with pytest.raises(BcvRateUnavailableError):
        failure.fetch_rates()

    rendered = metrics.render()
    assert 'rescate_bcv_requests_total{outcome="success"} 1' in rendered
    assert 'rescate_bcv_requests_total{outcome="failure"} 1' in rendered
    assert "rescate_bcv_request_duration_seconds_count 2" in rendered


def test_request_markers_have_correlation_without_path_query_or_token(caplog):
    marker_app = FastAPI()
    install_observability(marker_app)

    @marker_app.get("/private/{private_id}")
    def denied(private_id: str):
        from fastapi import HTTPException
        raise HTTPException(status_code=429, detail="denied")

    marker_client = TestClient(marker_app)
    with caplog.at_level(logging.WARNING, logger="rescate.security"):
        response = marker_client.get(
            "/private/private-case-123?token=private-token-123",
            headers={"X-Request-ID": "request-12345678"},
        )

    assert response.status_code == 429
    assert response.headers["X-Request-ID"] == "request-12345678"
    marker = json.loads(caplog.records[-1].message)
    assert marker == {
        "correlation_id": "request-12345678",
        "event": "access_denied",
        "method": "GET",
        "reason": "http_429",
    }
    assert "private-case-123" not in caplog.text
    assert "private-token-123" not in caplog.text
