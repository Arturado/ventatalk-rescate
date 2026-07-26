from fastapi.testclient import TestClient

from main import app


client = TestClient(app)


def test_firebase_hosting_origins_can_read_public_backend_endpoints():
    for origin in (
        "https://sos-ve-20fa3.web.app",
        "https://sos-ve-20fa3.firebaseapp.com",
    ):
        response = client.options(
            "/api/albergues/estado",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin


def test_unknown_origins_remain_disallowed():
    response = client.options(
        "/api/albergues/estado",
        headers={
            "Origin": "https://untrusted.example",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert "access-control-allow-origin" not in response.headers
