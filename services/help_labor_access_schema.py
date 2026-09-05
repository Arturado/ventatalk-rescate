from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from database import Base
import models
from services.help_retention import RetentionTargetError, assert_apply_target


HELP_LABOR_ACCESS_SCHEMA_VERSION = "help-labor-access-v1"
NEW_TABLES = (
    "ofertas_ayuda_desafios_acceso",
    "ofertas_ayuda_sesiones",
    "ofertas_ayuda_transiciones_terminales",
    "ofertas_ayuda_liberaciones_contacto",
)
OFFER_COLUMNS = {
    "actor_donante_email_cifrado",
    "expires_at",
    "lock_version",
    "terminal_at",
}
EXTENSION_COLUMNS = {
    "casos_ayuda_idempotencia": "oferta_id",
    "casos_ayuda_notificaciones": "oferta_id",
}
REUSABLE_ACCESS_COLUMNS = {
    "ofertas_ayuda_desafios_acceso": {
        "last_redeemed_at", "redeem_count", "rate_window_started_at", "rate_window_count",
    },
    "ofertas_ayuda_sesiones": {"client_context_hash"},
}


class LaborAccessMigrationError(RuntimeError):
    pass


class LaborAccessDefinitionError(LaborAccessMigrationError):
    pass


class LaborAccessDataError(LaborAccessMigrationError):
    pass


@dataclass(frozen=True)
class LaborAccessMigrationReport:
    mode: str
    schema_version: str
    definition_state: str
    ready: bool
    row_counts: dict = field(default_factory=dict)
    actions: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    reversible: bool = False
    backup_reference: str | None = None

    def public_dict(self):
        return {
            "mode": self.mode,
            "schema_version": self.schema_version,
            "definition_state": self.definition_state,
            "ready": self.ready,
            "row_counts": self.row_counts,
            "actions": self.actions,
            "blockers": self.blockers,
            "reversible": self.reversible,
            "backup_reference": self.backup_reference,
        }


def _assert_disposable_postgres(engine: Engine):
    assert_apply_target(engine)
    if not engine.url.drivername.startswith("postgresql"):
        raise RetentionTargetError("Labor access migration requires PostgreSQL")
    if not str(engine.url.database or "").endswith("_test"):
        raise RetentionTargetError("Labor access migration requires disposable *_test PostgreSQL")


def _columns(inspector, table_name):
    if table_name not in inspector.get_table_names():
        return set()
    return {item["name"] for item in inspector.get_columns(table_name)}


def classify_labor_access_schema(connectable):
    inspector = inspect(connectable)
    tables = set(inspector.get_table_names())
    if "ofertas_ayuda" not in tables:
        return "missing"
    offer_columns = _columns(inspector, "ofertas_ayuda")
    target_tables = set(NEW_TABLES) <= tables
    reusable_columns = all(
        required <= _columns(inspector, table_name)
        for table_name, required in REUSABLE_ACCESS_COLUMNS.items()
    )
    target_columns = OFFER_COLUMNS <= offer_columns
    extensions = all(column in _columns(inspector, table) for table, column in EXTENSION_COLUMNS.items())
    old_email = "actor_donante_email" in offer_columns
    obsolete_one_use = "consumed_at" in _columns(inspector, "ofertas_ayuda_desafios_acceso")
    if target_tables and reusable_columns and target_columns and extensions and not old_email and not obsolete_one_use:
        return "target"
    no_target_tables = not (set(NEW_TABLES) & tables)
    no_target_columns = not (OFFER_COLUMNS & offer_columns)
    no_extensions = all(column not in _columns(inspector, table) for table, column in EXTENSION_COLUMNS.items())
    if no_target_tables and no_target_columns and no_extensions and old_email:
        return "legacy"
    return "unknown"


def _row_counts(connection):
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    counts = {}
    for table_name in ("ofertas_ayuda", *NEW_TABLES):
        counts[table_name] = (
            int(connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one())
            if table_name in tables else 0
        )
    counts["ofertas_laborales"] = (
        int(connection.execute(text("SELECT COUNT(*) FROM ofertas_ayuda WHERE tipo='empleo'")).scalar_one())
        if "ofertas_ayuda" in tables else 0
    )
    return counts


def _rollback_blockers(connection):
    counts = _row_counts(connection)
    blockers = [name for name in NEW_TABLES if counts[name]]
    inspector = inspect(connection)
    for table_name in EXTENSION_COLUMNS:
        if "oferta_id" in _columns(inspector, table_name):
            total = connection.execute(text(
                f"SELECT COUNT(*) FROM {table_name} WHERE oferta_id IS NOT NULL"
            )).scalar_one()
            if total:
                blockers.append(f"{table_name}.oferta_id")
    if "destinatario_email_hash" in _columns(inspector, "casos_ayuda_notificaciones"):
        null_hashes = connection.execute(text(
            "SELECT COUNT(*) FROM casos_ayuda_notificaciones WHERE destinatario_email_hash IS NULL"
        )).scalar_one()
        if null_hashes:
            blockers.append("casos_ayuda_notificaciones.destinatario_email_hash")
    return blockers


def _planned_actions(state):
    if state == "target":
        return []
    return [
        "add nullable target columns",
        "encrypt and verify legacy donor email",
        "backfill expiration and lock version",
        "extend idempotency and notification outbox",
        "create labor access tables, constraints, uniques and indexes",
        "drop plaintext donor email after verification",
    ]


def describe_labor_access_migration(engine: Engine):
    state = classify_labor_access_schema(engine)
    with engine.connect() as connection:
        counts = _row_counts(connection)
        rollback_blockers = _rollback_blockers(connection) if state == "target" else []
    blockers = [] if state in {"legacy", "target"} else [f"schema_{state}"]
    return LaborAccessMigrationReport(
        mode="dry-run",
        schema_version=HELP_LABOR_ACCESS_SCHEMA_VERSION,
        definition_state=state,
        ready=state == "target",
        row_counts=counts,
        actions=_planned_actions(state),
        blockers=blockers,
        reversible=state == "target" and not rollback_blockers,
    )


def _add_column(connection, table_name, definition):
    column_name = definition.split()[0]
    if column_name not in _columns(inspect(connection), table_name):
        connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {definition}"))


def _add_fk_if_missing(connection, table_name, constraint_name):
    names = {item.get("name") for item in inspect(connection).get_foreign_keys(table_name)}
    if constraint_name not in names:
        connection.execute(text(
            f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} "
            "FOREIGN KEY (oferta_id) REFERENCES ofertas_ayuda(id) ON DELETE RESTRICT"
        ))


def _add_offer_checks(connection):
    checks = {item.get("name") for item in inspect(connection).get_check_constraints("ofertas_ayuda")}
    definitions = {
        "ck_ofertas_ayuda_lock_version": "lock_version >= 0",
        "ck_ofertas_ayuda_empleo_expiracion": (
            "tipo <> 'empleo' OR (expires_at IS NOT NULL AND created_at < expires_at)"
        ),
        "ck_ofertas_ayuda_empleo_terminal_at": (
            "tipo <> 'empleo' OR ((estado = 'pendiente_respuesta' AND terminal_at IS NULL) OR "
            "(estado IN ('aceptada','rechazada','vencida','cancelada') AND terminal_at IS NOT NULL))"
        ),
    }
    for name, condition in definitions.items():
        if name not in checks:
            connection.execute(text(
                f"ALTER TABLE ofertas_ayuda ADD CONSTRAINT {name} CHECK ({condition})"
            ))


def _create_target_indexes(connection):
    statements = (
        "CREATE INDEX IF NOT EXISTS ix_ofertas_ayuda_empleo_vencimiento ON ofertas_ayuda "
        "(expires_at, id) WHERE tipo='empleo' AND estado='pendiente_respuesta'",
        "CREATE INDEX IF NOT EXISTS ix_ofertas_ayuda_actor_donante_uid ON ofertas_ayuda "
        "(actor_donante_uid, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_casos_ayuda_idempotencia_oferta_operacion ON "
        "casos_ayuda_idempotencia (oferta_id, operacion)",
        "CREATE INDEX IF NOT EXISTS ix_casos_ayuda_notificaciones_oferta_estado ON "
        "casos_ayuda_notificaciones (oferta_id, estado, proximo_intento_at)",
    )
    for statement in statements:
        connection.execute(text(statement))


def _add_notification_email_check(connection):
    checks = {
        item.get("name")
        for item in inspect(connection).get_check_constraints("casos_ayuda_notificaciones")
    }
    name = "ck_casos_ayuda_notificacion_email_laboral"
    if name not in checks:
        connection.execute(text(
            "ALTER TABLE casos_ayuda_notificaciones ADD CONSTRAINT "
            f"{name} CHECK ("
            "(evento_tipo LIKE 'oferta_laboral_%' AND destinatario_email_hash IS NULL) OR "
            "(evento_tipo NOT LIKE 'oferta_laboral_%' AND length(destinatario_email_hash) = 64))"
        ))


def _replace_notification_event_check(connection, *, labor_enabled):
    connection.execute(text(
        "ALTER TABLE casos_ayuda_notificaciones DROP CONSTRAINT IF EXISTS "
        "ck_casos_ayuda_notificacion_evento"
    ))
    legacy = (
        "'ayuda_reportada','ayuda_confirmada','problema_reportado',"
        "'revision_resuelta','meta_alcanzada','acceso_temporal','account_changed'"
    )
    labor = (
        ",'oferta_laboral_aceptada','oferta_laboral_rechazada',"
        "'oferta_laboral_cancelada','oferta_laboral_vencida'"
    ) if labor_enabled else ""
    connection.execute(text(
        "ALTER TABLE casos_ayuda_notificaciones ADD CONSTRAINT "
        f"ck_casos_ayuda_notificacion_evento CHECK (evento_tipo IN ({legacy}{labor}))"
    ))


def apply_labor_access_migration(
    engine: Engine,
    *,
    email_encryptor=None,
    email_decryptor=None,
    backup_reference=None,
):
    _assert_disposable_postgres(engine)
    before = describe_labor_access_migration(engine)
    if before.definition_state == "target":
        return LaborAccessMigrationReport(**{
            **before.__dict__, "mode": "apply", "backup_reference": backup_reference,
        })
    if before.definition_state != "legacy":
        raise LaborAccessDefinitionError(
            f"Labor access schema definition is {before.definition_state}; expected legacy or target"
        )
    if not backup_reference:
        raise LaborAccessMigrationError("Apply requires a recorded backup reference")

    with engine.begin() as connection:
        _add_column(connection, "ofertas_ayuda", "actor_donante_email_cifrado TEXT")
        _add_column(connection, "ofertas_ayuda", "expires_at TIMESTAMP WITH TIME ZONE")
        _add_column(connection, "ofertas_ayuda", "lock_version INTEGER NOT NULL DEFAULT 0")
        _add_column(connection, "ofertas_ayuda", "terminal_at TIMESTAMP WITH TIME ZONE")
        _add_column(connection, "casos_ayuda_idempotencia", "oferta_id INTEGER")
        _add_column(connection, "casos_ayuda_notificaciones", "oferta_id INTEGER")
        _add_fk_if_missing(connection, "casos_ayuda_idempotencia", "fk_casos_ayuda_idempotencia_oferta")
        _add_fk_if_missing(connection, "casos_ayuda_notificaciones", "fk_casos_ayuda_notificaciones_oferta")
        connection.execute(text(
            "ALTER TABLE casos_ayuda_notificaciones ALTER COLUMN destinatario_email_hash DROP NOT NULL"
        ))

        rows = connection.execute(text(
            "SELECT id, actor_donante_email, tipo, created_at FROM ofertas_ayuda ORDER BY id"
        )).mappings().all()
        if rows and (email_encryptor is None or email_decryptor is None):
            raise LaborAccessDataError("Legacy donor emails require encryptor and verifier")
        for row in rows:
            ciphertext = email_encryptor(row["actor_donante_email"])
            if email_decryptor(ciphertext) != row["actor_donante_email"]:
                raise LaborAccessDataError("Encrypted donor email verification failed")
            expires_at = row["created_at"] + timedelta(days=7) if row["tipo"] == "empleo" else None
            connection.execute(text(
                "UPDATE ofertas_ayuda SET actor_donante_email_cifrado=:ciphertext, "
                "expires_at=:expires_at WHERE id=:id"
            ), {"ciphertext": ciphertext, "expires_at": expires_at, "id": row["id"]})
        connection.execute(text(
            "ALTER TABLE ofertas_ayuda ALTER COLUMN actor_donante_email_cifrado SET NOT NULL"
        ))
        _add_offer_checks(connection)
        _add_notification_email_check(connection)
        _replace_notification_event_check(connection, labor_enabled=True)
        _create_target_indexes(connection)
        for table_name in NEW_TABLES:
            Base.metadata.tables[table_name].create(bind=connection, checkfirst=True)
        connection.execute(text("ALTER TABLE ofertas_ayuda DROP COLUMN actor_donante_email"))

    after = describe_labor_access_migration(engine)
    if not after.ready or after.row_counts["ofertas_ayuda"] != before.row_counts["ofertas_ayuda"]:
        raise LaborAccessDataError("Migration did not preserve offers or reach target")
    return LaborAccessMigrationReport(**{
        **after.__dict__, "mode": "apply", "actions": before.actions,
        "backup_reference": backup_reference,
    })


def rollback_labor_access_migration(
    engine: Engine,
    *,
    email_decryptor=None,
    backup_reference=None,
):
    _assert_disposable_postgres(engine)
    if classify_labor_access_schema(engine) != "target":
        raise LaborAccessDefinitionError("Rollback requires target schema")
    if not backup_reference:
        raise LaborAccessMigrationError("Rollback requires a recorded backup reference")
    with engine.begin() as connection:
        blockers = _rollback_blockers(connection)
        if blockers:
            raise LaborAccessDataError(f"Rollback blocked by incompatible data: {blockers}")
        offer_count = connection.execute(text("SELECT COUNT(*) FROM ofertas_ayuda")).scalar_one()
        if offer_count and email_decryptor is None:
            raise LaborAccessDataError("Rollback with offers requires donor email decryptor")
        connection.execute(text("ALTER TABLE ofertas_ayuda ADD COLUMN actor_donante_email VARCHAR(320)"))
        rows = connection.execute(text(
            "SELECT id, actor_donante_email_cifrado FROM ofertas_ayuda ORDER BY id"
        )).mappings().all()
        for row in rows:
            connection.execute(text(
                "UPDATE ofertas_ayuda SET actor_donante_email=:email WHERE id=:id"
            ), {"email": email_decryptor(row["actor_donante_email_cifrado"]), "id": row["id"]})
        connection.execute(text("ALTER TABLE ofertas_ayuda ALTER COLUMN actor_donante_email SET NOT NULL"))
        for table_name in reversed(NEW_TABLES):
            Base.metadata.tables[table_name].drop(bind=connection, checkfirst=True)
        for table_name, constraint_name in (
            ("casos_ayuda_idempotencia", "fk_casos_ayuda_idempotencia_oferta"),
            ("casos_ayuda_notificaciones", "fk_casos_ayuda_notificaciones_oferta"),
        ):
            connection.execute(text(f"ALTER TABLE {table_name} DROP CONSTRAINT IF EXISTS {constraint_name}"))
            connection.execute(text(f"ALTER TABLE {table_name} DROP COLUMN oferta_id"))
        connection.execute(text(
            "ALTER TABLE casos_ayuda_notificaciones ALTER COLUMN destinatario_email_hash SET NOT NULL"
        ))
        connection.execute(text(
            "ALTER TABLE casos_ayuda_notificaciones DROP CONSTRAINT IF EXISTS "
            "ck_casos_ayuda_notificacion_email_laboral"
        ))
        _replace_notification_event_check(connection, labor_enabled=False)
        for constraint_name in (
            "ck_ofertas_ayuda_lock_version",
            "ck_ofertas_ayuda_empleo_expiracion",
            "ck_ofertas_ayuda_empleo_terminal_at",
        ):
            connection.execute(text(f"ALTER TABLE ofertas_ayuda DROP CONSTRAINT IF EXISTS {constraint_name}"))
        for column_name in ("actor_donante_email_cifrado", "expires_at", "lock_version", "terminal_at"):
            connection.execute(text(f"ALTER TABLE ofertas_ayuda DROP COLUMN {column_name}"))
    state = classify_labor_access_schema(engine)
    if state != "legacy":
        raise LaborAccessDataError(f"Rollback ended in unexpected state: {state}")
    with engine.connect() as connection:
        counts = _row_counts(connection)
    return LaborAccessMigrationReport(
        mode="rollback",
        schema_version=HELP_LABOR_ACCESS_SCHEMA_VERSION,
        definition_state=state,
        ready=False,
        row_counts=counts,
        actions=["restore legacy schema"],
        blockers=[],
        reversible=True,
        backup_reference=backup_reference,
    )
