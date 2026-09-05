import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text

from database import Base
from services.help_retention import RetentionTargetError
from services.help_offers_schema import (
    HELP_OFFERS_SCHEMA_VERSION,
    apply_help_offers_migration,
    describe_help_offers_migration,
    is_help_offers_ready,
)


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL migration tests",
)

_NEW_TABLES = ["casos_ayuda_responsables", "ofertas_ayuda"]


def _simulate_pre_offers_schema(engine):
    with engine.begin() as connection:
        for table_name in (
            "ofertas_ayuda_liberaciones_contacto",
            "ofertas_ayuda_transiciones_terminales",
            "ofertas_ayuda_sesiones",
            "ofertas_ayuda_desafios_acceso",
        ):
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))
        connection.execute(text(
            "ALTER TABLE casos_ayuda_idempotencia DROP CONSTRAINT IF EXISTS "
            "casos_ayuda_idempotencia_oferta_id_fkey"
        ))
        connection.execute(text(
            "ALTER TABLE casos_ayuda_notificaciones DROP CONSTRAINT IF EXISTS "
            "casos_ayuda_notificaciones_oferta_id_fkey"
        ))
        for table_name in _NEW_TABLES:
            connection.execute(text(f"DROP TABLE IF EXISTS {table_name}"))


@pytest.fixture()
def engine():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Migration tests require a disposable *_test database"
    test_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    _simulate_pre_offers_schema(test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def test_dry_run_reports_pending_tables_without_mutating(engine):
    report = describe_help_offers_migration(engine)

    assert report.mode == "dry-run"
    assert report.schema_version == HELP_OFFERS_SCHEMA_VERSION
    assert report.ready is False
    assert set(report.tables_created) == set(_NEW_TABLES)
    assert is_help_offers_ready(engine) is False


def test_apply_creates_missing_tables(engine):
    report = apply_help_offers_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

    assert report.mode == "apply"
    assert report.ready is True
    assert set(report.tables_created) == set(_NEW_TABLES)
    assert is_help_offers_ready(engine) is True


def test_second_apply_is_idempotent_and_creates_nothing_new(engine):
    apply_help_offers_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

    second = apply_help_offers_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

    assert second.tables_created == []
    assert second.ready is True


def test_apply_refuses_non_local_target_before_any_mutation():
    remote_engine = create_engine("postgresql://user:pass@remote-host:5432/rescate")

    with pytest.raises(RetentionTargetError):
        apply_help_offers_migration(remote_engine)
