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
from sqlalchemy import create_engine, inspect, text

from database import Base
import models  # noqa: F401 - registers the current schema in Base.metadata
from services.help_labor_access_schema import (
    LaborAccessDataError,
    LaborAccessDefinitionError,
    NEW_TABLES as MIGRATION_NEW_TABLES,
    apply_labor_access_migration,
    classify_labor_access_schema,
    describe_labor_access_migration,
    rollback_labor_access_migration,
)


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
        "sujeto_uid", "token_hash", "expires_at", "last_redeemed_at", "redeem_count",
        "rate_window_started_at", "rate_window_count", "revoked_at", "created_at",
    },
    "ofertas_ayuda_sesiones": {
        "id", "desafio_id", "oferta_id", "caso_id", "beneficiario_id", "sujeto_tipo",
        "sujeto_uid", "client_context_hash", "sesion_hash", "csrf_hash", "expires_at",
        "revoked_at", "created_at",
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
        "ix_ofertas_ayuda_desafios_rate_limit",
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
    session_uniques = _names(inspector.get_unique_constraints("ofertas_ayuda_sesiones"))
    assert "uq_ofertas_ayuda_sesiones_desafio" not in session_uniques


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
    assert "ck_casos_ayuda_notificacion_email_laboral" in _names(
        inspector.get_check_constraints("casos_ayuda_notificaciones")
    )
    event_check = next(
        item["sqltext"] for item in inspector.get_check_constraints("casos_ayuda_notificaciones")
        if item["name"] == "ck_casos_ayuda_notificacion_evento"
    )
    assert "oferta_laboral_aceptada" in event_check
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


def _simulate_legacy_schema(engine):
    with engine.begin() as connection:
        for table_name in reversed(MIGRATION_NEW_TABLES):
            Base.metadata.tables[table_name].drop(bind=connection, checkfirst=True)
        for table_name, constraint_name in (
            ("casos_ayuda_idempotencia", "fk_casos_ayuda_idempotencia_oferta"),
            ("casos_ayuda_notificaciones", "fk_casos_ayuda_notificaciones_oferta"),
        ):
            connection.execute(text(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}"))
            connection.execute(text(f"ALTER TABLE {table_name} DROP COLUMN IF EXISTS oferta_id"))
        connection.execute(text(
            "ALTER TABLE casos_ayuda_notificaciones ALTER COLUMN destinatario_email_hash SET NOT NULL"
        ))
        for constraint_name in (
            "ck_ofertas_ayuda_lock_version",
            "ck_ofertas_ayuda_empleo_expiracion",
            "ck_ofertas_ayuda_empleo_terminal_at",
        ):
            connection.execute(text(f"ALTER TABLE ofertas_ayuda DROP CONSTRAINT IF EXISTS {constraint_name}"))
        connection.execute(text("ALTER TABLE ofertas_ayuda ADD COLUMN actor_donante_email VARCHAR(320)"))
        connection.execute(text("ALTER TABLE ofertas_ayuda ALTER COLUMN actor_donante_email SET NOT NULL"))
        for column_name in ("actor_donante_email_cifrado", "expires_at", "lock_version", "terminal_at"):
            connection.execute(text(f"ALTER TABLE ofertas_ayuda DROP COLUMN IF EXISTS {column_name}"))


def _seed_legacy_labor_offer(engine):
    with engine.begin() as connection:
        beneficiary_id = connection.execute(text(
            "INSERT INTO beneficiarios_ayuda "
            "(organizacion_id,nombres_apellidos_cifrado,cedula_hash,cedula_cifrada,creado_por) "
            "VALUES ('org-gate2','enc-name',:hash,'enc-id','fixture@example.test') RETURNING id"
        ), {"hash": "a" * 64}).scalar_one()
        case_id = connection.execute(text(
            "INSERT INTO casos_ayuda_v2 "
            "(public_id,organizacion_id,beneficiario_id,tipo_sujeto,titulo_interno,categoria,"
            "acepta_ayuda_monetaria,acepta_ayuda_directa,estado,creado_por) "
            "VALUES ('gate2-existing-labor','org-gate2',:beneficiary_id,'persona','Caso laboral',"
            "'empleo',false,false,'publicado','fixture@example.test') RETURNING id"
        ), {"beneficiary_id": beneficiary_id}).scalar_one()
        return connection.execute(text(
            "INSERT INTO ofertas_ayuda "
            "(caso_id,organizacion_id,tipo,actor_donante_uid,actor_donante_email,"
            "contacto_cifrado,payload_cifrado,payload_version,estado) "
            "VALUES (:case_id,'org-gate2','empleo','firebase-gate2','donor@example.test',"
            "'enc-contact','enc-payload',1,'pendiente_respuesta') RETURNING id"
        ), {"case_id": case_id}).scalar_one()


def _encrypt(value):
    return f"cipher::{value}"


def _decrypt(value):
    prefix = "cipher::"
    if not str(value).startswith(prefix):
        raise ValueError("invalid fixture ciphertext")
    return str(value)[len(prefix):]


def test_gate2_dry_run_classifies_legacy_without_mutation(engine):
    _simulate_legacy_schema(engine)
    offer_id = _seed_legacy_labor_offer(engine)

    before_columns = {item["name"] for item in inspect(engine).get_columns("ofertas_ayuda")}
    report = describe_labor_access_migration(engine)

    assert report.mode == "dry-run"
    assert report.definition_state == "legacy"
    assert report.ready is False
    assert report.row_counts["ofertas_laborales"] == 1
    assert report.actions
    assert {item["name"] for item in inspect(engine).get_columns("ofertas_ayuda")} == before_columns
    with engine.connect() as connection:
        assert connection.execute(text("SELECT id FROM ofertas_ayuda")).scalar_one() == offer_id


def test_gate2_apply_preserves_existing_labor_offer_and_second_apply_is_idempotent(engine):
    _simulate_legacy_schema(engine)
    offer_id = _seed_legacy_labor_offer(engine)

    first = apply_labor_access_migration(
        engine,
        email_encryptor=_encrypt,
        email_decryptor=_decrypt,
        backup_reference="gate2-disposable-backup-1",
    )
    second = apply_labor_access_migration(
        engine,
        email_encryptor=_encrypt,
        email_decryptor=_decrypt,
        backup_reference="gate2-disposable-backup-1",
    )

    assert first.definition_state == "target"
    assert first.ready is True
    assert first.actions
    assert second.definition_state == "target"
    assert second.ready is True
    assert second.actions == []
    assert classify_labor_access_schema(engine) == "target"
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT id,actor_donante_email_cifrado,expires_at,lock_version,estado "
            "FROM ofertas_ayuda"
        )).mappings().one()
    assert row["id"] == offer_id
    assert _decrypt(row["actor_donante_email_cifrado"]) == "donor@example.test"
    assert row["expires_at"] is not None
    assert row["lock_version"] == 0
    assert row["estado"] == "pendiente_respuesta"


def test_gate2_rollback_restores_compatible_legacy_schema_and_rows(engine):
    _simulate_legacy_schema(engine)
    offer_id = _seed_legacy_labor_offer(engine)
    apply_labor_access_migration(
        engine,
        email_encryptor=_encrypt,
        email_decryptor=_decrypt,
        backup_reference="gate2-disposable-backup-2",
    )

    report = rollback_labor_access_migration(
        engine,
        email_decryptor=_decrypt,
        backup_reference="gate2-disposable-backup-2",
    )

    assert report.definition_state == "legacy"
    assert classify_labor_access_schema(engine) == "legacy"
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT id,actor_donante_email,estado FROM ofertas_ayuda"
        )).mappings().one()
    assert row == {
        "id": offer_id,
        "actor_donante_email": "donor@example.test",
        "estado": "pendiente_respuesta",
    }


def test_gate2_rollback_blocks_incompatible_labor_data(engine):
    _simulate_legacy_schema(engine)
    offer_id = _seed_legacy_labor_offer(engine)
    apply_labor_access_migration(
        engine,
        email_encryptor=_encrypt,
        email_decryptor=_decrypt,
        backup_reference="gate2-disposable-backup-3",
    )
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO casos_ayuda_idempotencia "
            "(actor_id,operacion,idempotency_key,request_hash,estado,oferta_id) "
            "VALUES ('worker','expire_labor_offer','gate2-expire-key-0001',:hash,'completed',:offer_id)"
        ), {"hash": "b" * 64, "offer_id": offer_id})

    with pytest.raises(LaborAccessDataError, match="Rollback blocked"):
        rollback_labor_access_migration(
            engine,
            email_decryptor=_decrypt,
            backup_reference="gate2-disposable-backup-3",
        )
    assert classify_labor_access_schema(engine) == "target"


def test_gate2_apply_rejects_unknown_partial_schema_without_mutation(engine):
    _simulate_legacy_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE ofertas_ayuda ADD COLUMN lock_version INTEGER"))

    assert classify_labor_access_schema(engine) == "unknown"
    with pytest.raises(LaborAccessDefinitionError):
        apply_labor_access_migration(
            engine,
            email_encryptor=_encrypt,
            email_decryptor=_decrypt,
            backup_reference="gate2-disposable-backup-4",
        )
    assert "actor_donante_email" in {item["name"] for item in inspect(engine).get_columns("ofertas_ayuda")}
