"""Node settings (scoped canon): per-node CRUD, inheritance, override."""
import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.routes.deps import get_conn


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "nodesettings.db"

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


def _mkwork(c, title="검과 마법"):
    r = c.post("/api/v1/works", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _handoff(c, uid):
    r = c.post(f"/api/v1/works/{uid}/handoff", json={})
    assert r.status_code == 200, r.text
    return r.json()


def _mksetting(c, node_id, **kw):
    body = {"category": "character", "title": "주인공",
            "summary": "요약", "detail": "상세"}
    body.update(kw)
    r = c.post(f"/api/v1/nodes/{node_id}/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_node_setting_crud_and_codes(client):
    c, _ = client
    w = _mkwork(c)
    h = _handoff(c, w["uid"])
    root = h["root_node"]["id"]
    assert root == "N001"
    s1 = _mksetting(c, root)
    assert s1["code"] == "S001"
    s2 = _mksetting(c, root, title="조연")
    assert s2["code"] == "S002"
    # codes run per design, so a different node continues the sequence
    e1 = _mksetting(c, "E001", title="1화 인물")
    assert e1["code"] == "S003"
    # ... and restart on a fresh design
    w2 = _mkwork(c, "두 번째 작품")
    h2 = _handoff(c, w2["uid"])
    assert _mksetting(c, h2["root_node"]["id"],
                      title="별개")["code"] == "S001"
    eff = c.get(f"/api/v1/nodes/{root}/settings").json()
    assert [s["code"] for s in eff["own"]] == ["S001", "S002"]
    assert eff["inherited"] == [] and eff["overridden"] == []
    r = c.patch(f"/api/v1/node-settings/{s1['id']}",
                json={"title": "주인공 개정", "summary": "새요약"})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "주인공 개정"
    assert r.json()["code"] == "S001"  # code immutable
    assert c.delete(f"/api/v1/node-settings/{s2['id']}").json() == {"deleted": s2["id"]}
    eff = c.get(f"/api/v1/nodes/{root}/settings").json()
    assert [s["code"] for s in eff["own"]] == ["S001"]


def test_node_setting_errors(client):
    c, _ = client
    w = _mkwork(c)
    h = _handoff(c, w["uid"])
    root = h["root_node"]["id"]
    assert c.get("/api/v1/nodes/ZZZ/settings").status_code == 404
    assert c.post("/api/v1/nodes/ZZZ/settings",
                  json={"title": "x"}).status_code == 404
    assert c.patch("/api/v1/node-settings/999",
                   json={"title": "x"}).status_code == 404
    assert c.delete("/api/v1/node-settings/999").status_code == 404
    assert c.post(f"/api/v1/nodes/{root}/settings",
                  json={"category": "bogus", "title": "x"}).status_code == 400
    assert c.post(f"/api/v1/nodes/{root}/settings",
                  json={"title": "x", "code": "D001"}).status_code == 400
    _mksetting(c, root)
    assert c.post(f"/api/v1/nodes/{root}/settings",
                  json={"title": "x", "code": "S001"}).status_code == 400
    # explicit codes must reference an ancestor's code
    assert c.post("/api/v1/nodes/E001/settings",
                  json={"title": "x", "code": "S999"}).status_code == 400
    s = _mksetting(c, root, title="t")
    assert c.patch(f"/api/v1/node-settings/{s['id']}",
                   json={"category": "bogus"}).status_code == 400
    assert c.patch(f"/api/v1/node-settings/{s['id']}",
                   json={}).status_code == 400


def test_inheritance_and_override(client):
    c, _ = client
    w = _mkwork(c)
    h = _handoff(c, w["uid"])
    root = h["root_node"]["id"]
    _mksetting(c, root, title="검", summary="평범한 검")
    eff = c.get("/api/v1/nodes/E001/settings").json()
    assert eff["own"] == []
    assert len(eff["inherited"]) == 1
    assert eff["inherited"][0]["source_node"] == root
    assert eff["inherited"][0]["code"] == "S001"
    # override in child shadows the parent definition
    _mksetting(c, "E001", title="검", summary="부러진 검", code="S001")
    eff = c.get("/api/v1/nodes/E001/settings").json()
    assert len(eff["own"]) == 1
    assert eff["inherited"] == []
    assert eff["overridden"] == ["S001"]
    # siblings are independent scopes
    eff = c.get("/api/v1/nodes/E002/settings").json()
    assert len(eff["inherited"]) == 1
    assert eff["inherited"][0]["summary"] == "평범한 검"


def test_archived_ancestor_skipped_but_chain_continues(client):
    c, _ = client
    w = _mkwork(c)
    h = _handoff(c, w["uid"])
    did, root = h["design_id"], h["root_node"]["id"]
    _mksetting(c, root, title="세계관", summary="달빛 세계")
    p = c.post(f"/api/v1/designs/{did}/nodes",
               json={"kind": "part", "parent_id": root,
                     "title": "1부"}).json()
    _mksetting(c, p["id"], title="1부 사건", summary="시작")
    e = c.post(f"/api/v1/designs/{did}/nodes",
               json={"kind": "episode", "parent_id": p["id"],
                     "title": "1화"}).json()
    eff = c.get(f"/api/v1/nodes/{e['id']}/settings").json()
    assert [s["source_node"] for s in eff["inherited"]] == [p["id"], root]
    c.post(f"/api/v1/nodes/{p['id']}/archive", json={"archived": True})
    eff = c.get(f"/api/v1/nodes/{e['id']}/settings").json()
    assert [s["title"] for s in eff["inherited"]] == ["세계관"]
