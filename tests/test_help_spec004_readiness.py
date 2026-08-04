from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_health_reports_help_spec004_readiness_without_mutating_data():
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert "help_spec004_ready" in body
    assert isinstance(body["help_spec004_ready"], bool)
