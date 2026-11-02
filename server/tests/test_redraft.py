"""Draft redraft: @ refs, @L line pins, full regen, request history."""
import pytest

from app import db as dbmod
from app import drafting, ideas, refs, state_machine
from app.errors import LLMStepError
from tests.test_drafting import FakeMeta
from tests.test_verify_pipeline import ScriptedMeta, _item


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal,"
              " constraints_json) VALUES(1,1,'T','G','[]')")
    c.commit()
    yield c
    c.close()


def test_parse_mentions():
    assert refs.parse_mentions("@서연 어때? @E001, @서연!") == ["서연", "E001"]
    assert refs.parse_mentions("@L12-15 고쳐줘") == ["L12-15"]
    assert refs.parse_mentions("멘션 없음") == []
    assert refs.parse_mentions("") == []


def test_parse_line_token():
    assert refs.parse_line_token("L7") == (7, 7)
    assert refs.parse_line_token("L12-15") == (12, 15)
    assert refs.parse_line_token("L15-12") == (12, 15)
    assert refs.parse_line_token("l3") == (3, 3)
    assert refs.parse_line_token("Luna") is None
    assert refs.parse_line_token("E001") is None
    assert refs.parse_line_token("") is None


def test_expand_line_refs_is_1_based(conn):
    base = "첫줄\n둘째줄\n셋째줄"
    quotes, rest = drafting.expand_line_refs(base, ["L2-3", "서연"])
    assert quotes == ["[원고 L2-3]\n둘째줄\n셋째줄"]
    assert rest == ["서연"]
    with pytest.raises(ValueError, match="out of range"):
        drafting.expand_line_refs(base, ["L9"])
    with pytest.raises(ValueError, match="out of range"):
        drafting.expand_line_refs(base, ["L0"])


def test_redraft_revision_mode_passes_base_and_instruction(conn):
    base = "옛날 옛적에\n" + "가" * 5000
    meta = FakeMeta(["새 원고 " + "나" * 5000])
    out = drafting.redraft_episode(meta, conn, design_id=1, work_id=1,
                                   episode_no=1, base_text=base,
                                   base_version=1, instruction="결말을 바꿔줘")
    assert out["version"] == 1 and out["base_version"] == 1
    sent = meta.calls[0]
    assert "옛날 옛적에" in sent  # manuscript_draft included
    assert "결말을 바꿔줘" in sent
    assert out["unknown_refs"] == []


def test_redraft_full_mode_omits_base(conn):
    meta = FakeMeta(["완전 새 원고 " + "나" * 5000])
    out = drafting.redraft_episode(meta, conn, design_id=1, work_id=1,
                                   episode_no=1, base_text="옛날 옛적에",
                                   base_version=1, instruction="", full=True)
    assert "manuscript_draft" not in meta.calls[0]
    assert "원고 전체를 새로 써라" in meta.calls[0]
    assert out["version"] == 1


def test_redraft_resolves_setting_ref(conn):
    ideas.create_setting(conn, 1, category="character", title="서연",
                         summary="주인공", detail="달빛 서점 주인")
    meta = FakeMeta(["고친 원고 " + "나" * 5000])
    out = drafting.redraft_episode(meta, conn, design_id=1, work_id=1,
                                   episode_no=1, base_text="원고 " + "가" * 5000,
                                   base_version=1,
                                   instruction="결말을 @서연 답게 고쳐줘")
    assert "달빛 서점 주인" in meta.calls[0]
    assert out["resolved_refs"] == ["서연"]
    assert out["unknown_refs"] == []


def test_redraft_unknown_ref_reported_not_fatal(conn):
    meta = FakeMeta(["고친 원고 " + "나" * 5000])
    out = drafting.redraft_episode(meta, conn, design_id=1, work_id=1,
                                   episode_no=1, base_text="원고 " + "가" * 5000,
                                   base_version=1, instruction="@없는것 고쳐줘")
    assert out["version"] == 1
    assert out["unknown_refs"] == ["없는것"]
    assert out["resolved_refs"] == []


def test_redraft_line_pin_quotes_target(conn):
    base = "첫줄\n둘째줄\n셋째줄"
    meta = FakeMeta(["고친 원고 " + "나" * 5000])
    out = drafting.redraft_episode(meta, conn, design_id=1, work_id=1,
                                   episode_no=1, base_text=base, base_version=1,
                                   instruction="@L2-3 다듬어줘")
    assert "[원고 L2-3]" in meta.calls[0] and "둘째줄" in meta.calls[0]
    assert out["line_quotes"] == 1


def test_redraft_rejects_bad_input_without_revision(conn):
    with pytest.raises(ValueError, match="must not be blank"):
        drafting.redraft_episode(FakeMeta(["x" * 5000]), conn, design_id=1,
                                 work_id=1, episode_no=1, base_text="b",
                                 base_version=1, instruction="  ")
    with pytest.raises(ValueError, match="out of range"):
        drafting.redraft_episode(FakeMeta(["x" * 5000]), conn, design_id=1,
                                 work_id=1, episode_no=1, base_text="a\nb",
                                 base_version=1, instruction="@L9 고쳐줘")
    with pytest.raises(ValueError, match="partial redraft only"):
        drafting.redraft_episode(FakeMeta(["x" * 5000]), conn, design_id=1,
                                 work_id=1, episode_no=1, base_text="a\nb",
                                 base_version=1, instruction="@L1 고쳐줘",
                                 full=True)
    n = conn.execute("SELECT COUNT(*) c FROM manuscript_revisions"
                     ).fetchone()["c"]
    assert n == 0


def _ready(conn):
    from app import memory
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    meta = ScriptedMeta(
        [_item("ch_0001", "set", "직업", "서점주인", "달빛 서점의 주인이 되었다")],
        [{"item_id": 1, "verdict": "ok", "reason": "신규"}])
    state_machine.advance(conn, meta, 1, design_id=1)
    state_machine.advance(conn, meta, 1)
    state_machine.advance(conn, meta, 1)
    return meta


def test_request_change_draft_redrafts_and_logs(conn):
    state_machine.advance(conn, FakeMeta(["가" * 5000]), 1, design_id=1)
    out = state_machine.request_change(conn, FakeMeta(["나" * 5000]), 1,
                                       "더 길게 써줘")
    assert out["state"] == "DRAFT"
    assert out["revision"]["version"] == 2
    job = state_machine.get_job(conn, 1)
    assert job["fix_instruction"] == "" and job["attempt"] == 0
    hist = drafting.list_draft_requests(conn, 1)
    assert len(hist) == 1
    row = hist[0]
    assert (row["instruction"], row["base_version"],
            row["result_version"], row["error"]) == ("더 길게 써줘", 1, 2, "")


def test_request_change_ready_discards_proposals(conn):
    meta = _ready(conn)
    job = state_machine.get_job(conn, 1)
    assert job["state"] == "READY_FOR_REVIEW"
    old_set = job["proposal_set_id"]
    out = state_machine.request_change(conn, meta, 1, "직업을 바꿔줘")
    assert out["state"] == "DRAFT" and out["revision"]["version"] == 2
    job = state_machine.get_job(conn, 1)
    assert job["proposal_set_id"] is None
    row = conn.execute("SELECT status FROM change_proposals"
                       " WHERE proposal_set_id=?", (old_set,)).fetchone()
    assert row["status"] == "discarded"


def test_request_change_wrong_state(conn):
    meta = ScriptedMeta([], [])
    state_machine.advance(conn, meta, 1, design_id=1)
    state_machine.advance(conn, meta, 1)  # now VERIFY
    assert state_machine.get_job(conn, 1)["state"] == "VERIFY"
    with pytest.raises(ValueError, match="DRAFT or READY_FOR_REVIEW"):
        state_machine.request_change(conn, meta, 1, "고쳐줘")
    with pytest.raises(KeyError, match="has no job"):
        state_machine.request_change(conn, meta, 9, "고쳐줘")


@pytest.fixture()
def api_client(tmp_path):
    from fastapi.testclient import TestClient

    from app import auth as authmod
    from app.main import app
    from app.routes.deps import get_conn, get_current_author
    db_path = tmp_path / "api.db"

    def _override():
        c = dbmod.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_conn] = _override
    seed = dbmod.connect(db_path)
    author = authmod.public_author(authmod.register(
        seed, login_id="tester", pen_name="T", password="pw1234"))
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: author
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_request_change_api_redrafts_and_lists_history(api_client):
    from app.main import app
    from app.routes.works import get_meta
    from tests.test_planning import FakeMeta as PlanFake
    c = api_client
    wid = c.post("/api/v1/works", json={"title": "w"}).json()["id"]
    app.dependency_overrides[get_meta] = lambda: PlanFake(2)
    did = c.post(f"/api/v1/works/{wid}/designs",
                 json={"keywords": "k", "total_episodes": 2}).json()["design_id"]
    app.dependency_overrides[get_meta] = lambda: FakeMeta(["가" * 5000])
    try:
        r = c.post("/api/v1/episodes/1/advance", json={"design_id": did})
        assert r.json()["state"] == "DRAFT"
    finally:
        del app.dependency_overrides[get_meta]
    app.dependency_overrides[get_meta] = lambda: FakeMeta(["나" * 5000, "다" * 5000])
    try:
        r = c.post("/api/v1/episodes/1/request-change",
                   json={"instruction": "어조를 바꿔줘"})
        assert r.status_code == 200, r.text
        assert r.json()["revision"]["version"] == 2
        r = c.post("/api/v1/episodes/1/request-change", json={"full": True})
        assert r.json()["revision"]["version"] == 3
        r = c.post("/api/v1/episodes/1/request-change",
                   json={"instruction": "@L999 고쳐줘"})
        assert r.status_code == 400
        r = c.get("/api/v1/episodes/1/draft-requests")
        rows = r.json()["requests"]
        assert [(x["result_version"], x["full"]) for x in rows] == [(2, False), (3, True)]
    finally:
        del app.dependency_overrides[get_meta]


def test_request_change_logs_failure_and_raises(conn):
    state_machine.advance(conn, FakeMeta(["가" * 5000]), 1, design_id=1)
    with pytest.raises(LLMStepError, match="manuscript_invalid"):
        state_machine.request_change(conn, FakeMeta(["짧다"] * 4), 1, "고쳐줘")
    job = state_machine.get_job(conn, 1)
    assert job["state"] == "DRAFT" and job["attempt"] == 1
    hist = drafting.list_draft_requests(conn, 1)
    assert len(hist) == 1
    assert hist[0]["result_version"] is None
    assert "manuscript_invalid" in hist[0]["error"]


def test_request_change_recovers_within_request(conn):
    state_machine.advance(conn, FakeMeta(["가" * 5000]), 1, design_id=1)
    out = state_machine.request_change(
        conn, FakeMeta(["짧다", "나" * 5000]), 1, "고쳐줘")
    assert out["state"] == "DRAFT" and out["attempt"] == 0
    assert out["revision"]["version"] == 2
    hist = drafting.list_draft_requests(conn, 1)
    assert len(hist) == 1 and hist[0]["result_version"] == 2
