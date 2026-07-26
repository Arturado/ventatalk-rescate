from sqlalchemy import inspect, text

import models


def ensure_retention_schema(target_engine):
    timestamp_type = (
        "TIMESTAMP WITH TIME ZONE"
        if target_engine.dialect.name == "postgresql"
        else "DATETIME"
    )
    columns_by_table = {
        "casos_ayuda_v2": {
            "retencion_aplicada_at": timestamp_type,
        },
        "casos_ayuda_documentos": {
            "retencion_estado": "VARCHAR(40)",
            "retencion_aplicada_at": timestamp_type,
        },
        "casos_ayuda_comprobantes": {
            "retencion_estado": "VARCHAR(40)",
            "retencion_aplicada_at": timestamp_type,
        },
        "casos_ayuda_notificaciones": {
            "retencion_redactada_at": timestamp_type,
        },
    }
    inspector = inspect(target_engine)
    existing_tables = set(inspector.get_table_names())
    with target_engine.begin() as connection:
        for table_name, columns in columns_by_table.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"] for column in inspect(connection).get_columns(table_name)
            }
            for column_name, column_type in columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    ))
        for index_name, table_name, column_name in (
            ("ix_casos_ayuda_v2_retencion_aplicada_at", "casos_ayuda_v2", "retencion_aplicada_at"),
            ("ix_casos_ayuda_documentos_retencion_estado", "casos_ayuda_documentos", "retencion_estado"),
            ("ix_casos_ayuda_comprobantes_retencion_estado", "casos_ayuda_comprobantes", "retencion_estado"),
            ("ix_casos_ayuda_notificaciones_retencion_redactada_at", "casos_ayuda_notificaciones", "retencion_redactada_at"),
        ):
            if table_name in existing_tables:
                connection.execute(text(
                    f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name} ({column_name})"
                ))
    models.RetencionLegalAyuda.__table__.create(bind=target_engine, checkfirst=True)
