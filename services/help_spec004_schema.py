from dataclasses import dataclass, field

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from services.help_retention import assert_apply_target


HELP_SPEC004_SCHEMA_VERSION = "help-spec004-v1"

_NEW_CASOS_AYUDA_V2_COLUMNS = {
    "tipo_sujeto": "VARCHAR(30) NOT NULL DEFAULT 'persona'",
    "acepta_ayuda_monetaria": "BOOLEAN NOT NULL DEFAULT TRUE",
    "acepta_ayuda_directa": "BOOLEAN NOT NULL DEFAULT FALSE",
    "detalle_condicional_cifrado": "TEXT",
    "detalle_condicional_version": "INTEGER",
}

_NULLABLE_CASOS_AYUDA_V2_COLUMNS = ["beneficiario_id", "meta_monto", "meta_moneda"]

_NEW_CASOS_AYUDA_V2_CHECKS = {
    "ck_casos_ayuda_v2_tipo_sujeto": "tipo_sujeto IN ('persona', 'campana_organizacion')",
    "ck_casos_ayuda_v2_sujeto_beneficiario": (
        "(tipo_sujeto = 'persona' AND beneficiario_id IS NOT NULL) OR "
        "(tipo_sujeto = 'campana_organizacion' AND beneficiario_id IS NULL)"
    ),
    "ck_casos_ayuda_v2_modalidad_monetaria_meta": (
        "(NOT acepta_ayuda_monetaria AND meta_monto IS NULL AND meta_moneda IS NULL) OR "
        "(acepta_ayuda_monetaria AND meta_monto IS NOT NULL AND meta_moneda IS NOT NULL)"
    ),
    "ck_casos_ayuda_v2_modalidades_no_vacias": (
        "categoria = 'empleo' OR acepta_ayuda_monetaria OR acepta_ayuda_directa"
    ),
    "ck_casos_ayuda_v2_empleo_coherente": (
        "categoria <> 'empleo' OR "
        "(tipo_sujeto = 'persona' AND NOT acepta_ayuda_monetaria AND NOT acepta_ayuda_directa)"
    ),
}

# Leaf-to-root deletion order for Help V2 pilot data: every table here either
# holds pilot case/beneficiary rows directly or references them by FK.
# Reference/security-ledger tables that are not pilot-case-scoped (BCV rate
# cache, donor terms acceptance, temporary-access rate-limit attempts) are
# intentionally excluded from this manifest.
_PILOT_CLEANUP_TABLES = [
    "casos_ayuda_acceso_cuentas",
    "casos_ayuda_accesos_cuentas_donantes",
    "casos_ayuda_accesos",
    "casos_ayuda_comprobantes",
    "casos_ayuda_confirmaciones",
    "casos_ayuda_ayudas",
    "casos_ayuda_cuentas",
    "casos_ayuda_documentos",
    "casos_ayuda_consentimientos",
    "casos_ayuda_publicaciones_versiones",
    "casos_ayuda_publicaciones",
    "casos_ayuda_notificaciones",
    "casos_ayuda_retencion_legal",
    "casos_ayuda_auditoria",
    "casos_ayuda_idempotencia",
    "casos_ayuda_v2",
    "beneficiarios_ayuda",
]


class Spec004MigrationError(RuntimeError):
    pass


class Spec004MigrationDataError(Spec004MigrationError):
    pass


@dataclass(frozen=True)
class Spec004MigrationReport:
    mode: str
    schema_version: str
    ready: bool
    pilot_counts: dict
    columns_added: list = field(default_factory=list)
    columns_relaxed: list = field(default_factory=list)
    constraints_added: list = field(default_factory=list)

    def public_dict(self):
        return {
            "mode": self.mode,
            "schema_version": self.schema_version,
            "ready": self.ready,
            "pilot_counts": self.pilot_counts,
            "columns_added": self.columns_added,
            "columns_relaxed": self.columns_relaxed,
            "constraints_added": self.constraints_added,
        }


def _existing_columns(connectable, table_name):
    return {column["name"] for column in inspect(connectable).get_columns(table_name)}


def _existing_check_names(connectable, table_name):
    return {check["name"] for check in inspect(connectable).get_check_constraints(table_name)}


def _still_not_nullable(connectable, table_name, column_names):
    columns = {column["name"]: column for column in inspect(connectable).get_columns(table_name)}
    return [
        name for name in column_names
        if name in columns and not columns[name].get("nullable", True)
    ]


def _pilot_counts(connection):
    counts = {}
    for table_name in _PILOT_CLEANUP_TABLES:
        counts[table_name] = connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
    return counts


def is_help_spec004_ready(engine: Engine) -> bool:
    inspector = inspect(engine)
    if "casos_ayuda_v2" not in inspector.get_table_names():
        return False
    existing_columns = {column["name"] for column in inspector.get_columns("casos_ayuda_v2")}
    if not set(_NEW_CASOS_AYUDA_V2_COLUMNS).issubset(existing_columns):
        return False
    if _still_not_nullable(engine, "casos_ayuda_v2", _NULLABLE_CASOS_AYUDA_V2_COLUMNS):
        return False
    existing_checks = {check["name"] for check in inspector.get_check_constraints("casos_ayuda_v2")}
    return set(_NEW_CASOS_AYUDA_V2_CHECKS).issubset(existing_checks)


def describe_help_spec004_migration(engine: Engine) -> Spec004MigrationReport:
    ready = is_help_spec004_ready(engine)
    with engine.connect() as connection:
        pilot_counts = _pilot_counts(connection)
    if ready or "casos_ayuda_v2" not in inspect(engine).get_table_names():
        pending_columns = sorted(_NEW_CASOS_AYUDA_V2_COLUMNS) if not ready else []
        pending_relax = sorted(_NULLABLE_CASOS_AYUDA_V2_COLUMNS) if not ready else []
    else:
        existing_columns = _existing_columns(engine, "casos_ayuda_v2")
        pending_columns = sorted(set(_NEW_CASOS_AYUDA_V2_COLUMNS) - existing_columns)
        pending_relax = sorted(_still_not_nullable(engine, "casos_ayuda_v2", _NULLABLE_CASOS_AYUDA_V2_COLUMNS))
    return Spec004MigrationReport(
        mode="dry-run",
        schema_version=HELP_SPEC004_SCHEMA_VERSION,
        ready=ready,
        pilot_counts=pilot_counts,
        columns_added=pending_columns,
        columns_relaxed=pending_relax,
        constraints_added=[] if ready else sorted(_NEW_CASOS_AYUDA_V2_CHECKS),
    )


def apply_help_spec004_migration(engine: Engine, *, local_test_marker=None) -> Spec004MigrationReport:
    assert_apply_target(engine, local_test_marker=local_test_marker)
    with engine.begin() as connection:
        pilot_counts = _pilot_counts(connection)
        try:
            for table_name in _PILOT_CLEANUP_TABLES:
                connection.execute(text(f"DELETE FROM {table_name}"))
        except IntegrityError as exc:
            raise Spec004MigrationDataError(
                "No se pudo limpiar el piloto Help V2: existen filas inesperadas que "
                "todavia referencian datos del piloto fuera del manifiesto de limpieza"
            ) from exc

        remaining = connection.execute(text("SELECT COUNT(*) FROM casos_ayuda_v2")).scalar()
        remaining += connection.execute(text("SELECT COUNT(*) FROM beneficiarios_ayuda")).scalar()
        if remaining:
            raise Spec004MigrationDataError(
                "Quedaron filas inesperadas en casos_ayuda_v2/beneficiarios_ayuda tras la "
                "limpieza del piloto Help V2"
            )

        existing_columns = _existing_columns(connection, "casos_ayuda_v2")
        columns_added = []
        for column_name, ddl in _NEW_CASOS_AYUDA_V2_COLUMNS.items():
            if column_name not in existing_columns:
                connection.execute(text(f"ALTER TABLE casos_ayuda_v2 ADD COLUMN {column_name} {ddl}"))
                columns_added.append(column_name)

        columns_relaxed = []
        for column_name in _still_not_nullable(connection, "casos_ayuda_v2", _NULLABLE_CASOS_AYUDA_V2_COLUMNS):
            connection.execute(text(f"ALTER TABLE casos_ayuda_v2 ALTER COLUMN {column_name} DROP NOT NULL"))
            columns_relaxed.append(column_name)

        existing_checks = _existing_check_names(connection, "casos_ayuda_v2")
        constraints_added = []
        for constraint_name, condition in _NEW_CASOS_AYUDA_V2_CHECKS.items():
            if constraint_name not in existing_checks:
                connection.execute(
                    text(f"ALTER TABLE casos_ayuda_v2 ADD CONSTRAINT {constraint_name} CHECK ({condition})")
                )
                constraints_added.append(constraint_name)

    return Spec004MigrationReport(
        mode="apply",
        schema_version=HELP_SPEC004_SCHEMA_VERSION,
        ready=is_help_spec004_ready(engine),
        pilot_counts=pilot_counts,
        columns_added=columns_added,
        columns_relaxed=columns_relaxed,
        constraints_added=constraints_added,
    )
