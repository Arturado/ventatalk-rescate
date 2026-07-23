import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from database import Base
from dependencies import ActorOrgContext
import models
from services.help_cases import CaseStateError
from services.help_confirmations import HelpConfirmationIdempotencyError, confirm_help_aid
from services.help_exchange import BcvRatesSnapshot, get_or_create_bcv_conversion


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL concurrency tests",
)


class FakeBcvProvider:
    current_date = date(2026, 7, 22)

    def fetch_rates(self):
        return BcvRatesSnapshot(
            rate_date=self.current_date,
            rates={"USD": Decimal("36.50"), "EUR": Decimal("40.00")},
            evidence={"transport_source": "DolarApi", "upstream_source": "BCV", "requests": {}},
        )


def test_concurrent_conversions_reuse_one_rate_snapshot():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    select_barrier = threading.Barrier(2)
    query_counts = threading.local()

    @event.listens_for(engine, "after_cursor_execute")
    def synchronize_snapshot_lookup(_connection, _cursor, statement, _parameters, _context, _executemany):
        normalized = " ".join(statement.lower().split())
        if not normalized.startswith("select") or "casos_ayuda_tasas" not in normalized:
            return
        query_counts.value = getattr(query_counts, "value", 0) + 1
        if query_counts.value == 2:
            select_barrier.wait(timeout=5)

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    def convert():
        with sessions() as db:
            result = get_or_create_bcv_conversion(
                db,
                amount=Decimal("10.00"),
                source_currency="USD",
                target_currency="EUR",
                provider=FakeBcvProvider(),
            )
            db.commit()
            return result.applied_rate

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            rates = [future.result(timeout=10) for future in [executor.submit(convert), executor.submit(convert)]]

        with sessions() as db:
            assert rates == [Decimal("0.9125000000"), Decimal("0.9125000000")]
            assert db.query(models.TasaCambioAyuda).count() == 1
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_concurrent_confirmation_of_one_aid_progresses_case_once():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    start_barrier = threading.Barrier(2)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with sessions() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-concurrency",
            nombres_apellidos_cifrado="encrypted-name",
            cedula_hash="a" * 64,
            cedula_cifrada="encrypted-id",
            creado_por="creator@example.test",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id="concurrency-case",
            organizacion_id="org-concurrency",
            beneficiario_id=beneficiary.id,
            titulo_interno="Concurrency test",
            categoria="medicamentos",
            meta_monto=Decimal("25.00"),
            meta_moneda="USD",
            estado="publicado",
            creado_por="creator@example.test",
        )
        db.add(case)
        db.flush()
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="principal",
            version=1,
            tipo_titular="beneficiario",
            titular_nombre_cifrado="encrypted-holder",
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda="USD",
            identificador_cifrado="encrypted-account",
            responsable_nombre_cifrado="encrypted-responsible",
            responsable_email_hash="b" * 64,
            responsable_email_cifrado="encrypted-email",
            consentimiento_version=1,
            estado="aprobada",
            creado_por="creator@example.test",
        )
        db.add(account)
        db.flush()
        report_operation = models.OperacionIdempotenteAyuda(
            actor_id="donor-concurrency",
            organizacion_id="org-concurrency",
            operacion="report_help",
            idempotency_key="report-concurrency-key",
            request_hash="c" * 64,
            estado="completed",
        )
        db.add(report_operation)
        db.flush()
        aid = models.AyudaMonetaria(
            caso_id=case.id,
            cuenta_id=account.id,
            cuenta_version=account.version,
            donante_id="donor-concurrency",
            monto_reportado=Decimal("25.00"),
            moneda_reportada="USD",
            fecha_transferencia=date(2026, 7, 22),
            estado="pendiente_confirmacion",
            idempotency_id=report_operation.id,
        )
        db.add(aid)
        db.commit()
        case_id = case.id
        aid_id = aid.id

    def confirm(actor_number):
        actor = ActorOrgContext(
            "org-concurrency",
            "coordinador",
            f"coordinator-{actor_number}@example.test",
            uid=f"coordinator-{actor_number}",
        )
        with sessions() as db:
            start_barrier.wait(timeout=5)
            try:
                confirm_help_aid(
                    db,
                    case_id=case_id,
                    aid_id=aid_id,
                    actor=actor,
                    idempotency_key=f"confirm-concurrency-key-{actor_number}",
                    received_amount=Decimal("25.00"),
                    received_currency="USD",
                    effective_date=date(2026, 7, 22),
                    rate_provider=FakeBcvProvider(),
                )
                db.commit()
                return "confirmed"
            except CaseStateError:
                db.rollback()
                return "already-confirmed"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [future.result(timeout=10) for future in [executor.submit(confirm, 1), executor.submit(confirm, 2)]]

        with sessions() as db:
            case = db.get(models.CasoAyudaV2, case_id)
            assert sorted(outcomes) == ["already-confirmed", "confirmed"]
            assert db.query(models.ConfirmacionAyuda).count() == 1
            assert case.monto_confirmado == Decimal("25.00")
            assert case.ayudas_confirmadas == 1
            assert case.estado == "meta_alcanzada"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_reused_idempotency_key_across_different_cases_conflicts_without_crashing():
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test"), "Concurrency tests require a disposable *_test database"
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    start_barrier = threading.Barrier(2)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    def create_published_case_with_pending_aid(suffix):
        with sessions() as db:
            beneficiary = models.BeneficiarioAyuda(
                organizacion_id="org-concurrency",
                nombres_apellidos_cifrado=f"encrypted-name-{suffix}",
                cedula_hash=(suffix * 64)[:64],
                cedula_cifrada=f"encrypted-id-{suffix}",
                creado_por="creator@example.test",
            )
            db.add(beneficiary)
            db.flush()
            case = models.CasoAyudaV2(
                public_id=f"concurrency-case-key-{suffix}",
                organizacion_id="org-concurrency",
                beneficiario_id=beneficiary.id,
                titulo_interno="Concurrency idempotency test",
                categoria="medicamentos",
                meta_monto=Decimal("100.00"),
                meta_moneda="USD",
                estado="publicado",
                creado_por="creator@example.test",
            )
            db.add(case)
            db.flush()
            account = models.CuentaCasoAyuda(
                caso_id=case.id,
                account_key="principal",
                version=1,
                tipo_titular="beneficiario",
                titular_nombre_cifrado="encrypted-holder",
                relacion_beneficiario="propia",
                medio="banco_venezolano",
                moneda="USD",
                identificador_cifrado="encrypted-account",
                responsable_nombre_cifrado="encrypted-responsible",
                responsable_email_hash=(suffix * 64)[:64],
                responsable_email_cifrado="encrypted-email",
                consentimiento_version=1,
                estado="aprobada",
                creado_por="creator@example.test",
            )
            db.add(account)
            db.flush()
            report_operation = models.OperacionIdempotenteAyuda(
                actor_id=f"donor-concurrency-{suffix}",
                organizacion_id="org-concurrency",
                operacion="report_help",
                idempotency_key=f"report-concurrency-key-{suffix}",
                request_hash="c" * 64,
                estado="completed",
            )
            db.add(report_operation)
            db.flush()
            aid = models.AyudaMonetaria(
                caso_id=case.id,
                cuenta_id=account.id,
                cuenta_version=account.version,
                donante_id=f"donor-concurrency-{suffix}",
                monto_reportado=Decimal("25.00"),
                moneda_reportada="USD",
                fecha_transferencia=date(2026, 7, 22),
                estado="pendiente_confirmacion",
                idempotency_id=report_operation.id,
            )
            db.add(aid)
            db.commit()
            return case.id, aid.id

    case_a_id, aid_a_id = create_published_case_with_pending_aid("a")
    case_b_id, aid_b_id = create_published_case_with_pending_aid("b")
    shared_idempotency_key = "confirm-shared-key-1234567890"

    def confirm(case_id, aid_id):
        actor = ActorOrgContext(
            "org-concurrency",
            "coordinador",
            "coordinator-shared@example.test",
            uid="coordinator-shared",
        )
        with sessions() as db:
            start_barrier.wait(timeout=5)
            try:
                confirm_help_aid(
                    db,
                    case_id=case_id,
                    aid_id=aid_id,
                    actor=actor,
                    idempotency_key=shared_idempotency_key,
                    received_amount=Decimal("25.00"),
                    received_currency="USD",
                    effective_date=date(2026, 7, 22),
                    rate_provider=FakeBcvProvider(),
                )
                db.commit()
                return "confirmed"
            except HelpConfirmationIdempotencyError:
                db.rollback()
                return "conflict"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [
                future.result(timeout=10)
                for future in [
                    executor.submit(confirm, case_a_id, aid_a_id),
                    executor.submit(confirm, case_b_id, aid_b_id),
                ]
            ]

        assert sorted(outcomes) == ["confirmed", "conflict"]
        with sessions() as db:
            confirmed_count = db.query(models.ConfirmacionAyuda).count()
            operation_count = db.query(models.OperacionIdempotenteAyuda).filter_by(
                actor_id="coordinator-shared",
                operacion="confirm_help",
                idempotency_key=shared_idempotency_key,
            ).count()
            assert confirmed_count == 1
            assert operation_count == 1
            aid_a = db.get(models.AyudaMonetaria, aid_a_id)
            aid_b = db.get(models.AyudaMonetaria, aid_b_id)
            confirmed_states = sorted([aid_a.estado, aid_b.estado])
            assert confirmed_states == ["confirmada", "pendiente_confirmacion"]
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
