import base64
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from services.help_notifications import claim_notification_batch, enqueue_notification


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL concurrency tests",
)


def test_concurrent_claims_are_disjoint(monkeypatch):
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    barrier = threading.Barrier(2)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with sessions() as db:
        for number in range(4):
            enqueue_notification(
                db,
                event_type="ayuda_confirmada",
                recipient_type="donante",
                recipient_email=f"donor-{number}@example.com",
                template_key="aid-confirmed-v1",
                payload={"case_public_id": f"public-{number}"},
                deduplication_key=f"concurrent-{number}",
            )
        db.commit()

    def claim():
        with sessions() as db:
            barrier.wait(timeout=5)
            return {item.id for item in claim_notification_batch(db, limit=2)}

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = [future.result(timeout=10) for future in [
                executor.submit(claim),
                executor.submit(claim),
            ]]
        assert claims[0].isdisjoint(claims[1])
        assert claims[0] | claims[1] == {1, 2, 3, 4}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_event_constraint_migration_is_idempotent_and_rejects_incompatible_data(monkeypatch):
    from main import ensure_notification_outbox_schema

    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Migration tests require a disposable *_test database"
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE casos_ayuda_notificaciones "
                "DROP CONSTRAINT ck_casos_ayuda_notificacion_evento"
            ))
            connection.execute(text(
                "ALTER TABLE casos_ayuda_notificaciones "
                "ADD CONSTRAINT ck_casos_ayuda_notificacion_evento CHECK (evento_tipo IN ("
                "'ayuda_reportada', 'ayuda_confirmada', 'problema_reportado', "
                "'revision_resuelta', 'meta_alcanzada', 'acceso_temporal'))"
            ))
        ensure_notification_outbox_schema(engine)
        ensure_notification_outbox_schema(engine)
        constraint = {
            item["name"]: item["sqltext"]
            for item in inspect(engine).get_check_constraints("casos_ayuda_notificaciones")
        }["ck_casos_ayuda_notificacion_evento"]
        assert "account_changed" in constraint

        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE casos_ayuda_notificaciones "
                "DROP CONSTRAINT ck_casos_ayuda_notificacion_evento"
            ))
            connection.execute(text(
                "INSERT INTO casos_ayuda_notificaciones ("
                "deduplication_key, evento_tipo, destinatario_tipo, destinatario_email_hash, "
                "destinatario_email_cifrado, template_key, payload_json"
                ") VALUES ('bad', 'unknown_event', 'admin', :hash, 'encrypted', 'bad-v1', 'encrypted')"
            ), {"hash": "a" * 64})
        with pytest.raises(RuntimeError, match="eventos incompatibles"):
            ensure_notification_outbox_schema(engine)
        assert "ck_casos_ayuda_notificacion_evento" not in {
            item["name"]
            for item in inspect(engine).get_check_constraints("casos_ayuda_notificaciones")
        }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
