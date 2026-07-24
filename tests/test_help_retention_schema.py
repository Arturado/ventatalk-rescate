from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, inspect

from services.help_retention_schema import ensure_retention_schema


def test_retention_startup_migration_is_additive_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migration_test.db'}")
    legacy = MetaData()
    Table("casos_ayuda_v2", legacy, Column("id", Integer, primary_key=True))
    Table("casos_ayuda_documentos", legacy, Column("id", Integer, primary_key=True))
    Table("casos_ayuda_comprobantes", legacy, Column("id", Integer, primary_key=True))
    Table("casos_ayuda_notificaciones", legacy, Column("id", Integer, primary_key=True))
    legacy.create_all(engine)

    ensure_retention_schema(engine)
    ensure_retention_schema(engine)

    schema = inspect(engine)
    assert "retencion_aplicada_at" in {
        column["name"] for column in schema.get_columns("casos_ayuda_v2")
    }
    assert {"retencion_estado", "retencion_aplicada_at"} <= {
        column["name"] for column in schema.get_columns("casos_ayuda_documentos")
    }
    assert {"retencion_estado", "retencion_aplicada_at"} <= {
        column["name"] for column in schema.get_columns("casos_ayuda_comprobantes")
    }
    assert "retencion_redactada_at" in {
        column["name"] for column in schema.get_columns("casos_ayuda_notificaciones")
    }
    assert "casos_ayuda_retencion_legal" in schema.get_table_names()
    engine.dispose()
