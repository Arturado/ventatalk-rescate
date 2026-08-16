import time
import hashlib
import hmac

import pytest
from fastapi import HTTPException

from dependencies import (
    _actor_org_signature,
    _delegate_affirmation_signature,
    require_verified_actor_org,
    require_verified_case_organization_actor,
    require_verified_delegate_affirmation,
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
        x_actor_org_signature_v2=signature,
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
            x_actor_org_signature_v2=None,
            x_actor_org_timestamp=str(int(time.time())),
        )

    assert error.value.status_code == 401


def test_accepts_legacy_signature_during_protocol_transition(monkeypatch):
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", SECRET)
    signed_at = str(int(time.time()))
    signature = hmac.new(
        SECRET.encode(),
        f"org-1|admin|admin@example.com|{signed_at}".encode(),
        hashlib.sha256,
    ).hexdigest()

    actor = require_verified_actor_org(
        x_actor_org_id="org-1",
        x_actor_role="admin",
        x_actor_email="admin@example.com",
        x_actor_uid=None,
        x_actor_ip_hash=None,
        x_actor_org_signature=signature,
        x_actor_org_signature_v2=None,
        x_actor_org_timestamp=signed_at,
    )

    assert actor.uid == ""
    assert actor.ip_hash == ""


def test_rejects_invalid_v2_signature_instead_of_falling_back_to_legacy(monkeypatch):
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", SECRET)
    signed_at = str(int(time.time()))
    legacy_signature = hmac.new(
        SECRET.encode(),
        f"org-1|admin|admin@example.com|{signed_at}".encode(),
        hashlib.sha256,
    ).hexdigest()

    with pytest.raises(HTTPException) as error:
        require_verified_actor_org(
            x_actor_org_id="org-1",
            x_actor_role="admin",
            x_actor_email="admin@example.com",
            x_actor_uid="admin-uid",
            x_actor_ip_hash="a" * 64,
            x_actor_org_signature=legacy_signature,
            x_actor_org_signature_v2="invalid",
            x_actor_org_timestamp=signed_at,
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


def signed_delegate_affirmation(
    monkeypatch,
    *,
    operation="assign_responsible",
    case_id="42",
    organization_id="org-1",
    target_uid="delegate-uid-123",
    target_email="delegado@example.com",
    timestamp=None,
):
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", SECRET)
    signed_at = str(timestamp or int(time.time()))
    normalized_email = target_email.strip().lower()
    signature = _delegate_affirmation_signature(
        operation, case_id, organization_id, target_uid, normalized_email, signed_at, SECRET,
    )
    return require_verified_delegate_affirmation(
        x_actor_delegate_operation=operation,
        x_actor_delegate_case_id=case_id,
        x_actor_delegate_org_id=organization_id,
        x_actor_delegate_target_uid=target_uid,
        x_actor_delegate_target_email=target_email,
        x_actor_delegate_timestamp=signed_at,
        x_actor_delegate_signature=signature,
    )


def test_accepts_valid_delegate_affirmation(monkeypatch):
    affirmation = signed_delegate_affirmation(monkeypatch, target_email="DELEGADO@EXAMPLE.COM")

    assert affirmation.operation == "assign_responsible"
    assert affirmation.case_id == 42
    assert affirmation.organization_id == "org-1"
    assert affirmation.target_uid == "delegate-uid-123"
    assert affirmation.target_email == "delegado@example.com"


def test_accepts_revoke_responsible_operation(monkeypatch):
    affirmation = signed_delegate_affirmation(monkeypatch, operation="revoke_responsible")

    assert affirmation.operation == "revoke_responsible"


def test_rejects_expired_delegate_affirmation(monkeypatch):
    with pytest.raises(HTTPException) as error:
        signed_delegate_affirmation(monkeypatch, timestamp=int(time.time()) - 120)

    assert error.value.status_code == 401


def test_rejects_invalid_delegate_affirmation_signature(monkeypatch):
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", SECRET)

    with pytest.raises(HTTPException) as error:
        require_verified_delegate_affirmation(
            x_actor_delegate_operation="assign_responsible",
            x_actor_delegate_case_id="42",
            x_actor_delegate_org_id="org-1",
            x_actor_delegate_target_uid="delegate-uid-123",
            x_actor_delegate_target_email="delegado@example.com",
            x_actor_delegate_timestamp=str(int(time.time())),
            x_actor_delegate_signature="invalid",
        )

    assert error.value.status_code == 401


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation", "borrar_todo"),
        ("case_id", "not-a-number"),
        ("organization_id", ""),
        ("target_uid", ""),
        ("target_email", "not-an-email"),
    ],
)
def test_rejects_tampered_delegate_affirmation_fields(monkeypatch, field, value):
    kwargs = {
        "operation": "assign_responsible",
        "case_id": "42",
        "organization_id": "org-1",
        "target_uid": "delegate-uid-123",
        "target_email": "delegado@example.com",
    }
    monkeypatch.setenv("ACTOR_SIGNING_SECRET", SECRET)
    signed_at = str(int(time.time()))
    # Sign the original (untampered) payload, then swap one field in the
    # request itself — proves the signature doesn't cover a manipulated
    # value, not just that mismatched signatures are rejected outright.
    signature = _delegate_affirmation_signature(
        kwargs["operation"], kwargs["case_id"], kwargs["organization_id"],
        kwargs["target_uid"], kwargs["target_email"], signed_at, SECRET,
    )
    kwargs[field] = value

    with pytest.raises(HTTPException) as error:
        require_verified_delegate_affirmation(
            x_actor_delegate_operation=kwargs["operation"],
            x_actor_delegate_case_id=kwargs["case_id"],
            x_actor_delegate_org_id=kwargs["organization_id"],
            x_actor_delegate_target_uid=kwargs["target_uid"],
            x_actor_delegate_target_email=kwargs["target_email"],
            x_actor_delegate_timestamp=signed_at,
            x_actor_delegate_signature=signature,
        )

    assert error.value.status_code == 401
