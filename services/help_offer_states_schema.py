from dataclasses import dataclass, field
import re

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from services.help_retention import RetentionTargetError, assert_apply_target


HELP_OFFER_STATES_SCHEMA_VERSION = "help-offer-states-v1"
TABLE_NAME = "ofertas_ayuda"
CONSTRAINT_NAME = "ck_ofertas_ayuda_estado"

DIRECT_STATES = ("pendiente", "contactada", "completada", "rechazada", "cancelada")
EMPLOYMENT_STATES = ("pendiente_respuesta", "aceptada", "rechazada", "vencida", "cancelada")

LEGACY_CONDITION = "estado IN ('pendiente', 'contactada', 'completada', 'rechazada', 'cancelada')"
TARGET_CONDITION = (
    "(tipo = 'directa' AND estado IN "
    "('pendiente', 'contactada', 'completada', 'rechazada', 'cancelada')) OR "
    "(tipo = 'empleo' AND estado IN "
    "('pendiente_respuesta', 'aceptada', 'rechazada', 'vencida', 'cancelada'))"
)


class HelpOfferStatesMigrationError(RuntimeError):
    pass


class HelpOfferStatesDefinitionError(HelpOfferStatesMigrationError):
    pass


class HelpOfferStatesDataError(HelpOfferStatesMigrationError):
    pass


@dataclass(frozen=True)
class HelpOfferStatesMigrationReport:
    mode: str
    schema_version: str
    target: dict
    ready: bool
    definition_state: str
    row_counts: dict = field(default_factory=dict)
    incompatible_row_ids: list = field(default_factory=list)
    planned_sql: list = field(default_factory=list)
    constraint_changed: bool = False
    reversible: bool = False
    blockers: list = field(default_factory=list)

    def public_dict(self):
        return {
            "mode": self.mode,
            "schema_version": self.schema_version,
            "target": self.target,
            "ready": self.ready,
            "definition_state": self.definition_state,
            "row_counts": self.row_counts,
            "incompatible_row_ids": self.incompatible_row_ids,
            "planned_sql": self.planned_sql,
            "constraint_changed": self.constraint_changed,
            "reversible": self.reversible,
            "blockers": self.blockers,
        }


_SQL_KEYWORDS = {"and", "or", "in", "any", "array"}
_SQL_SYMBOLS = {"(", ")", "[", "]", ",", "="}


def _condition_tokens(value):
    source = str(value or "")
    tokens = []
    position = 0
    while position < len(source):
        character = source[position]
        if character.isspace():
            position += 1
            continue
        if source.startswith("::", position):
            tokens.append(("::", "::"))
            position += 2
            continue
        if character in _SQL_SYMBOLS:
            tokens.append((character, character))
            position += 1
            continue
        if character in {"'", '"'}:
            quote = character
            position += 1
            content = []
            while position < len(source):
                if source[position] != quote:
                    content.append(source[position])
                    position += 1
                    continue
                if position + 1 < len(source) and source[position + 1] == quote:
                    content.append(quote)
                    position += 2
                    continue
                position += 1
                token_kind = "string" if quote == "'" else "quoted_identifier"
                tokens.append((token_kind, "".join(content)))
                break
            else:
                return None
            continue
        word = re.match(r"[a-z_][a-z0-9_$]*", source[position:], re.IGNORECASE)
        if not word:
            return None
        raw_value = word.group(0)
        normalized_value = raw_value.lower()
        token_kind = normalized_value if normalized_value in _SQL_KEYWORDS else "identifier"
        tokens.append((token_kind, normalized_value))
        position += len(raw_value)
    return tokens


class _ConditionParser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.position = 0

    def parse(self):
        result = self._or_expression()
        if self.position != len(self.tokens):
            raise ValueError("Unexpected condition token")
        return result

    def _accept(self, kind):
        if self.position < len(self.tokens) and self.tokens[self.position][0] == kind:
            token = self.tokens[self.position]
            self.position += 1
            return token
        return None

    def _require(self, kind):
        token = self._accept(kind)
        if token is None:
            raise ValueError(f"Expected {kind}")
        return token

    def _or_expression(self):
        operands = [self._and_expression()]
        while self._accept("or"):
            operands.append(self._and_expression())
        return operands[0] if len(operands) == 1 else ("or", tuple(operands))

    def _and_expression(self):
        operands = [self._primary()]
        while self._accept("and"):
            operands.append(self._primary())
        return operands[0] if len(operands) == 1 else ("and", tuple(operands))

    def _primary(self):
        if self._accept("("):
            expression = self._or_expression()
            self._require(")")
            return expression
        return self._predicate()

    def _identifier(self):
        if self.position >= len(self.tokens):
            raise ValueError("Expected identifier")
        token = self.tokens[self.position]
        if token[0] not in {"identifier", "quoted_identifier"}:
            raise ValueError("Expected identifier")
        self.position += 1
        return token

    def _cast(self):
        if not self._accept("::"):
            return
        type_name = self._require("identifier")[1]
        if type_name == "character":
            if self._require("identifier")[1] != "varying":
                raise ValueError("Unsupported cast")
        elif type_name != "text":
            raise ValueError("Unsupported cast")
        if self._accept("["):
            self._require("]")

    def _string(self):
        value = self._require("string")[1]
        self._cast()
        return value

    def _string_list(self, closing_token):
        values = [self._string()]
        while self._accept(","):
            values.append(self._string())
        self._require(closing_token)
        return tuple(values)

    def _predicate(self):
        identifier = self._identifier()
        self._cast()
        if self._accept("="):
            if self._accept("any"):
                self._require("(")
                self._require("array")
                self._require("[")
                values = self._string_list("]")
                self._cast()
                self._require(")")
                return ("in", identifier, values)
            return ("equals", identifier, self._string())
        self._require("in")
        self._require("(")
        return ("in", identifier, self._string_list(")"))


def _condition_structure(value):
    tokens = _condition_tokens(value)
    if not tokens:
        return None
    try:
        return _ConditionParser(tokens).parse()
    except ValueError:
        return None


def _constraint_sql(connectable):
    inspector = inspect(connectable)
    if TABLE_NAME not in inspector.get_table_names():
        return None
    for check in inspector.get_check_constraints(TABLE_NAME):
        if check.get("name") == CONSTRAINT_NAME:
            return check.get("sqltext")
    return None


def classify_help_offer_state_constraint(connectable):
    current = _constraint_sql(connectable)
    if current is None:
        return "missing"
    structure = _condition_structure(current)
    if structure == _condition_structure(LEGACY_CONDITION):
        return "legacy"
    if structure == _condition_structure(TARGET_CONDITION):
        return "target"
    return "unknown"


def _row_inventory(connection):
    if TABLE_NAME not in inspect(connection).get_table_names():
        return {}, [], []
    rows = connection.execute(text(
        "SELECT tipo, estado, COUNT(*) AS total FROM ofertas_ayuda "
        "GROUP BY tipo, estado ORDER BY tipo, estado"
    )).mappings().all()
    counts = {f"{row['tipo']}:{row['estado']}": int(row["total"]) for row in rows}
    incompatible = connection.execute(text(
        "SELECT id FROM ofertas_ayuda WHERE NOT ("
        "(tipo = 'directa' AND estado IN "
        "('pendiente', 'contactada', 'completada', 'rechazada', 'cancelada')) OR "
        "(tipo = 'empleo' AND estado IN "
        "('pendiente_respuesta', 'aceptada', 'rechazada', 'vencida', 'cancelada'))"
        ") ORDER BY id"
    )).scalars().all()
    labor_only = connection.execute(text(
        "SELECT id FROM ofertas_ayuda WHERE estado IN "
        "('pendiente_respuesta', 'aceptada', 'vencida') ORDER BY id"
    )).scalars().all()
    return counts, list(incompatible), list(labor_only)


def _planned_sql():
    return [
        f"ALTER TABLE {TABLE_NAME} DROP CONSTRAINT {CONSTRAINT_NAME}",
        f"ALTER TABLE {TABLE_NAME} ADD CONSTRAINT {CONSTRAINT_NAME} CHECK ({TARGET_CONDITION})",
    ]


def _report(connectable, *, mode, changed=False):
    definition_state = classify_help_offer_state_constraint(connectable)
    with connectable.connect() if isinstance(connectable, Engine) else _nullcontext(connectable) as connection:
        report_engine = connection.engine
        target = {
            "engine": report_engine.url.drivername,
            "database": report_engine.url.database,
            "schema": connection.execute(text("SELECT current_schema()")).scalar_one(),
        }
        row_counts, incompatible_ids, labor_only_ids = _row_inventory(connection)
    blockers = []
    if definition_state not in {"legacy", "target"}:
        blockers.append(f"constraint_{definition_state}")
    if incompatible_ids:
        blockers.append("incompatible_rows")
    return HelpOfferStatesMigrationReport(
        mode=mode,
        schema_version=HELP_OFFER_STATES_SCHEMA_VERSION,
        target=target,
        ready=definition_state == "target" and not incompatible_ids,
        definition_state=definition_state,
        row_counts=row_counts,
        incompatible_row_ids=incompatible_ids,
        planned_sql=[] if definition_state == "target" else _planned_sql(),
        constraint_changed=changed,
        reversible=not labor_only_ids and not incompatible_ids,
        blockers=blockers,
    )


class _nullcontext:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *_args):
        return False


def describe_help_offer_states_migration(engine: Engine):
    return _report(engine, mode="dry-run")


def _assert_disposable_postgres_target(engine, *, local_test_marker=None):
    assert_apply_target(engine, local_test_marker=local_test_marker)
    if not engine.url.drivername.startswith("postgresql"):
        raise RetentionTargetError("Apply requires disposable PostgreSQL")
    if not str(engine.url.database or "").endswith("_test"):
        raise RetentionTargetError("Apply requires a disposable *_test database")


def apply_help_offer_states_migration(engine: Engine, *, local_test_marker=None):
    _assert_disposable_postgres_target(engine, local_test_marker=local_test_marker)
    changed = False
    with engine.begin() as connection:
        before = _report(connection, mode="apply")
        if before.incompatible_row_ids:
            raise HelpOfferStatesDataError(
                f"Filas incompatibles con la matriz objetivo: {before.incompatible_row_ids}"
            )
        if before.definition_state not in {"legacy", "target"}:
            raise HelpOfferStatesDefinitionError(
                f"Definicion de {CONSTRAINT_NAME} no reconocida: {before.definition_state}"
            )
        if before.definition_state == "legacy":
            connection.execute(text(_planned_sql()[0]))
            connection.execute(text(_planned_sql()[1]))
            changed = True
        after = _report(connection, mode="apply", changed=changed)
        if not after.ready:
            raise HelpOfferStatesMigrationError("La definicion objetivo no quedo validada")
        if after.row_counts != before.row_counts:
            raise HelpOfferStatesDataError("La migracion altero los conteos de filas")
    return _report(engine, mode="apply", changed=changed)


def rollback_help_offer_states_migration(engine: Engine, *, local_test_marker=None):
    _assert_disposable_postgres_target(engine, local_test_marker=local_test_marker)
    with engine.begin() as connection:
        before = _report(connection, mode="rollback")
        if before.definition_state != "target":
            raise HelpOfferStatesDefinitionError("Rollback requiere la definicion objetivo")
        if not before.reversible:
            raise HelpOfferStatesDataError("Rollback bloqueado por estados exclusivamente laborales")
        connection.execute(text(f"ALTER TABLE {TABLE_NAME} DROP CONSTRAINT {CONSTRAINT_NAME}"))
        connection.execute(text(
            f"ALTER TABLE {TABLE_NAME} ADD CONSTRAINT {CONSTRAINT_NAME} CHECK ({LEGACY_CONDITION})"
        ))
    report = _report(engine, mode="rollback", changed=True)
    if report.definition_state != "legacy":
        raise HelpOfferStatesMigrationError("No se pudo restaurar la definicion legacy")
    return report
