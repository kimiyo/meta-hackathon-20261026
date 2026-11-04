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


def test_per_author_budget_isolation(conn):
    a1 = authmod.register(conn, login_id="user1", pen_name="p",
                          password="pw1234")["id"]
    a2 = authmod.register(conn, login_id="user2", pen_name="p",
                          password="pw1234")["id"]
    budgetmod.record_llm_call(
        conn, _rec(input_tokens=900, output_tokens=100, author_id=a1))
    budgetmod.record_llm_call(
        conn, _rec(input_tokens=10, output_tokens=10, author_id=a2))
    assert budgetmod.daily_usage(conn, author_id=a1)["total_tokens"] == 1000
    assert budgetmod.daily_usage(conn, author_id=a2)["total_tokens"] == 20
    with pytest.raises(BudgetExceeded):
        budgetmod.check_budget(conn, 1000, author_id=a1)
    s = budgetmod.check_budget(conn, 1000, author_id=a2)
    assert s["remaining"] == 980


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


def test_ops_budget_counts_requesting_author_only(api_client, tmp_path):
    seed = dbmod.connect(tmp_path / "api.db")
    tester = seed.execute(
        "SELECT id FROM authors WHERE login_id='tester'").fetchone()["id"]
    other = authmod.register(seed, login_id="other", pen_name="o",
                              password="pw1234")["id"]
    try:
        for aid, tokens in ((tester, 100), (other, 5000)):
            budgetmod.record_llm_call(
                seed, _rec(input_tokens=tokens, output_tokens=0,
                           author_id=aid))
        seed.commit()
    finally:
        seed.close()
    body = api_client.get("/api/v1/ops/budget").json()
    assert body["input_tokens"] == 100
    assert body["total_tokens"] == 100


def test_entities_api(api_client):
    r = api_client.get("/api/v1/entities")
    assert r.status_code == 200
    assert r.json()["entities"][0]["entity_id"] == "ch_0001"
    r = api_client.get("/api/v1/entities?q=서연")
    assert len(r.json()["entities"]) == 1
    # 기준 회차: 첫 등장(EP1) 이전으로는 시드 설정이 보이지 않는다.
    r = api_client.get("/api/v1/entities?as_of_episode=0")
    assert r.json()["entities"] == []
    r = api_client.get("/api/v1/entities?as_of_episode=1")
    assert [e["entity_id"] for e in r.json()["entities"]] == ["ch_0001"]
    r = api_client.get("/api/v1/entities?content=서점")
    assert r.json()["entities"] == []  # 시드에는 변경 이력이 없다
    r = api_client.get("/api/v1/entities/ch_0001/state?as_of_episode=1")
    assert r.json()["attributes"] == {}
    # 회차 미지정 시 최신 회차 기준(회차 없으면 0화).
    r = api_client.get("/api/v1/entities/ch_0001/state")
    assert r.status_code == 200
    assert r.json()["as_of_episode"] == 0
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
        assert "이미 시작" in r.json()["detail"]
        r = api_client.get(f"/api/v1/episodes/1?work={wid}")
        assert r.json()["revisions"][0]["length"] == 5000
        assert r.json()["issues"] == {"open": 0, "total": 0}
        r = api_client.get(f"/api/v1/episodes/1/manuscript?work={wid}")
        assert len(r.json()["text"]) == 5000
        r = api_client.get(f"/api/v1/episodes/9?work={wid}")
        assert r.status_code == 404
    finally:
        del app.dependency_overrides[get_meta]


def test_duplicate_start_message_en(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_planning import FakeMeta as PlanFake

    wid = api_client.post("/api/v1/works", json={
        "title": "EN", "market": "US",
        "writing_locale": "en-US"}).json()["id"]
    app.dependency_overrides[get_meta] = lambda: PlanFake(2)
    did = api_client.post(f"/api/v1/works/{wid}/designs",
                          json={"keywords": "k", "total_episodes": 2}).json()["design_id"]
    app.dependency_overrides[get_meta] = lambda: DraftFake(["word " * 6000])
    try:
        r = api_client.post(f"/api/v1/episodes/1/start?work={wid}",
                            json={"design_id": did})
        assert r.status_code == 200, r.text
        r = api_client.post(f"/api/v1/episodes/1/start?work={wid}",
                            json={"design_id": did})
        assert r.status_code == 409
        assert r.json()["detail"] == "episode 1 is already started (WRITING)"
    finally:
        del app.dependency_overrides[get_meta]


def test_design_episodes_shows_jobs_across_designs(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_planning import FakeMeta as PlanFake

    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    app.dependency_overrides[get_meta] = lambda: PlanFake(2)
    did_a = api_client.post(
        f"/api/v1/works/{wid}/designs",
        json={"keywords": "k", "total_episodes": 2}).json()["design_id"]
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 5000])
    try:
        r = api_client.post(f"/api/v1/episodes/1/start?work={wid}",
                            json={"design_id": did_a})
        assert r.status_code == 200, r.text
        app.dependency_overrides[get_meta] = lambda: PlanFake(2)
        did_b = api_client.post(
            f"/api/v1/works/{wid}/designs",
            json={"keywords": "k2", "total_episodes": 2}).json()["design_id"]
        assert did_b != did_a
        eps = api_client.get(f"/api/v1/designs/{did_b}/episodes").json()["episodes"]
        ep1 = next(e for e in eps if e["episode_no"] == 1)
        assert ep1["job"] is not None
        assert ep1["job"]["state"] == "WRITING"
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
            "reply": "모순 발견",
            "issues": [{"severity": "major", "title": "모순",
                        "manuscript_quote": "가가가"}],
        }])
        r = api_client.post(f"/api/v1/episodes/1/consistency/chat?work={wid}",
                            json={"message": "검사해줘"})
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


def test_proposals_return_saved_items_and_events_after_save(api_client):
    from app.routes.works import get_meta
    from app.main import app

    wid = _started_in_review(api_client)
    payload = {"items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                          "operation": "create", "attribute_path": "직업",
                          "old_value": "", "new_value": "서점주인",
                          "story_time": "", "evidence_quote": "가" * 100}],
               "mentions": [],
               "events": [{"summary": "사건A", "story_time": "",
                           "cause": "", "consequence": "",
                           "evidence_quote": "가" * 100, "participants": []}]}
    app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([payload])
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200
        prop = api_client.get(
            f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        # 저장 후에도 저장본 내용을 확인할 수 있어야 한다.
        assert prop["proposal_set"] is None
        assert prop["saved_set"] is not None
        assert [i["candidate_name"] for i in prop["saved_items"]] == ["민준"]
        assert [e["summary"] for e in prop["saved_events"]] == ["사건A"]
    finally:
        del app.dependency_overrides[get_meta]


def test_reset_episode_memory_wipes_episode_and_keeps_others(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_drafting import FakeMeta as DraftFake
    from tests.test_planning import FakeMeta as PlanFake

    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    plan_meta = PlanFake(2)
    app.dependency_overrides[get_meta] = lambda: plan_meta
    did = api_client.post(f"/api/v1/works/{wid}/designs",
                          json={"keywords": "k", "total_episodes": 2}).json()["design_id"]
    draft_meta = DraftFake(["가" * 5000, "나" * 5000])
    app.dependency_overrides[get_meta] = lambda: draft_meta
    api_client.post(f"/api/v1/episodes/1/start?work={wid}", json={"design_id": did})
    # WRITING에서는 초기화 불가.
    r = api_client.post(f"/api/v1/episodes/1/entities/reset?work={wid}")
    assert r.status_code == 400
    api_client.post(f"/api/v1/episodes/1/submit-review?work={wid}")
    r = api_client.post(f"/api/v1/episodes/2/start?work={wid}",
                        json={"design_id": did})
    assert r.status_code == 200, r.text
    r = api_client.post(f"/api/v1/episodes/2/submit-review?work={wid}")
    assert r.status_code == 200, r.text

    def _item(name, value, quote, kind="attribute", op="create", path="직업"):
        return {"entity_ref": f"new:{name}", "candidate_name": name,
                "operation": op, "kind": kind, "attribute_path": path,
                "old_value": "", "new_value": value,
                "story_time": "", "evidence_quote": quote}

    def _ev(summary, quote):
        return {"summary": summary, "story_time": "", "cause": "",
                "consequence": "", "evidence_quote": quote, "participants": []}

    p1 = {"items": [_item("민준", "서점주인", "가" * 100),
                    {"entity_ref": "new:민준", "candidate_name": "민준",
                     "operation": "create", "kind": "relation",
                     "attribute_path": "상사", "old_value": "",
                     "new_value": {"object": "인사팀장", "detail": ""},
                     "story_time": "", "evidence_quote": "가" * 100}],
          "mentions": [{"entity_ref": "new:장부지기", "quote": "가" * 100}],
          "events": [_ev("사건A", "가" * 100)]}
    p2 = {"items": [_item("지훈", "장부지기", "나" * 100)],
          "mentions": [{"entity_ref": "new:오탐", "quote": "나" * 100}],
          "events": []}
    p3 = {"items": [_item("민준", "서점주인", "가" * 100)],
          "mentions": [], "events": []}
    meta = EntityVerifyMeta([p1, p1, p2, p3])
    app.dependency_overrides[get_meta] = lambda: meta
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        # reset은 pending 임시안까지 모두 지운다.
        r = api_client.post(f"/api/v1/episodes/1/entities/reset?work={wid}")
        assert r.status_code == 200, r.text
        prop = api_client.get(
            f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        assert prop["proposal_set"] is None
        # 이 화 출처 별명 후보는 지워진다.
        assert api_client.get(
            f"/api/v1/works/{wid}/aliases").json()["candidates"] == []
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        cands = api_client.get(
            f"/api/v1/works/{wid}/aliases").json()["candidates"]
        assert {c["alias"] for c in cands} == {"장부지기"}
        r = api_client.post(
            f"/api/v1/aliases/{cands[0]['alias_id']}/resolve",
            json={"entity_id": None, "basis": "작가 신규 등록"})
        assert r.json()["status"] == "confirmed"
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200

        assert api_client.post(
            f"/api/v1/episodes/2/entities/extract?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/2/entities/save?work={wid}").status_code == 200
        assert len(api_client.get("/api/v1/entities?q=민준").json()["entities"]) == 1
        assert len(api_client.get("/api/v1/entities?q=지훈").json()["entities"]) == 1
        assert len(api_client.get(
            "/api/v1/entities?q=인사팀장").json()["entities"]) == 1

        r = api_client.post(f"/api/v1/episodes/1/entities/reset?work={wid}")
        assert r.status_code == 200, r.text
        assert r.json()["changes"] == 2  # attribute + relation
        assert r.json()["events"] == 1
        assert r.json()["aliases"] == 1  # 확정된 장부지기 연결
        prop = api_client.get(
            f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        assert prop["proposal_set"] is None
        assert prop["saved_set"] is None
        assert prop["saved_items"] == []
        assert prop["saved_events"] == []
        assert prop["committed_summary"] == {"changes": 0, "events": 0,
                                             "aliases": 0}
        # 1화에서만 쓰던 설정은 사라지고 2화 설정은 남는다.
        assert api_client.get("/api/v1/entities?q=민준").json()["entities"] == []
        assert api_client.get(
            "/api/v1/entities?q=인사팀장").json()["entities"] == []
        assert api_client.get(
            "/api/v1/entities?q=장부지기").json()["entities"] == []
        assert len(api_client.get("/api/v1/entities?q=지훈").json()["entities"]) == 1
        # 2화 출처 별명 후보는 남는다.
        cands = api_client.get(f"/api/v1/works/{wid}/aliases").json()["candidates"]
        assert {c["alias"] for c in cands} == {"오탐"}
        # 초기화 후 새로 추출·저장이 된다.
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200
        assert len(api_client.get("/api/v1/entities?q=민준").json()["entities"]) == 1
    finally:
        del app.dependency_overrides[get_meta]


def test_event_participant_remove_prevents_entity_creation(api_client):
    from app.routes.works import get_meta
    from app.main import app

    wid = _started_in_review(api_client)
    payload = {"items": [],
               "mentions": [],
               "events": [{"summary": "사건A", "story_time": "",
                           "cause": "", "consequence": "",
                           "evidence_quote": "가" * 100,
                           "participants": [
                               {"entity_ref": "new:민준", "role": "주체"},
                               {"entity_ref": "new:지훈", "role": "방관"}]}]}
    meta = EntityVerifyMeta([payload])
    app.dependency_overrides[get_meta] = lambda: meta
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        prop = api_client.get(
            f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        assert [p["candidate_name"]
                for p in prop["events"][0]["participants"]] == ["민준", "지훈"]
        r = api_client.delete(
            f"/api/v1/episodes/1/entity-events/0/participants/1?work={wid}")
        assert r.status_code == 200, r.text
        prop = api_client.get(
            f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
        assert [p["candidate_name"]
                for p in prop["events"][0]["participants"]] == ["민준"]
        r = api_client.delete(
            f"/api/v1/episodes/1/entity-events/0/participants/9?work={wid}")
        assert r.status_code == 400
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200
        assert len(api_client.get(
            "/api/v1/entities?q=민준").json()["entities"]) == 1
        assert api_client.get(
            "/api/v1/entities?q=지훈").json()["entities"] == []
        r = api_client.delete(
            f"/api/v1/episodes/1/entity-events/0/participants/0?work={wid}")
        assert r.status_code == 404
    finally:
        del app.dependency_overrides[get_meta]


def test_save_auto_links_same_name_candidate(api_client):
    from app.routes.works import get_meta
    from app.main import app

    wid = _started_in_review(api_client)
    payload = {"items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                          "operation": "create", "attribute_path": "직업",
                          "old_value": "", "new_value": "서점주인",
                          "story_time": "", "evidence_quote": "가" * 100}],
               "mentions": [{"entity_ref": "new:민준", "quote": "가" * 100}],
               "events": []}
    meta = EntityVerifyMeta([payload])
    app.dependency_overrides[get_meta] = lambda: meta
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        cands = api_client.get(
            f"/api/v1/works/{wid}/aliases").json()["candidates"]
        assert [c["alias"] for c in cands] == ["민준"]
        r = api_client.post(f"/api/v1/episodes/1/entities/save?work={wid}")
        assert r.status_code == 200, r.text
        assert r.json()["commit"]["aliases_linked"] == 1
        assert api_client.get(
            f"/api/v1/works/{wid}/aliases").json()["candidates"] == []
        eid = r.json()["commit"]["entities"][0]
        hist = api_client.get(
            f"/api/v1/entities/{eid}/history").json()["history"]
        assert [(h["old_value"], h["new_value"]) for h in hist] == [
            ("", "서점주인")]
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


def test_entities_any_search_returns_snippet(api_client):
    from app.routes.works import get_meta
    from app.main import app

    wid = _started_in_review(api_client)
    payload = {"items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                          "operation": "create", "attribute_path": "직업",
                          "old_value": "", "new_value": "서점주인",
                          "story_time": "", "evidence_quote": "가" * 100}],
               "mentions": [], "events": []}
    app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([payload])
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200
        r = api_client.get("/api/v1/entities?any=서점주인").json()["entities"]
        assert len(r) == 1
        assert "서점주인" in r[0]["match_snippet"]
        r = api_client.get("/api/v1/entities?any=민준").json()["entities"]
        assert len(r) == 1
        assert r[0]["match_snippet"] == ""
        assert api_client.get(
            "/api/v1/entities?any=없는말").json()["entities"] == []
    finally:
        del app.dependency_overrides[get_meta]


def test_analyze_records_completed_analysis(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_consistency import IssueMeta

    wid = _started_in_review(api_client)
    app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([{
        "items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                   "operation": "create", "attribute_path": "직업",
                   "old_value": "", "new_value": "서점주인",
                   "story_time": "", "evidence_quote": "가" * 100}],
        "mentions": [], "events": []}])
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200
        app.dependency_overrides[get_meta] = lambda: IssueMeta([{
            "issues": [{"severity": "major", "title": "모순", "detail": "d",
                        "verdict": "conflict", "manuscript_quote": "가가가",
                        "evidence_episode": None, "evidence_quote": "",
                        "entity_id": ""}]}])
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


def test_latest_analysis_reports_comparable(api_client):
    wid = _started_in_review(api_client)
    r = api_client.get(
        f"/api/v1/episodes/1/consistency/analyses/latest?work={wid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["analysis"] is None
    assert body["comparable"] is False


def test_entity_proposals_falls_back_to_latest_saved(api_client, tmp_path):
    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    seed = dbmod.connect(tmp_path / "api.db")
    try:
        did = seed.execute(
            "INSERT INTO story_designs(work_id, version, scope_total_episodes,"
            " concept_json, sections_json, status)"
            " VALUES(?,1,2,'{}','[]','approved')", (wid,)).lastrowid
        rev_old = seed.execute(
            "INSERT INTO manuscript_revisions(work_id, episode_no, version,"
            " text, created_at) VALUES(?,1,1,'old','t')", (wid,)).lastrowid
        rev_new = seed.execute(
            "INSERT INTO manuscript_revisions(work_id, episode_no, version,"
            " text, created_at) VALUES(?,1,2,'new','t')", (wid,)).lastrowid
        seed.execute(
            "INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
            " manuscript_revision_id, attempt, updated_at)"
            " VALUES(?,1,?,'IN_REVIEW',?,0,'t')", (wid, did, rev_new))
        memory.create_entity(seed, entity_id="ch_0002", type_="character",
                             work_id=wid, name="서연", first_episode=1)
        sid = memory.store_proposal(
            seed, work_id=wid, episode_no=1, revision_id=rev_old,
            items=[{"entity_id": "ch_0002", "operation": "set",
                    "attribute_path": "나이", "new_value": 26,
                    "evidence_quote": "스물여섯"}])
        seed.execute("UPDATE change_proposals SET status='committed'"
                     " WHERE proposal_set_id=?", (sid,))
        seed.commit()
    finally:
        seed.close()
    body = api_client.get(
        f"/api/v1/episodes/1/entity-proposals?work={wid}").json()
    assert body["saved_set"] is None
    assert body["latest_saved_set"]["proposal_set_id"] == sid
    assert body["latest_saved_set"]["version"] == 1
    assert len(body["latest_saved_items"]) == 1


def test_consistency_reset_clears_issues(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_consistency import IssueMeta

    wid = _started_in_review(api_client)
    app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([{
        "items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                   "operation": "create", "attribute_path": "직업",
                   "old_value": "", "new_value": "서점주인",
                   "story_time": "", "evidence_quote": "가" * 100}],
        "mentions": [], "events": []}])
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200
        app.dependency_overrides[get_meta] = lambda: IssueMeta([
            {"issues": [{"severity": "major", "title": "모순A",
                         "detail": "d", "verdict": "conflict",
                         "manuscript_quote": "가가가",
                         "evidence_episode": None, "evidence_quote": "",
                         "entity_id": ""}]},
            {"issues": [{"severity": "major", "title": "모순B",
                         "detail": "d", "verdict": "conflict",
                         "manuscript_quote": "가가가",
                         "evidence_episode": None, "evidence_quote": "",
                         "entity_id": ""}]}])
        assert api_client.post(
            f"/api/v1/episodes/1/consistency/analyze?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/1/consistency/analyze?work={wid}").status_code == 200
        got = api_client.get(
            f"/api/v1/episodes/1/consistency/issues?work={wid}").json()["issues"]
        assert sum(1 for i in got if i["status"] == "open") >= 1
        fixed_id = next(i["issue_id"] for i in got if i["status"] == "open")
        assert api_client.patch(
            f"/api/v1/consistency/issues/{fixed_id}",
            json={"status": "fixed"}).status_code == 200
        r = api_client.post(
            f"/api/v1/episodes/1/consistency/reset?work={wid}")
        assert r.status_code == 200, r.text
        assert r.json()["issues"] == len(got)
        got = api_client.get(
            f"/api/v1/episodes/1/consistency/issues?work={wid}").json()["issues"]
        assert got == []
        assert api_client.get(
            f"/api/v1/episodes/1/consistency/analyses/latest?work={wid}"
        ).json()["analysis"] is None
    finally:
        del app.dependency_overrides[get_meta]


def test_manuscript_fix_flow(api_client):
    from app.routes.works import get_meta
    from app.main import app
    from tests.test_consistency import IssueMeta

    wid = _started_in_review(api_client)
    app.dependency_overrides[get_meta] = lambda: EntityVerifyMeta([{
        "items": [{"entity_ref": "new:민준", "candidate_name": "민준",
                   "operation": "create", "attribute_path": "직업",
                   "old_value": "", "new_value": "서점주인",
                   "story_time": "", "evidence_quote": "가" * 100}],
        "mentions": [], "events": []}])
    try:
        assert api_client.post(
            f"/api/v1/episodes/1/entities/extract?work={wid}").status_code == 200
        assert api_client.post(
            f"/api/v1/episodes/1/entities/save?work={wid}").status_code == 200
        app.dependency_overrides[get_meta] = lambda: IssueMeta([{
            "issues": [{"severity": "major", "title": "모순",
                        "detail": "d", "verdict": "conflict",
                        "manuscript_quote": "가가가",
                        "evidence_episode": None, "evidence_quote": "",
                        "entity_id": ""}]}])
        assert api_client.post(
            f"/api/v1/episodes/1/consistency/analyze?work={wid}").status_code == 200
        got = api_client.get(
            f"/api/v1/episodes/1/consistency/issues?work={wid}").json()["issues"]
        open_ids = [i["issue_id"] for i in got if i["status"] == "open"]
        assert open_ids
        doc = api_client.get(
            f"/api/v1/episodes/1/manuscript-doc?work={wid}").json()["document"]
        orig = doc["versions"][-1]["content"]
        app.dependency_overrides[get_meta] = lambda: IssueMeta([
            {"manuscript": orig + "\n추가 문장."}])
        r = api_client.post(
            f"/api/v1/episodes/1/manuscript-fix?work={wid}",
            json={"issue_ids": open_ids})
        assert r.status_code == 200, r.text
        assert sorted(r.json()["fixed_issue_ids"]) == sorted(open_ids)
        ep = api_client.get(
            f"/api/v1/episodes/1?work={wid}").json()
        assert ep["state"] == "WRITING"
        got = api_client.get(
            f"/api/v1/episodes/1/consistency/issues?work={wid}").json()["issues"]
        assert all(i["status"] != "open" for i in got)
    finally:
        del app.dependency_overrides[get_meta]



