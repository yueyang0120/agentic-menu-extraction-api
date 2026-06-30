from fastapi.testclient import TestClient

from app import app, parse_ios_language


client = TestClient(app)


def test_health_check_loads_without_openai_key():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["version"] == "1.0.3"


def test_openapi_schema_includes_core_endpoints():
    response = client.get("/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]
    assert "/auth/register" in paths
    assert "/analyze-menu" in paths


def test_ios_language_mapping_prefers_target_language_specificity():
    assert parse_ios_language("Traditional Chinese (繁體中文)") == "Traditional Chinese (繁體中文)"
    assert parse_ios_language("Japanese") == "Japanese (日本語)"
    assert parse_ios_language("Unknown") == "English"
