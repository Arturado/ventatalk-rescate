from dataclasses import dataclass, field

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

import models
from database import Base
from services.help_retention import assert_apply_target


HELP_OFFERS_SCHEMA_VERSION = "help-offers-v1"

_NEW_TABLES = [
    models.CasoAyudaResponsable.__tablename__,
    models.OfertaAyudaDirecta.__tablename__,
]


@dataclass(frozen=True)
class HelpOffersMigrationReport:
    mode: str
    schema_version: str
    ready: bool
    tables_created: list = field(default_factory=list)

    def public_dict(self):
        return {
            "mode": self.mode,
            "schema_version": self.schema_version,
            "ready": self.ready,
            "tables_created": self.tables_created,
        }


def is_help_offers_ready(engine: Engine) -> bool:
    existing_tables = set(inspect(engine).get_table_names())
    return set(_NEW_TABLES).issubset(existing_tables)


def describe_help_offers_migration(engine: Engine) -> HelpOffersMigrationReport:
    ready = is_help_offers_ready(engine)
    existing_tables = set(inspect(engine).get_table_names())
    pending_tables = sorted(set(_NEW_TABLES) - existing_tables)
    return HelpOffersMigrationReport(
        mode="dry-run",
        schema_version=HELP_OFFERS_SCHEMA_VERSION,
        ready=ready,
        tables_created=[] if ready else pending_tables,
    )


def apply_help_offers_migration(engine: Engine, *, local_test_marker=None) -> HelpOffersMigrationReport:
    assert_apply_target(engine, local_test_marker=local_test_marker)
    existing_tables = set(inspect(engine).get_table_names())
    pending_tables = sorted(set(_NEW_TABLES) - existing_tables)
    tables_to_create = [
        Base.metadata.tables[name] for name in pending_tables
    ]
    if tables_to_create:
        Base.metadata.create_all(bind=engine, tables=tables_to_create)
    return HelpOffersMigrationReport(
        mode="apply",
        schema_version=HELP_OFFERS_SCHEMA_VERSION,
        ready=is_help_offers_ready(engine),
        tables_created=pending_tables,
    )
