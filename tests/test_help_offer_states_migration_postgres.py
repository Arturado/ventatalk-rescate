import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from database import Base
from services.help_offer_states_schema import (
    CONSTRAINT_NAME,
    EMPLOYMENT_STATES,
    HELP_OFFER_STATES_SCHEMA_VERSION,
    LEGACY_CONDITION,
    DIRECT_STATES,
    HelpOfferStatesDataError,
    HelpOfferStatesDefinitionError,
    apply_help_offer_states_migration,
    classify_help_offer_state_constraint,
    describe_help_offer_states_migration,
    rollback_help_offer_states_migration,
    TARGET_CONDITION,
    _condition_structure,
)
from services.help_retention import RetentionTargetError


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL migration tests",
)


@pytest.fixture()
def engine():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Migration tests require a disposable *_test database"
    test_engine = create_engine(TEST_DATABASE_URL)
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(bind=test_engine)
        test_engine.dispose()


def _install_legacy_constraint(engine):
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE ofertas_ayuda DROP CONSTRAINT {CONSTRAINT_NAME}"))
        connection.execute(text(
            f"ALTER TABLE ofertas_ayuda ADD CONSTRAINT {CONSTRAINT_NAME} CHECK ({LEGACY_CONDITION})"
        ))


def _install_constraint(engine, condition):
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE ofertas_ayuda DROP CONSTRAINT {CONSTRAINT_NAME}"))
        connection.execute(text(
            f"ALTER TABLE ofertas_ayuda ADD CONSTRAINT {CONSTRAINT_NAME} CHECK ({condition})"
        ))


def _create_case(engine):
    with engine.begin() as connection:
        return connection.execute(text(
            "INSERT INTO casos_ayuda_v2 "
            "(public_id, organizacion_id, tipo_sujeto, titulo_interno, categoria, "
            "acepta_ayuda_monetaria, acepta_ayuda_directa, creado_por) "
            "VALUES ('offer-state-case', 'org-test', 'campana_organizacion', "
            "'Caso ficticio', 'insumo_recurso', false, true, 'test@example.test') RETURNING id"
        )).scalar_one()


def _insert_offer(engine, case_id, *, suffix, offer_type, state):
    with engine.begin() as connection:
        return connection.execute(text(
            "INSERT INTO ofertas_ayuda "
            "(caso_id, organizacion_id, tipo, actor_donante_uid, actor_donante_email, "
            "contacto_cifrado, payload_cifrado, payload_version, estado) "
            "VALUES (:case_id, 'org-test', :offer_type, :uid, 'donor@example.test', "
            "'encrypted-contact', 'encrypted-payload', 1, :state) RETURNING id"
        ), {
            "case_id": case_id,
            "offer_type": offer_type,
            "uid": f"donor-{suffix}",
            "state": state,
        }).scalar_one()


def _constraint_definition(engine):
    return next(
        check["sqltext"]
        for check in inspect(engine).get_check_constraints("ofertas_ayuda")
        if check["name"] == CONSTRAINT_NAME
    )


def test_dry_run_inventories_legacy_rows_without_mutating(engine):
    _install_legacy_constraint(engine)
    case_id = _create_case(engine)
    for index, state in enumerate(DIRECT_STATES):
        _insert_offer(engine, case_id, suffix=index, offer_type="directa", state=state)
    definition_before = _constraint_definition(engine)

    report = describe_help_offer_states_migration(engine)

    assert report.mode == "dry-run"
    assert report.schema_version == HELP_OFFER_STATES_SCHEMA_VERSION
    assert report.target == {
        "engine": "postgresql+psycopg2",
        "database": urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1],
        "schema": "public",
    }
    assert "password" not in json.dumps(report.public_dict()).lower()
    assert "postgresql://" not in json.dumps(report.public_dict()).lower()
    assert report.definition_state == "legacy"
    assert report.ready is False
    assert report.incompatible_row_ids == []
    assert report.reversible is True
    assert sum(report.row_counts.values()) == len(DIRECT_STATES)
    assert report.planned_sql
    assert _constraint_definition(engine) == definition_before


def test_apply_preserves_direct_rows_and_is_idempotent(engine):
    _install_legacy_constraint(engine)
    case_id = _create_case(engine)
    for index, state in enumerate(DIRECT_STATES):
        _insert_offer(engine, case_id, suffix=index, offer_type="directa", state=state)

    first = apply_help_offer_states_migration(engine)
    second = apply_help_offer_states_migration(engine)

    assert first.constraint_changed is True
    assert first.ready is True
    assert second.constraint_changed is False
    assert second.ready is True
    assert second.definition_state == "target"
    assert first.row_counts == second.row_counts
    assert sum(second.row_counts.values()) == len(DIRECT_STATES)


def test_target_enforces_complete_type_state_matrix(engine):
    case_id = _create_case(engine)
    assert classify_help_offer_state_constraint(engine) == "target"
    all_states = sorted(set(DIRECT_STATES) | set(EMPLOYMENT_STATES) | {"estado_desconocido"})
    expected = {
        "directa": set(DIRECT_STATES),
        "empleo": set(EMPLOYMENT_STATES),
        "tipo_desconocido": set(),
    }
    for offer_type, allowed_states in expected.items():
        for index, state in enumerate(all_states):
            operation = lambda: _insert_offer(
                engine,
                case_id,
                suffix=f"{offer_type}-{index}",
                offer_type=offer_type,
                state=state,
            )
            if state in allowed_states:
                operation()
            else:
                with pytest.raises(IntegrityError):
                    operation()


@pytest.mark.parametrize("condition", [
    (
        "tipo = 'directa' AND (estado IN "
        "('pendiente', 'contactada', 'completada', 'rechazada', 'cancelada') OR "
        "tipo = 'empleo') AND estado IN "
        "('pendiente_respuesta', 'aceptada', 'rechazada', 'vencida', 'cancelada')"
    ),
    (
        "(tipo = 'directa' AND estado IN "
        "('pendiente', 'contactada', 'completada', 'rechazada', 'cancelada') OR "
        "tipo = 'empleo') AND estado IN "
        "('pendiente_respuesta', 'aceptada', 'rechazada', 'vencida', 'cancelada')"
    ),
])
def test_same_tokens_with_different_logical_grouping_are_unknown(engine, condition):
    _install_constraint(engine, condition)
    definition_before = _constraint_definition(engine)

    report = describe_help_offer_states_migration(engine)

    assert classify_help_offer_state_constraint(engine) == "unknown"
    assert report.definition_state == "unknown"
    assert report.ready is False
    assert report.blockers == ["constraint_unknown"]
    with pytest.raises(HelpOfferStatesDefinitionError):
        apply_help_offer_states_migration(engine)
    assert _constraint_definition(engine) == definition_before


UPPERCASE_SQL_TARGET_CONDITION = (
    "(TIPO = 'directa' AND ESTADO IN "
    "('pendiente', 'contactada', 'completada', 'rechazada', 'cancelada')) OR "
    "(TIPO = 'empleo' AND ESTADO IN "
    "('pendiente_respuesta', 'aceptada', 'rechazada', 'vencida', 'cancelada'))"
)


def test_uppercase_sql_keywords_and_identifiers_preserve_target_recognition(engine):
    _install_constraint(engine, UPPERCASE_SQL_TARGET_CONDITION)

    report = describe_help_offer_states_migration(engine)

    assert classify_help_offer_state_constraint(engine) == "target"
    assert report.definition_state == "target"
    assert report.ready is True


@pytest.mark.parametrize("condition", [
    TARGET_CONDITION.upper(),
    TARGET_CONDITION.replace("'directa'", "'DIRECTA'"),
    TARGET_CONDITION.replace("'pendiente_respuesta'", "'PENDIENTE_RESPUESTA'"),
    TARGET_CONDITION.replace("'empleo'", "'EmPlEo'"),
    TARGET_CONDITION.replace("'directa'", "'direc''ta'"),
])
def test_literal_variants_are_unknown_and_immutable(engine, condition):
    _install_constraint(engine, condition)
    definition_before = _constraint_definition(engine)

    report = describe_help_offer_states_migration(engine)

    assert classify_help_offer_state_constraint(engine) == "unknown"
    assert report.definition_state == "unknown"
    assert report.ready is False
    assert report.blockers == ["constraint_unknown"]
    with pytest.raises(HelpOfferStatesDefinitionError):
        apply_help_offer_states_migration(engine)
    assert _constraint_definition(engine) == definition_before


def test_quoted_identifier_with_distinct_capitalization_is_unknown_and_immutable(engine):
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE ofertas_ayuda ADD COLUMN "Tipo" VARCHAR(20)'))
    condition = TARGET_CONDITION.replace("tipo =", '"Tipo" =')
    _install_constraint(engine, condition)
    definition_before = _constraint_definition(engine)

    report = describe_help_offer_states_migration(engine)

    assert classify_help_offer_state_constraint(engine) == "unknown"
    assert report.definition_state == "unknown"
    assert report.ready is False
    assert report.blockers == ["constraint_unknown"]
    with pytest.raises(HelpOfferStatesDefinitionError):
        apply_help_offer_states_migration(engine)
    assert _constraint_definition(engine) == definition_before


def test_quoted_identifier_tokens_preserve_capitalization():
    lower = _condition_structure('"tipo" = \'directa\'')
    upper = _condition_structure('"Tipo" = \'directa\'')

    assert lower is not None
    assert upper is not None
    assert lower != upper


def test_apply_fails_closed_for_incompatible_existing_rows(engine):
    _install_legacy_constraint(engine)
    case_id = _create_case(engine)
    bad_id = _insert_offer(engine, case_id, suffix="incompatible", offer_type="empleo", state="pendiente")

    report = describe_help_offer_states_migration(engine)
    assert report.incompatible_row_ids == [bad_id]
    assert report.blockers == ["incompatible_rows"]

    with pytest.raises(HelpOfferStatesDataError):
        apply_help_offer_states_migration(engine)
    assert classify_help_offer_state_constraint(engine) == "legacy"


def test_apply_fails_closed_for_unknown_constraint_definition(engine):
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE ofertas_ayuda DROP CONSTRAINT {CONSTRAINT_NAME}"))
        connection.execute(text(
            f"ALTER TABLE ofertas_ayuda ADD CONSTRAINT {CONSTRAINT_NAME} CHECK (estado <> 'inventado')"
        ))

    report = describe_help_offer_states_migration(engine)
    assert report.definition_state == "unknown"
    assert report.blockers == ["constraint_unknown"]
    with pytest.raises(HelpOfferStatesDefinitionError):
        apply_help_offer_states_migration(engine)


def test_rollback_restores_legacy_only_without_labor_only_states(engine):
    case_id = _create_case(engine)
    _insert_offer(engine, case_id, suffix="direct", offer_type="directa", state="pendiente")

    report = rollback_help_offer_states_migration(engine)

    assert report.definition_state == "legacy"
    assert report.constraint_changed is True


def test_rollback_blocks_without_transforming_labor_rows(engine):
    case_id = _create_case(engine)
    labor_id = _insert_offer(
        engine,
        case_id,
        suffix="labor",
        offer_type="empleo",
        state="pendiente_respuesta",
    )

    with pytest.raises(HelpOfferStatesDataError):
        rollback_help_offer_states_migration(engine)

    assert classify_help_offer_state_constraint(engine) == "target"
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT estado FROM ofertas_ayuda WHERE id = :id"), {"id": labor_id}
        ).scalar_one() == "pendiente_respuesta"


def test_cli_process_dry_run_and_double_apply_are_private_and_idempotent(engine):
    _install_legacy_constraint(engine)
    case_id = _create_case(engine)
    contact_sentinel = "CONTACT_SENTINEL_GATE1A"
    payload_sentinel = "PAYLOAD_SENTINEL_GATE1A"
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO ofertas_ayuda "
            "(caso_id, organizacion_id, tipo, actor_donante_uid, actor_donante_email, "
            "contacto_cifrado, payload_cifrado, payload_version, estado) "
            "VALUES (:case_id, 'org-test', 'directa', 'privacy-user', 'privacy@example.test', "
            ":contact, :payload, 1, 'pendiente')"
        ), {"case_id": case_id, "contact": contact_sentinel, "payload": payload_sentinel})
    parsed_url = urlsplit(TEST_DATABASE_URL)
    assert parsed_url.username and parsed_url.password
    credential_sentinels = [f'"{parsed_url.username}"', parsed_url.password]
    command = [sys.executable, "-m", "scripts.migrate_help_offer_states"]
    command_env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL}
    repository_root = Path(__file__).resolve().parents[1]

    def run_cli(mode):
        completed = subprocess.run(
            [*command, mode],
            cwd=repository_root,
            env=command_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        report = json.loads(completed.stdout)
        for output in [completed.stdout, completed.stderr, json.dumps(report)]:
            for sentinel in [*credential_sentinels, contact_sentinel, payload_sentinel]:
                assert sentinel not in output
        return report

    definition_before = _constraint_definition(engine)
    dry_run = run_cli("--dry-run")
    assert dry_run["mode"] == "dry-run"
    assert dry_run["definition_state"] == "legacy"
    assert _constraint_definition(engine) == definition_before

    first = run_cli("--apply")
    second = run_cli("--apply")
    assert first["mode"] == "apply"
    assert first["constraint_changed"] is True
    assert first["ready"] is True
    assert second["constraint_changed"] is False
    assert second["ready"] is True


def test_apply_refuses_non_local_target_before_connecting():
    remote_engine = create_engine("postgresql://user:pass@remote-host.example.com/rescate_prod")
    with pytest.raises(RetentionTargetError):
        apply_help_offer_states_migration(remote_engine)


def test_apply_refuses_local_persistent_database_even_with_disposable_marker():
    persistent_engine = create_engine("postgresql://user:pass@localhost/rescate")
    with pytest.raises(RetentionTargetError):
        apply_help_offer_states_migration(
            persistent_engine,
            local_test_marker="DISPOSABLE_LOCAL_TEST",
        )
