import base64
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def test_outbox_schema_supports_donor_address_account_change_and_processing_lease():
    assert hasattr(models.AyudaMonetaria, "donante_email_hash")
    assert hasattr(models.AyudaMonetaria, "donante_email_cifrado")
    assert hasattr(models.NotificacionCasoAyuda, "procesando_desde")

    with TestingSessionLocal() as db:
        notification = models.NotificacionCasoAyuda(
            deduplication_key="account:1:changed:recipient",
            evento_tipo="account_changed",
            destinatario_tipo="responsable",
            destinatario_email_hash="d" * 64,
            destinatario_email_cifrado="enc:v1:ciphertext",
            template_key="account-changed-v1",
            payload_json="enc:v1:ciphertext",
        )
        db.add(notification)
        db.commit()


def test_labor_notification_templates_are_closed_and_exclude_free_text_and_pii():
    from services.help_notifications import TEMPLATE_CONTENT, _validated_payload

    matrix = {
        "labor-offer-aceptada-v1": {"offer_id": 1, "state": "aceptada"},
        "labor-offer-rechazada-v1": {"offer_id": 1, "state": "rechazada"},
        "labor-offer-vencida-v1": {"offer_id": 1, "state": "vencida"},
        "labor-offer-cancelada-v1": {"offer_id": 1, "state": "cancelada", "public_reason": "riesgo_privacidad"},
    }
    for template, payload in matrix.items():
        serialized = _validated_payload(template, payload)
        rendered = TEMPLATE_CONTENT[template][1].format(**payload)
        assert "descripcion" not in serialized.lower()
        assert "telefono" not in serialized.lower()
        assert "correo" not in serialized.lower()
        assert "@" not in rendered
    with pytest.raises(Exception):
        _validated_payload("labor-offer-aceptada-v1", {"offer_id": 1, "state": "aceptada", "description": "PII"})


def test_enqueue_encrypts_allowlisted_data_deduplicates_and_rolls_back():
    from services.help_notifications import enqueue_notification

    with TestingSessionLocal() as db:
        first = enqueue_notification(
            db,
            event_type="ayuda_reportada",
            recipient_type="responsable",
            recipient_email=" Responsible@Example.COM ",
            template_key="aid-reported-v1",
            payload={"case_public_id": "public-1"},
            deduplication_key="aid:1:reported",
            organization_id="org-1",
            case_id=None,
        )
        duplicate = enqueue_notification(
            db,
            event_type="ayuda_reportada",
            recipient_type="responsable",
            recipient_email="responsible@example.com",
            template_key="aid-reported-v1",
            payload={"case_public_id": "public-1"},
            deduplication_key="aid:1:reported",
            organization_id="org-1",
            case_id=None,
        )
        assert duplicate.id == first.id
        db.commit()

        stored = db.query(models.NotificacionCasoAyuda).one()
        markers = json.dumps({
            "email": stored.destinatario_email_cifrado,
            "payload": stored.payload_json,
        })
        assert stored.destinatario_email_hash != "responsible@example.com"
        assert stored.destinatario_email_cifrado.startswith("enc:v1:")
        assert stored.payload_json.startswith("enc:v1:")
        assert "responsible@example.com" not in markers.lower()
        assert "public-1" not in markers

    with TestingSessionLocal() as db:
        enqueue_notification(
            db,
            event_type="ayuda_reportada",
            recipient_type="responsable",
            recipient_email="rollback@example.com",
            template_key="aid-reported-v1",
            payload={"case_public_id": "rollback-case"},
            deduplication_key="aid:rollback:reported",
        )
        db.rollback()
        assert db.query(models.NotificacionCasoAyuda).filter_by(
            deduplication_key="aid:rollback:reported"
        ).count() == 0


def test_enqueue_rejects_unknown_template_and_private_payload_fields():
    from services.help_notifications import NotificationPayloadError, enqueue_notification

    with TestingSessionLocal() as db:
        with pytest.raises(NotificationPayloadError):
            enqueue_notification(
                db,
                event_type="ayuda_reportada",
                recipient_type="responsable",
                recipient_email="responsible@example.com",
                template_key="dynamic-template",
                payload={"case_public_id": "public-1"},
                deduplication_key="unknown-template",
            )
        with pytest.raises(NotificationPayloadError):
            enqueue_notification(
                db,
                event_type="ayuda_reportada",
                recipient_type="responsable",
                recipient_email="responsible@example.com",
                template_key="aid-reported-v1",
                payload={"case_public_id": "public-1", "bank_number": "PRIVATE-123"},
                deduplication_key="private-payload",
            )
        with pytest.raises(NotificationPayloadError):
            enqueue_notification(
                db,
                event_type="problema_reportado",
                recipient_type="responsable",
                recipient_email="responsible@example.com",
                template_key="aid-confirmed-v1",
                payload={"case_public_id": "public-1"},
                deduplication_key="mismatched-event-template",
            )


def test_enqueue_rejects_unknown_recipient_type_before_database_insert():
    from services.help_notifications import NotificationPayloadError, enqueue_notification

    with TestingSessionLocal() as db:
        with pytest.raises(NotificationPayloadError, match="tipo de destinatario"):
            enqueue_notification(
                db,
                event_type="ayuda_confirmada",
                recipient_type="admin_global",
                recipient_email="admin@example.com",
                template_key="aid-confirmed-v1",
                payload={"case_public_id": "public-1"},
                deduplication_key="invalid-recipient-type",
            )
        assert db.query(models.NotificacionCasoAyuda).count() == 0


def _enqueue(
    db,
    *,
    key="notification-1",
    event_type="ayuda_confirmada",
    template="aid-confirmed-v1",
    payload=None,
):
    from services.help_notifications import enqueue_notification

    return enqueue_notification(
        db,
        event_type=event_type,
        recipient_type="donante",
        recipient_email="donor@example.com",
        template_key=template,
        payload=payload or {"case_public_id": "public-safe"},
        deduplication_key=key,
    )


def test_resend_request_uses_safe_rendered_template_and_runtime_configuration(monkeypatch):
    from services.help_notifications import ResendProvider

    monkeypatch.setenv("RESEND_API_KEY", "resend-api-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "help@example.com")
    captured = {}

    def fake_transport(url, body, headers, timeout):
        captured.update(url=url, body=json.loads(body), headers=headers, timeout=timeout)
        return 200, b'{"id":"message-123"}'

    with TestingSessionLocal() as db:
        notification = _enqueue(db)
        provider_reference = ResendProvider(transport=fake_transport).send(notification)

    serialized = json.dumps(captured)
    assert provider_reference == "message-123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["headers"]["Authorization"] == "Bearer resend-api-key"
    assert captured["headers"]["User-Agent"] == "ventatalk-rescate/1.0"
    assert captured["body"]["to"] == ["donor@example.com"]
    assert "public-safe" in captured["body"]["text"]
    for forbidden in ("bank", "medical", "receipt", "private", "storage_path"):
        assert forbidden not in serialized.lower()


def test_temporary_access_template_builds_frontend_link_without_exposing_token_in_db(monkeypatch):
    from services.help_notifications import ResendProvider

    monkeypatch.setenv("RESEND_API_KEY", "resend-api-key")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "help@example.com")
    monkeypatch.setenv("HELP_FRONTEND_BASE_URL", "https://rescate.example/app/")
    captured = {}

    def fake_transport(_url, body, _headers, _timeout):
        captured.update(json.loads(body))
        return 200, b'{"id":"temporary-123"}'

    with TestingSessionLocal() as db:
        notification = _enqueue(
            db,
            key="temporary-1",
            event_type="acceso_temporal",
            template="temporary-access-v1",
            payload={"challenge_token": "secret-challenge-token"},
        )
        notification.destinatario_tipo = "responsable"
        db.flush()
        assert "secret-challenge-token" not in notification.payload_json
        ResendProvider(transport=fake_transport).send(notification)

    assert "https://rescate.example/app/revisar-ayuda?token=secret-challenge-token" in captured["text"]


def test_worker_sends_retries_terminally_fails_and_recovers_stale_processing(monkeypatch):
    from services.help_notifications import MAX_NOTIFICATION_ATTEMPTS, process_notification_batch

    class SequenceProvider:
        configured = True
        name = "resend"

        def __init__(self, outcomes):
            self.outcomes = iter(outcomes)

        def send(self, _notification):
            outcome = next(self.outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    from services.help_notifications import NotificationProviderError

    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    with TestingSessionLocal() as db:
        notification = _enqueue(db)
        db.commit()
        result = process_notification_batch(
            db,
            provider=SequenceProvider([NotificationProviderError("timeout", code="transport")]),
            limit=1,
            now=now,
        )
        db.refresh(notification)
        assert result == {"claimed": 1, "sent": 0, "failed": 1}
        assert notification.estado == "fallida"
        assert notification.intentos == 1
        assert notification.ultimo_error_codigo == "transport"
        assert notification.proximo_intento_at.replace(tzinfo=timezone.utc) > now

        notification.proximo_intento_at = now
        db.commit()
        result = process_notification_batch(
            db,
            provider=SequenceProvider(["provider-message-id"]),
            limit=1,
            now=now,
        )
        db.refresh(notification)
        assert result["sent"] == 1
        assert notification.estado == "enviada"
        assert notification.proveedor == "resend"
        assert notification.proveedor_referencia == "provider-message-id"
        assert notification.enviada_at.replace(tzinfo=timezone.utc) == now

        terminal = _enqueue(db, key="terminal")
        terminal.intentos = MAX_NOTIFICATION_ATTEMPTS - 1
        stale = _enqueue(db, key="stale")
        stale.estado = "procesando"
        stale.procesando_desde = now - timedelta(hours=1)
        db.commit()
        result = process_notification_batch(
            db,
            provider=SequenceProvider([
                NotificationProviderError("provider body with PII", code="http_503"),
                "recovered-message-id",
            ]),
            limit=2,
            now=now,
        )
        db.refresh(terminal)
        db.refresh(stale)
        assert result == {"claimed": 2, "sent": 1, "failed": 1}
        assert terminal.estado == "fallida"
        assert terminal.intentos == MAX_NOTIFICATION_ATTEMPTS
        assert terminal.proximo_intento_at is None
        assert terminal.ultimo_error_codigo == "http_503"
        assert "provider body" not in terminal.ultimo_error_codigo
        assert stale.estado == "enviada"


def test_missing_provider_config_does_not_claim_and_readiness_exposes_queue(monkeypatch):
    from services.help_notifications import (
        ResendProvider,
        ProviderConfigurationError,
        notification_readiness,
        process_notification_batch,
    )

    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    monkeypatch.setenv("HELP_ADMIN_NOTIFICATION_EMAILS", "")
    with TestingSessionLocal() as db:
        notification = _enqueue(db)
        db.commit()
        with pytest.raises(ProviderConfigurationError):
            process_notification_batch(db, provider=ResendProvider(), limit=1)
        db.refresh(notification)
        assert notification.estado == "pendiente"
        assert notification.intentos == 0
        assert notification_readiness(db) == {
            "provider_configured": False,
            "pending_count": 1,
            "failing_count": 0,
            "global_admin_recipient_count": 0,
            "global_admin_recipients_configured": False,
            "global_admin_recipient_source": "HELP_ADMIN_NOTIFICATION_EMAILS_interim_until_002_memberships",
        }


def test_late_provider_configuration_failure_releases_entire_claimed_batch():
    from services.help_notifications import ProviderConfigurationError, process_notification_batch

    class LateMissingConfigurationProvider:
        configured = True

        def send(self, _notification):
            raise ProviderConfigurationError("frontend base missing")

    with TestingSessionLocal() as db:
        _enqueue(db, key="late-config-1")
        _enqueue(db, key="late-config-2")
        db.commit()

        with pytest.raises(ProviderConfigurationError):
            process_notification_batch(
                db,
                provider=LateMissingConfigurationProvider(),
                limit=2,
            )

        notifications = db.query(models.NotificacionCasoAyuda).order_by(
            models.NotificacionCasoAyuda.id
        ).all()
        assert [(item.estado, item.intentos) for item in notifications] == [
            ("pendiente", 0),
            ("pendiente", 0),
        ]


def test_additive_startup_migration_adds_nullable_donor_and_processing_columns():
    from main import ensure_notification_outbox_schema

    legacy_engine = create_engine("sqlite://")
    with legacy_engine.begin() as connection:
        connection.execute(text("CREATE TABLE casos_ayuda_ayudas (id INTEGER PRIMARY KEY)"))
        connection.execute(text(
            "CREATE TABLE casos_ayuda_notificaciones ("
            "id INTEGER PRIMARY KEY, evento_tipo VARCHAR(40) NOT NULL)"
        ))

    ensure_notification_outbox_schema(legacy_engine)

    inspector = inspect(legacy_engine)
    assert {column["name"] for column in inspector.get_columns("casos_ayuda_ayudas")} >= {
        "donante_email_hash", "donante_email_cifrado"
    }
    assert {column["name"] for column in inspector.get_columns("casos_ayuda_notificaciones")} >= {
        "procesando_desde"
    }
