import base64
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from services.help_crypto import HelpDataCipher
from services.help_temporary_access import (
    TemporaryAccessDeniedError,
    TemporaryAccessRateLimitError,
    redeem_temporary_challenge,
    request_temporary_access,
)


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL concurrency tests",
)


def test_concurrent_challenge_redemption_has_exactly_one_winner(monkeypatch):
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    cipher = HelpDataCipher.from_environment()
    challenge = "concurrent-high-entropy-challenge-token-1234567890"
    now = datetime.now(timezone.utc)
    barrier = threading.Barrier(2)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with sessions() as db:
        db.add(models.AccesoTemporalAyuda(
            destinatario_email_hash="e" * 64,
            destinatario_email_cifrado=cipher.encrypt(
                "responsible@example.com", field="account.responsible_email"
            ),
            challenge_hash=cipher.blind_index(
                challenge, purpose="temporary-access-challenge"
            ),
            challenge_expires_at=now + timedelta(minutes=15),
            challenge_ttl_seconds=900,
            request_ip_hash="1" * 64,
            session_ttl_seconds=28800,
            solicitado_por="firebase_functions",
        ))
        db.commit()

    def redeem():
        with sessions() as db:
            barrier.wait(timeout=5)
            try:
                _access, session_token = redeem_temporary_challenge(
                    db, challenge_token=challenge
                )
                db.commit()
                return session_token
            except TemporaryAccessDeniedError:
                db.rollback()
                return None

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = [future.result(timeout=10) for future in [
                executor.submit(redeem),
                executor.submit(redeem),
            ]]

        assert sum(result is not None for result in results) == 1
        with sessions() as db:
            access = db.query(models.AccesoTemporalAyuda).one()
            assert access.estado == "activo"
            assert access.session_hash is not None
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_concurrent_requests_cannot_overshoot_email_quota(monkeypatch):
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("TEMPORARY_ACCESS_EMAIL_LIMIT", "3")
    monkeypatch.setenv("TEMPORARY_ACCESS_IP_LIMIT", "10")
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    barrier = threading.Barrier(8)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    def request(index):
        with sessions() as db:
            barrier.wait(timeout=10)
            try:
                request_temporary_access(
                    db,
                    email="missing@example.com",
                    ip_hash=f"{index:064x}",
                )
                db.commit()
                return True
            except TemporaryAccessRateLimitError:
                db.commit()
                return False

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            accepted = list(executor.map(request, range(8)))

        assert sum(accepted) == 3
        with sessions() as db:
            assert db.query(models.IntentoSolicitudAccesoTemporal).count() == 8
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
