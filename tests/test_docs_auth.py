import inspect

from fastapi.testclient import TestClient

import main


def test_docs_use_header_auth_without_api_key_in_urls(monkeypatch):
    monkeypatch.setenv("API_KEY", "docs-secret")
    client = TestClient(main.app)

    denied = client.get("/cowboy-bebop?api_key=docs-secret")
    allowed = client.get("/cowboy-bebop", headers={"X-API-Key": "docs-secret"})

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert "api_key=" not in allowed.text
    assert "docs-secret" not in allowed.text
    assert "/cowboy-bebop/openapi.json" not in allowed.text
    assert "spec:" in allowed.text
    assert "Query" not in str(inspect.signature(main.custom_swagger))
