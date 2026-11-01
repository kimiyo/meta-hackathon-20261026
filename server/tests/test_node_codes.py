"""Per-design node codes: P/C/E/S restart at 001 in each tree.

The global text id stays the immutable primary key; the user-facing
identity is the short code (E001) within a tree and the full code
(N002-E001) across trees. N numbers the tree itself and keeps running
globally.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.routes.deps import get_conn
from app.schemas import ideas as sch


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "nodecodes.db"

    def _override():
        c = dbmod.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    from app.main import app
    app.dependency_overrides[get_conn] = _override
    yield TestClient(app), db_path
    app.dependency_overrides.clear()


class DesignChatFake:
    def __init__(self):
        self.last = {}

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens,
                  work_id=None):
        self.last[bundle] = user
        assert schema is sch.DesignChatOut
        return sch.DesignChatOut.model_validate(
            {"reply": "ok", "proposed_children": [],
             "proposed_patch": {}}).model_dump()


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


def _by_code(c, did):
    nodes = c.get(f"/api/v1/designs/{did}/nodes").json()["nodes"]
    return {n["code"]: n for n in nodes}


def test_codes_restart_per_design(client):
    c, _ = client
    h1 = _handoff(c, _mkwork(c, "작품1")["uid"])
    h2 = _handoff(c, _mkwork(c, "작품2")["uid"])
    assert h1["root_node"]["code"] == "N001"
    assert h1["root_node"]["full_code"] == "N001"
    assert h2["root_node"]["code"] == "N002"
    assert h2["root_node"]["full_code"] == "N002"
    n1, n2 = _by_code(c, h1["design_id"]), _by_code(c, h2["design_id"])
    assert n1["E001"]["full_code"] == "N001-E001"
    assert n1["E001"]["id"] == "E001"
    # same short code exists in the other tree (duplicate across works),
    # disambiguated by the N qualifier
    assert n2["E001"]["full_code"] == "N002-E001"
    assert n2["E001"]["id"] == "E026"
    assert n1["E025"]["full_code"] == "N001-E025"


def test_new_nodes_continue_per_design_kind(client):
    c, _ = client
    h1 = _handoff(c, _mkwork(c, "작품1")["uid"])
    h2 = _handoff(c, _mkwork(c, "작품2")["uid"])
    d1, d2 = h1["design_id"], h2["design_id"]
    p1 = c.post(f"/api/v1/designs/{d1}/nodes",
                json={"kind": "part", "parent_id": "N001",
                      "title": "1부"}).json()
    assert (p1["code"], p1["full_code"]) == ("P001", "N001-P001")
    p2 = c.post(f"/api/v1/designs/{d2}/nodes",
                json={"kind": "part", "parent_id": "N002",
                      "title": "1부"}).json()
    assert (p2["code"], p2["full_code"]) == ("P001", "N002-P001")
    assert p1["id"] != p2["id"]
    p3 = c.post(f"/api/v1/designs/{d1}/nodes",
                json={"kind": "part", "parent_id": "N001",
                      "title": "2부"}).json()
    assert (p3["code"], p3["full_code"]) == ("P002", "N001-P002")


def test_delete_does_not_reuse_seq(client):
    c, _ = client
    h = _handoff(c, _mkwork(c)["uid"])
    did = h["design_id"]
    assert c.delete("/api/v1/nodes/E001").status_code == 200
    ep = c.post(f"/api/v1/designs/{did}/nodes",
                json={"kind": "episode", "parent_id": "N001",
                      "title": "새화"}).json()
    assert ep["code"] == "E026"
    assert ep["full_code"] == "N001-E026"


def test_search_by_code(client):
    c, _ = client
    h1 = _handoff(c, _mkwork(c, "작품1")["uid"])
    h2 = _handoff(c, _mkwork(c, "작품2")["uid"])
    got = c.get(f"/api/v1/designs/{h1['design_id']}/nodes",
                params={"q": "E002"}).json()["nodes"]
    assert [n["code"] for n in got] == ["E002"]
    got = c.get(f"/api/v1/designs/{h1['design_id']}/nodes",
                params={"q": "N001-E003"}).json()["nodes"]
    assert [n["code"] for n in got] == ["E003"]
    # codes, not global ids, are searchable in the second tree too
    got = c.get(f"/api/v1/designs/{h2['design_id']}/nodes",
                params={"q": "E001"}).json()["nodes"]
    assert [(n["code"], n["id"]) for n in got] == [("E001", "E026")]


def test_backfill_assigns_seqs(client):
    c, db_path = client
    h1 = _handoff(c, _mkwork(c, "작품1")["uid"])
    h2 = _handoff(c, _mkwork(c, "작품2")["uid"])
    conn = sqlite3.connect(str(db_path))
    try:
        # simulate a pre-migration table: no index, seqs all zero
        conn.execute("DROP INDEX IF EXISTS ux_design_nodes_dks")
        conn.execute("UPDATE design_nodes SET seq=0")
        conn.commit()
        dbmod.backfill_node_seq(conn)
        conn.commit()
        # recreating the index proves the backfill left no duplicates
        conn.execute("CREATE UNIQUE INDEX ux_design_nodes_dks"
                     " ON design_nodes(design_id, kind, seq)")
        conn.commit()
    finally:
        conn.close()
    n1, n2 = _by_code(c, h1["design_id"]), _by_code(c, h2["design_id"])
    assert n1["N001"]["full_code"] == "N001"
    assert n2["N002"]["full_code"] == "N002"
    assert n2["E001"]["id"] == "E026"
    assert n1["E025"]["code"] == "E025" and len(
        [k for k in n1 if k.startswith("E")]) == 25


def test_chat_refs_resolve_codes(client, fakes):
    c, _ = client
    h1 = _handoff(c, _mkwork(c, "작품1")["uid"])
    h2 = _handoff(c, _mkwork(c, "작품2")["uid"])
    d2 = h2["design_id"]
    ch = c.post(f"/api/v1/designs/{d2}/nodes",
                json={"kind": "chapter", "parent_id": "N002",
                      "title": "1장"}).json()
    assert ch["id"] == "C001" and ch["code"] == "C001"
    c.patch("/api/v1/nodes/E026", json={"title": "W2첫화"})
    r = c.post(f"/api/v1/nodes/{ch['id']}/chat",
               json={"content": "어때?",
                     "refs": ["E001", "N002-E002", "E999", "@C001"]})
    assert r.status_code == 200, r.text
    user = fakes.last["design_chat"]
    # selected node and context speak codes, not global ids
    assert "선택 노드: C001(chapter)" in user
    # short codes resolve within the current tree (E001 = this tree's
    # first episode, global id E026 — not the other tree's E001)
    assert "W2첫화" in user
    assert "N002-E002" in user
    assert "E999" in user and "알 수 없음" in user
