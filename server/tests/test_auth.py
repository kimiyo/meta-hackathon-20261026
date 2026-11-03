"""Author accounts: register/login, per-author works, advance guard.

 kimiyo claims ownerless (pre-auth) works on registration.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db as dbmod
from app.routes.deps import get_conn


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "auth.db"

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


class DraftFake:
    def chat_json(self, *, bundle, episode, system, user, schema,
                  max_tokens, work_id=None):
        return {"manuscript": "가" * 5000, "lookup_requests": []}


def _register(c, login_id="kimiyo", pen_name="김요", password="pw1234"):
    r = c.post("/api/v1/auth/register",
               json={"login_id": login_id, "pen_name": pen_name,
                     "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def _login(c, login_id="kimiyo", password="pw1234"):
    r = c.post("/api/v1/auth/login",
               json={"login_id": login_id, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _mkwork(c, token, title="작품"):
    r = c.post("/api/v1/works", json={"title": title},
               headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()


def test_status_register_login_roundtrip(client):
    c, _ = client
    assert c.get("/api/v1/auth/status").json() == {
        "authors_exist": False, "logged_in": False,
        "login_id": None, "pen_name": None}
    reg = _register(c)
    assert reg["author"]["login_id"] == "kimiyo"
    assert reg["author"]["pen_name"] == "김요"
    assert "password" not in str(reg).lower().replace("password_hash", "")
    # duplicate id rejected
    r = c.post("/api/v1/auth/register",
               json={"login_id": "kimiyo", "pen_name": "도플",
                     "password": "xxxx"})
    assert r.status_code == 409
    # wrong password rejected
    r = c.post("/api/v1/auth/login",
               json={"login_id": "kimiyo", "password": "nope"})
    assert r.status_code == 401
    token = _login(c)
    me = c.get("/api/v1/auth/me", headers=_h(token)).json()
    assert me["pen_name"] == "김요"
    assert c.get("/api/v1/auth/me").status_code == 401
    st = c.get("/api/v1/auth/status", headers=_h(token)).json()
    assert st == {"authors_exist": True, "logged_in": True,
                  "login_id": "kimiyo", "pen_name": "김요"}
    assert c.post("/api/v1/auth/logout", headers=_h(token)).status_code == 200
    assert c.get("/api/v1/auth/me", headers=_h(token)).status_code == 401


def test_register_validation(client):
    c, _ = client
    assert c.post("/api/v1/auth/register",
                  json={"login_id": "ab", "pen_name": "짧",
                        "password": "pw1234"}).status_code == 400
    assert c.post("/api/v1/auth/register",
                  json={"login_id": "with space", "pen_name": "공백",
                        "password": "pw1234"}).status_code == 400
    assert c.post("/api/v1/auth/register",
                  json={"login_id": "okid", "pen_name": "",
                        "password": "pw1234"}).status_code == 400
    assert c.post("/api/v1/auth/register",
                  json={"login_id": "okid", "pen_name": "짧은비번",
                        "password": "pw"}).status_code == 400


def test_works_are_per_author(client):
    c, _ = client
    _register(c)
    ta = _login(c)
    wa = _mkwork(c, ta, "A작품")
    _register(c, login_id="other", pen_name="타인")
    tb = _login(c, login_id="other")
    assert c.get("/api/v1/works", headers=_h(tb)).json()["works"] == []
    # foreign work key is invisible
    assert c.get(f"/api/v1/works/{wa['uid']}",
                 headers=_h(tb)).status_code == 404
    assert c.patch(f"/api/v1/works/{wa['uid']}", json={"title": "해킹"},
                   headers=_h(tb)).status_code == 404
    got = c.get("/api/v1/works", headers=_h(ta)).json()["works"]
    assert [w["title"] for w in got] == ["A작품"]
    assert c.get("/api/v1/works").status_code == 401


def test_kimiyo_claims_legacy_works(client):
    c, db_path = client
    _register(c, login_id="other", pen_name="타인")
    to = _login(c, login_id="other")
    w = _mkwork(c, to, "예전작품")
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE works SET author_id=NULL WHERE id=?", (w["id"],))
    conn.commit()
    conn.close()
    assert c.get("/api/v1/works", headers=_h(to)).json()["works"] == []
    _register(c)  # kimiyo registers
    tk = _login(c)
    got = c.get("/api/v1/works", headers=_h(tk)).json()["works"]
    assert [x["title"] for x in got] == ["예전작품"]


def test_rename_pen_name_needs_password(client):
    c, _ = client
    _register(c)
    token = _login(c)
    # wrong password keeps the old name
    r = c.patch("/api/v1/auth/me", json={"pen_name": "새필명",
                                         "password": "wrong"},
                headers=_h(token))
    assert r.status_code == 401, r.text
    assert c.get("/api/v1/auth/me", headers=_h(token)).json()["pen_name"] == "김요"
    # correct password renames
    r = c.patch("/api/v1/auth/me", json={"pen_name": "새필명",
                                         "password": "pw1234"},
                headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.json()["pen_name"] == "새필명"
    assert r.json()["login_id"] == "kimiyo"
    assert c.get("/api/v1/auth/me", headers=_h(token)).json()["pen_name"] == "새필명"
    # blank name rejected
    r = c.patch("/api/v1/auth/me", json={"pen_name": "  ",
                                         "password": "pw1234"},
                headers=_h(token))
    assert r.status_code == 400
    assert c.patch("/api/v1/auth/me",
                   json={"pen_name": "x", "password": "pw1234"}).status_code == 401


def test_same_episode_coexists_across_works(client):
    c, _ = client
    from app.main import app
    from app.routes.works import get_meta as works_meta
    app.dependency_overrides[works_meta] = lambda: DraftFake()
    try:
        _register(c)
        ta = _login(c)
        wa = _mkwork(c, ta, "A작품")
        ha = c.post(f"/api/v1/works/{wa['uid']}/handoff", json={},
                    headers=_h(ta)).json()
        r = c.post(f"/api/v1/episodes/1/start?work={wa['uid']}",
                   json={"design_id": ha["design_id"]}, headers=_h(ta))
        assert r.json()["state"] == "WRITING", r.text
        _register(c, login_id="other", pen_name="타인")
        tb = _login(c, login_id="other")
        wb = _mkwork(c, tb, "B작품")
        hb = c.post(f"/api/v1/works/{wb['uid']}/handoff", json={},
                    headers=_h(tb)).json()
        # same episode number in another work coexists (§5.1)
        r = c.post(f"/api/v1/episodes/1/start?work={wb['uid']}",
                   json={"design_id": hb["design_id"]}, headers=_h(tb))
        assert r.json()["state"] == "WRITING", r.text
        # ...but neither author can see the other's episode
        assert c.get(f"/api/v1/episodes/1?work={wa['uid']}",
                     headers=_h(tb)).status_code == 404
        assert c.get(f"/api/v1/episodes/1?work={wb['uid']}",
                     headers=_h(ta)).status_code == 404
        assert c.get(f"/api/v1/episodes/1?work={wb['uid']}",
                     headers=_h(tb)).status_code == 200
        # same work + same number is still a conflict
        r = c.post(f"/api/v1/episodes/1/start?work={wa['uid']}",
                   json={"design_id": ha["design_id"]}, headers=_h(ta))
        assert r.status_code == 409, r.text
    finally:
        del app.dependency_overrides[works_meta]
