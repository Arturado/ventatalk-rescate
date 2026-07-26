import base64
import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from dependencies import ActorOrgContext
from services.help_donors import DonorRateLimitError, disclose_donor_accounts
from test_help_donor_router import create_published_case


TEST_DATABASE_URL = os.getenv("HELP_CONCURRENCY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="HELP_CONCURRENCY_TEST_DATABASE_URL is required for PostgreSQL concurrency tests",
)


def test_concurrent_disclosures_do_not_exceed_threshold(monkeypatch):
    database_name = urlsplit(TEST_DATABASE_URL).path.rsplit("/", 1)[-1]
    assert database_name.endswith("_test")
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-concurrency")
    monkeypatch.setenv("DONOR_ACCOUNT_ACTOR_LIMIT", "1")
    engine = create_engine(TEST_DATABASE_URL)
    sessions = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    actor = ActorOrgContext("", "donor", "donor@example.test", uid="donor-1", ip_hash="a" * 64)

    with sessions() as db:
        from services.help_crypto import HelpDataCipher
        cipher = HelpDataCipher.from_environment()
        beneficiary = models.BeneficiarioAyuda(
            organizacion_id="org-concurrency", nombres_apellidos_cifrado="enc",
            cedula_hash="a" * 64, cedula_cifrada="enc", creado_por="creator",
        )
        db.add(beneficiary)
        db.flush()
        case = models.CasoAyudaV2(
            public_id="quota-concurrency", organizacion_id="org-concurrency",
            beneficiario_id=beneficiary.id, titulo_interno="Quota", categoria="medicamentos",
            meta_monto=100, meta_moneda="USD", estado="publicado", creado_por="creator",
        )
        db.add(case)
        db.flush()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id, nombre_publico="P", titulo_publico="T",
            descripcion_publica="D", activa=True,
        ))
        consent = models.ConsentimientoCasoAyuda(
            caso_id=case.id, version=1, texto_version="v1", alcance_json="{}",
            firmante_nombre_cifrado="enc", firmante_tipo="beneficiario",
            registrado_por="creator", vigente=True,
        )
        db.add(consent)
        db.flush()
        db.add(models.CuentaCasoAyuda(
            caso_id=case.id, account_key="main", version=1,
            tipo_titular="beneficiario", titular_nombre_cifrado=cipher.encrypt("Holder", field="account.holder_name"),
            relacion_beneficiario="propia", medio="banco_venezolano", moneda="USD",
            identificador_cifrado=cipher.encrypt("account", field="account.identifier"),
            responsable_nombre_cifrado="enc", responsable_email_hash="b" * 64,
            responsable_email_cifrado="enc", consentimiento_version=1,
            estado="aprobada", creado_por="creator",
        ))
        db.add(models.AceptacionTerminosDonante(actor_uid=actor.uid, terms_version="donor-v1", ip_hash=actor.ip_hash))
        db.commit()

    def disclose():
        with sessions() as db:
            try:
                disclose_donor_accounts(db, actor=actor, public_id="quota-concurrency")
                db.commit()
                return "allowed"
            except DonorRateLimitError:
                db.commit()
                return "denied"

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = [future.result(timeout=10) for future in [executor.submit(disclose), executor.submit(disclose)]]
        assert sorted(outcomes) == ["allowed", "denied"]
        with sessions() as db:
            assert db.query(models.AccesoCuentaDonante).count() == 1
            assert db.query(models.AuditoriaCasoAyuda).filter_by(accion="limite_seguridad_denegado").count() == 1
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
