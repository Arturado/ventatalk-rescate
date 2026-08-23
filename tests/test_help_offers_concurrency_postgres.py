import base64
import os
import threading
import time
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from database import Base
from dependencies import ActorOrgContext
import models
from services.help_crypto import HelpDataCipher
from services.help_offers import create_labor_offer


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL concurrency tests",
)

SENSITIVE_PAYLOAD = {
    "tipo_trabajo": "Contrato centinela",
    "descripcion": "DESCRIPCION-CENTINELA-FASE1",
    "remuneracion_estimada": "REMUNERACION-CENTINELA-FASE1",
    "telefono": "+58-TELEFONO-CENTINELA-FASE1",
    "correo": "correo-centinela-fase1@example.test",
}


@pytest.fixture()
def engine(monkeypatch):
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    test_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def _create_published_employment_case(session_factory):
    with session_factory() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-concurrency",
            nombres_apellidos_cifrado="encrypted-beneficiary",
            cedula_hash="c" * 64,
            cedula_cifrada="encrypted-identity",
            creado_por="coordinador@example.test",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id="employment-race-case",
            organizacion_id="org-concurrency",
            beneficiario_id=beneficiary.id,
            tipo_sujeto="persona",
            titulo_interno="Busqueda laboral de prueba",
            categoria="empleo",
            acepta_ayuda_monetaria=False,
            acepta_ayuda_directa=False,
            estado="publicado",
            creado_por="coordinador@example.test",
        )
        db.add(case)
        db.commit()
        return case.id, case.public_id


def _actor():
    return ActorOrgContext(
        organizacion_id="",
        role="donor",
        email="donor-concurrency@example.test",
        uid="donor-concurrency-uid",
        ip_hash="a" * 64,
    )


def test_concurrent_labor_offer_replays_winner_without_duplicate_or_failed_transaction(engine):
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    case_id, public_id = _create_published_employment_case(session_factory)
    lock_session = session_factory()
    lock_session.execute(
        text("SELECT id FROM casos_ayuda_v2 WHERE id = :case_id FOR UPDATE"),
        {"case_id": case_id},
    )
    start = threading.Barrier(2)
    results = []
    errors = []

    def worker():
        try:
            with session_factory() as db:
                start.wait(timeout=5)
                response, created = create_labor_offer(
                    db,
                    actor=_actor(),
                    public_id=public_id,
                    idempotency_key="labor-race-key-0000001",
                    cipher=HelpDataCipher.from_environment(),
                    **SENSITIVE_PAYLOAD,
                )
                assert db.execute(text("SELECT 1")).scalar_one() == 1
                db.commit()
                results.append((response, created))
        except Exception as exc:  # pragma: no cover - surfaced via errors
            errors.append(exc)

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for thread in threads:
        thread.start()

    # Both transactions must pass the initial idempotency read and block on the
    # case row held by this third transaction before either can insert.
    deadline = time.monotonic() + 5
    waiting = 0
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' "
                "AND query ILIKE '%casos_ayuda_v2%'"
            )).scalar_one()
        if waiting >= 2:
            break
        time.sleep(0.02)
    lock_session.commit()
    lock_session.close()
    assert waiting >= 2, "Both requests must pass the initial read and wait on the case lock"

    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)
    assert not errors, errors
    assert len(results) == 2
    assert sorted(created for _response, created in results) == [False, True]
    assert results[0][0] == results[1][0]

    with session_factory() as db:
        assert db.query(models.OfertaAyudaDirecta).filter_by(tipo="empleo").count() == 1
        assert db.query(models.OperacionIdempotenteAyuda).filter_by(
            operacion="create_labor_offer",
            idempotency_key="labor-race-key-0000001",
        ).count() == 1
        # A savepoint conflict must not leave the caller transaction unusable.
        assert db.execute(text("SELECT 1")).scalar_one() == 1


def test_concurrent_labor_offer_different_payload_returns_controlled_conflict(engine):
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    case_id, public_id = _create_published_employment_case(session_factory)
    lock_session = session_factory()
    lock_session.execute(
        text("SELECT id FROM casos_ayuda_v2 WHERE id = :case_id FOR UPDATE"),
        {"case_id": case_id},
    )
    start = threading.Barrier(2)
    outcomes = []

    def worker(description):
        with session_factory() as db:
            start.wait(timeout=5)
            try:
                response, created = create_labor_offer(
                    db,
                    actor=_actor(),
                    public_id=public_id,
                    idempotency_key="labor-race-conflict-key-0001",
                    cipher=HelpDataCipher.from_environment(),
                    **{**SENSITIVE_PAYLOAD, "descripcion": description},
                )
                db.commit()
                outcomes.append(("success", response, created))
            except Exception as exc:
                db.rollback()
                outcomes.append(("error", type(exc).__name__, str(exc)))

    threads = [
        threading.Thread(target=worker, args=("DESCRIPCION-CENTINELA-FASE1-A",)),
        threading.Thread(target=worker, args=("DESCRIPCION-CENTINELA-FASE1-B",)),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 5
    waiting = 0
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            waiting = connection.execute(text(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() "
                "AND wait_event_type = 'Lock' "
                "AND query ILIKE '%casos_ayuda_v2%'"
            )).scalar_one()
        if waiting >= 2:
            break
        time.sleep(0.02)
    lock_session.commit()
    lock_session.close()
    assert waiting >= 2, "Both conflicting payloads must pass the initial read"
    for thread in threads:
        thread.join(timeout=10)

    assert sum(outcome[0] == "success" for outcome in outcomes) == 1
    conflicts = [outcome for outcome in outcomes if outcome[0] == "error"]
    assert len(conflicts) == 1
    assert conflicts[0][1] == "OfferIdempotencyConflictError"
    assert "CENTINELA" not in conflicts[0][2]
    with session_factory() as db:
        assert db.query(models.OfertaAyudaDirecta).filter_by(tipo="empleo").count() == 1
        assert db.query(models.OperacionIdempotenteAyuda).filter_by(
            operacion="create_labor_offer",
        ).count() == 1
