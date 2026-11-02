"""Work style policy: length target + voice preset GET/PATCH, budget scaling."""
import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import db as dbmod
from app import drafting
from app.routes.deps import get_conn, get_current_author
from tests.test_drafting import FakeMeta


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "style.db"

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
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: author
    yield TestClient(app), db_path
    app.dependency_overrides.clear()


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


def _mkwork(c):
    return c.post("/api/v1/works", json={"title": "w"}).json()


def test_get_style_returns_default_without_creating_row(client):
    c, db_path = client
    w = _mkwork(c)
    r = c.get(f"/api/v1/works/{w['uid']}/style")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["work_id"] == w["id"]
    assert (body["length_min"], body["length_max"], body["is_default"],
            body["preset"]) == (5000, 7000, True, "fusion-fantasy")
    assert [p["key"] for p in body["presets"]] == [
        "fusion-fantasy", "romance", "mystery", "classic"]
    conn = dbmod.connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM style_policies").fetchall()
        assert rows == []
    finally:
        conn.close()


def test_patch_style_roundtrip(client):
    c, _ = client
    w = _mkwork(c)
    r = c.patch(f"/api/v1/works/{w['uid']}/style",
                json={"length_min": 6000, "length_max": 8000})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["length_min"], body["length_max"], body["is_default"],
            body["preset"]) == (6000, 8000, False, "fusion-fantasy")
    assert "presets" in body
    r = c.get(f"/api/v1/works/{w['uid']}/style")
    assert (r.json()["length_min"], r.json()["length_max"]) == (6000, 8000)


def test_patch_style_validates_range(client):
    c, _ = client
    w = _mkwork(c)
    url = f"/api/v1/works/{w['uid']}/style"
    assert c.patch(url, json={"length_min": 8000,
                              "length_max": 6000}).status_code == 400
    assert c.patch(url, json={"length_min": 5000,
                              "length_max": 10001}).status_code == 400
    assert c.patch(url, json={"length_min": 0,
                              "length_max": 6000}).status_code == 400
    assert c.patch(url, json={"length_min": 5000}).status_code == 400
    assert c.patch(url, json={}).status_code == 400
    assert c.patch(url, json={"preset": "nope"}).status_code == 400
    # Rejected values leave the stored target untouched.
    assert c.get(url).json()["is_default"] is True


def test_patch_style_preserves_other_columns(client):
    c, db_path = client
    w = _mkwork(c)
    conn = dbmod.connect(db_path)
    try:
        conn.execute("INSERT INTO style_policies(work_id, pov, diction,"
                     " length_target, feedback, updated_at)"
                     " VALUES(?,?,?,?,?,?)",
                     (w["id"], "1인칭", "담백", "5000-7000", "짧게", "t"))
        conn.commit()
    finally:
        conn.close()
    r = c.patch(f"/api/v1/works/{w['uid']}/style",
                json={"length_min": 5000, "length_max": 9000})
    assert r.status_code == 200, r.text
    conn = dbmod.connect(db_path)
    try:
        row = dict(conn.execute("SELECT * FROM style_policies"
                                " WHERE work_id=?", (w["id"],)).fetchone())
    finally:
        conn.close()
    assert row["length_target"] == "5000-9000"
    assert (row["pov"], row["diction"], row["feedback"]) == ("1인칭", "담백", "짧게")


def test_patch_preset_switches_voice(client):
    c, db_path = client
    w = _mkwork(c)
    r = c.patch(f"/api/v1/works/{w['uid']}/style", json={"preset": "mystery"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preset"] == "mystery"
    assert (body["length_min"], body["length_max"]) == (5000, 7000)
    conn = dbmod.connect(db_path)
    try:
        row = dict(conn.execute("SELECT * FROM style_policies"
                                " WHERE work_id=?", (w["id"],)).fetchone())
    finally:
        conn.close()
    assert row["style_preset"] == "mystery"
    assert "제한 시점" in row["pov"]
    # Lengths survive a preset-only patch and vice versa.
    r = c.patch(f"/api/v1/works/{w['uid']}/style",
                json={"length_min": 6000, "length_max": 8000})
    assert r.json()["preset"] == "mystery"


def test_legacy_row_adopts_default_preset(conn):
    conn.execute("INSERT INTO style_policies(work_id, length_target, updated_at)"
                 " VALUES(?,?,?)", (1, "5000-7000", "t"))
    conn.commit()
    style = drafting.get_style(conn, 1)
    assert style["style_preset"] == "fusion-fantasy"
    assert "단문 위주" in style["diction"]
    # Custom voice is kept, only the key is backfilled.
    conn.execute("UPDATE style_policies SET style_preset='', pov='내 문체'"
                 " WHERE work_id=1")
    conn.commit()
    assert drafting.get_style(conn, 1)["pov"] == "내 문체"


def test_draft_input_carries_preset_voice(conn):
    drafting.set_style_preset(conn, 1, "romance")
    meta = FakeMeta(["가" * 5000])
    drafting.draft_episode(meta, conn, design_id=1, work_id=1, episode_no=1)
    assert "밀착 시점" in meta.calls[0]


def test_style_missing_work_404(client):
    c, _ = client
    assert c.get("/api/v1/works/99999/style").status_code == 404
    assert c.patch("/api/v1/works/99999/style",
                   json={"length_min": 1, "length_max": 2}).status_code == 404


def test_custom_target_applies_to_next_draft(conn):
    drafting.set_length_target(conn, 1, 500, 600)
    out = drafting.draft_episode(FakeMeta(["가" * 550]), conn, design_id=1,
                                 work_id=1, episode_no=1)
    assert out["length"] == 550


class RecordingMeta(FakeMeta):
    def __init__(self, manuscripts):
        super().__init__(manuscripts)
        self.max_tokens_seen = []

    def chat_json(self, **kw):
        self.max_tokens_seen.append(kw["max_tokens"])
        return super().chat_json(**kw)


def test_output_budget_scales_with_target(conn):
    assert drafting.draft_output_budget(7000) == 13650
    assert drafting.draft_output_budget(10000) == 19500
    assert drafting.draft_output_budget(100) == 8000
    drafting.set_length_target(conn, 1, 9000, 10000)
    meta = RecordingMeta(["가" * 9500])
    drafting.draft_episode(meta, conn, design_id=1, work_id=1, episode_no=1)
    assert meta.max_tokens_seen == [19500]
