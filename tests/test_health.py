from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_returns_404():
    response = client.get("/does-not-exist")
    assert response.status_code == 404


def test_post_health_returns_405():
    response = client.post("/health")
    assert response.status_code == 405
