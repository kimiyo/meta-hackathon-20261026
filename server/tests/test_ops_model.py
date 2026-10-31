"""Model 조회/전환 API (실 호출 없이 검증 모킹)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from app.main import app  # noqa: PLC0415 (import after env ready)

    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_admin(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)


@pytest.fixture()
def restore_settings():
    from app import main as mainmod

    before = mainmod.settings
    yield
    mainmod.settings = before


def test_get_model(client):
    resp = client.get("/api/v1/ops/model")
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] and body["mode"] and body["base_url"]
    assert body["persistent"] is False
    assert {p["id"] for p in body["presets"]} >= {
        "muse-wonder-1.0", "muse-spark-1.3"}
    assert "MODEL_API_KEY" not in resp.text
    assert "api_key" not in resp.text.lower()


def test_post_invalid_id_keeps_old(client, restore_settings):
    from app import main as mainmod

    before = mainmod.settings.model_text_id
    resp = client.post("/api/v1/ops/model", json={"model": "bad id!!"})
    assert resp.status_code == 400
    assert mainmod.settings.model_text_id == before


def test_post_switch_ok_applies_to_next_call(client, restore_settings,
                                             monkeypatch):
    from app import main as mainmod
    from app.routes import ops as opsmod

    monkeypatch.setattr(opsmod, "_verify_model", lambda model_id: None)
    resp = client.post("/api/v1/ops/model", json={"model": "muse-spark-9.9"})
    assert resp.status_code == 200
    assert resp.json()["model"] == "muse-spark-9.9"
    assert mainmod.settings.model_text_id == "muse-spark-9.9"
    assert mainmod.get_meta_client().model_id == "muse-spark-9.9"


def test_post_verify_fail_keeps_old(client, restore_settings, monkeypatch):
    from fastapi import HTTPException

    from app import main as mainmod
    from app.routes import ops as opsmod

    def boom(model_id):
        raise HTTPException(400, "model verify failed: nope")

    monkeypatch.setattr(opsmod, "_verify_model", boom)
    before = mainmod.settings.model_text_id
    resp = client.post("/api/v1/ops/model", json={"model": "muse-nope-0"})
    assert resp.status_code == 400
    assert mainmod.settings.model_text_id == before


def test_post_admin_token_enforced_when_set(client, restore_settings,
                                            monkeypatch):
    from app.routes import ops as opsmod

    monkeypatch.setattr(opsmod, "_verify_model", lambda model_id: None)
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    body = {"model": "muse-spark-1.3"}
    assert client.post("/api/v1/ops/model", json=body).status_code == 403
    assert client.post("/api/v1/ops/model", json=body,
                       headers={"X-Admin-Token": "wrong"}).status_code == 403
    resp = client.post("/api/v1/ops/model", json=body,
                       headers={"X-Admin-Token": "s3cret"})
    assert resp.status_code == 200
