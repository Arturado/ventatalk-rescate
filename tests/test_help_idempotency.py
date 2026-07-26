import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models
from services.help_idempotency import (
    hash_idempotency_payload,
    normalize_idempotency_key,
    validate_idempotency_operation,
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


def test_normalizes_valid_idempotency_key():
    assert normalize_idempotency_key("  help:12345678-abcd  ") == "help:12345678-abcd"


@pytest.mark.parametrize("key", [None, "", "short", "a" * 129, "unsafe key 123456"])
def test_rejects_invalid_idempotency_key(key):
    with pytest.raises(HTTPException) as error:
        normalize_idempotency_key(key)

    assert error.value.status_code == 400


@pytest.mark.parametrize("operation", ["report_help", "confirm_help"])
def test_accepts_supported_idempotency_operations(operation):
    assert validate_idempotency_operation(operation) == operation


def test_rejects_unsupported_idempotency_operation():
    with pytest.raises(ValueError, match="Operación idempotente"):
        validate_idempotency_operation("publish_case")


def test_payload_hash_is_canonical_for_object_key_order():
    first = hash_idempotency_payload({"amount": "10.00", "currency": "USD"})
    second = hash_idempotency_payload({"currency": "USD", "amount": "10.00"})

    assert first == second
    assert first != hash_idempotency_payload({"amount": "11.00", "currency": "USD"})


def test_rejects_duplicate_key_for_same_actor_and_operation():
    first = models.OperacionIdempotenteAyuda(
        actor_id="donante@example.com",
        operacion="report_help",
        idempotency_key="help:12345678-abcd",
        request_hash="a" * 64,
    )
    duplicate = models.OperacionIdempotenteAyuda(
        actor_id="donante@example.com",
        operacion="report_help",
        idempotency_key="help:12345678-abcd",
        request_hash="a" * 64,
    )

    with TestingSessionLocal() as db:
        db.add(first)
        db.commit()
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()


def test_allows_same_key_in_a_different_scope():
    records = [
        models.OperacionIdempotenteAyuda(
            actor_id="donante@example.com",
            operacion="report_help",
            idempotency_key="shared:123456789",
            request_hash="a" * 64,
        ),
        models.OperacionIdempotenteAyuda(
            actor_id="donante@example.com",
            operacion="confirm_help",
            idempotency_key="shared:123456789",
            request_hash="b" * 64,
        ),
        models.OperacionIdempotenteAyuda(
            actor_id="otro@example.com",
            operacion="report_help",
            idempotency_key="shared:123456789",
            request_hash="c" * 64,
        ),
    ]

    with TestingSessionLocal() as db:
        db.add_all(records)
        db.commit()

        assert db.query(models.OperacionIdempotenteAyuda).count() == 3
