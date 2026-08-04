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


def create_beneficiary(db, *, organization_id="org-1", identity_hash="a" * 64):
    beneficiary = models.BeneficiarioAyuda(
        organizacion_id=organization_id,
        nombres_apellidos_cifrado="encrypted-name",
        cedula_hash=identity_hash,
        cedula_cifrada="encrypted-id",
        creado_por="coordinador@example.com",
    )
    db.add(beneficiary)
    db.flush()
    return beneficiary


def create_case(db, beneficiary, *, goal=Decimal("100.00"), currency="USD"):
    case = models.CasoAyudaV2(
        public_id="case-public-1",
        organizacion_id=beneficiary.organizacion_id,
        beneficiario_id=beneficiary.id,
        titulo_interno="Caso de prueba",
        categoria="medicamentos",
        meta_monto=goal,
        meta_moneda=currency,
        creado_por="coordinador@example.com",
    )
    db.add(case)
    db.flush()
    return case


def test_create_all_is_idempotent_and_creates_v2_core_tables():
    Base.metadata.create_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    table_names = set(inspect(engine).get_table_names())
    assert {
        "beneficiarios_ayuda",
        "casos_ayuda_v2",
        "casos_ayuda_publicaciones",
        "casos_ayuda_consentimientos",
        "casos_ayuda_documentos",
    }.issubset(table_names)


def test_beneficiary_identity_hash_is_unique_inside_organization():
    with TestingSessionLocal() as db:
        create_beneficiary(db)
        db.commit()

        with pytest.raises(IntegrityError):
            create_beneficiary(db)


def test_same_beneficiary_identity_hash_is_allowed_in_another_organization():
    with TestingSessionLocal() as db:
        create_beneficiary(db, organization_id="org-1")
        create_beneficiary(db, organization_id="org-2")
        db.commit()

        assert db.query(models.BeneficiarioAyuda).count() == 2


def test_case_stores_decimal_goal_and_starts_as_draft():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = create_case(db, beneficiary, goal=Decimal("123.45"), currency="EUR")
        db.commit()
        db.refresh(case)

        assert case.meta_monto == Decimal("123.45")
        assert case.meta_moneda == "EUR"
        assert case.estado == "borrador"
        assert case.monto_confirmado == Decimal("0.00")


@pytest.mark.parametrize(
    ("goal", "currency"),
    [
        (Decimal("0.00"), "USD"),
        (Decimal("-1.00"), "USD"),
        (Decimal("10.00"), "GBP"),
    ],
)
def test_case_rejects_invalid_goal_or_currency(goal, currency):
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)

        with pytest.raises(IntegrityError):
            create_case(db, beneficiary, goal=goal, currency=currency)


def test_case_cannot_reference_beneficiary_from_another_organization():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db, organization_id="org-1")
        case = create_case(db, beneficiary)
        case.organizacion_id = "org-2"

        with pytest.raises(IntegrityError):
            db.commit()


def test_case_has_only_one_publication_configuration():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = create_case(db, beneficiary)
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Ana",
            titulo_publico="Ayuda para tratamiento",
            descripcion_publica="Descripción autorizada",
            version=1,
        ))
        db.commit()
        db.add(models.PublicacionCasoAyuda(
            caso_id=case.id,
            nombre_publico="Ana",
            titulo_publico="Otra publicación",
            descripcion_publica="Otra descripción",
            version=2,
        ))

        with pytest.raises(IntegrityError):
            db.commit()


def test_consent_version_is_unique_per_case():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = create_case(db, beneficiary)
        consent_data = {
            "caso_id": case.id,
            "version": 1,
            "texto_version": "mvp1-v1",
            "alcance_json": "{}",
            "firmante_nombre_cifrado": "encrypted-signer",
            "registrado_por": "coordinador@example.com",
        }
        db.add(models.ConsentimientoCasoAyuda(**consent_data))
        db.commit()
        db.add(models.ConsentimientoCasoAyuda(**consent_data))

        with pytest.raises(IntegrityError):
            db.commit()


def test_document_versions_are_unique_and_use_closed_states():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = create_case(db, beneficiary)
        document_data = {
            "caso_id": case.id,
            "document_key": "medical-report-1",
            "version": 1,
            "tipo": "informe_medico",
            "clasificacion": "publico",
            "estado_revision": "pendiente",
            "storage_path": "public/cases/report.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1024,
            "checksum_sha256": "b" * 64,
            "cargado_por": "coordinador@example.com",
        }
        db.add(models.DocumentoCasoAyuda(**document_data))
        db.commit()
        db.add(models.DocumentoCasoAyuda(**document_data))

        with pytest.raises(IntegrityError):
            db.commit()


def test_case_defaults_to_persona_subject_with_monetary_modality():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = create_case(db, beneficiary)
        db.commit()
        db.refresh(case)

        assert case.tipo_sujeto == "persona"
        assert case.acepta_ayuda_monetaria is True
        assert case.acepta_ayuda_directa is False


def test_campana_case_allows_null_beneficiary_and_directa_without_meta():
    with TestingSessionLocal() as db:
        case = models.CasoAyudaV2(
            public_id="case-campana-1",
            organizacion_id="org-1",
            beneficiario_id=None,
            tipo_sujeto="campana_organizacion",
            titulo_interno="Campana de insumos",
            categoria="insumo_recurso",
            acepta_ayuda_monetaria=False,
            acepta_ayuda_directa=True,
            meta_monto=None,
            meta_moneda=None,
            creado_por="coordinador@example.com",
        )
        db.add(case)
        db.commit()
        db.refresh(case)

        assert case.beneficiario_id is None
        assert case.meta_monto is None


def test_persona_case_requires_beneficiary():
    with TestingSessionLocal() as db:
        case = models.CasoAyudaV2(
            public_id="case-sin-beneficiario",
            organizacion_id="org-1",
            beneficiario_id=None,
            tipo_sujeto="persona",
            titulo_interno="Caso sin beneficiario",
            categoria="salud",
            meta_monto=Decimal("10.00"),
            meta_moneda="USD",
            creado_por="coordinador@example.com",
        )
        db.add(case)

        with pytest.raises(IntegrityError):
            db.commit()


def test_monetary_modality_requires_goal_and_currency():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = models.CasoAyudaV2(
            public_id="case-monetaria-sin-meta",
            organizacion_id=beneficiary.organizacion_id,
            beneficiario_id=beneficiary.id,
            tipo_sujeto="persona",
            titulo_interno="Caso monetario sin meta",
            categoria="salud",
            acepta_ayuda_monetaria=True,
            acepta_ayuda_directa=False,
            meta_monto=None,
            meta_moneda=None,
            creado_por="coordinador@example.com",
        )
        db.add(case)

        with pytest.raises(IntegrityError):
            db.commit()


def test_case_requires_at_least_one_modality_unless_empleo():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = models.CasoAyudaV2(
            public_id="case-sin-modalidad",
            organizacion_id=beneficiary.organizacion_id,
            beneficiario_id=beneficiary.id,
            tipo_sujeto="persona",
            titulo_interno="Caso sin modalidad",
            categoria="insumo_recurso",
            acepta_ayuda_monetaria=False,
            acepta_ayuda_directa=False,
            meta_monto=None,
            meta_moneda=None,
            creado_por="coordinador@example.com",
        )
        db.add(case)

        with pytest.raises(IntegrityError):
            db.commit()


def test_empleo_case_must_have_no_monetary_or_directa_modality():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = models.CasoAyudaV2(
            public_id="case-empleo-invalido",
            organizacion_id=beneficiary.organizacion_id,
            beneficiario_id=beneficiary.id,
            tipo_sujeto="persona",
            titulo_interno="Busco empleo",
            categoria="empleo",
            acepta_ayuda_monetaria=True,
            acepta_ayuda_directa=False,
            meta_monto=Decimal("10.00"),
            meta_moneda="USD",
            creado_por="coordinador@example.com",
        )
        db.add(case)

        with pytest.raises(IntegrityError):
            db.commit()


def test_case_stores_encrypted_conditional_detail_with_schema_version():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case_id = create_case(db, beneficiary).id
        db.commit()

    with TestingSessionLocal() as db:
        case = db.query(models.CasoAyudaV2).filter_by(id=case_id).one()
        case.detalle_condicional_cifrado = "enc:1:payload"
        case.detalle_condicional_version = 1
        db.commit()

    with TestingSessionLocal() as db:
        reloaded = db.query(models.CasoAyudaV2).filter_by(id=case_id).one()
        assert reloaded.detalle_condicional_cifrado == "enc:1:payload"
        assert reloaded.detalle_condicional_version == 1


def test_document_rejects_invalid_classification():
    with TestingSessionLocal() as db:
        beneficiary = create_beneficiary(db)
        case = create_case(db, beneficiary)
        db.add(models.DocumentoCasoAyuda(
            caso_id=case.id,
            document_key="medical-report-1",
            version=1,
            tipo="informe_medico",
            clasificacion="secreto",
            estado_revision="pendiente",
            storage_path="private/cases/report.pdf",
            content_type="application/pdf",
            size_bytes=1024,
            checksum_sha256="b" * 64,
            cargado_por="coordinador@example.com",
        ))

        with pytest.raises(IntegrityError):
            db.commit()
