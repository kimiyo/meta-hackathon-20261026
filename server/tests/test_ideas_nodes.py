"""Ideas/design-tree API: uid works, chat, synopsis, settings, handoff,
nodes + outline sync, assistant bundles (all AI faked, none called)."""
import pytest
from fastapi.testclient import TestClient

from app import budget as budgetmod
from app import auth as authmod
from app import db as dbmod
from app.routes.deps import get_conn, get_current_author
from app.schemas import ideas as sch


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "ideas.db"

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
        seed, login_id="tester", pen_name="테스터",
        password="pw1234"))
    seed.close()
    app.dependency_overrides[get_current_author] = \
        lambda: _author
    yield TestClient(app), db_path
    app.dependency_overrides.clear()


class AssistFake:
    """Scripted MetaClient for the four assistant bundles."""

    def __init__(self):
        self.last = {}
        self.calls = []
        self.chat_out = None  # override the idea_chat output per test

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens,
                  work_id=None):
        self.last[bundle] = user
        self.calls.append({"bundle": bundle, "work_id": work_id})
        if bundle == "idea_chat":
            assert schema is sch.IdeaChatOut
            if self.chat_out is not None:
                return sch.IdeaChatOut.model_validate(self.chat_out).model_dump()
            return sch.IdeaChatOut.model_validate(
                {"reply": "좋은 방향이에요", "choices": ["A안", "B안"],
                 "settings": [{"category": "world", "title": "달빛 규칙",
                               "summary": "요약", "detail": "상세"}],
                 "synopsis_proposal": "줄거리 개정안",
                 "structure": {"use_part": True, "use_chapter": True,
                               "total_episodes": 120,
                               "parts": [{"title": "1부", "summary": "시작"}]}}).model_dump()
        if bundle == "idea_extract":
            assert schema is sch.ExtractOut
            return sch.ExtractOut.model_validate(
                {"synopsis_proposal": "줄거리 개정안",
                 "settings": [{"category": "world", "title": "달빛 규칙",
                               "summary": "달빛엔 힘이 있다", "detail": "상세"}],
                 "structure": {"use_part": True, "use_chapter": True,
                               "total_episodes": 25,
                               "reason": "장편이라 필요"}}).model_dump()
        if bundle == "design_chat":
            assert schema is sch.DesignChatOut
            return sch.DesignChatOut.model_validate(
                {"reply": "이 장은 후반 전환이 핵심",
                 "proposed_children": [{"kind": "episode", "title": "전환",
                                        "logline": "주인공이 결심한다",
                                        "body": {}}],
                 "proposed_patch": {"logline": "고친 줄거리"}}).model_dump()
        if bundle == "design_expand":
            assert schema is sch.ExpandOut
            return sch.ExpandOut.model_validate(
                {"children": [{"kind": "episode", "title": "E1",
                                "logline": "시작", "body": {}},
                               {"kind": "episode", "title": "E2",
                                "logline": "전개", "body": {}}]}).model_dump()
        if bundle == "handoff_migrate":
            assert schema is sch.HandoffOut
            return sch.HandoffOut.model_validate(
                {"tree": [{"kind": "part", "title": "1부",
                            "summary": "시작", "episode_no": 0,
                            "children": [
                                {"kind": "episode", "title": "첫 만남",
                                 "summary": "마법 각성", "episode_no": 1,
                                 "children": []}]}]}).model_dump()
        raise AssertionError(f"unexpected bundle {bundle}")


@pytest.fixture()
def fakes():
    from app.main import app
    from app.routes.ideas import get_meta as ideas_meta
    from app.routes.nodes import get_meta as nodes_meta
    fake = AssistFake()
    app.dependency_overrides[ideas_meta] = lambda: fake
    app.dependency_overrides[nodes_meta] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def _mkwork(c, title="달빛 서점"):
    r = c.post("/api/v1/works", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- works

def test_work_uid_and_validation(client):
    c, _ = client
    w = _mkwork(c)
    assert len(w["uid"]) == 12
    assert w["stage"] == "ideas"
    r = c.get(f"/api/v1/works/{w['uid']}")
    assert r.json()["id"] == w["id"]
    r = c.get(f"/api/v1/works/{w['id']}")
    assert r.json()["uid"] == w["uid"]
    assert c.post("/api/v1/works", json={"title": "  "}).status_code == 400
    assert c.post("/api/v1/works",
                  json={"title": "x" * 81}).status_code == 400
    assert c.patch(f"/api/v1/works/{w['uid']}",
                   json={"title": "y" * 81}).status_code == 400
    r = c.get("/api/v1/works")
    assert r.json()["works"][0]["completed_episodes"] == 0


def test_legacy_rows_backfilled(tmp_path):
    p = tmp_path / "legacy.db"
    conn = dbmod.connect(p)
    conn.execute("INSERT INTO works(title, created_at) VALUES('w','t')")
    conn.commit()
    conn.close()
    conn = dbmod.connect(p)  # migrate runs on every connect
    row = dict(conn.execute("SELECT * FROM works").fetchone())
    conn.close()
    assert len(row["uid"]) == 12
    assert row["stage"] == "ideas"
    assert row["updated_at"] == "t"


def test_delete_work_needs_title_and_keeps_logs(client):
    c, db_path = client
    w = _mkwork(c)
    conn = dbmod.connect(db_path)
    budgetmod.record_llm_call(conn, {"bundle": "planning", "episode_no": None,
                                     "model": "m", "input_tokens": 1,
                                     "output_tokens": 1, "latency_ms": 1,
                                     "ok": 1, "error": ""})
    conn.commit()
    conn.close()
    assert c.delete(f"/api/v1/works/{w['uid']}",
                    json={"title": "엉뚱한 제목"}).status_code == 400
    r = c.delete(f"/api/v1/works/{w['uid']}", json={"title": w["title"]})
    assert r.json()["deleted"] == w["id"]
    assert c.get(f"/api/v1/works/{w['uid']}").status_code == 404
    assert c.get("/api/v1/works").json()["works"] == []
    conn = dbmod.connect(db_path)
    n = conn.execute("SELECT COUNT(*) c FROM llm_calls").fetchone()["c"]
    conn.close()
    assert n == 1


# ---------------------------------------------------------------- ideas

def test_threads_and_chat_roundtrip(client, fakes):
    c, _ = client
    w = _mkwork(c)
    r = c.post(f"/api/v1/works/{w['uid']}/threads", json={"title": "대화 1"})
    tid = r.json()["id"]
    r = c.post(f"/api/v1/threads/{tid}/messages",
               json={"content": "추리물을 구상 중이야", "refs": [], "history": 5})
    assert r.status_code == 200, r.text
    assert r.json()["assistant"]["choices"] == ["A안", "B안"]
    assert r.json()["assistant"]["proposals"]["settings"][0]["title"] == "달빛 규칙"
    assert r.json()["assistant"]["proposals"]["synopsis_proposal"] == "줄거리 개정안"
    assert r.json()["assistant"]["proposals"]["structure"]["total_episodes"] == 120
    r = c.get(f"/api/v1/threads/{tid}/messages")
    assert [m["role"] for m in r.json()["messages"]] == ["user", "assistant"]
    assert r.json()["messages"][1]["proposals"]["settings"][0]["title"] == "달빛 규칙"
    assert c.post(f"/api/v1/threads/{tid}/messages",
                  json={"content": "  "}).status_code == 400
    assert c.get("/api/v1/threads/999/messages").status_code == 404


def test_message_inclusion_controls(client, fakes):
    c, _ = client
    w = _mkwork(c)
    s1 = c.post(f"/api/v1/works/{w['uid']}/settings",
                json={"category": "world", "title": "달빛규칙",
                      "summary": "요약1", "detail": "상세1"}).json()
    s2 = c.post(f"/api/v1/works/{w['uid']}/settings",
                json={"category": "character", "title": "추적자",
                      "summary": "요약2", "detail": "상세2"}).json()
    c.put(f"/api/v1/works/{w['uid']}/synopsis", json={"content": "줄거리본문"})
    tid = c.post(f"/api/v1/works/{w['uid']}/threads",
                 json={"title": "t"}).json()["id"]
    c.post(f"/api/v1/threads/{tid}/messages", json={"content": "첫번째이야기"})
    m1 = c.get(f"/api/v1/threads/{tid}/messages").json()["messages"][0]
    r = c.post(f"/api/v1/threads/{tid}/messages", json={
        "content": "두번째질문", "include_synopsis": False,
        "include_settings": [s1["id"]], "include_messages": [m1["id"]]})
    assert r.status_code == 200, r.text
    u = fakes.last["idea_chat"]
    assert "달빛규칙" in u and "상세1" in u
    # 선택하지 않은 설정은 번호·제목만 색인으로 보인다 (중복 생성 방지)
    assert f"{s2['code']} [character] 추적자" in u and "상세2" not in u
    assert "첫번째이야기" in u and "두번째질문" in u  # 질문은 항상 포함
    assert "[전체 줄거리 미포함]" in u
    assert "줄거리본문" not in u
    # @ 언급은 제목으로 찾아 전문 포함
    r = c.post(f"/api/v1/threads/{tid}/messages", json={
        "content": "@추적자 어때?", "refs": ["추적자"],
        "include_synopsis": False, "include_settings": [],
        "include_messages": []})
    assert r.status_code == 200, r.text
    u = fakes.last["idea_chat"]
    assert "상세2" in u
    assert "첫번째이야기" not in u
    # 다른 작품의 설정 id는 무시
    w2 = _mkwork(c, "다른작품")
    s3 = c.post(f"/api/v1/works/{w2['uid']}/settings",
                json={"title": "남의설정"}).json()
    c.post(f"/api/v1/threads/{tid}/messages", json={
        "content": "hey", "include_settings": [s3["id"]],
        "include_messages": []})
    assert "남의설정" not in fakes.last["idea_chat"]


def test_chat_threads_work_id_and_ai_log(client, fakes):
    c, db_path = client
    w = _mkwork(c)
    tid = c.post(f"/api/v1/works/{w['uid']}/threads",
                 json={"title": "t"}).json()["id"]
    c.post(f"/api/v1/threads/{tid}/messages", json={"content": "hi"})
    assert {"bundle": "idea_chat", "work_id": w["id"]} in fakes.calls
    # ai-log is scoped to the work and carries prompts + output
    conn = dbmod.connect(db_path)
    budgetmod.record_llm_call(conn, {"bundle": "idea_chat", "work_id": w["id"],
                                     "model": "m", "input_tokens": 5,
                                     "output_tokens": 7, "latency_ms": 11,
                                     "ok": 1, "prompt_system": "SYS",
                                     "prompt_user": "HELLO",
                                     "output_text": '{"a": 1}'})
    budgetmod.record_llm_call(conn, {"bundle": "drafting", "work_id": 999,
                                     "model": "m", "ok": 1})
    conn.close()
    r = c.get(f"/api/v1/works/{w['uid']}/ai-log")
    assert r.status_code == 200, r.text
    calls = r.json()["calls"]
    assert len(calls) == 1
    assert (calls[0]["prompt_user"], calls[0]["output_text"]) == ("HELLO", '{"a": 1}')
    assert (calls[0]["input_tokens"], calls[0]["latency_ms"]) == (5, 11)
    assert c.get("/api/v1/works/nope/ai-log").status_code == 404


def test_structure_crud_and_handoff_preserves_parts(client, fakes):
    c, _ = client
    w = _mkwork(c)
    r = c.get(f"/api/v1/works/{w['uid']}/structure")
    assert r.json()["total_episodes"] == 25
    body = {"use_part": True, "use_chapter": True, "total_episodes": 120,
            "parts": [{"title": "1부", "summary": "시작"},
                      {"title": "2부", "summary": "전개"}]}
    r = c.put(f"/api/v1/works/{w['uid']}/structure", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["parts"][1]["title"] == "2부"
    assert c.put(f"/api/v1/works/{w['uid']}/structure",
                 json={"total_episodes": 0}).status_code == 400
    assert c.put(f"/api/v1/works/{w['uid']}/structure",
                 json={"parts": [{"title": "x"}] * 21}).status_code == 400
    p = c.get(f"/api/v1/works/{w['uid']}/handoff/preview").json()
    assert p["counts"]["episodes"] == 120
    r = c.post(f"/api/v1/works/{w['uid']}/handoff", json=p["plan"])
    assert r.status_code == 200, r.text
    assert r.json()["structure"]["parts"][0]["title"] == "1부"
    assert r.json()["counts"] == p["counts"]
    assert c.put(f"/api/v1/works/{w['uid']}/structure",
                 json=body).status_code == 409
    assert c.get("/api/v1/works/nope/structure").status_code == 404


def test_include_structure_in_context(client, fakes):
    c, _ = client
    w = _mkwork(c)
    c.put(f"/api/v1/works/{w['uid']}/structure",
          json={"use_part": True, "total_episodes": 120,
                "parts": [{"title": "1부", "summary": "시작요약"}]})
    tid = c.post(f"/api/v1/works/{w['uid']}/threads",
                 json={"title": "t"}).json()["id"]
    c.post(f"/api/v1/threads/{tid}/messages",
           json={"content": "hi", "include_structure": True,
                 "include_messages": []})
    u = fakes.last["idea_chat"]
    assert "[포함된 작품 구조]:" in u
    assert "1부" in u and "시작요약" in u
    c.post(f"/api/v1/threads/{tid}/messages",
           json={"content": "hi", "include_structure": False,
                 "include_messages": []})
    assert "[작품 구조 미포함]" in fakes.last["idea_chat"]


def test_synopsis_versioning(client):
    c, _ = client
    w = _mkwork(c)
    assert c.get(f"/api/v1/works/{w['uid']}/synopsis").json()["version"] == 0
    r = c.put(f"/api/v1/works/{w['uid']}/synopsis", json={"content": "v1"})
    assert r.json()["version"] == 1
    r = c.put(f"/api/v1/works/{w['uid']}/synopsis", json={"content": "v2"})
    assert r.json()["version"] == 2
    revs = c.get(f"/api/v1/works/{w['uid']}/synopsis/revisions").json()
    assert [v["version"] for v in revs["revisions"]] == [2, 1]


def test_settings_crud_and_validation(client):
    c, _ = client
    w = _mkwork(c)
    r = c.post(f"/api/v1/works/{w['uid']}/settings",
               json={"category": "world", "title": "규칙",
                     "summary": "요약", "detail": "상세"})
    sid = r.json()["id"]
    assert r.json()["status"] == "draft"
    assert r.json()["code"] == "D001"
    r = c.post(f"/api/v1/works/{w['uid']}/settings",
               json={"category": "world", "title": "직행",
                     "status": "applied"})
    assert r.json()["status"] == "applied"
    assert r.json()["code"] == "D002"
    sid2 = r.json()["id"]
    w2 = _mkwork(c, "다른작품")
    r = c.post(f"/api/v1/works/{w2['uid']}/settings", json={"title": "첫설정"})
    assert r.json()["code"] == "D001"
    assert c.post(f"/api/v1/works/{w['uid']}/settings",
                  json={"title": "t", "status": "bogus"}).status_code == 400
    r = c.patch(f"/api/v1/settings/{sid}", json={"status": "applied"})
    assert r.json()["status"] == "applied"
    assert c.patch(f"/api/v1/settings/{sid}",
                   json={"status": "bogus"}).status_code == 400
    assert c.post(f"/api/v1/works/{w['uid']}/settings",
                  json={"category": "bogus", "title": "t"}).status_code == 400
    assert c.delete(f"/api/v1/settings/{sid}").json()["deleted"] == sid
    assert c.delete(f"/api/v1/settings/{sid2}").json()["deleted"] == sid2
    assert c.get(f"/api/v1/works/{w['uid']}/settings").json()["settings"] == []
    assert c.delete("/api/v1/settings/999").status_code == 404


def test_extract_and_handoff_lock(client, fakes):
    c, _ = client
    w = _mkwork(c)
    assert c.post(f"/api/v1/works/{w['uid']}/extract",
                  json={}).status_code == 400  # no conversation yet
    tid = c.post(f"/api/v1/works/{w['uid']}/threads",
                 json={"title": "t"}).json()["id"]
    c.post(f"/api/v1/threads/{tid}/messages", json={"content": "hi"})
    r = c.post(f"/api/v1/works/{w['uid']}/extract", json={"thread_id": tid})
    assert r.status_code == 200, r.text
    assert r.json()["structure"]["total_episodes"] == 25
    r = c.post(f"/api/v1/works/{w['uid']}/handoff", json={})
    assert r.status_code == 200, r.text
    assert r.json()["root_node"]["id"] == "N001"
    assert r.json()["structure"]["use_part"] is False
    assert r.json()["counts"] == {"parts": 0, "chapters": 0, "episodes": 25,
                                  "stub_episodes": 25}
    w2 = c.get(f"/api/v1/works/{w['uid']}").json()
    assert w2["ideas_locked"] == 1 and w2["stage"] == "design"
    assert c.post(f"/api/v1/works/{w['uid']}/handoff",
                  json={}).status_code == 409
    assert c.post(f"/api/v1/threads/{tid}/messages",
                  json={"content": "hi"}).status_code == 409
    assert c.put(f"/api/v1/works/{w['uid']}/synopsis",
                 json={"content": "x"}).status_code == 409
    assert c.post(f"/api/v1/works/{w['uid']}/settings",
                  json={"title": "x"}).status_code == 409


def test_second_handoff_gets_globally_unique_node_ids(client, fakes):
    c, _ = client
    w1 = _mkwork(c, "work one")
    w2 = _mkwork(c, "work two")
    r1 = c.post(f"/api/v1/works/{w1['uid']}/handoff", json={})
    assert r1.status_code == 200, r1.text
    r2 = c.post(f"/api/v1/works/{w2['uid']}/handoff", json={})
    assert r2.status_code == 200, r2.text
    assert r1.json()["root_node"]["id"] == "N001"
    assert r2.json()["root_node"]["id"] != "N001"
    ids1 = {n["id"] for n in
            c.get(f"/api/v1/designs/{r1.json()['design_id']}/nodes").json()["nodes"]}
    ids2 = {n["id"] for n in
            c.get(f"/api/v1/designs/{r2.json()['design_id']}/nodes").json()["nodes"]}
    assert not ids1 & ids2


def test_handoff_preview_and_ai_migration(client, fakes):
    c, _ = client
    w = _mkwork(c)
    c.put(f"/api/v1/works/{w['uid']}/synopsis",
          json={"content": "바닷가 마을에 숨어든 모자 이야기"})
    c.post(f"/api/v1/works/{w['uid']}/settings",
           json={"category": "character", "title": "주인공 아들",
                 "summary": "열 살 소년", "detail": "마법 각성"})
    c.put(f"/api/v1/works/{w['uid']}/structure",
          json={"use_part": True, "total_episodes": 3,
                "parts": [{"title": "1부", "summary": "은신"}]})
    p = c.get(f"/api/v1/works/{w['uid']}/handoff/preview").json()
    assert p["levels"] == ["Part(부)", "Episode(화)"]
    assert p["counts"] == {"parts": 1, "chapters": 0, "episodes": 3,
                           "stub_episodes": 2}
    assert p["tree"][0]["title"] == "1부"
    eps = p["tree"][0]["children"]
    assert [e["episode_no"] for e in eps] == [1, 2, 3]
    assert [e["stub"] for e in eps] == [False, True, True]
    assert eps[0]["title"] == "첫 만남"
    assert eps[1]["title"] == "제2화"
    assert p["synopsis_chars"] > 0
    assert p["settings"] == [{"code": "D001", "category": "character",
                              "title": "주인공 아들"}]
    assert p["locked_after"] is True
    assert "바닷가" in fakes.last["handoff_migrate"]
    assert "주인공 아들" in fakes.last["handoff_migrate"]
    # Preview writes nothing.
    assert c.get(f"/api/v1/works/{w['uid']}").json()["ideas_locked"] == 0
    r = c.post(f"/api/v1/works/{w['uid']}/handoff", json=p["plan"])
    assert r.status_code == 200, r.text
    assert r.json()["counts"] == p["counts"]
    root = c.get("/api/v1/nodes/N001").json()
    assert root["body"]["synopsis"] == "바닷가 마을에 숨어든 모자 이야기"
    assert root["body"]["settings"][0]["code"] == "D001"
    assert root["body"]["settings"][0]["detail"] == "마법 각성"
    assert root["body"]["structure"]["parts"][0]["title"] == "1부"
    assert c.get(f"/api/v1/works/{w['uid']}/handoff/preview").status_code == 409
    assert c.get("/api/v1/works/nope/handoff/preview").status_code == 404


# ---------------------------------------------------------------- nodes

def _handed_off(c, total=25):
    w = _mkwork(c)
    c.put(f"/api/v1/works/{w['uid']}/structure",
          json={"use_part": True, "use_chapter": True,
                "total_episodes": total})
    r = c.post(f"/api/v1/works/{w['uid']}/handoff", json={})
    assert r.status_code == 200, r.text
    return w, r.json()


def test_tree_ids_and_episode_sync(client, fakes):
    c, _ = client
    w, h = _handed_off(c)
    did = h["design_id"]
    assert h["counts"] == {"parts": 0, "chapters": 0, "episodes": 25,
                           "stub_episodes": 25}
    nodes = {n["id"]: n for n in
             c.get(f"/api/v1/designs/{did}/nodes").json()["nodes"]}
    assert nodes["N001"]["kind"] == "work"
    assert nodes["E001"]["body"]["episode_no"] == 1
    assert nodes["E001"]["parent_id"] == "N001"
    assert nodes["E001"]["title"] == "제1화"  # stub fill
    d = c.get(f"/api/v1/designs/{did}").json()
    assert [o["episode_no"] for o in d["outlines"]] == list(range(1, 26))
    c.patch("/api/v1/nodes/E001", json={"logline": "새목표"})
    d = c.get(f"/api/v1/designs/{did}").json()
    assert d["outlines"][0]["goal"] == "새목표"
    # AI patch bodies arrive without episode_no (design_chat has no such
    # key); merging must keep the stored number instead of 400ing.
    r = c.patch("/api/v1/nodes/E001",
                json={"title": "새제목", "logline": "새줄거리",
                      "body": {"goal": "목표"}})
    assert r.status_code == 200, r.text
    assert r.json()["body"]["episode_no"] == 1
    assert r.json()["body"]["goal"] == "목표"
    d = c.get(f"/api/v1/designs/{did}").json()
    assert d["outlines"][0]["episode_no"] == 1
    assert d["outlines"][0]["title"] == "새제목"
    full = c.get("/api/v1/nodes/N001").json()
    assert len(full["children"]) == 25
    assert "E001" in [k["id"] for k in full["children"]]
    # id sequencing continues after the migrated tree
    p1 = c.post(f"/api/v1/designs/{did}/nodes",
                json={"kind": "part", "parent_id": "N001",
                      "title": "1부"}).json()
    assert p1["id"] == "P001"
    ch = c.post(f"/api/v1/designs/{did}/nodes",
                json={"kind": "chapter", "parent_id": "P001", "title": "1장",
                      "logline": "시작"}).json()
    assert ch["id"] == "C001"
    ep = c.post(f"/api/v1/designs/{did}/nodes",
                json={"kind": "episode", "parent_id": "C001", "title": "추가화",
                      "logline": "목표X"}).json()
    assert ep["id"] == "E026"
    assert ep["body"]["episode_no"] == 26
    assert c.get("/api/v1/designs/999/nodes").status_code == 404
    assert c.post(f"/api/v1/designs/{did}/nodes",
                  json={"kind": "bogus"}).status_code == 400


def test_move_guards_archive_and_search(client, fakes):
    c, _ = client
    w, h = _handed_off(c)
    did = h["design_id"]
    c.post(f"/api/v1/designs/{did}/nodes",
           json={"kind": "part", "parent_id": "N001", "title": "1부"})
    c.post(f"/api/v1/designs/{did}/nodes",
           json={"kind": "chapter", "parent_id": "P001", "title": "1장"})
    assert c.patch("/api/v1/nodes/P001",
                   json={"parent_id": "P001"}).status_code == 400
    assert c.patch("/api/v1/nodes/P001",
                   json={"parent_id": "C001"}).status_code == 400  # cycle
    assert c.patch("/api/v1/nodes/P001",
                   json={"parent_id": "N999"}).status_code == 404
    assert c.post("/api/v1/nodes/P001/archive",
                  json={"archived": True}).json()["archived"] == 1
    ids = [n["id"] for n in c.get(f"/api/v1/designs/{did}/nodes").json()["nodes"]]
    assert "P001" not in ids
    ids = [n["id"] for n in c.get(
        f"/api/v1/designs/{did}/nodes?archived=true").json()["nodes"]]
    assert "P001" in ids
    found = c.get(f"/api/v1/designs/{did}/nodes?q=1장").json()["nodes"]
    assert [n["id"] for n in found] == ["C001"]
    assert c.patch("/api/v1/nodes/N999",
                   json={"title": "x"}).status_code == 404


def test_design_assistant_bundles(client, fakes):
    c, _ = client
    w, h = _handed_off(c)
    did = h["design_id"]
    c.post(f"/api/v1/designs/{did}/nodes",
           json={"kind": "chapter", "parent_id": "N001", "title": "1장"})
    r = c.post("/api/v1/nodes/C001/chat",
               json={"content": "이 장 어때?", "refs": []})
    assert r.status_code == 200, r.text
    assert r.json()["proposed_children"][0]["kind"] == "episode"
    assert r.json()["proposed_patch"]["logline"] == "고친 줄거리"
    assert c.post("/api/v1/nodes/C001/chat",
                  json={"content": "  "}).status_code == 400
    r = c.post("/api/v1/nodes/C001/expand", json={"kind": "episode"})
    assert len(r.json()["children"]) == 2
    assert c.post("/api/v1/nodes/C001/expand",
                  json={"kind": "bogus"}).status_code == 400
    assert c.post("/api/v1/nodes/N999/chat",
                  json={"content": "x"}).status_code == 404


def test_advance_marks_writing_stage(client, fakes):
    from app.main import app
    from app.routes.works import get_meta
    from tests.test_drafting import FakeMeta as DraftFake

    c, _ = client
    w, h = _handed_off(c, total=2)
    did = h["design_id"]
    c.post(f"/api/v1/designs/{did}/nodes",
           json={"kind": "episode", "parent_id": "N001", "title": "1화",
                 "logline": "목표1"})
    app.dependency_overrides[get_meta] = lambda: DraftFake(["가" * 3000])
    try:
        r = c.post("/api/v1/episodes/1/advance", json={"design_id": did})
        assert r.json()["state"] == "DRAFT", r.text
    finally:
        del app.dependency_overrides[get_meta]
    assert c.get(f"/api/v1/works/{w['uid']}").json()["stage"] == "writing"


def test_chat_blank_proposal_title_falls_back_to_summary(client):
    """Blank AI titles must not reach the buttons as bare '무제'.

    Regression: the model returned title:"" so every proposal button
    read '+ [인물] 무제' and the entries were indistinguishable.
    """
    from app.main import app
    from app.routes.ideas import get_meta as ideas_meta

    class BlankTitleFake:
        def chat_json(self, *, bundle, episode, system, user, schema,
                      max_tokens, work_id=None):
            assert schema is sch.IdeaChatOut
            return sch.IdeaChatOut.model_validate(
                {"reply": "정리했어요", "choices": [],
                 "settings": [
                     {"category": "character", "title": "  ",
                      "summary": "마법을 숨기고 사는 엄마", "detail": "d"},
                     {"category": "world", "title": "",
                      "summary": "", "detail": ""}],
                 "synopsis_proposal": "",
                 "structure": None}).model_dump()

    c, _ = client
    app.dependency_overrides[ideas_meta] = lambda: BlankTitleFake()
    try:
        w = _mkwork(c)
        tid = c.post(f"/api/v1/works/{w['uid']}/threads",
                     json={"title": "t"}).json()["id"]
        r = c.post(f"/api/v1/threads/{tid}/messages", json={"content": "hi"})
        assert r.status_code == 200, r.text
        stored = r.json()["assistant"]["proposals"]["settings"]
        assert stored[0]["title"] == "마법을 숨기고 사는 엄마"
        assert stored[1]["title"] == "무제"
    finally:
        del app.dependency_overrides[ideas_meta]


def test_ideas_chat_prompt_requires_setting_titles():
    """The chat prompt must tell the model to fill every title."""
    from pathlib import Path
    text = (Path(__file__).parent.parent / "app" / "prompts"
            / "ideas_chat.txt").read_text(encoding="utf-8")
    assert "title" in text


def test_ideas_chat_prompt_states_episode_limit():
    """The model must know total_episodes is capped at 300.

    Regression: it proposed 201 episodes, the server rejected the
    structure update with 400, and the apply button silently did nothing.
    """
    from pathlib import Path
    text = (Path(__file__).parent.parent / "app" / "prompts"
            / "ideas_chat.txt").read_text(encoding="utf-8")
    assert "1~300" in text or "1..300" in text


def test_structure_accepts_up_to_300(client, fakes):
    c, _ = client
    w = _mkwork(c)
    r = c.put(f"/api/v1/works/{w['uid']}/structure",
              json={"use_part": True, "use_chapter": True,
                    "total_episodes": 300,
                    "parts": [{"title": "1부", "summary": "시작"}]})
    assert r.status_code == 200, r.text
    assert r.json()["total_episodes"] == 300


def test_structure_rejects_over_limit_total(client, fakes):
    c, _ = client
    w = _mkwork(c)
    r = c.put(f"/api/v1/works/{w['uid']}/structure",
              json={"use_part": True, "use_chapter": True,
                    "total_episodes": 301,
                    "parts": [{"title": "1부", "summary": "시작"}]})
    assert r.status_code == 400, r.text
    assert "exceeds" in r.text and "300" in r.text


def _thread_with(c, w):
    return c.post(f"/api/v1/works/{w['uid']}/threads",
                  json={"title": "t"}).json()["id"]


def test_chat_code_match_merge_and_digest(client, fakes):
    c, _ = client
    w = _mkwork(c)
    s1 = c.post(f"/api/v1/works/{w['uid']}/settings",
                json={"category": "event", "title": "퇴사후반전",
                      "summary": "a", "detail": "b"}).json()
    s2 = c.post(f"/api/v1/works/{w['uid']}/settings",
                json={"category": "event", "title": "퇴사후증명",
                      "summary": "c", "detail": "d"}).json()
    tid = _thread_with(c, w)
    fakes.chat_out = {
        "reply": "합쳤어요", "choices": [{"label": "A", "text": "상사 방해"}],
        "settings": [{"code": "", "category": "event", "title": "퇴사당일역전",
                      "summary": "합친 요약", "detail": "합친 상세"},
                     {"code": "D999", "category": "world", "title": "가짜번호",
                      "summary": "x", "detail": "y"}],
        "merge_from": [s1["code"], s2["code"], "D999", s1["code"]]}
    r = c.post(f"/api/v1/threads/{tid}/messages",
               json={"content": "정리해 줘", "include_messages": []})
    assert r.status_code == 200, r.text
    a = r.json()["assistant"]
    assert a["choices"] == ["A) 상사 방해"]  # 객체 선택지도 문자열로
    assert a["proposals"]["settings"][1]["code"] == ""  # 없는 번호는 새 항목
    assert [x["code"] for x in a["proposals"]["merge_from"]] == \
        [s1["code"], s2["code"]]
    # 다음 턴 이력에 이 답변의 제안과 반영 여부가 붙는다
    r = c.post(f"/api/v1/threads/{tid}/messages",
               json={"content": "다음", "include_messages": [a["id"]]})
    u = fakes.last["idea_chat"]
    assert "(이 답변의 제안: 설정 [event]퇴사당일역전(미반영)" in u
    assert f"통합 삭제 {s1['code']}·{s2['code']}(미반영)" in u


def test_chat_synopsis_guard_and_latest_question_kept(client, fakes):
    c, _ = client
    w = _mkwork(c)
    long_syn = "가" * 7000
    c.put(f"/api/v1/works/{w['uid']}/synopsis", json={"content": long_syn})
    for i in range(40):
        c.post(f"/api/v1/works/{w['uid']}/settings",
               json={"category": "misc", "title": f"설정{i}",
                     "summary": "요" * 200, "detail": "상" * 300})
    ids = [s["id"] for s in c.get(
        f"/api/v1/works/{w['uid']}/settings").json()["settings"]]
    tid = _thread_with(c, w)
    fakes.chat_out = {"reply": "r", "synopsis_proposal": "짧은 새 줄거리"}
    question = "마지막질문" + "!" * 100
    r = c.post(f"/api/v1/threads/{tid}/messages",
               json={"content": question, "include_synopsis": True,
                     "include_settings": ids, "include_messages": []})
    u = fakes.last["idea_chat"]
    assert u.rstrip().endswith(question)  # 큰 컨텍스트에서도 질문은 온전히
    assert "뒷부분 생략됨" in u
    props = r.json()["assistant"]["proposals"]
    assert props["synopsis_proposal"] == "" and props["synopsis_blocked"]
    # 줄거리를 빼고 보낸 턴의 전문 개정도 보류
    c.put(f"/api/v1/works/{w['uid']}/synopsis", json={"content": "짧은 기존"})
    r = c.post(f"/api/v1/threads/{tid}/messages",
               json={"content": "q", "include_synopsis": False,
                     "include_messages": []})
    assert r.json()["assistant"]["proposals"]["synopsis_blocked"]
    # 포함해서 보내면 그대로 제안
    r = c.post(f"/api/v1/threads/{tid}/messages",
               json={"content": "q", "include_synopsis": True,
                     "include_messages": []})
    props = r.json()["assistant"]["proposals"]
    assert props["synopsis_proposal"] == "짧은 새 줄거리"
    assert not props["synopsis_blocked"]
