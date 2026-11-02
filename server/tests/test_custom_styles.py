"""User-defined writing styles: CRUD, per-author ownership, work apply."""
import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import custom_styles
from app import db as dbmod
from app import drafting
from app.routes.deps import get_conn, get_current_author


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    authmod.register(c, login_id="owner", pen_name="주인", password="pw1234")
    c.commit()
    yield c
    c.close()


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "cstyle.db"

    def _override():
        c = dbmod.connect(db_path)
        try:
            yield c
        finally:
            c.close()

    from app.main import app
    app.dependency_overrides[get_conn] = _override
    seed = dbmod.connect(db_path)
    author = authmod.public_author(authmod.register(
        seed, login_id="stylist", pen_name="스타일", password="pw1234"))
    other = authmod.public_author(authmod.register(
        seed, login_id="otherone", pen_name="타인", password="pw1234"))
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: author
    yield TestClient(app), db_path, author, other
    app.dependency_overrides.clear()


def _mkwork(c):
    return c.post("/api/v1/works", json={"title": "w"}).json()


def test_crud_roundtrip_and_name_order(client):
    c, _, _, _ = client
    for name in ["밤 스타일", "가 스타일"]:
        r = c.post("/api/v1/style-customs",
                   json={"name": name, "pov": "1인칭", "diction": "담백",
                         "feedback": "짧게"})
        assert r.status_code == 200, r.text
    r = c.get("/api/v1/style-customs")
    assert [s["name"] for s in r.json()["custom_styles"]] == ["가 스타일", "밤 스타일"]
    sid = r.json()["custom_styles"][0]["id"]
    r = c.patch(f"/api/v1/style-customs/{sid}", json={"diction": "화려"})
    assert r.json()["diction"] == "화려"
    assert r.json()["pov"] == "1인칭"
    r = c.delete(f"/api/v1/style-customs/{sid}")
    assert r.json() == {"deleted": sid, "works_reset": 0}
    assert c.get("/api/v1/style-customs").json()["custom_styles"] != []


def test_create_validates_name(client):
    c, _, _, _ = client
    assert c.post("/api/v1/style-customs",
                  json={"name": "  "}).status_code == 400
    assert c.post("/api/v1/style-customs",
                  json={"name": "x" * 41}).status_code == 400
    assert c.post("/api/v1/style-customs", json={"name": "유일"}).status_code == 200
    r = c.post("/api/v1/style-customs", json={"name": "유일"})
    assert r.status_code == 409, r.text
    r = c.post("/api/v1/style-customs", json={"name": "둘째"})
    dup = c.patch(f"/api/v1/style-customs/{r.json()['id']}", json={"name": "유일"})
    assert dup.status_code == 409


def test_ownership_is_per_author(client):
    c, _, author, other = client
    from app.main import app
    r = c.post("/api/v1/style-customs", json={"name": "내 것"})
    sid = r.json()["id"]
    app.dependency_overrides[get_current_author] = lambda: other
    try:
        assert c.get("/api/v1/style-customs").json()["custom_styles"] == []
        assert c.patch(f"/api/v1/style-customs/{sid}",
                       json={"pov": "x"}).status_code == 404
        assert c.delete(f"/api/v1/style-customs/{sid}").status_code == 404
        # Same name is fine for another author.
        assert c.post("/api/v1/style-customs",
                      json={"name": "내 것"}).status_code == 200
    finally:
        app.dependency_overrides[get_current_author] = lambda: author


def test_apply_custom_and_switch_back_to_preset(client):
    c, _, _, _ = client
    w = _mkwork(c)
    sid = c.post("/api/v1/style-customs",
                 json={"name": "내 것", "pov": "2인칭", "diction": "담백",
                       "feedback": "짧게"}).json()["id"]
    r = c.patch(f"/api/v1/works/{w['uid']}/style",
                json={"custom_style_id": sid})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset"] is None
    assert (body["custom_style_id"], body["custom_style_name"]) == (sid, "내 것")
    assert len(body["custom_styles"]) == 1
    r = c.patch(f"/api/v1/works/{w['uid']}/style",
                json={"preset": "mystery"})
    body = r.json()
    assert body["preset"] == "mystery"
    assert body["custom_style_id"] is None


def test_patch_rejects_exclusive_and_unknown(client):
    c, _, _, _ = client
    w = _mkwork(c)
    r = c.patch(f"/api/v1/works/{w['uid']}/style",
                json={"preset": "mystery", "custom_style_id": 1})
    assert r.status_code == 400
    r = c.patch(f"/api/v1/works/{w['uid']}/style",
                json={"custom_style_id": 4242})
    assert r.status_code == 404


def test_edit_custom_does_not_rewrite_work(conn):
    row = custom_styles.create_custom(conn, 1, "원본", "옛 시점", "옛 문체", "옛 전개")
    custom_styles.apply_custom(conn, 1, 1, row["id"])
    custom_styles.update_custom(conn, 1, row["id"], pov="새 시점")
    style = drafting.get_style(conn, 1)
    assert style["pov"] == "옛 시점"  # snapshot, not a live link


def test_get_style_keeps_custom_voice(conn):
    row = custom_styles.create_custom(conn, 1, "원본", "내 시점", "내 문체", "내 전개")
    custom_styles.apply_custom(conn, 1, 1, row["id"])
    style = drafting.get_style(conn, 1)
    assert (style["pov"], style["diction"]) == ("내 시점", "내 문체")


def test_delete_resets_using_works(client):
    c, _, _, _ = client
    w = _mkwork(c)
    sid = c.post("/api/v1/style-customs",
                 json={"name": "쓸 것", "pov": "P"}).json()["id"]
    c.patch(f"/api/v1/works/{w['uid']}/style", json={"custom_style_id": sid})
    r = c.delete(f"/api/v1/style-customs/{sid}")
    assert r.json() == {"deleted": sid, "works_reset": 1}
    body = c.get(f"/api/v1/works/{w['uid']}/style").json()
    assert body["preset"] == "fusion-fantasy"
    assert body["custom_style_id"] is None
    assert body["custom_styles"] == []


def test_migrate_adds_custom_style_id(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE works(id INTEGER PRIMARY KEY, title TEXT,"
              " created_at TEXT, updated_at TEXT, uid TEXT)")
    c.execute("CREATE TABLE setting_entries(id INTEGER PRIMARY KEY,"
              " work_id INTEGER)")
    c.execute("CREATE TABLE style_policies(id INTEGER PRIMARY KEY,"
              " work_id INTEGER NOT NULL, pov TEXT DEFAULT '',"
              " diction TEXT DEFAULT '', length_target TEXT DEFAULT '',"
              " feedback TEXT DEFAULT '', style_preset TEXT DEFAULT '',"
              " updated_at TEXT NOT NULL)")
    c.commit()
    c.close()
    conn = dbmod.connect(p)
    try:
        cols = [r[1] for r in
                conn.execute("PRAGMA table_info(style_policies)")]
        assert "custom_style_id" in cols
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "custom_styles" in tables
    finally:
        conn.close()
