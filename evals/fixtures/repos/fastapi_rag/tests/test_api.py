from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_validation() -> None:
    response = client.post("/api/v1/query", json={"question": "?"})
    assert response.status_code == 422
