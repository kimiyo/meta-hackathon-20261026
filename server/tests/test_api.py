"""Phase 2: budget recording/aggregation + memory API (mocked AI: none)."""
import pytest
from fastapi.testclient import TestClient

from app import budget as budgetmod
from app import auth as authmod
from app import db as dbmod
from app import memory
from app.errors import BudgetExceeded
from app.routes.deps import get_conn, get_current_author


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


def test_connect_usable_across_threads(tmp_path):
    """FastAPI runs sync deps/endpoints/teardown on different threadpool
    threads, so a request-scoped connection must survive cross-thread
    handoff (used sequentially, never concurrently)."""
    import threading

    c = dbmod.connect(tmp_path / "t.db")
    errors = []

    def use_and_close():
        try:
            c.execute("INSERT INTO works(title, logline, created_at)"
                      " VALUES(?,?,?)", ("t", "", "now"))
            c.commit()
            row = c.execute("SELECT COUNT(*) c FROM works").fetchone()
            assert row["c"] == 1
        except Exception as e:  # noqa: BLE001 - surfaced below
            errors.append(e)
        finally:
            c.close()

    t = threading.Thread(target=use_and_close)
    t.start()
    t.join()
    assert not errors


def test_patch_work_api(api_client):
    wid = api_client.post("/api/v1/works", json={"title": "원제"}).json()["id"]
    r = api_client.patch(f"/api/v1/works/{wid}", json={"title": "바뀐제목"})
    assert r.status_code == 200
    assert r.json()["title"] == "바뀐제목"
    r = api_client.get(f"/api/v1/works/{wid}")
    assert r.json()["title"] == "바뀐제목"
    r = api_client.patch(f"/api/v1/works/{wid}", json={"title": "  "})
    assert r.status_code == 400
    r = api_client.patch(f"/api/v1/works/{wid}", json={})
    assert r.status_code == 400
    r = api_client.patch("/api/v1/works/999", json={"title": "x"})
    assert r.status_code == 404


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
    _author = authmod.public_author(authmod.register(
        seed, login_id="tester", pen_name="테스터", password="pw1234"))
    memory.create_entity(seed, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: _author
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
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 5000])
    try:
        r = api_client.post("/api/v1/episodes/1/start",
                            json={"design_id": did})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "WRITING"
        r = api_client.post("/api/v1/episodes/1/start",
                            json={"design_id": did})
        assert r.status_code == 409
        r = api_client.get("/api/v1/episodes/1")
        assert r.json()["revisions"][0]["length"] == 5000
        assert r.json()["issues"] == {"open": 0, "total": 0}
        r = api_client.get("/api/v1/episodes/1/manuscript")
        assert len(r.json()["text"]) == 5000
        r = api_client.get("/api/v1/episodes/9")
        assert r.status_code == 404
    finally:
        del app.dependency_overrides[get_meta]


def test_review_flow_api(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_planning import FakeMeta as PlanFake

    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    app.dependency_overrides[get_meta] = lambda: PlanFake(1)
    did = api_client.post(f"/api/v1/works/{wid}/designs",
                          json={"keywords": "k", "total_episodes": 1}).json()["design_id"]
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 5000])
    try:
        api_client.post("/api/v1/episodes/1/start", json={"design_id": did})
        r = api_client.post("/api/v1/episodes/1/back-to-writing",
                            json={"reason": "x"})
        assert r.status_code == 400
        r = api_client.post("/api/v1/episodes/1/submit-review")
        assert r.json()["state"] == "IN_REVIEW"
        r = api_client.post("/api/v1/episodes/1/back-to-writing",
                            json={"reason": "다듬기"})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "WRITING"
        r = api_client.get("/api/v1/episodes/1")
        assert r.json()["return_reason"] == "다듬기"
        r = api_client.post("/api/v1/episodes/1/submit-review")
        assert r.json()["state"] == "IN_REVIEW"
        r = api_client.post("/api/v1/episodes/9/submit-review")
        assert r.status_code == 404
    finally:
        del app.dependency_overrides[get_meta]


def test_publish_and_ops_api(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_consistency import IssueMeta

    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    from tests.test_planning import FakeMeta as PlanFake
    app.dependency_overrides[get_meta] = lambda: PlanFake(1)
    did = api_client.post(f"/api/v1/works/{wid}/designs",
                          json={"keywords": "k", "total_episodes": 1}).json()["design_id"]
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 5000])
    try:
        api_client.post("/api/v1/episodes/1/start", json={"design_id": did})
        api_client.post("/api/v1/episodes/1/submit-review")
        app.dependency_overrides[get_meta] = lambda: IssueMeta([{
            "issues": [{"severity": "major", "title": "모순",
                        "manuscript_quote": "가가가"}],
        }])
        r = api_client.post("/api/v1/episodes/1/consistency/analyze")
        assert r.status_code == 200, r.text
        assert len(r.json()["issues"]) == 1
        r = api_client.post("/api/v1/episodes/1/request-publish")
        assert r.status_code == 400  # open issue blocks publish
        issues = api_client.get("/api/v1/episodes/1/consistency/issues").json()["issues"]
        r = api_client.patch(f"/api/v1/consistency/issues/{issues[0]['issue_id']}",
                             json={"status": "resolved"})
        assert r.json()["status"] == "resolved"
        r = api_client.post("/api/v1/episodes/1/request-publish")
        assert r.status_code == 400  # no extraction yet
        app.dependency_overrides[get_meta] = lambda: IssueMeta([{
            "items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                       "operation": "create", "attribute_path": "직업",
                       "old_value": "", "new_value": "서점주인",
                       "story_time": "", "evidence_quote": "가" * 100}],
            "mentions": [], "events": []}])
        r = api_client.post("/api/v1/episodes/1/entities/extract")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 1
        r = api_client.get("/api/v1/episodes/1/entity-proposals")
        assert r.json()["fresh"] is True
        assert r.json()["saved_set"] is None
        assert "events" in r.json()
        r = api_client.post("/api/v1/episodes/1/entities/save")
        assert r.status_code == 200, r.text
        assert r.json()["commit"]["applied"] == 1
        r = api_client.post("/api/v1/episodes/1/entities/save")
        assert r.json().get("already") is True  # idempotent re-save
        r = api_client.get("/api/v1/entities?q=민준")
        assert len(r.json()["entities"]) == 1
        r = api_client.get("/api/v1/episodes/1/entity-proposals")
        assert r.json()["saved_set"] is not None
        r = api_client.post("/api/v1/episodes/1/request-publish")
        assert r.json()["state"] == "PUBLISHING"
        assert r.json()["commit"].get("already_saved") is True
        r = api_client.get(f"/api/v1/designs/{did}/episodes")
        assert r.json()["episodes"][0]["job"]["state"] == "PUBLISHING"
        r = api_client.get(f"/api/v1/designs/{did}/publishing")
        assert len(r.json()["episodes"]) == 1
        r = api_client.get("/api/v1/ops/budget")
        assert r.json()["budget"] > 0
        r = api_client.post("/api/v1/admin/reset-demo")
        assert r.status_code == 403  # no token
    finally:
        del app.dependency_overrides[get_meta]
