import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, inspect, text

from database import Base


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL migration tests",
)


def test_aid_state_constraint_migration_adds_cancelada_idempotently():
    from main import ensure_help_aid_state_schema

    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Migration tests require a disposable *_test database"
    engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "ALTER TABLE casos_ayuda_ayudas DROP CONSTRAINT ck_casos_ayuda_ayuda_estado"
            ))
            connection.execute(text(
                "ALTER TABLE casos_ayuda_ayudas ADD CONSTRAINT ck_casos_ayuda_ayuda_estado "
                "CHECK (estado IN ('pendiente_confirmacion', 'confirmada', 'problema_reportado', "
                "'en_revision', 'rechazada'))"
            ))

        ensure_help_aid_state_schema(engine)
        ensure_help_aid_state_schema(engine)

        constraint = {
            item["name"]: item["sqltext"]
            for item in inspect(engine).get_check_constraints("casos_ayuda_ayudas")
        }["ck_casos_ayuda_ayuda_estado"]
        assert "cancelada" in constraint
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
