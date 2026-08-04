from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from dependencies import ActorOrgContext
from services.help_cases import OrganizationAccessError, _require_organization_access
from services.help_feature_flags import enabled_help_v2_organization_ids


CEDULA_MATCH_RATE_LIMIT_WINDOW_MINUTES = 10
CEDULA_MATCH_RATE_LIMIT_MAX_REQUESTS = 20
_CEDULA_MATCH_AUDIT_ACTION = "coincidencia_cedula_consultada"
_PUBLIC_CASE_STATES = {"publicado", "pausado", "meta_alcanzada"}


class BeneficiaryDomainError(ValueError):
    pass


class CedulaMatchRateLimitError(BeneficiaryDomainError):
    pass


@dataclass(frozen=True)
class OwnOrganizationCaseMatch:
    id: int
    public_id: str
    internal_title: str
    category: str
    state: str
    created_at: object


@dataclass(frozen=True)
class ExternalPublicCaseMatch:
    public_id: str
    title: str
    category: str
    state: str


@dataclass(frozen=True)
class CedulaMatchResult:
    own_organization_matches: list = field(default_factory=list)
    external_public_matches: list = field(default_factory=list)
    other_private_matches_present: bool = False


def get_or_create_beneficiary(
    db: Session,
    *,
    organization_id: str,
    identity_hash: str,
    identity_encrypted: str,
    name_encrypted: str,
    actor_email: str,
):
    existing = db.query(models.BeneficiarioAyuda).filter_by(
        organizacion_id=organization_id,
        cedula_hash=identity_hash,
    ).one_or_none()
    if existing is not None:
        return existing, False

    beneficiary = models.BeneficiarioAyuda(
        organizacion_id=organization_id,
        nombres_apellidos_cifrado=name_encrypted,
        cedula_hash=identity_hash,
        cedula_cifrada=identity_encrypted,
        creado_por=actor_email,
    )
    try:
        with db.begin_nested():
            db.add(beneficiary)
            db.flush()
    except IntegrityError:
        beneficiary = db.query(models.BeneficiarioAyuda).filter_by(
            organizacion_id=organization_id,
            cedula_hash=identity_hash,
        ).one()
        return beneficiary, False
    return beneficiary, True


def _enforce_cedula_match_rate_limit(db: Session, *, actor: ActorOrgContext):
    window_start = datetime.now(timezone.utc) - timedelta(minutes=CEDULA_MATCH_RATE_LIMIT_WINDOW_MINUTES)
    recent_count = db.query(models.AuditoriaCasoAyuda).filter(
        models.AuditoriaCasoAyuda.accion == _CEDULA_MATCH_AUDIT_ACTION,
        models.AuditoriaCasoAyuda.actor_id == actor.email,
        models.AuditoriaCasoAyuda.created_at >= window_start,
    ).count()
    if recent_count >= CEDULA_MATCH_RATE_LIMIT_MAX_REQUESTS:
        raise CedulaMatchRateLimitError(
            "Se alcanzo el limite de consultas de coincidencia de cedula, intenta mas tarde"
        )


def _audit_cedula_match_query(db: Session, *, actor: ActorOrgContext, identity_hash: str):
    db.add(models.AuditoriaCasoAyuda(
        event_id=str(uuid4()),
        organizacion_id=actor.organizacion_id or "",
        caso_id=None,
        accion=_CEDULA_MATCH_AUDIT_ACTION,
        actor_id=actor.email,
        actor_tipo=actor.role,
        entidad_tipo="beneficiario_cedula_hash",
        entidad_id=identity_hash[:16],
    ))
    db.flush()


def find_cedula_matches(
    db: Session,
    *,
    actor: ActorOrgContext,
    identity_hash: str,
    organization_id: str = None,
) -> CedulaMatchResult:
    if not actor.can_manage_organization_cases:
        raise OrganizationAccessError("El actor no puede consultar coincidencias de cedula")
    if len(identity_hash or "") != 64:
        raise BeneficiaryDomainError("identity_hash debe ser SHA-256")

    _enforce_cedula_match_rate_limit(db, actor=actor)

    own_organization_id = None
    if actor.is_org_scoped:
        own_organization_id = _require_organization_access(actor, actor.organizacion_id)
    elif organization_id:
        own_organization_id = _require_organization_access(actor, organization_id)

    candidate_organizations = enabled_help_v2_organization_ids()
    matching_beneficiaries = db.query(models.BeneficiarioAyuda).filter(
        models.BeneficiarioAyuda.cedula_hash == identity_hash,
        models.BeneficiarioAyuda.organizacion_id.in_(candidate_organizations),
    ).all()

    own_matches = []
    external_public_matches = []
    other_private_present = False

    for beneficiary in matching_beneficiaries:
        cases = db.query(models.CasoAyudaV2).filter_by(beneficiario_id=beneficiary.id).all()
        is_own_organization = (
            own_organization_id is not None and beneficiary.organizacion_id == own_organization_id
        )
        for case in cases:
            if is_own_organization:
                own_matches.append(OwnOrganizationCaseMatch(
                    id=case.id,
                    public_id=case.public_id,
                    internal_title=case.titulo_interno,
                    category=case.categoria,
                    state=case.estado,
                    created_at=case.created_at,
                ))
                continue

            publication = db.query(models.PublicacionCasoAyuda).filter_by(caso_id=case.id).one_or_none()
            is_public = (
                case.estado in _PUBLIC_CASE_STATES
                and publication is not None
                and publication.activa
            )
            if is_public:
                external_public_matches.append(ExternalPublicCaseMatch(
                    public_id=case.public_id,
                    title=publication.titulo_publico,
                    category=case.categoria,
                    state=case.estado,
                ))
            else:
                other_private_present = True

    _audit_cedula_match_query(db, actor=actor, identity_hash=identity_hash)

    return CedulaMatchResult(
        own_organization_matches=own_matches,
        external_public_matches=external_public_matches,
        other_private_matches_present=other_private_present,
    )
