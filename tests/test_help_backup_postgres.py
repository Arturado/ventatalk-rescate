import base64
import hashlib
import os
import shutil
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from services.help_backup import (
    BackupArchiveError,
    create_postgres_backup,
    restore_postgres_backup,
)
from services.help_crypto import HelpDataCipher, HelpDataCryptoError
from services.help_retention import run_retention


SOURCE_URL = os.getenv("HELP_RETENTION_SOURCE_DATABASE_URL")
RESTORE_URL = os.getenv("HELP_RETENTION_RESTORE_DATABASE_URL")
PG_DUMP_BIN = os.getenv("HELP_RETENTION_PG_DUMP_BIN", "pg_dump")
PG_RESTORE_BIN = os.getenv("HELP_RETENTION_PG_RESTORE_BIN", "pg_restore")
pytestmark = pytest.mark.skipif(
    not SOURCE_URL or not RESTORE_URL,
    reason="Disposable PostgreSQL drill URLs are required",
)


def _assert_disposable_local(url):
    parsed = urlsplit(url)
    assert parsed.hostname in {"127.0.0.1", "localhost"}
    assert parsed.path.rsplit("/", 1)[-1].endswith("_test")


def _seed_source(sessions, root, now):
    cipher = HelpDataCipher.from_environment()
    with sessions() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="drill-org",
            nombres_apellidos_cifrado=cipher.encrypt("Synthetic Beneficiary", field="beneficiary.name"),
            cedula_hash=cipher.identity_hash("SYNTHETIC-001"),
            cedula_cifrada=cipher.encrypt("SYNTHETIC-001", field="beneficiary.identity"),
            telefono_cifrado=cipher.encrypt("000-000", field="beneficiary.phone"),
            creado_por="synthetic-drill",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id="synthetic-drill-case",
            organizacion_id="drill-org",
            beneficiario_id=beneficiary.id,
            titulo_interno="Synthetic recovery drill",
            relato_privado_cifrado=cipher.encrypt("Synthetic story", field="case.private_story"),
            categoria="medicamentos",
            meta_monto=Decimal("100.00"),
            meta_moneda="USD",
            monto_confirmado=Decimal("25.00"),
            ayudas_confirmadas=1,
            estado="cerrado",
            creado_por="synthetic-drill",
            cerrado_at=now.replace(year=now.year - 7),
        )
        db.add(case)
        db.flush()
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key="synthetic",
            version=1,
            tipo_titular="beneficiario",
            titular_nombre_cifrado=cipher.encrypt("Synthetic", field="account.holder_name"),
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda="USD",
            identificador_cifrado=cipher.encrypt("SYNTHETIC-ACCOUNT", field="account.identifier"),
            responsable_nombre_cifrado=cipher.encrypt("Synthetic", field="account.responsible_name"),
            responsable_email_hash=cipher.blind_index("synthetic@example.test", purpose="account-email"),
            responsable_email_cifrado=cipher.encrypt("synthetic@example.test", field="account.responsible_email"),
            consentimiento_version=1,
            estado="aprobada",
            creado_por="synthetic-drill",
        )
        db.add(account)
        db.flush()
        operation = models.OperacionIdempotenteAyuda(
            actor_id="synthetic-donor",
            organizacion_id="drill-org",
            operacion="report_help",
            idempotency_key="synthetic-drill",
            request_hash="1" * 64,
            estado="completed",
        )
        db.add(operation)
        db.flush()
        aid = models.AyudaMonetaria(
            caso_id=case.id,
            cuenta_id=account.id,
            cuenta_version=1,
            donante_id="synthetic-donor",
            monto_reportado=Decimal("25.00"),
            moneda_reportada="USD",
            fecha_transferencia=date(2019, 7, 23),
            estado="confirmada",
            idempotency_id=operation.id,
        )
        db.add(aid)
        db.flush()
        medical_plaintext = b"%PDF-1.7 SYNTHETIC-MEDICAL-DRILL"
        receipt_plaintext = b"%PDF-1.7 SYNTHETIC-RECEIPT-DRILL"
        paths = {
            "medical": f"private/casos-ayuda/{case.id}/medical.enc",
            "receipt": f"private/casos-ayuda/{case.id}/receipt.enc",
        }
        for key, plaintext, field in (
            ("medical", medical_plaintext, "document.content"),
            ("receipt", receipt_plaintext, "aid.receipt"),
        ):
            destination = root / paths[key]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(cipher.encrypt_bytes(plaintext, field=field))
        document = models.DocumentoCasoAyuda(
            caso_id=case.id,
            document_key="medical",
            version=1,
            tipo="informe_medico",
            clasificacion="privado",
            storage_path=paths["medical"],
            content_type="application/pdf",
            size_bytes=len(medical_plaintext),
            checksum_sha256=hashlib.sha256(medical_plaintext).hexdigest(),
            cargado_por="synthetic-drill",
        )
        db.add(document)
        db.flush()
        db.add(models.ConsentimientoCasoAyuda(
            caso_id=case.id,
            version=1,
            texto_version="synthetic-v1",
            alcance_json="{}",
            firmante_nombre_cifrado=cipher.encrypt("Synthetic", field="consent.signer_name"),
            evidencia_documento_id=document.id,
            registrado_por="synthetic-drill",
        ))
        db.add(models.ComprobanteAyuda(
            ayuda_id=aid.id,
            version=1,
            storage_path=paths["receipt"],
            content_type="application/pdf",
            size_bytes=len(receipt_plaintext),
            checksum_sha256=hashlib.sha256(receipt_plaintext).hexdigest(),
            estado="vigente",
            cargado_por="synthetic-donor",
        ))
        db.add(models.AuditoriaCasoAyuda(
            event_id="synthetic-drill-audit",
            organizacion_id="drill-org",
            caso_id=case.id,
            accion="synthetic_drill_seeded",
            actor_id="synthetic-drill",
            actor_tipo="sistema",
            entidad_tipo="caso",
            entidad_id=str(case.id),
            metadata_json="{}",
        ))
        db.add(models.NotificacionCasoAyuda(
            deduplication_key="synthetic-drill-notification",
            evento_tipo="ayuda_confirmada",
            organizacion_id="drill-org",
            caso_id=case.id,
            destinatario_tipo="donante",
            destinatario_email_hash="2" * 64,
            destinatario_email_cifrado="synthetic-encrypted-email",
            template_key="aid-confirmed-v1",
            payload_json="synthetic-encrypted-payload",
            estado="fallida",
            intentos=1,
            created_at=now - timedelta(days=90),
        ))
        access = models.AccesoTemporalAyuda(
            destinatario_email_hash="3" * 64,
            destinatario_email_cifrado="synthetic-encrypted-access-email",
            challenge_hash="4" * 64,
            challenge_expires_at=now - timedelta(days=30),
            challenge_ttl_seconds=900,
            session_ttl_seconds=3600,
            estado="expirado",
            solicitado_por="synthetic-drill",
            created_at=now - timedelta(days=30),
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
    return medical_plaintext, receipt_plaintext, paths


def _snapshot(sessions, root):
    with sessions() as db:
        counts = {
            model.__tablename__: db.query(model).count()
            for model in (
                models.BeneficiarioAyuda,
                models.CasoAyudaV2,
                models.CuentaCasoAyuda,
                models.AyudaMonetaria,
                models.ConsentimientoCasoAyuda,
                models.DocumentoCasoAyuda,
                models.ComprobanteAyuda,
                models.AuditoriaCasoAyuda,
                models.NotificacionCasoAyuda,
                models.AccesoTemporalAyuda,
                models.AlcanceCuentaAccesoTemporal,
            )
        }
        totals = {
            "aid_reported": str(db.query(func.sum(models.AyudaMonetaria.monto_reportado)).scalar()),
            "case_confirmed": str(db.query(func.sum(models.CasoAyudaV2.monto_confirmado)).scalar()),
            "case_confirmations": db.query(func.sum(models.CasoAyudaV2.ayudas_confirmadas)).scalar(),
        }
        audit_actions = tuple(
            row[0] for row in db.query(models.AuditoriaCasoAyuda.accion).order_by(
                models.AuditoriaCasoAyuda.id
            ).all()
        )
    file_checksums = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*.enc")
    }
    return counts, totals, audit_actions, file_checksums


def test_disposable_postgres_backup_restore_and_retention_drill(monkeypatch, tmp_path):
    _assert_disposable_local(SOURCE_URL)
    _assert_disposable_local(RESTORE_URL)
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    source_engine = create_engine(SOURCE_URL)
    restore_engine = create_engine(RESTORE_URL)
    source_sessions = sessionmaker(bind=source_engine, autoflush=False)
    restore_sessions = sessionmaker(bind=restore_engine, autoflush=False)
    source_root = tmp_path / "source-uploads"
    restore_root = tmp_path / "restore-uploads"
    source_root.mkdir()
    restore_root.mkdir()
    artifact = tmp_path / "drill.backup.enc"
    now = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    Base.metadata.drop_all(source_engine)
    Base.metadata.drop_all(restore_engine)
    Base.metadata.create_all(source_engine)
    try:
        medical, receipt, paths = _seed_source(source_sessions, source_root, now)
        before = _snapshot(source_sessions, source_root)
        create_postgres_backup(
            database_url=SOURCE_URL,
            upload_root=source_root,
            output_path=artifact,
            passphrase="synthetic-drill-passphrase",
            key_versions=["v1"],
            schema_identifier="help-v2-retention-v1",
            pg_dump_bin=PG_DUMP_BIN,
        )

        Base.metadata.drop_all(source_engine)
        shutil.rmtree(source_root)
        restore_postgres_backup(
            database_url=RESTORE_URL,
            destination_root=restore_root,
            artifact_path=artifact,
            passphrase="synthetic-drill-passphrase",
            pg_restore_bin=PG_RESTORE_BIN,
        )
        after = _snapshot(restore_sessions, restore_root)
        assert after == before
        cipher = HelpDataCipher.from_environment()
        assert cipher.decrypt_bytes(
            (restore_root / paths["medical"]).read_bytes(), field="document.content"
        ) == medical
        assert cipher.decrypt_bytes(
            (restore_root / paths["receipt"]).read_bytes(), field="aid.receipt"
        ) == receipt

        monkeypatch.delenv("HELP_DATA_ENCRYPTION_KEY_V1")
        with pytest.raises(BackupArchiveError, match="key version"):
            restore_postgres_backup(
                database_url=RESTORE_URL,
                destination_root=restore_root,
                artifact_path=artifact,
                passphrase="synthetic-drill-passphrase",
                pg_restore_bin=PG_RESTORE_BIN,
            )
        with pytest.raises(HelpDataCryptoError):
            HelpDataCipher.from_environment()
        monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())

        with restore_sessions() as db:
            dry_run = run_retention(db, upload_root=restore_root, now=now)
            applied = run_retention(db, upload_root=restore_root, now=now, apply=True)
        with restore_sessions() as db:
            repeated = run_retention(db, upload_root=restore_root, now=now, apply=True)
            assert db.query(models.AyudaMonetaria).count() == 1
            assert db.query(models.AuditoriaCasoAyuda).count() == 1
            assert db.query(models.AccesoTemporalAyuda).count() == 0
            assert db.query(func.sum(models.AyudaMonetaria.monto_reportado)).scalar() == Decimal("25.00")
            assert db.query(func.sum(models.CasoAyudaV2.monto_confirmado)).scalar() == Decimal("25.00")
            assert db.query(func.sum(models.CasoAyudaV2.ayudas_confirmadas)).scalar() == 1
        assert dry_run.mutation_count > 0
        assert applied.mutation_count == dry_run.mutation_count
        assert repeated.mutation_count == 0
        assert not list(restore_root.rglob("*.enc"))
    finally:
        Base.metadata.drop_all(source_engine)
        Base.metadata.drop_all(restore_engine)
        source_engine.dispose()
        restore_engine.dispose()
        artifact.unlink(missing_ok=True)
        shutil.rmtree(source_root, ignore_errors=True)
        shutil.rmtree(restore_root, ignore_errors=True)
