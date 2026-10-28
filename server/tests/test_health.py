"""Phase 0: health endpoint + config validation (no AI calls)."""
import pytest
from fastapi.testclient import TestClient


def test_health_ok():
    from app.main import app  # noqa: PLC0415 (import after env ready)

    client = TestClient(app)
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["model"]  # model id present, key never present
    assert "MODEL_API_KEY" not in resp.text
    assert "api_key" not in resp.text.lower().replace("api key", "")


def test_config_refuses_boot_without_key(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "_parse_dotenv", lambda path: {})
    with pytest.raises(RuntimeError, match="MODEL_API_KEY"):
        config.load_settings(env={})
