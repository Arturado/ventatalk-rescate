from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import models


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, _):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_case(db, *, organization_id="org-1", suffix="1"):
    beneficiary = models.BeneficiarioAyuda(
        organizacion_id=organization_id,
        nombres_apellidos_cifrado="encrypted-name",
        cedula_hash=(suffix * 64)[:64],
        cedula_cifrada="encrypted-id",
        creado_por="coordinador@example.com",
    )
    db.add(beneficiary)
    db.flush()
    case = models.CasoAyudaV2(
        public_id=f"case-public-{suffix}",
        organizacion_id=organization_id,
        beneficiario_id=beneficiary.id,
        titulo_interno="Caso financiero",
        categoria="medicamentos",
        meta_monto=Decimal("100.00"),
        meta_moneda="USD",
        creado_por="coordinador@example.com",
    )
    db.add(case)
    db.flush()
    return case


def create_idempotency(db, *, actor="donante@example.com", operation="report_help", key="help:12345678-abcd"):
    record = models.OperacionIdempotenteAyuda(
        actor_id=actor,
        operacion=operation,
        idempotency_key=key,
        request_hash="a" * 64,
    )
    db.add(record)
    db.flush()
    return record


def create_account(db, case, *, account_key="account-1", version=1, owner_type="beneficiario"):
    account = models.CuentaCasoAyuda(
        caso_id=case.id,
        account_key=account_key,
        version=version,
        tipo_titular=owner_type,
        titular_nombre_cifrado="encrypted-holder",
        relacion_beneficiario="propia" if owner_type == "beneficiario" else "tercero autorizado",
        medio="banco_venezolano",
        moneda="USD",
        identificador_cifrado="encrypted-account",
        responsable_nombre_cifrado="encrypted-responsible",
        responsable_email_hash="b" * 64,
        responsable_email_cifrado="encrypted-email",
        consentimiento_version=1,
        creado_por="coordinador@example.com",
    )
    db.add(account)
    db.flush()
    return account


def create_help(db, case, account, idempotency):
    help_record = models.AyudaMonetaria(
        caso_id=case.id,
        cuenta_id=account.id,
        cuenta_version=account.version,
        donante_id="donante@example.com",
        monto_reportado=Decimal("10.25"),
        moneda_reportada="USD",
        fecha_transferencia=date(2026, 7, 20),
        referencia_cifrada="encrypted-reference",
        idempotency_id=idempotency.id,
    )
    db.add(help_record)
    db.flush()
    return help_record


def test_create_all_is_idempotent_and_creates_financial_tables():
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "casos_ayuda_cuentas",
        "casos_ayuda_ayudas",
        "casos_ayuda_comprobantes",
        "casos_ayuda_confirmaciones",
        "casos_ayuda_tasas",
    }.issubset(table_names)


def test_account_version_is_unique_per_case_and_logical_key():
    with TestingSessionLocal() as db:
        case = create_case(db)
        create_account(db, case)
        db.commit()

        with pytest.raises(IntegrityError):
            create_account(db, case)


def test_exceptional_account_requires_distinct_second_approval():
    with TestingSessionLocal() as db:
        case = create_case(db)
        account = create_account(db, case, owner_type="coordinador")
        account.estado = "aprobada"
        account.justificacion_cifrada = "encrypted-justification"
        account.aprobado_por = "coordinador@example.com"

        with pytest.raises(IntegrityError):
            db.commit()


def test_account_rejects_unsupported_currency():
    with TestingSessionLocal() as db:
        case = create_case(db)
        account = create_account(db, case)
        account.moneda = "GBP"

        with pytest.raises(IntegrityError):
            db.commit()


def test_help_keeps_exact_account_version_and_decimal_amount():
    with TestingSessionLocal() as db:
        case = create_case(db)
        account = create_account(db, case)
        idempotency = create_idempotency(db)
        help_record = create_help(db, case, account, idempotency)
        db.commit()
        db.refresh(help_record)

        assert help_record.cuenta_version == 1
        assert help_record.monto_reportado == Decimal("10.25")
        assert help_record.estado == "pendiente_confirmacion"


def test_help_rejects_account_from_another_case():
    with TestingSessionLocal() as db:
        first_case = create_case(db, suffix="1")
        second_case = create_case(db, suffix="2")
        account = create_account(db, first_case)
        idempotency = create_idempotency(db)

        with pytest.raises(IntegrityError):
            create_help(db, second_case, account, idempotency)


def test_help_rejects_non_positive_amount_or_unknown_currency():
    with TestingSessionLocal() as db:
        case = create_case(db)
        account = create_account(db, case)
        idempotency = create_idempotency(db)
        help_record = create_help(db, case, account, idempotency)
        help_record.monto_reportado = Decimal("0.00")
        help_record.moneda_reportada = "GBP"

        with pytest.raises(IntegrityError):
            db.commit()


def test_receipt_versions_are_private_and_unique_per_help():
    with TestingSessionLocal() as db:
        case = create_case(db)
        account = create_account(db, case)
        idempotency = create_idempotency(db)
        help_record = create_help(db, case, account, idempotency)
        receipt_data = {
            "ayuda_id": help_record.id,
            "version": 1,
            "storage_path": "private/help/receipt-1.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1024,
            "checksum_sha256": "c" * 64,
            "cargado_por": "donante@example.com",
        }
        db.add(models.ComprobanteAyuda(**receipt_data))
        db.commit()
        duplicate = {**receipt_data, "storage_path": "private/help/receipt-2.pdf"}
        db.add(models.ComprobanteAyuda(**duplicate))

        with pytest.raises(IntegrityError):
            db.commit()


def test_help_accepts_only_one_final_confirmation():
    with TestingSessionLocal() as db:
        case = create_case(db)
        account = create_account(db, case)
        report_idempotency = create_idempotency(db)
        help_record = create_help(db, case, account, report_idempotency)
        confirmation_idempotency = create_idempotency(
            db,
            actor="responsable@example.com",
            operation="confirm_help",
            key="confirm:12345678-abcd",
        )
        confirmation_data = {
            "ayuda_id": help_record.id,
            "idempotency_id": confirmation_idempotency.id,
            "monto_recibido": Decimal("10.25"),
            "moneda_recibida": "USD",
            "monto_meta_equivalente": Decimal("10.25"),
            "confirmado_por": "responsable@example.com",
            "fecha_transferencia_confirmada": date(2026, 7, 20),
        }
        db.add(models.ConfirmacionAyuda(**confirmation_data))
        db.commit()
        db.add(models.ConfirmacionAyuda(**confirmation_data))

        with pytest.raises(IntegrityError):
            db.commit()


def test_exchange_rate_snapshot_is_positive_and_unique():
    with TestingSessionLocal() as db:
        rate_data = {
            "fuente": "BCV",
            "fecha_tasa": date(2026, 7, 20),
            "moneda_base": "USD",
            "moneda_cotizada": "VES",
            "valor": Decimal("36.5000000000"),
            "evidencia_json": "{}",
        }
        db.add(models.TasaCambioAyuda(**rate_data))
        db.commit()
        db.add(models.TasaCambioAyuda(**rate_data))

        with pytest.raises(IntegrityError):
            db.commit()
