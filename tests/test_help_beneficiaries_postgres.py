import os
import threading
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database import Base
import models
from services.help_beneficiaries import get_or_create_beneficiary


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL concurrency tests",
)

IDENTITY_HASH = "d" * 64


@pytest.fixture()
def engine():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    test_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def test_concurrent_get_or_create_produces_one_beneficiary_and_independent_cases(engine):
    session_factory = sessionmaker(bind=engine)
    barrier = threading.Barrier(2)
    results = {}
    errors = []

    def worker(name, suffix):
        try:
            with session_factory() as db:
                barrier.wait(timeout=5)
                beneficiary, created = get_or_create_beneficiary(
                    db,
                    organization_id="org-concurrency",
                    identity_hash=IDENTITY_HASH,
                    identity_encrypted="encrypted-id",
                    name_encrypted="encrypted-name",
                    actor_email="coordinador@example.com",
                )
                db.commit()
                case = models.CasoAyudaV2(
                    public_id=f"race-case-{suffix}",
                    organizacion_id="org-concurrency",
                    beneficiario_id=beneficiary.id,
                    titulo_interno=f"Necesidad {suffix}",
                    categoria="salud",
                    meta_monto=100,
                    meta_moneda="USD",
                    creado_por="coordinador@example.com",
                )
                db.add(case)
                db.commit()
                results[name] = (beneficiary.id, created, case.id)
        except Exception as exc:  # pragma: no cover - surfaced via errors list
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("first", "a")),
        threading.Thread(target=worker, args=("second", "b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, errors
    assert len(results) == 2
    beneficiary_ids = {value[0] for value in results.values()}
    case_ids = {value[2] for value in results.values()}
    created_flags = [value[1] for value in results.values()]

    assert len(beneficiary_ids) == 1, "Both requests must resolve to the same beneficiary"
    assert len(case_ids) == 2, "Each request must still create its own independent case"
    assert sorted(created_flags) == [False, True], "Exactly one request should have won the insert race"

    with session_factory() as db:
        assert db.query(models.BeneficiarioAyuda).filter_by(
            organizacion_id="org-concurrency", cedula_hash=IDENTITY_HASH
        ).count() == 1
        assert db.query(models.CasoAyudaV2).filter_by(organizacion_id="org-concurrency").count() == 2
