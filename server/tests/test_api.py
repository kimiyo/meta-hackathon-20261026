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
    _wid = seed.execute(
        "INSERT INTO works(author_id, title, logline, created_at, updated_at)"
        " VALUES(?,'seed','', 't','t')", (_author["id"],)).lastrowid
    memory.create_entity(seed, entity_id="ch_0001", work_id=_wid,
                         type_="character", name="서연", first_episode=1)
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
        titles = [w["title"] for w in r.json()["works"]]
        assert "달빛 서점" in titles  # fixture also seeds one work
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
        r = api_client.post(f"/api/v1/episodes/1/start?work={wid}",
                            json={"design_id": did})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "WRITING"
        r = api_client.post(f"/api/v1/episodes/1/start?work={wid}",
                            json={"design_id": did})
        assert r.status_code == 409
        r = api_client.get(f"/api/v1/episodes/1?work={wid}")
        assert r.json()["revisions"][0]["length"] == 5000
        assert r.json()["issues"] == {"open": 0, "total": 0}
        r = api_client.get(f"/api/v1/episodes/1/manuscript?work={wid}")
        assert len(r.json()["text"]) == 5000
        r = api_client.get(f"/api/v1/episodes/9?work={wid}")
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
        api_client.post(f"/api/v1/episodes/1/start?work={wid}", json={"design_id": did})
        r = api_client.post(f"/api/v1/episodes/1/back-to-writing?work={wid}",
                            json={"reason": "x"})
        assert r.status_code == 400
        r = api_client.post(f"/api/v1/episodes/1/submit-review?work={wid}")
        assert r.json()["state"] == "IN_REVIEW"
        r = api_client.post(f"/api/v1/episodes/1/back-to-writing?work={wid}",
                            json={"reason": "다듬기"})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "WRITING"
        r = api_client.get(f"/api/v1/episodes/1?work={wid}")
        assert r.json()["return_reason"] == "다듬기"
        r = api_client.post(f"/api/v1/episodes/1/submit-review?work={wid}")
        assert r.json()["state"] == "IN_REVIEW"
        r = api_client.post(f"/api/v1/episodes/9/submit-review?work={wid}")
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
        api_client.post(f"/api/v1/episodes/1/start?work={wid}", json={"design_id": did})
        api_client.post(f"/api/v1/episodes/1/submit-review?work={wid}")
        app.dependency_overrides[get_meta] = lambda: IssueMeta([{
            "issues": [{"severity": "major", "title": "모순",
                        "manuscript_quote": "가가가"}],
        }])
        r = api_client.post(f"/api/v1/episodes/1/consistency/analyze?work={wid}")
        assert r.status_code == 200, r.text
        assert len(r.json()["issues"]) == 1
        r = api_client.post(f"/api/v1/episodes/1/request-publish?work={wid}")
        assert r.status_code == 400  # open issue blocks publish
        issues = api_client.get(f"/api/v1/episodes/1/consistency/issues?work={wid}").json()["issues"]
        r = api_client.patch(f"/api/v1/consistency/issues/{issues[0]['issue_id']}",
                             json={"status": "resolved"})
        assert r.json()["status"] == "fixed"  # legacy alias normalized
        r = api_client.post(f"/api/v1/episodes/1/request-publish?work={wid}")
        assert r.status_code == 400  # no extraction yet
        app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([{
            "items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                       "operation": "create", "attribute_path": "직업",
                       "old_value": "", "new_value": "서점주인",
                       "story_time": "", "evidence_quote": "가" * 100}],
            "mentions": [], "events": []}])
        r = api_client.post(f"/api/v1/episodes/1/entities/extract?work={wid}")
        assert r.status_code == 200, r.text
        assert len(r.json()["items"]) == 1
        r = api_client.get(f"/api/v1/episodes/1/entity-proposals?work={wid}")
        assert r.json()["fresh"] is True
        assert r.json()["saved_set"] is None
        assert "events" in r.json()
        r = api_client.post(f"/api/v1/episodes/1/entities/save?work={wid}")
        assert r.status_code == 200, r.text
        assert r.json()["commit"]["applied"] == 1
        r = api_client.post(f"/api/v1/episodes/1/entities/save?work={wid}")
        assert r.json().get("already") is True  # idempotent re-save
        r = api_client.get("/api/v1/entities?q=민준")
        assert len(r.json()["entities"]) == 1
        r = api_client.get(f"/api/v1/episodes/1/entity-proposals?work={wid}")
        assert r.json()["saved_set"] is not None
        r = api_client.post(f"/api/v1/episodes/1/request-publish?work={wid}")
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


class EntityVerifyMeta:
    """Entity payloads in order; verify calls get ok for every sent item."""

    def __init__(self, entity_payloads):
        self.payloads = list(entity_payloads)
        self.calls = []

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens,
                  work_id=None):
        import json as _json

        self.calls.append(user)
        if bundle == "verify":
            payload = _json.loads(user.split("\n", 1)[1])
            verdicts = [{"item_id": i["item_id"], "verdict": "ok",
                         "reason": "t", "counter_evidence_quote": ""}
                        for i in payload["proposal_items"]]
            return schema.model_validate({"verdicts": verdicts}).model_dump()
        return schema.model_validate(self.payloads.pop(0)).model_dump()


def test_entity_reextract_replaces_and_event_delete(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_planning import FakeMeta as PlanFake

    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    app.dependency_overrides[get_meta] = lambda: PlanFake(1)
    did = api_client.post(f"/api/v1/works/{wid}/designs",
                          json={"keywords": "k", "total_episodes": 1}).json()["design_id"]
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 5000])
    api_client.post(f"/api/v1/episodes/1/start?work={wid}", json={"design_id": did})
    api_client.post(f"/api/v1/episodes/1/submit-review?work={wid}")

    def _item(name, value):
        return {"entity_ref": f"new:{name}", "candidate_name": name,
                "operation": "create", "attribute_path": "직업",
                "old_value": "", "new_value": value,
                "story_time": "", "evidence_quote": "가" * 100}

    def _ev(summary):
        return {"summary": summary, "story_time": "", "cause": "",
                "consequence": "", "evidence_quote": "가" * 100,
                "participants": []}

    meta = EntityVerifyMeta([
        {"items": [_item("민준", "서점주인")], "mentions": [],
         "events": [_ev("사건A"), _ev("사건B")]},
        {"items": [_item("서연", "장부지기")], "mentions": [],
         "events": [_ev("사건C")]},
    ])
    app.dependency_overrides[get_meta] = lambda: meta
    try:
        r = api_client.post(f"/api/v1/episodes/1/entities/extract?work={wid}")
        assert r.status_code == 200, r.text
        prop = api_client.get(f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        assert len(prop["items"]) == 1
        assert [e["summary"] for e in prop["events"]] == ["사건A", "사건B"]

        r = api_client.delete(f"/api/v1/episodes/1/entity-events/0?work={wid}")
        assert r.status_code == 200, r.text
        assert r.json() == {"proposal_set_id": prop["proposal_set"]["proposal_set_id"],
                            "deleted": 0, "events": 1}
        prop = api_client.get(f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        assert [e["summary"] for e in prop["events"]] == ["사건B"]

        r = api_client.delete(f"/api/v1/episodes/1/entity-events/9?work={wid}")
        assert r.status_code == 400

        # 재추출은 대체: 이전 임시 항목·사건이 남지 않는다.
        r = api_client.post(f"/api/v1/episodes/1/entities/extract?work={wid}")
        assert r.status_code == 200, r.text
        prop = api_client.get(f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        assert len(prop["items"]) == 1
        assert prop["items"][0]["candidate_name"] == "서연"
        assert [e["summary"] for e in prop["events"]] == ["사건C"]
    finally:
        del app.dependency_overrides[get_meta]


def _started_in_review(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_planning import FakeMeta as PlanFake

    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    app.dependency_overrides[get_meta] = lambda: PlanFake(1)
    did = api_client.post(f"/api/v1/works/{wid}/designs",
                          json={"keywords": "k", "total_episodes": 1}).json()["design_id"]
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 5000])
    api_client.post(f"/api/v1/episodes/1/start?work={wid}", json={"design_id": did})
    api_client.post(f"/api/v1/episodes/1/submit-review?work={wid}")
    return wid


def test_extract_failure_keeps_pending_and_records_failed(api_client):
    from app.errors import LLMStepError
    from app.routes.works import get_meta
    from app.main import app

    wid = _started_in_review(api_client)
    first = {"items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                        "operation": "create", "attribute_path": "직업",
                        "old_value": "", "new_value": "서점주인",
                        "story_time": "", "evidence_quote": "가" * 100}],
             "mentions": [], "events": []}
    app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([first])
    try:
        r = api_client.post(f"/api/v1/episodes/1/entities/extract?work={wid}")
        assert r.status_code == 200, r.text
        before = api_client.get(f"/api/v1/episodes/1/entity-proposals?work={wid}").json()

        class RaiseMeta:
            def chat_json(self, **kw):
                raise LLMStepError("entity", 1, "timeout", "boom")

        app.dependency_overrides[get_meta] = lambda: RaiseMeta()
        r = api_client.post(f"/api/v1/episodes/1/entities/extract?work={wid}")
        assert r.status_code == 502
        after = api_client.get(f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        # 기존 임시안은 유지되고, 실패 실행이 별도로 기록된다.
        assert after["proposal_set"]["proposal_set_id"] == \
            before["proposal_set"]["proposal_set_id"]
        assert after["latest_run"]["status"] == "failed"
        assert "boom" in after["latest_run"]["error"]
    finally:
        del app.dependency_overrides[get_meta]


def test_alias_resolve_reject_flow(api_client):
    from app.routes.works import get_meta
    from app.main import app

    wid = _started_in_review(api_client)
    payload = {"items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                          "operation": "create", "attribute_path": "직업",
                          "old_value": "", "new_value": "서점주인",
                          "story_time": "", "evidence_quote": "가" * 100}],
               "mentions": [{"entity_ref": "new:장부지기", "quote": "가" * 100},
                            {"entity_ref": "new:오탐", "quote": "가" * 100}],
               "events": []}
    app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([payload])
    try:
        assert api_client.post(f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        cands = api_client.get(f"/api/v1/works/{wid}/aliases").json()["candidates"]
        assert {c["alias"] for c in cands} == {"장부지기", "오탐"}
        r = api_client.post(f"/api/v1/episodes/1/entities/save?work={wid}")
        assert r.status_code == 200, r.text
        minjun = [e for e in r.json()["commit"]["entities"]]
        assert len(minjun) == 1
        by_alias = {c["alias"]: c for c in cands}
        # 기존 인물에 연결.
        r = api_client.post(f"/api/v1/aliases/{by_alias['장부지기']['alias_id']}/resolve",
                            json={"entity_id": minjun[0], "basis": "같은 사람"})
        assert r.json()["status"] == "confirmed"
        assert r.json()["new_entity"] is False
        # 오탐 기각.
        r = api_client.post(f"/api/v1/aliases/{by_alias['오탐']['alias_id']}/reject",
                            json={"basis": "오탐"})
        assert r.json()["status"] == "rejected"
        assert api_client.get(f"/api/v1/works/{wid}/aliases").json()["candidates"] == []
    finally:
        del app.dependency_overrides[get_meta]


def test_analyze_records_completed_analysis(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_consistency import IssueMeta

    wid = _started_in_review(api_client)
    app.dependency_overrides[get_meta] = lambda: IssueMeta([{
        "issues": [{"severity": "major", "title": "모순", "detail": "d",
                    "verdict": "conflict", "manuscript_quote": "가가가",
                    "evidence_episode": None, "evidence_quote": "",
                    "entity_id": ""}]}])
    try:
        r = api_client.post(f"/api/v1/episodes/1/consistency/analyze?work={wid}")
        assert r.status_code == 200, r.text
        assert r.json()["issues"][0]["verdict"] == "conflict"
        assert r.json()["issues"][0]["manuscript_start"] is not None
        ana = api_client.get(
            f"/api/v1/episodes/1/consistency/analyses/latest?work={wid}").json()["analysis"]
        assert ana["status"] == "completed"
        assert ana["manuscript_revision_id"] is not None
    finally:
        del app.dependency_overrides[get_meta]
