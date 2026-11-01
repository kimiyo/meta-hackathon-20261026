"""Design partner threads: per-node conversations with history inclusion."""
import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import db as dbmod
from app.routes.deps import get_conn, get_current_author
from app.schemas import ideas as sch


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "dthreads.db"

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
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: _author
    yield TestClient(app), db_path
    app.dependency_overrides.clear()


class DesignChatFake:
    def __init__(self):
        self.last = {}

    def chat_json(self, *, bundle, episode, system, user, schema,
                  max_tokens, work_id=None):
        self.last[bundle] = user
        assert schema is sch.DesignChatOut
        return sch.DesignChatOut.model_validate({
            "reply": "답변이다",
            "proposed_children": [
                {"kind": "scene", "title": "장면1", "logline": "로그1",
                 "body": {}}],
            "proposed_patch": {"title": "고친제목", "logline": "고친줄거리"},
        }).model_dump()


@pytest.fixture()
def fakes():
    from app.main import app
    from app.routes.nodes import get_meta as nodes_meta
    fake = DesignChatFake()
    app.dependency_overrides[nodes_meta] = lambda: fake
    yield fake
    app.dependency_overrides.clear()


def _mkwork(c, title="작품"):
    r = c.post("/api/v1/works", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _handoff(c, uid):
    r = c.post(f"/api/v1/works/{uid}/handoff", json={})
    assert r.status_code == 200, r.text
    return r.json()


def _thread(c, did, node_id="E001"):
    r = c.post(f"/api/v1/designs/{did}/threads",
               json={"node_id": node_id})
    assert r.status_code == 200, r.text
    return r.json()


def _post(c, tid, content, include="__omit__"):
    body = {"content": content}
    if include != "__omit__":
        body["include_messages"] = include
    r = c.post(f"/api/v1/design-threads/{tid}/messages", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_thread_crud_and_node_scope(client):
    c, _ = client
    h = _handoff(c, _mkwork(c)["uid"])
    did = h["design_id"]
    t1 = _thread(c, did, "E001")
    assert t1["node_code"] == "E001" and t1["title"] == ""
    t2 = _thread(c, did, "E002")
    got = c.get(f"/api/v1/designs/{did}/threads",
                params={"node_id": "E001"}).json()["threads"]
    assert [t["id"] for t in got] == [t1["id"]]
    assert c.get(f"/api/v1/designs/{did}/threads",
                 params={"node_id": "N999"}).status_code == 404
    # node from another design is rejected
    h2 = _handoff(c, _mkwork(c, "작품2")["uid"])
    r = c.post(f"/api/v1/designs/{did}/threads", json={"node_id": "E026"})
    assert r.status_code == 400
    r = c.post(f"/api/v1/designs/{did}/threads", json={"node_id": "N999"})
    assert r.status_code == 404
    assert h2["design_id"] != did
    # posting bumps the thread to the top of the design list
    _post(c, t1["id"], "첫 질문", [])
    order = [t["id"] for t in
             c.get(f"/api/v1/designs/{did}/threads").json()["threads"]]
    assert order == [t1["id"], t2["id"]]


def test_post_message_title_and_history(client, fakes):
    c, _ = client
    h = _handoff(c, _mkwork(c)["uid"])
    t = _thread(c, h["design_id"])
    out = _post(c, t["id"], "이 화를 설계해 줘", [])
    assert out["thread"]["title"] == "이 화를 설계해 줘"
    assert out["assistant"]["proposals"]["patch"]["title"] == "고친제목"
    u1, a1 = out["user"]["id"], out["assistant"]["id"]
    # empty inclusion: only the question reaches the model
    out2 = _post(c, t["id"], "두 번째 질문", [])
    assert "이 화를 설계해 줘" not in fakes.last["design_chat"]
    assert "두 번째 질문" in fakes.last["design_chat"]
    assert out2["thread"]["title"] == "이 화를 설계해 줘"  # title kept
    # explicit inclusion: checked history + always the question
    _post(c, t["id"], "세 번째 질문", [u1, a1])
    u = fakes.last["design_chat"]
    assert "이 화를 설계해 줘" in u and "답변이다" in u
    assert "세 번째 질문" in u
    # foreign ids are ignored, the question still arrives
    _post(c, t["id"], "네 번째 질문", [u1, 9999])
    u = fakes.last["design_chat"]
    assert "이 화를 설계해 줘" in u and "네 번째 질문" in u


def test_applied_digest(client, fakes):
    c, _ = client
    h = _handoff(c, _mkwork(c)["uid"])
    t = _thread(c, h["design_id"])
    out = _post(c, t["id"], "설계해 줘", [])
    a1 = out["assistant"]["id"]
    r = c.patch(f"/api/v1/design-messages/{a1}/applied",
                json={"child": 0})
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == {"patch": False, "children": [0]}
    r = c.patch(f"/api/v1/design-messages/{a1}/applied",
                json={"patch": True})
    assert r.json()["applied"] == {"patch": True, "children": [0]}
    assert c.patch("/api/v1/design-messages/9999/applied",
                   json={"patch": True}).status_code == 404
    assert c.patch(f"/api/v1/design-messages/{a1}/applied",
                   json={"child": 5}).status_code == 400
    _post(c, t["id"], "다음", [a1])
    u = fakes.last["design_chat"]
    assert "장면1(반영됨)" in u and "노드수정(반영됨)" in u


def test_search_threads(client, fakes):
    c, _ = client
    h = _handoff(c, _mkwork(c)["uid"])
    did = h["design_id"]
    t1 = _thread(c, did, "E001")
    _post(c, t1["id"], "출근길 장면을 설계해 줘", [])
    t2 = _thread(c, did, "E002")
    _post(c, t2["id"], "전개를 물어본다", [])
    h2 = _handoff(c, _mkwork(c, "작품2")["uid"])
    to = _thread(c, h2["design_id"], "E026")
    _post(c, to["id"], "출근길 다른 설계", [])
    got = c.get(f"/api/v1/designs/{did}/threads",
                params={"q": "출근"}).json()["threads"]
    assert [(t["id"], t["node_code"]) for t in got] == [(t1["id"], "E001")]
    assert "출근" in (got[0]["snippet"] or "")
    got = c.get(f"/api/v1/designs/{did}/threads",
                params={"q": "전개"}).json()["threads"]
    assert [t["id"] for t in got] == [t2["id"]]
    got = c.get(f"/api/v1/designs/{did}/threads",
                params={"q": "zzz"}).json()["threads"]
    assert got == []


def test_delete_node_cascades_threads(client, fakes):
    c, _ = client
    h = _handoff(c, _mkwork(c)["uid"])
    did = h["design_id"]
    t1 = _thread(c, did, "E001")
    _post(c, t1["id"], "질문", [])
    _thread(c, did, "E002")
    r = c.delete("/api/v1/nodes/E001")
    assert r.status_code == 200, r.text
    assert r.json()["threads"] == 1
    assert c.get(f"/api/v1/designs/{did}/threads",
                 params={"node_id": "E001"}).status_code == 404
    got = c.get(f"/api/v1/designs/{did}/threads").json()["threads"]
    assert len(got) == 1 and got[0]["node_id"] == "E002"


def test_patch_string_body_is_coerced(client):
    """AI가 proposed_patch.body에 문자열을 넣어도 적용이 422 없이 된다."""
    from app import design_threads as dtmod
    from app.main import app
    from app.routes.nodes import get_meta as nodes_meta

    class StringBodyFake(DesignChatFake):
        def chat_json(self, *, bundle, episode, system, user, schema,
                      max_tokens, work_id=None):
            assert schema is sch.DesignChatOut
            return sch.DesignChatOut.model_validate({
                "reply": "줄거리를 고쳐 봤다",
                "proposed_children": [],
                "proposed_patch": {
                    "title": "마지막 출근길",
                    "body": "2026년 12월 31일 아침 줄거리 전문이다"},
            }).model_dump()

    app.dependency_overrides[nodes_meta] = lambda: StringBodyFake()
    try:
        c, _ = client
        h = _handoff(c, _mkwork(c)["uid"])
        t = _thread(c, h["design_id"])
        out = _post(c, t["id"], "이 화 줄거리를 수정해 줘", [])
        patch = out["assistant"]["proposals"]["patch"]
        assert patch.get("body", {}) == {} or isinstance(
            patch.get("body"), dict)
        # 프론트와 같은 방식으로 적용하면 422 없이 반영된다
        fields = {}
        if patch.get("title") is not None:
            fields["title"] = patch["title"]
        if patch.get("logline") is not None:
            fields["logline"] = patch["logline"]
        if patch.get("body") is not None:
            fields["body"] = patch["body"]
        r = c.patch("/api/v1/nodes/E001", json=fields)
        assert r.status_code == 200, r.text
        assert "줄거리 전문이다" in r.json()["logline"]
    finally:
        del app.dependency_overrides[nodes_meta]
    # logline과 문자열 body가 함께 오면 문자열 body는 버린다
    assert dtmod.sanitize_patch(
        {"logline": "짧은줄거리", "body": "긴본문"}) == {
            "logline": "짧은줄거리"}
    # dict body는 그대로 둔다
    assert dtmod.sanitize_patch(
        {"logline": "줄거리", "body": {"a": 1}}) == {
            "logline": "줄거리", "body": {"a": 1}}


def test_thread_ownership(client):
    c, db_path = client
    from app.main import app
    h = _handoff(c, _mkwork(c)["uid"])
    did = h["design_id"]
    t = _thread(c, did)
    conn = dbmod.connect(db_path)
    other = authmod.public_author(authmod.register(
        conn, login_id="other", pen_name="타인", password="pw1234"))
    conn.close()
    app.dependency_overrides[get_current_author] = lambda: other
    try:
        assert c.get(f"/api/v1/designs/{did}/threads").status_code == 404
        assert c.post(f"/api/v1/designs/{did}/threads",
                      json={"node_id": "E001"}).status_code == 404
        assert c.get(f"/api/v1/design-threads/{t['id']}/messages"
                     ).status_code == 404
        assert c.post(f"/api/v1/design-threads/{t['id']}/messages",
                      json={"content": "hi"}).status_code == 404
    finally:
        del app.dependency_overrides[get_current_author]
