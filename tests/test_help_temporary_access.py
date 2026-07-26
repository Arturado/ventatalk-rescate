import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import models
from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
from main import app
from services.help_crypto import HelpDataCipher


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
organization_actor = {"value": None}


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


def current_organization_actor():
    return organization_actor["value"]


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "firebase-functions"
app.dependency_overrides[require_verified_case_organization_actor] = current_organization_actor
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch, tmp_path):
    monkeypatch.delenv("HELP_ADMIN_NOTIFICATION_EMAILS", raising=False)
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path))
    monkeypatch.setenv("RESEND_API_KEY", "RESEND_API_TEST")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "help@example.test")
    monkeypatch.setenv("HELP_FRONTEND_BASE_URL", "https://example.test")
    organization_actor["value"] = ActorOrgContext(
        "org-1", "coordinador", "coordinator@example.com", uid="coordinator-1"
    )
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_scoped_aid(
    *,
    organization_id="org-1",
    public_id="temporary-case",
    responsible_email="Responsible@Example.com",
    account_key="principal",
    account_version=1,
    account_status="aprobada",
    case_status="publicado",
):
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id=organization_id,
            nombres_apellidos_cifrado=cipher.encrypt("Private Person", field="beneficiary.name"),
            cedula_hash=hashlib.sha256(f"{organization_id}:{public_id}".encode()).hexdigest(),
            cedula_cifrada=cipher.encrypt("V-12345678", field="beneficiary.identity"),
            correo_cifrado=cipher.encrypt("beneficiary@example.com", field="beneficiary.email"),
            creado_por="coordinator@example.com",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id=public_id,
            organizacion_id=organization_id,
            beneficiario_id=beneficiary.id,
            titulo_interno="Private title",
            categoria="medicamentos",
            meta_monto=Decimal("100.00"),
            meta_moneda="USD",
            estado=case_status,
            creado_por="coordinator@example.com",
        )
        db.add(case)
        db.flush()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Public Person",
            titulo_publico="Public help title",
            descripcion_publica="Public description",
            version=1,
            activa=True,
        ))
        account = models.CuentaCasoAyuda(
            caso_id=case.id,
            account_key=account_key,
            version=account_version,
            tipo_titular="beneficiario",
            titular_nombre_cifrado=cipher.encrypt("Account Holder", field="account.holder_name"),
            relacion_beneficiario="propia",
            medio="banco_venezolano",
            moneda="USD",
            identificador_cifrado=cipher.encrypt("0102-SECRET-ACCOUNT", field="account.identifier"),
            instrucciones_cifrado=cipher.encrypt("Use exact reference", field="account.instructions"),
            responsable_nombre_cifrado=cipher.encrypt("Responsible", field="account.responsible_name"),
            responsable_email_hash=cipher.blind_index(responsible_email, purpose="responsible-email"),
            responsable_email_cifrado=cipher.encrypt(responsible_email, field="account.responsible_email"),
            consentimiento_version=1,
            estado=account_status,
            creado_por="coordinator@example.com",
            aprobado_por="coordinator@example.com" if account_status == "aprobada" else None,
            aprobado_at=datetime.now(timezone.utc) if account_status == "aprobada" else None,
        )
        db.add(account)
        db.flush()
        operation = models.OperacionIdempotenteAyuda(
            actor_id=f"donor-{organization_id}-{public_id}",
            organizacion_id=organization_id,
            operacion="report_help",
            idempotency_key=f"report-{organization_id}-{public_id}",
            request_hash="f" * 64,
            estado="completed",
            response_status=201,
            response_body="{}",
        )
        db.add(operation)
        db.flush()
        aid = models.AyudaMonetaria(
            caso_id=case.id,
            cuenta_id=account.id,
            cuenta_version=account.version,
            donante_id=operation.actor_id,
            monto_reportado=Decimal("25.00"),
            moneda_reportada="USD",
            fecha_transferencia=date(2026, 7, 20),
            estado="pendiente_confirmacion",
            idempotency_id=operation.id,
        )
        db.add(aid)
        db.commit()
        return case.id, account.id, aid.id


def request_access(email=" responsible@example.com ", public_id=None, ip_hash="1" * 64):
    payload = {"email": email, "ip_hash": ip_hash}
    if public_id is not None:
        payload["public_id"] = public_id
    return client.post("/api/v2/accesos-temporales/solicitar", json=payload)


def test_request_fails_closed_before_email_lookup_when_resend_is_not_configured(monkeypatch):
    create_scoped_aid()
    monkeypatch.delenv("RESEND_API_KEY")

    existing = request_access()
    missing = request_access("missing@example.com", ip_hash="2" * 64)

    assert existing.status_code == missing.status_code == 503
    assert existing.json() == missing.json()
    with TestingSessionLocal() as db:
        assert db.query(models.AccesoTemporalAyuda).count() == 0


def raw_challenge_from_outbox():
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        notification = db.query(models.NotificacionCasoAyuda).one()
        encrypted_payload = notification.payload_json
        payload = json.loads(cipher.decrypt(encrypted_payload, field="notification.payload"))
        return payload["challenge_token"], notification


def redeem(challenge):
    return client.post(
        "/api/v2/accesos-temporales/canjear",
        json={"challenge_token": challenge},
    )


def create_active_session():
    request_access()
    challenge, _ = raw_challenge_from_outbox()
    response = redeem(challenge)
    assert response.status_code == 200
    return response.json()["session_token"]


def attach_receipt(aid_id, tmp_path, *, state="vigente"):
    plaintext = b"%PDF-1.7 TEMPORARY-ACCESS-RECEIPT"
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        aid = db.get(models.AyudaMonetaria, aid_id)
        relative_path = f"private/casos-ayuda/{aid.caso_id}/temporary-receipt.enc"
        destination = tmp_path / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(cipher.encrypt_bytes(plaintext, field="aid.receipt"))
        receipt = models.ComprobanteAyuda(
            ayuda_id=aid.id,
            version=1,
            storage_path=relative_path,
            nombre_original_cifrado=cipher.encrypt(
                "responsible-private-receipt.pdf", field="receipt.original_name"
            ),
            content_type="application/pdf",
            size_bytes=len(plaintext),
            checksum_sha256=hashlib.sha256(plaintext).hexdigest(),
            estado=state,
            cargado_por=aid.donante_id,
        )
        db.add(receipt)
        db.commit()
        return receipt.id, plaintext, relative_path


def temporary_receipt_download(session_token, aid_id, receipt_id):
    return client.get(
        f"/api/v2/accesos-temporales/ayudas/{aid_id}/comprobantes/{receipt_id}/descargar",
        headers={"X-Temporary-Session": session_token},
    )


def test_request_is_non_enumerable_and_stores_only_encrypted_secrets():
    create_scoped_aid()

    matched = request_access()
    with TestingSessionLocal() as db:
        access = db.query(models.AccesoTemporalAyuda).one()
        notification = db.query(models.NotificacionCasoAyuda).one()
        database_values = " ".join(
            str(value)
            for value in (
                access.destinatario_email_cifrado,
                access.challenge_hash,
                access.solicitado_por,
                notification.destinatario_email_cifrado,
                notification.payload_json,
                notification.deduplication_key,
            )
        )
        assert access.session_ttl_seconds == 8 * 60 * 60
        assert access.challenge_ttl_seconds == 15 * 60
        assert access.challenge_expires_at <= access.created_at + timedelta(minutes=15)
        assert access.request_ip_hash == "1" * 64
        assert "responsible@example.com" not in database_values.lower()
        assert notification.evento_tipo == "acceso_temporal"
        assert notification.payload_json.startswith("enc:v1:")
        challenge, _ = raw_challenge_from_outbox()
        assert challenge not in database_values

    missing = request_access("missing@example.com")

    assert matched.status_code == missing.status_code == 202
    assert matched.json() == missing.json() == {"status": "accepted"}
    assert "token" not in json.dumps(matched.json()).lower()


def test_existing_and_nonexistent_requests_record_indistinguishable_attempt_evidence():
    create_scoped_aid()

    existing = request_access()
    missing = request_access("missing@example.com", ip_hash="2" * 64)

    assert existing.status_code == missing.status_code == 202
    assert existing.json() == missing.json() == {"status": "accepted"}
    with TestingSessionLocal() as db:
        attempts = db.query(models.IntentoSolicitudAccesoTemporal).order_by(
            models.IntentoSolicitudAccesoTemporal.id
        ).all()
        assert len(attempts) == 2
        assert all(len(item.email_hash) == len(item.ip_hash) == 64 for item in attempts)
        assert all(item.created_at is not None for item in attempts)


def test_attempt_table_has_no_plaintext_or_result_column():
    columns = {column["name"] for column in inspect(engine).get_columns(
        models.IntentoSolicitudAccesoTemporal.__tablename__
    )}

    assert columns == {"id", "email_hash", "ip_hash", "created_at"}
    request_access("private.person@example.com", ip_hash="a" * 64)
    with TestingSessionLocal() as db:
        attempt = db.query(models.IntentoSolicitudAccesoTemporal).one()
        serialized = f"{attempt.email_hash} {attempt.ip_hash}"
        assert "private.person@example.com" not in serialized
        assert "a" * 64 not in serialized


@pytest.mark.parametrize(
    ("limited_email", "first_ip", "second_ip"),
    [
        ("responsible@example.com", "1" * 64, "2" * 64),
        ("missing@example.com", "1" * 64, "2" * 64),
    ],
)
def test_email_threshold_is_identical_for_existing_and_nonexistent_accounts(
    monkeypatch, limited_email, first_ip, second_ip
):
    monkeypatch.setenv("TEMPORARY_ACCESS_EMAIL_LIMIT", "1")
    monkeypatch.setenv("TEMPORARY_ACCESS_IP_LIMIT", "10")
    create_scoped_aid()

    accepted = request_access(limited_email, ip_hash=first_ip)
    denied = request_access(limited_email, ip_hash=second_ip)

    assert accepted.status_code == 202
    assert denied.status_code == 429
    assert denied.json() == {"detail": "Demasiadas solicitudes; intenta nuevamente mas tarde"}
    with TestingSessionLocal() as db:
        assert db.query(models.IntentoSolicitudAccesoTemporal).count() == 2


def test_ip_threshold_counts_different_emails(monkeypatch):
    monkeypatch.setenv("TEMPORARY_ACCESS_EMAIL_LIMIT", "10")
    monkeypatch.setenv("TEMPORARY_ACCESS_IP_LIMIT", "1")

    assert request_access("first@example.com").status_code == 202
    denied = request_access("second@example.com")

    assert denied.status_code == 429
    with TestingSessionLocal() as db:
        assert db.query(models.IntentoSolicitudAccesoTemporal).count() == 2


def test_request_limit_window_expires(monkeypatch):
    monkeypatch.setenv("TEMPORARY_ACCESS_EMAIL_LIMIT", "1")
    monkeypatch.setenv("TEMPORARY_ACCESS_IP_LIMIT", "1")
    monkeypatch.setenv("TEMPORARY_ACCESS_LIMIT_WINDOW_SECONDS", "60")
    assert request_access("missing@example.com").status_code == 202
    with TestingSessionLocal() as db:
        attempt = db.query(models.IntentoSolicitudAccesoTemporal).one()
        attempt.created_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        db.commit()

    assert request_access("missing@example.com").status_code == 202


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("TEMPORARY_ACCESS_EMAIL_LIMIT", "0"),
        ("TEMPORARY_ACCESS_IP_LIMIT", "1001"),
        ("TEMPORARY_ACCESS_LIMIT_WINDOW_SECONDS", "86401"),
        ("TEMPORARY_ACCESS_ATTEMPT_RETENTION_DAYS", "366"),
    ],
)
def test_temporary_access_limit_environment_is_bounded(monkeypatch, name, value):
    from services.help_config import temporary_access_limit_config

    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        temporary_access_limit_config()


def test_rate_limit_signal_is_low_cardinality_and_contains_no_hashes(monkeypatch):
    from observability import metrics

    monkeypatch.setenv("TEMPORARY_ACCESS_EMAIL_LIMIT", "1")
    monkeypatch.setenv("TEMPORARY_ACCESS_IP_LIMIT", "10")
    metrics.clear()
    request_access("missing@example.com")

    denied = request_access("missing@example.com", ip_hash="2" * 64)
    rendered = metrics.render()

    assert denied.status_code == 429
    assert 'rescate_rate_limits_total{scope="temporary_access_email"}' in rendered
    assert "missing@example.com" not in rendered
    assert "1" * 64 not in rendered
    assert "2" * 64 not in rendered


def test_request_matches_only_latest_approved_account_version_and_optional_case():
    first_case_id, old_account_id, _ = create_scoped_aid(public_id="requested-case")
    create_scoped_aid(public_id="other-case", account_key="other")
    cipher = HelpDataCipher.from_environment()
    with TestingSessionLocal() as db:
        old = db.get(models.CuentaCasoAyuda, old_account_id)
        old.estado = "inactiva"
        latest = models.CuentaCasoAyuda(
            caso_id=first_case_id,
            account_key=old.account_key,
            version=2,
            tipo_titular=old.tipo_titular,
            titular_nombre_cifrado=old.titular_nombre_cifrado,
            relacion_beneficiario=old.relacion_beneficiario,
            medio=old.medio,
            moneda=old.moneda,
            identificador_cifrado=cipher.encrypt("LATEST-ACCOUNT", field="account.identifier"),
            responsable_nombre_cifrado=old.responsable_nombre_cifrado,
            responsable_email_hash=old.responsable_email_hash,
            responsable_email_cifrado=old.responsable_email_cifrado,
            consentimiento_version=old.consentimiento_version,
            estado="aprobada",
            creado_por="coordinator@example.com",
            aprobado_por="coordinator@example.com",
            aprobado_at=datetime.now(timezone.utc),
        )
        db.add(latest)
        db.commit()
        latest_id = latest.id

    response = request_access(public_id="requested-case")

    assert response.status_code == 202
    with TestingSessionLocal() as db:
        scopes = db.query(models.AlcanceCuentaAccesoTemporal).all()
        assert [(scope.caso_id, scope.cuenta_id, scope.cuenta_version) for scope in scopes] == [
            (first_case_id, latest_id, 2)
        ]


def test_challenge_has_one_winner_and_session_is_opaque_and_bounded():
    create_scoped_aid()
    request_access()
    challenge, _ = raw_challenge_from_outbox()

    first = redeem(challenge)
    second = redeem(challenge)

    assert first.status_code == 200
    assert second.status_code == 401
    session_token = first.json()["session_token"]
    assert len(session_token) >= 43
    with TestingSessionLocal() as db:
        access = db.query(models.AccesoTemporalAyuda).one()
        assert access.estado == "activo"
        assert access.challenge_consumed_at is not None
        assert access.session_expires_at <= access.challenge_consumed_at + timedelta(hours=8)
        assert access.session_hash != session_token


@pytest.mark.parametrize("terminal_state", ["expired", "revoked"])
def test_expired_or_revoked_challenge_is_rejected(terminal_state):
    create_scoped_aid()
    request_access()
    challenge, _ = raw_challenge_from_outbox()
    with TestingSessionLocal() as db:
        access = db.query(models.AccesoTemporalAyuda).one()
        if terminal_state == "expired":
            access.challenge_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        else:
            access.estado = "revocado"
            access.revocado_at = datetime.now(timezone.utc)
            access.revocado_por = "coordinator@example.com"
            access.motivo_revocacion = "riesgo_seguridad"
        db.commit()

    response = redeem(challenge)

    assert response.status_code == 401


def test_expired_session_is_rejected_on_every_request():
    create_scoped_aid()
    session_token = create_active_session()
    with TestingSessionLocal() as db:
        access = db.query(models.AccesoTemporalAyuda).one()
        access.session_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    response = client.get(
        "/api/v2/accesos-temporales/contexto",
        headers={"X-Temporary-Session": session_token},
    )

    assert response.status_code == 401


def test_context_returns_only_exact_scoped_case_account_version_and_aids():
    case_id, account_id, aid_id = create_scoped_aid()
    create_scoped_aid(
        public_id="private-other-case",
        account_key="other",
        responsible_email="other@example.com",
    )
    session_token = create_active_session()

    response = client.get(
        "/api/v2/accesos-temporales/contexto",
        headers={"X-Temporary-Session": session_token},
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "cases": [{
            "case_id": case_id,
            "public_id": "temporary-case",
            "public_name": "Public Person",
            "public_title": "Public help title",
            "status": "publicado",
            "goal_amount": "100.00",
            "goal_currency": "USD",
            "confirmed_amount": "0.00",
            "confirmed_help_count": 0,
            "accounts": [{
                "account_id": account_id,
                "account_version": 1,
                "medium": "banco_venezolano",
                "currency": "USD",
                "identifier": "0102-SECRET-ACCOUNT",
                "instructions": "Use exact reference",
                "aids": [{
                    "aid_id": aid_id,
                    "reported_amount": "25.00",
                    "reported_currency": "USD",
                    "transfer_date": "2026-07-20",
                    "status": "pendiente_confirmacion",
                    "problem_type": None,
                }],
            }],
        }]
    }
    assert "Private Person" not in json.dumps(body)
    assert "Private title" not in json.dumps(body)


def test_context_includes_receipt_id_only_for_current_receipt(tmp_path):
    _case_id, _account_id, aid_id = create_scoped_aid()
    receipt_id, _plaintext, _path = attach_receipt(aid_id, tmp_path)
    session_token = create_active_session()

    current = client.get(
        "/api/v2/accesos-temporales/contexto",
        headers={"X-Temporary-Session": session_token},
    )
    with TestingSessionLocal() as db:
        db.get(models.ComprobanteAyuda, receipt_id).estado = "reemplazado"
        db.commit()
    replaced = client.get(
        "/api/v2/accesos-temporales/contexto",
        headers={"X-Temporary-Session": session_token},
    )

    assert current.status_code == 200
    assert current.json()["cases"][0]["accounts"][0]["aids"][0]["receipt_id"] == receipt_id
    assert "receipt_id" not in replaced.json()["cases"][0]["accounts"][0]["aids"][0]


def test_temporary_actor_downloads_scoped_receipt_with_safe_headers_and_audit(tmp_path):
    _case_id, _account_id, aid_id = create_scoped_aid()
    receipt_id, plaintext, storage_path = attach_receipt(aid_id, tmp_path)
    session_token = create_active_session()

    response = temporary_receipt_download(session_token, aid_id, receipt_id)

    assert response.status_code == 200
    assert response.content == plaintext
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        f'attachment; filename="comprobante-ayuda-{aid_id}-v1.pdf"'
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    with TestingSessionLocal() as db:
        access = db.query(models.AccesoTemporalAyuda).one()
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(
            accion="comprobante_descargado"
        ).one()
        assert audit.actor_tipo == "responsable_temporal"
        assert audit.actor_id == f"temporary-access:{access.id}"
        assert audit.entidad_id == str(receipt_id)
        serialized = " ".join(
            filter(None, [audit.actor_id, audit.motivo_codigo, audit.metadata_json])
        )
        assert "responsible@example.com" not in serialized.lower()
        assert session_token not in serialized
        assert storage_path not in serialized
        assert hashlib.sha256(plaintext).hexdigest() not in serialized
        assert plaintext.decode() not in serialized


def test_outside_scope_receipt_is_not_found_before_decryption(tmp_path, monkeypatch):
    _case_id, _account_id, _aid_id = create_scoped_aid()
    _other_case_id, _other_account_id, other_aid_id = create_scoped_aid(
        public_id="other-receipt-case",
        responsible_email="other@example.com",
        account_key="other-receipt-account",
    )
    receipt_id, _plaintext, _path = attach_receipt(other_aid_id, tmp_path)
    session_token = create_active_session()
    decrypt_called = False

    def fail_if_called(*_args, **_kwargs):
        nonlocal decrypt_called
        decrypt_called = True
        raise AssertionError("outside-scope receipt must not be decrypted")

    monkeypatch.setattr(HelpDataCipher, "decrypt_bytes", fail_if_called)

    response = temporary_receipt_download(session_token, other_aid_id, receipt_id)

    assert response.status_code == 404
    assert response.json() == {"detail": "Comprobante no encontrado"}
    assert decrypt_called is False


def test_temporary_receipt_rejects_corrupt_content_without_audit(tmp_path):
    _case_id, _account_id, aid_id = create_scoped_aid()
    receipt_id, _plaintext, _path = attach_receipt(aid_id, tmp_path)
    session_token = create_active_session()
    with TestingSessionLocal() as db:
        db.get(models.ComprobanteAyuda, receipt_id).checksum_sha256 = "0" * 64
        db.commit()

    response = temporary_receipt_download(session_token, aid_id, receipt_id)

    assert response.status_code == 404
    assert response.json() == {"detail": "Comprobante no encontrado"}
    with TestingSessionLocal() as db:
        assert db.query(models.AuditoriaCasoAyuda).count() == 0


@pytest.mark.parametrize("session_state", ["revoked", "expired"])
def test_temporary_receipt_rejects_inactive_session(tmp_path, session_state):
    _case_id, _account_id, aid_id = create_scoped_aid()
    receipt_id, _plaintext, _path = attach_receipt(aid_id, tmp_path)
    session_token = create_active_session()
    with TestingSessionLocal() as db:
        access = db.query(models.AccesoTemporalAyuda).one()
        if session_state == "revoked":
            access.estado = "revocado"
            access.revocado_at = datetime.now(timezone.utc)
            access.revocado_por = "coordinator@example.com"
            access.motivo_revocacion = "riesgo_seguridad"
        else:
            access.session_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()

    response = temporary_receipt_download(session_token, aid_id, receipt_id)

    assert response.status_code == 401
    with TestingSessionLocal() as db:
        assert db.query(models.AuditoriaCasoAyuda).count() == 0


def test_temporary_actor_can_only_report_problem_for_scoped_aid():
    _case_id, _account_id, aid_id = create_scoped_aid()
    _other_case_id, _other_account_id, other_aid_id = create_scoped_aid(
        public_id="other-case", account_key="other", responsible_email="other@example.com"
    )
    session_token = create_active_session()
    payload = {"problem_type": "transferencia_no_recibida", "detail": "No llego la transferencia"}

    denied = client.post(
        f"/api/v2/accesos-temporales/ayudas/{other_aid_id}/problema",
        headers={"X-Temporary-Session": session_token},
        json=payload,
    )
    allowed = client.post(
        f"/api/v2/accesos-temporales/ayudas/{aid_id}/problema",
        headers={"X-Temporary-Session": session_token},
        json=payload,
    )

    assert denied.status_code == 404
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "problema_reportado"
    with TestingSessionLocal() as db:
        audit = db.query(models.AuditoriaCasoAyuda).filter_by(accion="problema_reportado").one()
        assert audit.actor_tipo == "responsable_temporal"
        assert "@" not in audit.actor_id
        serialized = f"{audit.actor_id} {audit.metadata_json}"
        assert session_token not in serialized
        notifications = db.query(models.NotificacionCasoAyuda).filter_by(
            evento_tipo="problema_reportado"
        ).all()
        assert [item.destinatario_tipo for item in notifications] == ["coordinador"]


def test_organization_lists_and_immediately_revokes_case_access():
    case_id, _account_id, _aid_id = create_scoped_aid()
    session_token = create_active_session()

    listed = client.get(f"/api/v2/casos-ayuda/{case_id}/accesos-temporales")
    access_id = listed.json()[0]["access_id"]
    revoked = client.post(
        f"/api/v2/casos-ayuda/{case_id}/accesos-temporales/{access_id}/revocar",
        json={"reason": "solicitud_responsable"},
    )
    context = client.get(
        "/api/v2/accesos-temporales/contexto",
        headers={"X-Temporary-Session": session_token},
    )

    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "activo"
    assert "email" not in json.dumps(listed.json()).lower()
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revocado"
    assert context.status_code == 401


def test_other_organization_cannot_list_or_revoke_case_access():
    case_id, _account_id, _aid_id = create_scoped_aid()
    create_active_session()
    organization_actor["value"] = ActorOrgContext(
        "org-2", "coordinador", "other@example.com", uid="coordinator-2"
    )

    listed = client.get(f"/api/v2/casos-ayuda/{case_id}/accesos-temporales")
    revoked = client.post(
        f"/api/v2/casos-ayuda/{case_id}/accesos-temporales/1/revocar",
        json={"reason": "solicitud_responsable"},
    )

    assert listed.status_code == 404
    assert revoked.status_code == 404


def test_revocation_does_not_cross_organization_boundaries():
    first_case_id, _account_id, _aid_id = create_scoped_aid()
    second_case_id, _account_id, _aid_id = create_scoped_aid(
        organization_id="org-2",
        public_id="org-2-case",
        account_key="org-2-account",
    )
    response = request_access()
    assert response.status_code == 202

    first_accesses = client.get(
        f"/api/v2/casos-ayuda/{first_case_id}/accesos-temporales"
    ).json()
    client.post(
        f"/api/v2/casos-ayuda/{first_case_id}/accesos-temporales/"
        f"{first_accesses[0]['access_id']}/revocar",
        json={"reason": "riesgo_seguridad"},
    )
    organization_actor["value"] = ActorOrgContext(
        "org-2", "coordinador", "org2@example.com", uid="coordinator-org-2"
    )

    second_accesses = client.get(
        f"/api/v2/casos-ayuda/{second_case_id}/accesos-temporales"
    )

    assert second_accesses.status_code == 200
    assert second_accesses.json()[0]["status"] == "pendiente"
