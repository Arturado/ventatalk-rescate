import base64
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from dependencies import ActorOrgContext, require_verified_case_organization_actor, verify_api_key
import models
import schemas
from routers.casos_ayuda_publicos import router as public_router
from routers.casos_ayuda_v2 import router as management_router
from services.help_case_drafts import create_help_case_draft
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
app = FastAPI()
app.include_router(management_router)
app.include_router(public_router)


def override_get_db():
    with TestingSessionLocal() as db:
        yield db


def actor(email="uploader@example.com", organization_id="org-1"):
    return ActorOrgContext(organization_id, "coordinador", email)


app.dependency_overrides[get_db] = override_get_db
app.dependency_overrides[verify_api_key] = lambda: "server@example.com"
app.dependency_overrides[require_verified_case_organization_actor] = actor
client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(autouse=True)
def crypto_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HELP_DATA_ENCRYPTION_KEY_V1", base64.b64encode(b"e" * 32).decode())
    monkeypatch.setenv("HELP_DATA_ACTIVE_KEY_VERSION", "v1")
    monkeypatch.setenv("HELP_IDENTITY_HASH_KEY", base64.b64encode(b"h" * 32).decode())
    monkeypatch.setenv("HELP_PRIVATE_UPLOAD_ROOT", str(tmp_path))
    return tmp_path


def create_managed_case(*, suffix="1", state="borrador", visible=True):
    with TestingSessionLocal() as db:
        cipher = HelpDataCipher.from_environment()
        identity_number = int(hashlib.sha256(str(suffix).encode()).hexdigest()[:8], 16) % 900_000_000 + 100_000_000
        canonical_identity = f"V-{identity_number}"
        summary, _created = create_help_case_draft(
            db,
            actor=actor(),
            organization_id="org-1",
            public_id=f"document-case-{suffix}",
            payload=schemas.CasoAyudaV2DraftCreateRequest(
                category="salud",
                subject_type="persona",
                aid_modes=["monetaria"],
                beneficiary_name="Nombre privado",
                beneficiary_identity=canonical_identity,
                title="Caso de documentos",
                story="Historia de prueba para el caso de documentos.",
                goal_amount=Decimal("100.00"),
                goal_currency="USD",
            ),
            idempotency_key=f"document-case-key-{suffix}-0000001",
            cipher=cipher,
        )
        case = db.query(models.CasoAyudaV2).filter_by(id=summary["id"]).one()
        case.estado = state
        publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id).one()
        publication.nombre_publico = "Ana"
        publication.titulo_publico = "Ayuda medica"
        publication.descripcion_publica = "Descripcion publica autorizada"
        publication.activa = visible
        db.add(models.ConsentimientoCasoAyuda(
            caso_id=case.id,
            version=1,
            texto_version="mvp1-v1",
            alcance_json="{}",
            firmante_nombre_cifrado="encrypted-signer",
            registrado_por="uploader@example.com",
            vigente=True,
        ))
        db.commit()
        return case.id, case.public_id


def upload_document(case_id, *, document_key="medical-report", classification="publico", payload=None):
    content = payload or b"%PDF-1.7 DOCUMENT-PLAINTEXT-MARKER"
    return client.post(f"/api/v2/casos-ayuda/{case_id}/documentos", json={
        "document_key": document_key,
        "document_type": "informe_medico",
        "classification": classification,
        "file_name": "../../expediente secreto.pdf",
        "content_type": "application/pdf",
        "content_base64": base64.b64encode(content).decode(),
        "storage_path": "public/client-controlled/plaintext.pdf",
    })


def review_document(case_id, document_id, *, email="reviewer@example.com", approve=True):
    app.dependency_overrides[require_verified_case_organization_actor] = lambda: actor(email)
    try:
        return client.post(
            f"/api/v2/casos-ayuda/{case_id}/documentos/{document_id}/revisar",
            json={"approve": approve, "reason": None if approve else "No corresponde"},
        )
    finally:
        app.dependency_overrides[require_verified_case_organization_actor] = actor


def publish_case(case_id, *, visible=True):
    with TestingSessionLocal() as db:
        case = db.get(models.CasoAyudaV2, case_id)
        case.estado = "publicado"
        db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case_id).one().activa = visible
        db.commit()


def test_upload_encrypts_real_bytes_and_returns_only_safe_document_summary(crypto_environment):
    case_id, _ = create_managed_case()
    plaintext = b"%PDF-1.7 DOCUMENT-PLAINTEXT-MARKER"

    response = upload_document(case_id, payload=plaintext)

    assert response.status_code == 201
    summary = response.json()
    assert summary == {
        "id": summary["id"],
        "document_key": "medical-report",
        "version": 1,
        "tipo": "informe_medico",
        "clasificacion": "publico",
        "estado_revision": "aprobado",
        "content_type": "application/pdf",
        "size_bytes": len(plaintext),
        "cargado_por": "uploader@example.com",
        "revisado_por": "uploader@example.com",
    }
    assert "storage" not in response.text
    assert "checksum" not in response.text
    assert "cipher" not in response.text
    assert "DOCUMENT-PLAINTEXT-MARKER" not in response.text
    with TestingSessionLocal() as db:
        document = db.get(models.DocumentoCasoAyuda, summary["id"])
        assert document.storage_path.startswith(f"public/casos-ayuda/{case_id}/")
        assert document.storage_path != "public/client-controlled/plaintext.pdf"
        assert document.checksum_sha256 == hashlib.sha256(plaintext).hexdigest()
        assert document.nombre_original_cifrado.startswith("enc:v1:")
        stored_file = Path(crypto_environment, document.storage_path)
        ciphertext = stored_file.read_bytes()
        assert plaintext not in ciphertext
        assert HelpDataCipher.from_environment().decrypt_bytes(
            ciphertext,
            field="document.content",
        ) == plaintext


@pytest.mark.parametrize(
    ("content_type", "payload", "expected_status"),
    [
        ("text/plain", b"plain text", 422),
        ("application/pdf", b"not a pdf", 400),
        ("application/pdf", b"%PDF-" + b"x" * (5 * 1024 * 1024), 400),
    ],
)
def test_upload_rejects_unsupported_mismatched_or_oversized_content(content_type, payload, expected_status):
    case_id, _ = create_managed_case(suffix=content_type.replace("/", "-") + str(len(payload)))

    response = client.post(f"/api/v2/casos-ayuda/{case_id}/documentos", json={
        "document_key": "medical-report",
        "document_type": "informe_medico",
        "classification": "privado",
        "file_name": "report.bin",
        "content_type": content_type,
        "content_base64": base64.b64encode(payload).decode(),
    })

    assert response.status_code == expected_status
    with TestingSessionLocal() as db:
        assert db.query(models.DocumentoCasoAyuda).count() == 0


def test_document_upload_auto_approves_without_a_separate_review_step():
    case_id, _ = create_managed_case()

    uploaded = upload_document(case_id).json()

    assert uploaded["estado_revision"] == "aprobado"
    assert uploaded["revisado_por"] == "uploader@example.com"


def test_rejecting_an_already_approved_document_retracts_it():
    case_id, _ = create_managed_case()
    uploaded = upload_document(case_id).json()

    rejected = review_document(case_id, uploaded["id"], approve=False)

    assert rejected.status_code == 200
    assert rejected.json()["estado_revision"] == "rechazado"


def test_management_detail_exposes_safe_document_summaries_and_replacement_versions():
    case_id, _ = create_managed_case()
    first = upload_document(case_id).json()

    second_plaintext = b"%PDF-1.7 REPLACEMENT-CONTENT"
    second = upload_document(case_id, payload=second_plaintext).json()
    detail = client.get(f"/api/v2/casos-ayuda/{case_id}")

    assert second["version"] == 2
    assert detail.status_code == 200
    assert [item["version"] for item in detail.json()["documentos"]] == [2, 1]
    assert detail.json()["documentos"][1]["estado_revision"] == "aprobado"
    assert not ({"storage_path", "checksum_sha256", "nombre_original_cifrado"} & set(detail.json()["documentos"][0]))
    with TestingSessionLocal() as db:
        approved = db.get(models.DocumentoCasoAyuda, first["id"])
        assert approved.version == 1
        assert approved.estado_revision == "aprobado"
        assert approved.checksum_sha256 == hashlib.sha256(b"%PDF-1.7 DOCUMENT-PLAINTEXT-MARKER").hexdigest()
        assert db.get(models.DocumentoCasoAyuda, second["id"]).checksum_sha256 == hashlib.sha256(second_plaintext).hexdigest()


def test_current_public_document_download_decrypts_with_safe_attachment_headers():
    case_id, public_id = create_managed_case()
    plaintext = b"%PDF-1.7 DOWNLOAD-PLAINTEXT-MARKER"
    document = upload_document(case_id, payload=plaintext).json()
    review_document(case_id, document["id"])
    publish_case(case_id)

    response = client.get(f"/api/v2/public/casos-ayuda/{public_id}/documentos/{document['id']}")

    assert response.status_code == 200
    assert response.content == plaintext
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == f'attachment; filename="informe-medico-{document["id"]}-v1.pdf"'
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "storage" not in response.headers


@pytest.mark.parametrize(
    "variant",
    ["private", "pending", "rejected", "superseded", "stale-consent", "hidden-case", "wrong-case"],
)
def test_public_download_returns_not_found_for_noncanonical_document_variants(variant):
    case_id, public_id = create_managed_case()
    classification = "privado" if variant == "private" else "publico"
    document = upload_document(case_id, classification=classification).json()
    if variant == "pending":
        # Documents auto-approve on upload; simulate a never-reviewed row directly
        # (e.g. a pre-auto-approval data remnant) to prove the public endpoint still
        # fails closed for it.
        with TestingSessionLocal() as db:
            stored = db.query(models.DocumentoCasoAyuda).filter_by(id=document["id"]).one()
            stored.estado_revision = "pendiente"
            stored.revisado_por = None
            stored.revisado_at = None
            db.commit()
    elif variant != "private":
        review_document(case_id, document["id"], approve=variant != "rejected")
    if variant == "superseded":
        upload_document(case_id)
    if variant == "stale-consent":
        with TestingSessionLocal() as db:
            db.query(models.ConsentimientoCasoAyuda).filter_by(caso_id=case_id).one().vigente = False
            db.add(models.ConsentimientoCasoAyuda(
                caso_id=case_id,
                version=2,
                texto_version="mvp1-v2",
                alcance_json="{}",
                firmante_nombre_cifrado="encrypted-signer",
                registrado_por="uploader@example.com",
                vigente=True,
            ))
            db.commit()
    if variant == "wrong-case":
        _, public_id = create_managed_case(suffix="other", state="publicado")
    publish_case(case_id, visible=variant != "hidden-case")

    response = client.get(f"/api/v2/public/casos-ayuda/{public_id}/documentos/{document['id']}")

    assert response.status_code == 404
