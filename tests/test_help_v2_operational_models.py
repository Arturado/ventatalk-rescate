from datetime import date, datetime, timedelta, timezone
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


def create_case(db, *, suffix="1"):
    beneficiary = models.BeneficiarioAyuda(
        organizacion_id="org-1",
        nombres_apellidos_cifrado="encrypted-name",
        cedula_hash=(suffix * 64)[:64],
        cedula_cifrada="encrypted-id",
        creado_por="coordinador@example.com",
    )
    db.add(beneficiary)
    db.flush()
    case = models.CasoAyudaV2(
        public_id=f"operational-case-{suffix}",
        organizacion_id="org-1",
        beneficiario_id=beneficiary.id,
        titulo_interno="Caso operativo",
        categoria="medicamentos",
        meta_monto=Decimal("100.00"),
        meta_moneda="USD",
        creado_por="coordinador@example.com",
    )
    db.add(case)
    db.flush()
    return case


def create_account(db, case, *, version=1):
    account = models.CuentaCasoAyuda(
        caso_id=case.id,
        account_key="account-1",
        version=version,
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
        creado_por="coordinador@example.com",
    )
    db.add(account)
    db.flush()
    return account


def create_access(db, *, suffix="1", challenge_ttl=900, session_ttl=86400):
    now = datetime.now(timezone.utc)
    access = models.AccesoTemporalAyuda(
        destinatario_email_hash=(suffix * 64)[:64],
        destinatario_email_cifrado="encrypted-email",
        challenge_hash=(("c" + suffix) * 64)[:64],
        challenge_expires_at=now + timedelta(seconds=challenge_ttl),
        challenge_ttl_seconds=challenge_ttl,
        session_ttl_seconds=session_ttl,
        solicitado_por="public-request",
    )
    db.add(access)
    db.flush()
    return access


def test_create_all_is_idempotent_and_creates_operational_tables():
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "casos_ayuda_accesos",
        "casos_ayuda_acceso_cuentas",
        "casos_ayuda_auditoria",
        "casos_ayuda_notificaciones",
    }.issubset(table_names)


@pytest.mark.parametrize(("challenge_ttl", "session_ttl"), [(901, 86400), (900, 86401), (0, 86400)])
def test_temporary_access_rejects_ttl_above_limits_or_non_positive(challenge_ttl, session_ttl):
    with TestingSessionLocal() as db:
        with pytest.raises(IntegrityError):
            create_access(db, challenge_ttl=challenge_ttl, session_ttl=session_ttl)


def test_temporary_access_hashes_are_unique_and_not_plain_tokens():
    with TestingSessionLocal() as db:
        access = create_access(db)
        access.session_hash = "s" * 64
        access.session_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
        access.challenge_consumed_at = datetime.now(timezone.utc)
        access.estado = "activo"
        db.commit()

        with pytest.raises(IntegrityError):
            create_access(db)


def test_access_scope_keeps_exact_account_version_inside_case():
    with TestingSessionLocal() as db:
        first_case = create_case(db, suffix="1")
        second_case = create_case(db, suffix="2")
        account = create_account(db, first_case)
        access = create_access(db)
        db.add(models.AlcanceCuentaAccesoTemporal(
            acceso_id=access.id,
            caso_id=second_case.id,
            cuenta_id=account.id,
            cuenta_version=account.version,
        ))

        with pytest.raises(IntegrityError):
            db.commit()


def test_access_scope_is_unique_per_account_version():
    with TestingSessionLocal() as db:
        case = create_case(db)
        account = create_account(db, case)
        access = create_access(db)
        scope_data = {
            "acceso_id": access.id,
            "caso_id": case.id,
            "cuenta_id": account.id,
            "cuenta_version": account.version,
        }
        db.add(models.AlcanceCuentaAccesoTemporal(**scope_data))
        db.commit()
        db.add(models.AlcanceCuentaAccesoTemporal(**scope_data))

        with pytest.raises(IntegrityError):
            db.commit()


def test_revoked_access_requires_auditable_revocation_fields():
    with TestingSessionLocal() as db:
        access = create_access(db)
        access.estado = "revocado"

        with pytest.raises(IntegrityError):
            db.commit()


def test_audit_event_is_structured_and_immutable():
    with TestingSessionLocal() as db:
        case = create_case(db)
        audit = models.AuditoriaCasoAyuda(
            event_id="event-1",
            organizacion_id=case.organizacion_id,
            caso_id=case.id,
            accion="cuenta_consultada",
            actor_id="donante-uid",
            actor_tipo="donante",
            entidad_tipo="cuenta",
            entidad_id="1",
            metadata_json="{}",
        )
        db.add(audit)
        db.commit()
        audit.accion = "evento_alterado"

        with pytest.raises(ValueError, match="inmutables"):
            db.commit()


def test_audit_rejects_unknown_actor_type():
    with TestingSessionLocal() as db:
        db.add(models.AuditoriaCasoAyuda(
            event_id="event-1",
            organizacion_id="org-1",
            accion="acceso_global",
            actor_id="unknown",
            actor_tipo="invitado",
            entidad_tipo="caso",
            entidad_id="1",
            metadata_json="{}",
        ))

        with pytest.raises(IntegrityError):
            db.commit()


def test_notification_outbox_deduplicates_domain_event_and_recipient():
    with TestingSessionLocal() as db:
        notification_data = {
            "deduplication_key": "help-1:reported:responsible",
            "evento_tipo": "ayuda_reportada",
            "destinatario_tipo": "responsable",
            "destinatario_email_hash": "d" * 64,
            "destinatario_email_cifrado": "encrypted-email",
            "template_key": "help-reported-v1",
            "payload_json": "{}",
        }
        db.add(models.NotificacionCasoAyuda(**notification_data))
        db.commit()
        db.add(models.NotificacionCasoAyuda(**notification_data))

        with pytest.raises(IntegrityError):
            db.commit()


def test_notification_rejects_unknown_event_or_negative_attempts():
    with TestingSessionLocal() as db:
        db.add(models.NotificacionCasoAyuda(
            deduplication_key="unknown-event",
            evento_tipo="datos_bancarios",
            destinatario_tipo="responsable",
            destinatario_email_hash="d" * 64,
            destinatario_email_cifrado="encrypted-email",
            template_key="unsafe-v1",
            payload_json="{}",
            intentos=-1,
        ))

        with pytest.raises(IntegrityError):
            db.commit()


def test_sent_notification_requires_provider_result_and_timestamp():
    with TestingSessionLocal() as db:
        db.add(models.NotificacionCasoAyuda(
            deduplication_key="help-1:confirmed:donor",
            evento_tipo="ayuda_confirmada",
            destinatario_tipo="donante",
            destinatario_email_hash="d" * 64,
            destinatario_email_cifrado="encrypted-email",
            template_key="help-confirmed-v1",
            payload_json="{}",
            estado="enviada",
            intentos=1,
        ))

        with pytest.raises(IntegrityError):
            db.commit()
