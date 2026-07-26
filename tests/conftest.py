import pytest


@pytest.fixture(autouse=True)
def enable_help_v2_for_test_organizations(monkeypatch):
    monkeypatch.setenv("HELP_CASES_V2_ENABLED", "true")
    monkeypatch.setenv(
        "HELP_CASES_V2_PILOT_ORGANIZATION_IDS",
        "org-1,org-2,org-demo,org-prueba,org-concurrency",
    )
