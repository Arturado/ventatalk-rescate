import time

import pytest
from fastapi import HTTPException

from dependencies import (
    _actor_org_signature,
    require_verified_actor_org,
    require_verified_case_organization_actor,
)


SECRET = "test-signing-secret"


def signed_context(
    monkeypatch,
    *,
    organization_id="",
    role="donor",
    email="actor@example.com",
    uid="firebase-uid-123",
    ip_hash="a" * 64,
    timestamp=None,
):
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", SECRET)
    signed_at = str(timestamp or int(time.time()))
    normalized_email = email.strip().lower()
    signature = _actor_org_signature(
        organization_id,
        role,
        normalized_email,
        uid,
        ip_hash,
        signed_at,
        SECRET,
    )
    return require_verified_actor_org(
        x_actor_org_id=organization_id,
        x_actor_role=role,
        x_actor_email=email,
        x_actor_uid=uid,
        x_actor_ip_hash=ip_hash,
        x_actor_org_signature=signature,
        x_actor_org_timestamp=signed_at,
    )


def test_accepts_donor_without_organization_for_public_case_operations(monkeypatch):
    actor = signed_context(monkeypatch, email="DONANTE@EXAMPLE.COM")

    assert actor.email == "donante@example.com"
    assert actor.role == "donor"
    assert actor.organizacion_id == ""
    assert actor.uid == "firebase-uid-123"
    assert actor.ip_hash == "a" * 64
    assert actor.can_access_public_cases is True
    assert actor.can_manage_organization_cases is False


def test_preserves_admin_role_and_donor_capability(monkeypatch):
    actor = signed_context(
        monkeypatch,
        organization_id="org-1",
        role="admin",
        email="admin@example.com",
    )

    assert actor.can_access_public_cases is True
    assert actor.can_manage_organization_cases is True
    assert actor.is_org_scoped is True


@pytest.mark.parametrize(
    ("organization_id", "role", "email"),
    [
        ("", "unknown", "actor@example.com"),
        ("org-1", "donor", "actor@example.com"),
        ("", "coordinador", "coordinador@example.com"),
        ("", "donor", ""),
    ],
)
def test_rejects_invalid_signed_actor_semantics(monkeypatch, organization_id, role, email):
    with pytest.raises(HTTPException) as error:
        signed_context(
            monkeypatch,
            organization_id=organization_id,
            role=role,
            email=email,
        )

    assert error.value.status_code == 401


def test_rejects_expired_signature(monkeypatch):
    with pytest.raises(HTTPException) as error:
        signed_context(monkeypatch, timestamp=int(time.time()) - 120)

    assert error.value.status_code == 401


def test_rejects_invalid_signature(monkeypatch):
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", SECRET)

    with pytest.raises(HTTPException) as error:
        require_verified_actor_org(
            x_actor_org_id="",
            x_actor_role="donor",
            x_actor_email="actor@example.com",
            x_actor_uid="firebase-uid-123",
            x_actor_ip_hash="a" * 64,
            x_actor_org_signature="invalid",
            x_actor_org_timestamp=str(int(time.time())),
        )

    assert error.value.status_code == 401


def test_case_organization_dependency_rejects_donor(monkeypatch):
    actor = signed_context(monkeypatch)

    with pytest.raises(HTTPException) as error:
        require_verified_case_organization_actor(actor)

    assert error.value.status_code == 403


def test_case_organization_dependency_accepts_scoped_coordinator(monkeypatch):
    actor = signed_context(
        monkeypatch,
        organization_id="org-1",
        role="coordinador",
        email="coordinador@example.com",
    )

    assert require_verified_case_organization_actor(actor) is actor


def test_case_organization_dependency_accepts_global_super_admin(monkeypatch):
    actor = signed_context(
        monkeypatch,
        role="super_admin",
        email="admin@example.com",
    )

    assert require_verified_case_organization_actor(actor) is actor
