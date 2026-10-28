"""Phase 2: budget recording/aggregation + memory API (mocked AI: none)."""
import pytest
from fastapi.testclient import TestClient

from app import budget as budgetmod
from app import db as dbmod
from app import memory
from app.errors import BudgetExceeded
from app.routes.deps import get_conn


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    yield c
    c.close()


def _rec(**kw):
    base = {"bundle": "drafting", "episode_no": 1, "model": "m",
            "input_tokens": 100, "output_tokens": 200, "latency_ms": 5,
            "ok": 1, "error": ""}
    base.update(kw)
    return base


def test_record_and_daily_usage(conn):
    budgetmod.record_llm_call(conn, _rec())
    budgetmod.record_llm_call(conn, _rec(input_tokens=50, output_tokens=50))
    u = budgetmod.daily_usage(conn)
    assert (u["input_tokens"], u["output_tokens"], u["calls"]) == (150, 250, 2)


def test_check_budget_raises_when_exhausted(conn):
    budgetmod.record_llm_call(conn, _rec(input_tokens=900, output_tokens=100))
    with pytest.raises(BudgetExceeded):
        budgetmod.check_budget(conn, 1000)
    s = budgetmod.budget_status(conn, 2000)
    assert s["remaining"] == 1000


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"

    def _override():
        c = dbmod.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    from app.main import app
    app.dependency_overrides[get_conn] = _override
    seed = dbmod.connect(db_path)
    memory.create_entity(seed, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    seed.commit()
    seed.close()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_entities_api(api_client):
    r = api_client.get("/api/v1/entities")
    assert r.status_code == 200
    assert r.json()["entities"][0]["entity_id"] == "ch_0001"
    r = api_client.get("/api/v1/entities?q=서연")
    assert len(r.json()["entities"]) == 1
    r = api_client.get("/api/v1/entities/ch_0001/state?as_of_episode=1")
    assert r.json()["attributes"] == {}
    r = api_client.get("/api/v1/entities/nope/state?as_of_episode=1")
    assert r.status_code == 404
    r = api_client.get("/api/v1/entities/ch_0001/history")
    assert r.json()["history"] == []
    r = api_client.get("/api/v1/episodes/1/proposals")
    assert r.json()["items"] == []


def test_alias_resolve_api(api_client):
    r = api_client.post("/api/v1/aliases/999/resolve", json={"entity_id": "ch_0001"})
    assert r.status_code == 404


def test_works_designs_api(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_planning import FakeMeta

    app.dependency_overrides[get_meta] = lambda: FakeMeta(12)
    try:
        r = api_client.post("/api/v1/works", json={"title": "달빛 서점"})
        assert r.status_code == 200
        wid = r.json()["id"]
        r = api_client.get("/api/v1/works")
        assert len(r.json()["works"]) == 1
        r = api_client.post(f"/api/v1/works/{wid}/designs",
                            json={"keywords": "서점", "total_episodes": 12})
        assert r.status_code == 200, r.text
        did = r.json()["design_id"]
        assert r.json()["outline_count"] == 12
        r = api_client.get(f"/api/v1/designs/{did}")
        assert len(r.json()["outlines"]) == 12
        r = api_client.post(f"/api/v1/designs/{did}/approve")
        assert r.json()["status"] == "approved"
        r = api_client.patch(f"/api/v1/designs/{did}/outlines/3",
                             json={"title": "바뀐제목"})
        assert r.json()["updated"] is True
        r = api_client.get(f"/api/v1/designs/{did}")
        assert r.json()["outlines"][2]["title"] == "바뀐제목"
        r = api_client.post("/api/v1/works/999/designs",
                            json={"keywords": "x", "total_episodes": 5})
        assert r.status_code == 404
    finally:
        del app.dependency_overrides[get_meta]


def test_episodes_api(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_planning import FakeMeta as PlanFake

    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    app.dependency_overrides[get_meta] = lambda: PlanFake(2)
    did = api_client.post(f"/api/v1/works/{wid}/designs",
                          json={"keywords": "k", "total_episodes": 2}).json()["design_id"]
    api_client.post(f"/api/v1/designs/{did}/approve")
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 3000])
    try:
        r = api_client.post("/api/v1/episodes/1/advance",
                            json={"design_id": did})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "DRAFT"
        r = api_client.get("/api/v1/episodes/1")
        assert r.json()["revisions"][0]["length"] == 3000
        r = api_client.get("/api/v1/episodes/1/manuscript")
        assert len(r.json()["text"]) == 3000
        r = api_client.get("/api/v1/episodes/9")
        assert r.status_code == 404
    finally:
        del app.dependency_overrides[get_meta]
