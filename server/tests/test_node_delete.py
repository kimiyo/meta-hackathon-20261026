"""Node hard delete: subtree cascade, owned cleanup, root guard."""
import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import db as dbmod
from app.routes.deps import get_conn, get_current_author


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "nodedelete.db"

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


def _mkwork(c, title="검과 마법"):
    r = c.post("/api/v1/works", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()


def _handoff(c, uid):
    r = c.post(f"/api/v1/works/{uid}/handoff", json={})
    assert r.status_code == 200, r.text
    return r.json()


def test_delete_leaf_removes_settings_and_outline(client):
    c, _ = client
    w = _mkwork(c)
    h = _handoff(c, w["uid"])
    did = h["design_id"]
    c.post(f"/api/v1/nodes/E001/settings",
           json={"category": "character", "title": "검"})
    assert len(c.get(f"/api/v1/designs/{did}").json()["outlines"]) == 25
    r = c.delete("/api/v1/nodes/E001")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": ["E001"], "threads": 0}
    assert c.get("/api/v1/nodes/E001").status_code == 404
    assert c.get("/api/v1/nodes/E001/settings").status_code == 404
    outlines = c.get(f"/api/v1/designs/{did}").json()["outlines"]
    assert len(outlines) == 24
    assert all(o["episode_no"] != 1 for o in outlines)
    # siblings untouched
    assert c.get("/api/v1/nodes/E002").status_code == 200


def test_delete_branch_cascades(client):
    c, _ = client
    w = _mkwork(c)
    h = _handoff(c, w["uid"])
    did, root = h["design_id"], h["root_node"]["id"]
    p = c.post(f"/api/v1/designs/{did}/nodes",
               json={"kind": "part", "parent_id": root,
                     "title": "1부"}).json()
    e = c.post(f"/api/v1/designs/{did}/nodes",
               json={"kind": "episode", "parent_id": p["id"],
                     "title": "1화"}).json()
    c.post(f"/api/v1/nodes/{p['id']}/settings",
           json={"category": "event", "title": "사건"})
    r = c.delete(f"/api/v1/nodes/{p['id']}")
    assert r.status_code == 200, r.text
    assert set(r.json()["deleted"]) == {p["id"], e["id"]}
    assert c.get(f"/api/v1/nodes/{p['id']}").status_code == 404
    assert c.get(f"/api/v1/nodes/{e['id']}").status_code == 404
    assert c.get(f"/api/v1/nodes/{root}").status_code == 200


def test_delete_guards(client):
    c, _ = client
    w = _mkwork(c)
    h = _handoff(c, w["uid"])
    root = h["root_node"]["id"]
    assert c.delete(f"/api/v1/nodes/{root}").status_code == 400
    assert c.get(f"/api/v1/nodes/{root}").status_code == 200
    assert c.delete("/api/v1/nodes/ZZZ").status_code == 404
