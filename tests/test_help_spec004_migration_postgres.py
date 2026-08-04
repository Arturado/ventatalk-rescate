import os
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, text

from database import Base
from services.help_retention import RetentionTargetError
from services.help_spec004_schema import (
    HELP_SPEC004_SCHEMA_VERSION,
    Spec004MigrationDataError,
    apply_help_spec004_migration,
    describe_help_spec004_migration,
    is_help_spec004_ready,
)


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL migration tests",
)

_NEW_COLUMNS = [
    "tipo_sujeto",
    "acepta_ayuda_monetaria",
    "acepta_ayuda_directa",
    "detalle_condicional_cifrado",
    "detalle_condicional_version",
]
_NEW_CHECKS = [
    "ck_casos_ayuda_v2_tipo_sujeto",
    "ck_casos_ayuda_v2_sujeto_beneficiario",
    "ck_casos_ayuda_v2_modalidad_monetaria_meta",
    "ck_casos_ayuda_v2_modalidades_no_vacias",
    "ck_casos_ayuda_v2_empleo_coherente",
]


def _simulate_pre_spec004_schema(engine):
    with engine.begin() as connection:
        for check_name in _NEW_CHECKS:
            connection.execute(text(f"ALTER TABLE casos_ayuda_v2 DROP CONSTRAINT IF EXISTS {check_name}"))
        for column_name in _NEW_COLUMNS:
            connection.execute(text(f"ALTER TABLE casos_ayuda_v2 DROP COLUMN IF EXISTS {column_name}"))
        connection.execute(text("ALTER TABLE casos_ayuda_v2 ALTER COLUMN beneficiario_id SET NOT NULL"))
        connection.execute(text("ALTER TABLE casos_ayuda_v2 ALTER COLUMN meta_monto SET NOT NULL"))
        connection.execute(text("ALTER TABLE casos_ayuda_v2 ALTER COLUMN meta_moneda SET NOT NULL"))


@pytest.fixture()
def engine():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Migration tests require a disposable *_test database"
    test_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    _simulate_pre_spec004_schema(test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def _seed_pilot_case(engine):
    # Inserted with raw SQL (not the ORM model) because the fixture simulates
    # the pre-spec004 schema, which lacks the columns the current model class
    # declares.
    with engine.begin() as connection:
        beneficiary_id = connection.execute(
            text(
                "INSERT INTO beneficiarios_ayuda "
                "(organizacion_id, nombres_apellidos_cifrado, cedula_hash, cedula_cifrada, creado_por) "
                "VALUES ('org-1', 'enc-name', :cedula_hash, 'enc-id', 'coordinador@example.com') "
                "RETURNING id"
            ),
            {"cedula_hash": "a" * 64},
        ).scalar()
        case_id = connection.execute(
            text(
                "INSERT INTO casos_ayuda_v2 "
                "(public_id, organizacion_id, beneficiario_id, titulo_interno, categoria, "
                "meta_monto, meta_moneda, creado_por) "
                "VALUES ('pilot-case-1', 'org-1', :beneficiario_id, 'Caso piloto', 'medicamentos', "
                "100, 'USD', 'coordinador@example.com') "
                "RETURNING id"
            ),
            {"beneficiario_id": beneficiary_id},
        ).scalar()
        return case_id, beneficiary_id


def test_dry_run_reports_pending_changes_without_mutating(engine):
    _seed_pilot_case(engine)

    report = describe_help_spec004_migration(engine)

    assert report.mode == "dry-run"
    assert report.ready is False
    assert report.pilot_counts["casos_ayuda_v2"] == 1
    assert report.pilot_counts["beneficiarios_ayuda"] == 1
    assert set(report.columns_added) == set(_NEW_COLUMNS)
    assert set(report.constraints_added) == set(_NEW_CHECKS)

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM casos_ayuda_v2")).scalar() == 1
    assert is_help_spec004_ready(engine) is False


def test_apply_removes_pilot_data_and_adds_schema(engine):
    _seed_pilot_case(engine)

    report = apply_help_spec004_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

    assert report.mode == "apply"
    assert report.schema_version == HELP_SPEC004_SCHEMA_VERSION
    assert set(report.columns_added) == set(_NEW_COLUMNS)
    assert set(report.constraints_added) == set(_NEW_CHECKS)
    assert is_help_spec004_ready(engine) is True

    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM casos_ayuda_v2")).scalar() == 0
        assert connection.execute(text("SELECT COUNT(*) FROM beneficiarios_ayuda")).scalar() == 0


def test_apply_allows_campaign_case_without_beneficiary_or_goal(engine):
    apply_help_spec004_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO casos_ayuda_v2 "
            "(public_id, organizacion_id, beneficiario_id, tipo_sujeto, titulo_interno, categoria, "
            "acepta_ayuda_monetaria, acepta_ayuda_directa, meta_monto, meta_moneda, creado_por) "
            "VALUES ('campana-post-migracion', 'org-1', NULL, 'campana_organizacion', 'Colecta', "
            "'insumo_recurso', false, true, NULL, NULL, 'coordinador@example.com')"
        ))

    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT COUNT(*) FROM casos_ayuda_v2 WHERE public_id = 'campana-post-migracion'")
        ).scalar() == 1


def test_second_apply_is_idempotent_and_mutates_nothing_new(engine):
    _seed_pilot_case(engine)
    apply_help_spec004_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

    second_report = apply_help_spec004_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

    assert second_report.columns_added == []
    assert second_report.constraints_added == []
    assert second_report.pilot_counts["casos_ayuda_v2"] == 0
    assert is_help_spec004_ready(engine) is True


def test_apply_fails_closed_when_unmanaged_table_still_references_pilot_case(engine):
    case_id, _ = _seed_pilot_case(engine)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE casos_ayuda_v2_unmanaged_ref ("
            "id SERIAL PRIMARY KEY, caso_id INTEGER NOT NULL REFERENCES casos_ayuda_v2(id))"
        ))
        connection.execute(
            text("INSERT INTO casos_ayuda_v2_unmanaged_ref (caso_id) VALUES (:caso_id)"),
            {"caso_id": case_id},
        )

    try:
        with pytest.raises(Spec004MigrationDataError):
            apply_help_spec004_migration(engine, local_test_marker="DISPOSABLE_LOCAL_TEST")

        assert is_help_spec004_ready(engine) is False
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM casos_ayuda_v2")).scalar() == 1
            assert connection.execute(text("SELECT COUNT(*) FROM beneficiarios_ayuda")).scalar() == 1
    finally:
        with engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS casos_ayuda_v2_unmanaged_ref"))


def test_apply_refuses_non_local_target_before_any_mutation():
    remote_engine = create_engine("postgresql://user:pass@remote-host.example.com/rescate_prod")

    with pytest.raises(RetentionTargetError):
        apply_help_spec004_migration(remote_engine, local_test_marker=None)
