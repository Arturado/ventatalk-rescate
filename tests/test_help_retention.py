import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from services.help_retention import (
    RetentionPathError,
    RetentionTargetError,
    assert_apply_target,
    run_retention,
)
from scripts.help_retention import parser as retention_parser


NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def database(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'retention_test.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    try:
        yield engine, sessions
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_temporary_request_attempts_are_deleted_after_thirty_days(database, tmp_path):
    _engine, sessions = database
    with sessions() as db:
        db.add_all([
            models.IntentoSolicitudAccesoTemporal(
                email_hash="a" * 64,
                ip_hash="b" * 64,
                created_at=NOW - timedelta(days=30, seconds=1),
            ),
            models.IntentoSolicitudAccesoTemporal(
                email_hash="c" * 64,
                ip_hash="d" * 64,
                created_at=NOW - timedelta(days=29),
            ),
        ])
        db.commit()

        report = run_retention(db, upload_root=tmp_path, now=NOW, apply=True)

        remaining = db.query(models.IntentoSolicitudAccesoTemporal).all()
    assert report.counts["temporary_access_attempts"] == 1
    assert [item.email_hash for item in remaining] == ["c" * 64]


def seed_closed_case(
    db,
    root,
    *,
    suffix,
    closed_at,
    document_path=None,
    notification_created_at=None,
    access_created_at=None,
):
    beneficiary = models.BeneficiarioAyuda(
        organizacion_id="org-test",
        nombres_apellidos_cifrado=f"encrypted-name-{suffix}",
        cedula_hash=hashlib.sha256(f"identity-{suffix}".encode()).hexdigest(),
        cedula_cifrada=f"encrypted-identity-{suffix}",
        telefono_cifrado=f"encrypted-phone-{suffix}",
        correo_cifrado=f"encrypted-email-{suffix}",
        creado_por="synthetic-creator",
    )
    db.add(beneficiary)
    db.flush()
    case = models.CasoAyudaV2(
        public_id=f"retention-{suffix}",
        organizacion_id="org-test",
        beneficiario_id=beneficiary.id,
        titulo_interno=f"Synthetic case {suffix}",
        relato_privado_cifrado=f"encrypted-story-{suffix}",
        categoria="medicamentos",
        meta_monto=Decimal("100.00"),
        meta_moneda="USD",
        estado="cerrado",
        creado_por="synthetic-creator",
        cerrado_at=closed_at,
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
        instrucciones_cifrado="encrypted-instructions",
        responsable_nombre_cifrado="encrypted-responsible",
        responsable_email_hash=hashlib.sha256(f"responsible-{suffix}".encode()).hexdigest(),
        responsable_email_cifrado="encrypted-responsible-email",
        consentimiento_version=1,
        estado="aprobada",
        creado_por="synthetic-creator",
    )
    db.add(account)
    db.flush()

    relative_document = document_path or f"private/casos-ayuda/{case.id}/medical.enc"
    if ".." not in relative_document:
        destination = root / relative_document
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"encrypted-medical-marker")
    document = models.DocumentoCasoAyuda(
        caso_id=case.id,
        document_key="medical-report",
        version=1,
        tipo="informe_medico",
        clasificacion="privado",
        storage_path=relative_document,
        nombre_original_cifrado="encrypted-file-name",
        content_type="application/pdf",
        size_bytes=24,
        checksum_sha256="a" * 64,
        cargado_por="synthetic-creator",
    )
    db.add(document)
    db.flush()
    db.add(models.ConsentimientoCasoAyuda(
        caso_id=case.id,
        version=1,
        texto_version="synthetic-v1",
        alcance_json="{}",
        firmante_nombre_cifrado="encrypted-signer",
        evidencia_documento_id=document.id,
        registrado_por="synthetic-creator",
    ))
    db.add(models.AuditoriaCasoAyuda(
        event_id=f"retention-audit-{suffix}",
        organizacion_id="org-test",
        caso_id=case.id,
        accion="synthetic_event",
        actor_id="synthetic-actor",
        actor_tipo="sistema",
        entidad_tipo="caso",
        entidad_id=str(case.id),
        metadata_json="{}",
    ))

    operation = models.OperacionIdempotenteAyuda(
        actor_id=f"synthetic-donor-{suffix}",
        organizacion_id="org-test",
        operacion="report_help",
        idempotency_key=f"retention-{suffix}",
        request_hash="b" * 64,
        estado="completed",
    )
    db.add(operation)
    db.flush()
    aid = models.AyudaMonetaria(
        caso_id=case.id,
        cuenta_id=account.id,
        cuenta_version=1,
        donante_id=f"synthetic-donor-{suffix}",
        donante_email_hash="c" * 64,
        donante_email_cifrado="encrypted-donor-email",
        monto_reportado=Decimal("25.00"),
        moneda_reportada="USD",
        fecha_transferencia=date(2019, 7, 23),
        estado="confirmada",
        idempotency_id=operation.id,
    )
    db.add(aid)
    db.flush()
    receipt_path = f"private/casos-ayuda/{case.id}/receipt.enc"
    receipt_file = root / receipt_path
    receipt_file.parent.mkdir(parents=True, exist_ok=True)
    receipt_file.write_bytes(b"encrypted-receipt-marker")
    receipt = models.ComprobanteAyuda(
        ayuda_id=aid.id,
        version=1,
        storage_path=receipt_path,
        nombre_original_cifrado="encrypted-receipt-name",
        content_type="application/pdf",
        size_bytes=24,
        checksum_sha256="d" * 64,
        estado="vigente",
        cargado_por="synthetic-donor",
    )
    db.add(receipt)
    notification = models.NotificacionCasoAyuda(
        deduplication_key=f"retention-{suffix}",
        evento_tipo="ayuda_confirmada",
        organizacion_id="org-test",
        caso_id=case.id,
        destinatario_tipo="donante",
        destinatario_email_hash="e" * 64,
        destinatario_email_cifrado="encrypted-notification-email",
        template_key="aid-confirmed-v1",
        payload_json="encrypted-notification-payload",
        estado="enviada",
        proveedor="synthetic",
        proveedor_referencia="synthetic-reference",
        enviada_at=NOW - timedelta(days=91),
        created_at=notification_created_at or NOW - timedelta(days=90),
    )
    db.add(notification)
    access = models.AccesoTemporalAyuda(
        destinatario_email_hash="f" * 64,
        destinatario_email_cifrado="encrypted-access-email",
        challenge_hash=hashlib.sha256(f"challenge-{suffix}".encode()).hexdigest(),
        challenge_expires_at=NOW - timedelta(days=31),
        session_ttl_seconds=3600,
        challenge_ttl_seconds=900,
        estado="expirado",
        solicitado_por="synthetic",
        created_at=access_created_at or NOW - timedelta(days=30),
    )
    db.add(access)
    db.flush()
    db.add(models.AlcanceCuentaAccesoTemporal(
        acceso_id=access.id,
        caso_id=case.id,
        cuenta_id=account.id,
        cuenta_version=1,
    ))
    db.commit()
    return case.id, beneficiary.id, account.id, document.id, receipt.id, notification.id, access.id


def test_dry_run_reports_exact_cutoffs_without_mutating(database, tmp_path):
    _engine, sessions = database
    root = tmp_path / "uploads"
    with sessions() as db:
        ids = seed_closed_case(db, root, suffix="boundary", closed_at=NOW - timedelta(days=730))
        report = run_retention(db, upload_root=root, now=NOW)

    assert report.mode == "dry-run"
    assert report.counts["closed_cases"] == 1
    assert report.counts["temporary_sessions"] == 1
    assert report.counts["notifications"] == 1
    assert report.counts["private_files"] == 2
    with sessions() as db:
        case = db.get(models.CasoAyudaV2, ids[0])
        assert case.retencion_aplicada_at is None
        assert db.get(models.BeneficiarioAyuda, ids[1]).telefono_cifrado == "encrypted-phone-boundary"
        assert db.get(models.NotificacionCasoAyuda, ids[5]).payload_json == "encrypted-notification-payload"
        assert db.get(models.AccesoTemporalAyuda, ids[6]) is not None
    assert len(list(root.rglob("*.enc"))) == 2


def test_cli_defaults_to_dry_run():
    assert retention_parser().parse_args([]).apply is False


def test_records_one_second_newer_than_cutoffs_are_not_selected(database, tmp_path):
    _engine, sessions = database
    root = tmp_path / "uploads"
    one_second = timedelta(seconds=1)
    with sessions() as db:
        seed_closed_case(
            db,
            root,
            suffix="newer",
            closed_at=NOW.replace(year=NOW.year - 2) + one_second,
            notification_created_at=NOW - timedelta(days=90) + one_second,
            access_created_at=NOW - timedelta(days=30) + one_second,
        )
        report = run_retention(db, upload_root=root, now=NOW)

    assert report.mutation_count == 0
    assert report.counts["minimum_evidence_preserved"] == 0


def test_apply_is_idempotent_and_preserves_minimum_financial_evidence(database, tmp_path):
    _engine, sessions = database
    root = tmp_path / "uploads"
    with sessions() as db:
        ids = seed_closed_case(db, root, suffix="old", closed_at=NOW.replace(year=NOW.year - 7))
        first = run_retention(db, upload_root=root, now=NOW, apply=True)
    with sessions() as db:
        second = run_retention(db, upload_root=root, now=NOW, apply=True)

    assert first.counts["closed_cases"] == 1
    assert first.counts["minimum_evidence_preserved"] >= 3
    assert second.mutation_count == 0
    assert not list(root.rglob("*.enc"))
    with sessions() as db:
        case = db.get(models.CasoAyudaV2, ids[0])
        beneficiary = db.get(models.BeneficiarioAyuda, ids[1])
        account = db.get(models.CuentaCasoAyuda, ids[2])
        document = db.get(models.DocumentoCasoAyuda, ids[3])
        receipt = db.get(models.ComprobanteAyuda, ids[4])
        notification = db.get(models.NotificacionCasoAyuda, ids[5])
        assert case.retencion_aplicada_at is not None
        assert beneficiary.telefono_cifrado is None
        assert "encrypted" not in beneficiary.nombres_apellidos_cifrado
        assert account.estado == "inactiva"
        assert "encrypted" not in account.identificador_cifrado
        assert document.retencion_estado == "archivo_eliminado"
        assert document.nombre_original_cifrado is None
        assert receipt.retencion_estado == "archivo_eliminado"
        assert receipt.checksum_sha256 == "d" * 64
        assert notification.payload_json == "{}"
        assert notification.estado == "enviada"
        assert notification.proveedor == "synthetic"
        assert notification.proveedor_referencia == "synthetic-reference"
        assert db.get(models.AccesoTemporalAyuda, ids[6]) is None
        assert db.query(models.AyudaMonetaria).count() == 1


def test_case_hold_skips_case_children_sessions_and_notifications(database, tmp_path):
    _engine, sessions = database
    root = tmp_path / "uploads"
    with sessions() as db:
        ids = seed_closed_case(db, root, suffix="held", closed_at=NOW - timedelta(days=731))
        db.add(models.RetencionLegalAyuda(
            entidad_tipo="caso",
            entidad_id=str(ids[0]),
            caso_id=ids[0],
            motivo_codigo="litigation",
            autorizado_por="legal-role",
        ))
        db.commit()
        report = run_retention(db, upload_root=root, now=NOW, apply=True)

    assert report.counts["legal_hold_skipped"] >= 1
    assert report.mutation_count == 0
    with sessions() as db:
        assert db.get(models.AccesoTemporalAyuda, ids[6]) is not None
        assert db.get(models.NotificacionCasoAyuda, ids[5]).retencion_redactada_at is None
    assert len(list(root.rglob("*.enc"))) == 2


def test_apply_preflights_paths_before_any_mutation(database, tmp_path):
    _engine, sessions = database
    root = tmp_path / "uploads"
    outside = tmp_path / "outside.enc"
    outside.write_bytes(b"do-not-delete")
    with sessions() as db:
        ids = seed_closed_case(
            db,
            root,
            suffix="traversal",
            closed_at=NOW - timedelta(days=731),
            document_path="../outside.enc",
        )
        with pytest.raises(RetentionPathError):
            run_retention(db, upload_root=root, now=NOW, apply=True)

    assert outside.read_bytes() == b"do-not-delete"
    with sessions() as db:
        assert db.get(models.CasoAyudaV2, ids[0]).retencion_aplicada_at is None


def test_apply_guard_requires_test_database_or_explicit_local_marker(tmp_path):
    unsafe = create_engine(f"sqlite:///{tmp_path / 'rescate.db'}")
    safe = create_engine(f"sqlite:///{tmp_path / 'rescate_test.db'}")
    try:
        with pytest.raises(RetentionTargetError):
            assert_apply_target(unsafe)
        assert_apply_target(safe)
        assert_apply_target(unsafe, local_test_marker="DISPOSABLE_LOCAL_TEST")
        with pytest.raises(RetentionTargetError):
            assert_apply_target(unsafe, local_test_marker="yes")
    finally:
        unsafe.dispose()
        safe.dispose()


def test_apply_guard_rejects_remote_database_even_with_marker():
    remote = create_engine("postgresql+psycopg2://user:password@db.example.test/rescate_test")
    try:
        with pytest.raises(RetentionTargetError, match="local"):
            assert_apply_target(remote, local_test_marker="DISPOSABLE_LOCAL_TEST")
    finally:
        remote.dispose()
