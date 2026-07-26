import pytest

from services.help_feature_flags import (
    HelpV2FeatureDisabledError,
    enabled_help_v2_organization_ids,
    is_help_v2_enabled_for_organization,
    require_help_v2_organization,
)


def test_feature_gate_fails_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("HELP_CASES_V2_ENABLED", raising=False)
    monkeypatch.delenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", raising=False)

    assert enabled_help_v2_organization_ids() == frozenset()
    assert is_help_v2_enabled_for_organization("org-1") is False
    with pytest.raises(HelpV2FeatureDisabledError):
        require_help_v2_organization("org-1")


def test_feature_gate_requires_global_enable_and_allowlisted_organization(monkeypatch):
    monkeypatch.setenv("HELP_CASES_V2_ENABLED", "true")
    monkeypatch.setenv(
        "HELP_CASES_V2_PILOT_ORGANIZATION_IDS",
        " org-1,ORG-2,org-1, ,",
    )

    assert enabled_help_v2_organization_ids() == frozenset({"org-1", "org-2"})
    assert is_help_v2_enabled_for_organization("ORG-1") is True
    assert is_help_v2_enabled_for_organization("org-3") is False
    assert require_help_v2_organization("org-2") == "org-2"


@pytest.mark.parametrize("value", ["false", "0", "yes", "enabled", ""])
def test_feature_gate_only_accepts_explicit_true(monkeypatch, value):
    monkeypatch.setenv("HELP_CASES_V2_ENABLED", value)
    monkeypatch.setenv("HELP_CASES_V2_PILOT_ORGANIZATION_IDS", "org-1")

    assert is_help_v2_enabled_for_organization("org-1") is False
