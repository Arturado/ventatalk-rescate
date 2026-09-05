"""Gate 1 RED contract for the protected labor-offer schema.

These tests describe the approved target without implementing models or a
migration. They must remain RED until Gate 1 is independently approved and
Gate 2 is authorized against disposable PostgreSQL.
"""

import importlib.util
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, inspect

from database import Base


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL schema RED tests",
)

NEW_TABLES = {
    "ofertas_ayuda_desafios_acceso",
    "ofertas_ayuda_sesiones",
    "ofertas_ayuda_transiciones_terminales",
    "ofertas_ayuda_liberaciones_contacto",
}

EXPECTED_COLUMNS = {
    "ofertas_ayuda_desafios_acceso": {
        "id", "oferta_id", "caso_id", "beneficiario_id", "sujeto_tipo",
        "sujeto_uid", "token_hash", "expires_at", "consumed_at", "revoked_at", "created_at",
    },
    "ofertas_ayuda_sesiones": {
        "id", "desafio_id", "oferta_id", "caso_id", "beneficiario_id", "sujeto_tipo",
        "sujeto_uid", "sesion_hash", "csrf_hash", "expires_at", "revoked_at", "created_at",
    },
    "ofertas_ayuda_transiciones_terminales": {
        "id", "oferta_id", "caso_id", "estado_terminal", "actor_tipo", "actor_uid",
        "razon_codigo", "sesion_id", "idempotencia_id", "oferta_version_anterior", "created_at",
    },
    "ofertas_ayuda_liberaciones_contacto": {
        "id", "oferta_id", "caso_id", "transicion_id", "sesion_id",
        "autorizado_uid", "autorizado_tipo", "created_at",
    },
}

EXPECTED_UNIQUES = {
    "ofertas_ayuda_desafios_acceso": {"uq_ofertas_ayuda_desafios_token_hash"},
    "ofertas_ayuda_sesiones": {
        "uq_ofertas_ayuda_sesiones_desafio",
        "uq_ofertas_ayuda_sesiones_hash",
        "uq_ofertas_ayuda_sesiones_csrf_hash",
    },
    "ofertas_ayuda_transiciones_terminales": {
        "uq_ofertas_ayuda_transiciones_oferta",
        "uq_ofertas_ayuda_transiciones_idempotencia",
    },
    "ofertas_ayuda_liberaciones_contacto": {
        "uq_ofertas_ayuda_liberaciones_oferta",
        "uq_ofertas_ayuda_liberaciones_transicion",
    },
}

EXPECTED_INDEXES = {
    "ofertas_ayuda": {
        "ix_ofertas_ayuda_empleo_vencimiento",
        "ix_ofertas_ayuda_actor_donante_uid",
    },
    "ofertas_ayuda_desafios_acceso": {
        "ix_ofertas_ayuda_desafios_oferta_estado",
        "ix_ofertas_ayuda_desafios_sujeto",
        "ix_ofertas_ayuda_desafios_expiracion",
    },
    "ofertas_ayuda_sesiones": {
        "ix_ofertas_ayuda_sesiones_oferta_activa",
        "ix_ofertas_ayuda_sesiones_sujeto",
        "ix_ofertas_ayuda_sesiones_caso",
    },
    "ofertas_ayuda_transiciones_terminales": {
        "ix_ofertas_ayuda_transiciones_caso",
        "ix_ofertas_ayuda_transiciones_actor",
    },
    "ofertas_ayuda_liberaciones_contacto": {
        "ix_ofertas_ayuda_liberaciones_sesion",
        "ix_ofertas_ayuda_liberaciones_actor",
    },
}


@pytest.fixture()
def engine():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Schema RED tests require disposable *_test PostgreSQL"
    test_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def _names(items):
    return {item["name"] for item in items if item.get("name")}


def test_red_offer_has_expiration_concurrency_and_encrypted_notification_email(engine):
    columns = {item["name"]: item for item in inspect(engine).get_columns("ofertas_ayuda")}

    assert {"actor_donante_email_cifrado", "expires_at", "lock_version", "terminal_at"} <= set(columns)
    assert "actor_donante_email" not in columns
    assert columns["lock_version"]["nullable"] is False
    assert str(columns["lock_version"]["default"]).strip("'()") == "0"
    assert EXPECTED_INDEXES["ofertas_ayuda"] <= _names(inspect(engine).get_indexes("ofertas_ayuda"))


def test_red_all_nominal_labor_tables_and_columns_exist(engine):
    inspector = inspect(engine)

    assert NEW_TABLES <= set(inspector.get_table_names())
    for table_name, expected in EXPECTED_COLUMNS.items():
        assert expected == {column["name"] for column in inspector.get_columns(table_name)}


def test_red_foreign_keys_are_restrict_and_point_to_the_approved_objects(engine):
    inspector = inspect(engine)
    expected_targets = {
        "ofertas_ayuda", "casos_ayuda_v2", "beneficiarios_ayuda",
        "ofertas_ayuda_desafios_acceso", "ofertas_ayuda_sesiones",
        "ofertas_ayuda_transiciones_terminales", "casos_ayuda_idempotencia",
    }

    for table_name in NEW_TABLES:
        foreign_keys = inspector.get_foreign_keys(table_name)
        assert foreign_keys
        assert {item["referred_table"] for item in foreign_keys} <= expected_targets
        assert all((item.get("options") or {}).get("ondelete") == "RESTRICT" for item in foreign_keys)


def test_red_uniques_checks_and_worker_indexes_enforce_the_target(engine):
    inspector = inspect(engine)

    for table_name, expected in EXPECTED_UNIQUES.items():
        assert expected <= _names(inspector.get_unique_constraints(table_name))
    for table_name, expected in EXPECTED_INDEXES.items():
        assert expected <= _names(inspector.get_indexes(table_name))
    assert "ck_ofertas_ayuda_empleo_expiracion" in _names(inspector.get_check_constraints("ofertas_ayuda"))
    assert "ck_ofertas_ayuda_lock_version" in _names(inspector.get_check_constraints("ofertas_ayuda"))
    assert "ck_ofertas_ayuda_empleo_terminal_at" in _names(inspector.get_check_constraints("ofertas_ayuda"))


def test_red_existing_idempotency_and_outbox_are_extended_without_duplicates(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert "oferta_id" in {item["name"] for item in inspector.get_columns("casos_ayuda_idempotencia")}
    assert "ix_casos_ayuda_idempotencia_oferta_operacion" in _names(
        inspector.get_indexes("casos_ayuda_idempotencia")
    )
    assert "oferta_id" in {item["name"] for item in inspector.get_columns("casos_ayuda_notificaciones")}
    assert "ix_casos_ayuda_notificaciones_oferta_estado" in _names(
        inspector.get_indexes("casos_ayuda_notificaciones")
    )
    assert not ({"ofertas_ayuda_idempotencia", "ofertas_ayuda_outbox"} & tables)


def test_red_migration_contract_is_versioned_and_external_to_application_startup():
    root = Path(__file__).resolve().parents[1]
    service = root / "services" / "help_labor_access_schema.py"
    command = root / "scripts" / "migrate_help_labor_access.py"

    assert service.exists()
    assert command.exists()
    assert importlib.util.spec_from_file_location("help_labor_access_schema", service) is not None
    source = command.read_text(encoding="utf-8")
    assert "--dry-run" in source
    assert "--apply" in source
    assert "rollback" in source
