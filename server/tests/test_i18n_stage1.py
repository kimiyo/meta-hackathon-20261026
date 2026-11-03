"""Stage-1 i18n contracts: registry, locales API, works market/locale,
prompt loader, migration idempotence."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import db as dbmod
from app import locales, prompt_loader
from app.routes.deps import get_conn, get_current_author


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "i18n.db")
    yield c
    c.close()


@pytest.fixture()
def api_client(tmp_path):
    db_path = tmp_path / "i18n_api.db"

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
        seed, login_id="i18n", pen_name="i18n-tester", password="pw1234"))
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: _author
    yield TestClient(app)
    app.dependency_overrides.clear()


# --- registry ---

def test_registry_contents():
    assert set(locales.MARKETS) == {"KR", "US"}
    assert locales.MARKETS["KR"]["default_writing_locale"] == "ko-KR"
    assert locales.MARKETS["US"]["default_writing_locale"] == "en-US"
    assert locales.MARKETS["KR"]["default_length_unit"] == "characters"
    assert locales.MARKETS["US"]["default_length_unit"] == "words"
    assert locales.MARKETS["KR"]["preset_catalog"] == "kr"
    assert locales.MARKETS["US"]["preset_catalog"] == "us"
    assert locales.UI_LOCALES == ["ko-KR", "en-US"]


def test_registry_validation():
    assert locales.validate_market("kr") == "KR"
    assert locales.validate_writing_locale("en-US") == "en-US"
    assert locales.validate_ui_locale("ko-KR") == "ko-KR"
    for bad_call in (lambda: locales.validate_market("GB"),
                     lambda: locales.validate_market(""),
                     lambda: locales.validate_writing_locale("ja-JP"),
                     lambda: locales.validate_ui_locale("en-GB")):
        with pytest.raises(ValueError, match="[Uu]nknown"):
            bad_call()


# --- locales API ---

def test_locales_api_shape(api_client):
    r = api_client.get("/api/v1/locales")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "markets": [
            {"code": "KR", "name_key": "market.KR",
             "default_ui_locale": "ko-KR",
             "default_writing_locale": "ko-KR"},
            {"code": "US", "name_key": "market.US",
             "default_ui_locale": "en-US",
             "default_writing_locale": "en-US"},
        ],
        "ui_locales": ["ko-KR", "en-US"],
    }


# --- works market/locale/platform ---

def test_create_defaults_to_kr(api_client):
    r = api_client.post("/api/v1/works", json={"title": "KR-Default"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market"] == "KR"
    assert body["writing_locale"] == "ko-KR"
    assert body["platform"] == "문피아"


def test_create_us_stores_empty_platform(api_client):
    r = api_client.post("/api/v1/works",
                        json={"title": "US", "market": "US",
                              "writing_locale": "en-US"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["market"] == "US"
    assert body["writing_locale"] == "en-US"
    assert body["platform"] == ""
    assert body["platform"] != "문피아"


def test_create_rejects_unknown(api_client):
    r = api_client.post("/api/v1/works",
                        json={"title": "x", "market": "GB"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UNKNOWN_MARKET"
    r = api_client.post("/api/v1/works",
                        json={"title": "x", "writing_locale": "ja-JP"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "UNKNOWN_WRITING_LOCALE"


def test_patch_locale_fresh_work_allowed(api_client):
    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    r = api_client.patch(f"/api/v1/works/{wid}",
                         json={"market": "US", "writing_locale": "en-US"})
    assert r.status_code == 200, r.text
    assert r.json()["market"] == "US"
    assert r.json()["writing_locale"] == "en-US"


def test_patch_locale_locked_after_content(api_client, tmp_path):
    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    # Same-value patch is a no-op, not a change.
    r = api_client.patch(f"/api/v1/works/{wid}", json={"market": "KR"})
    assert r.status_code == 400  # no valid work fields
    # Add design content directly, then a real change must be refused.
    db_path = tmp_path / "i18n_api.db"
    c = dbmod.connect(db_path)
    c.execute("INSERT INTO story_designs(work_id, version,"
              " scope_total_episodes, concept_json, sections_json)"
              " VALUES(?,1,20,'{}','{}')", (wid,))
    c.commit()
    c.close()
    r = api_client.patch(f"/api/v1/works/{wid}", json={"market": "US"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "WORK_LOCALE_LOCKED"


def test_patch_platform_empty_allowed(api_client):
    wid = api_client.post("/api/v1/works", json={"title": "w"}).json()["id"]
    r = api_client.patch(f"/api/v1/works/{wid}", json={"platform": ""})
    assert r.status_code == 200, r.text
    assert r.json()["platform"] == ""


# --- can_change_locale ---

def test_can_change_locale(conn):
    cur = conn.execute(
        "INSERT INTO works(title, logline, created_at, updated_at)"
        " VALUES('w','','t','t')")
    wid = cur.lastrowid
    assert locales.can_change_locale(conn, wid) == (True, "")
    # Lazily created empty doc row does not lock.
    conn.execute("INSERT INTO manuscript_docs(work_id, episode_no,"
                 " draft_content, revision, updated_at)"
                 " VALUES(?,1,'',0,'t')", (wid,))
    assert locales.can_change_locale(conn, wid) == (True, "")
    # Real content locks.
    conn.execute("INSERT INTO idea_threads(work_id, title, created_at)"
                 " VALUES(?,'t','t')", (wid,))
    allowed, reason = locales.can_change_locale(conn, wid)
    assert allowed is False and reason.startswith("idea_threads")


def test_can_change_locale_design_and_manuscript(conn):
    cur = conn.execute(
        "INSERT INTO works(title, logline, created_at, updated_at)"
        " VALUES('w','','t','t')")
    wid = cur.lastrowid
    conn.execute("INSERT INTO story_designs(work_id, version,"
                 " scope_total_episodes, concept_json, sections_json)"
                 " VALUES(?,1,20,'{}','{}')", (wid,))
    assert locales.can_change_locale(conn, wid)[0] is False
    conn.execute("DELETE FROM story_designs WHERE work_id=?", (wid,))
    conn.execute("INSERT INTO manuscript_revisions(work_id, episode_no,"
                 " version, text, created_at) VALUES(?,1,1,'x','t')", (wid,))
    assert locales.can_change_locale(conn, wid)[0] is False


# --- prompt loader ---

def _prompt_tree(tmp_path):
    base = tmp_path / "prompts"
    (base / "ko-KR").mkdir(parents=True)
    (base / "en-US").mkdir(parents=True)
    (base / "markets" / "US").mkdir(parents=True)
    (base / "ko-KR" / "drafting.txt").write_text("KOREAN", encoding="utf-8")
    (base / "en-US" / "drafting.txt").write_text("ENGLISH", encoding="utf-8")
    (base / "markets" / "US" / "drafting.txt").write_text("US-NOTE",
                                                          encoding="utf-8")
    (base / "legacy_only.txt").write_text("LEGACY", encoding="utf-8")
    return base


def test_loader_locale_and_overlay(tmp_path):
    base = _prompt_tree(tmp_path)
    b = prompt_loader.load("drafting", "en-US", "US", base_dir=base)
    assert b.text == "ENGLISH\n\nUS-NOTE"
    assert b.overlay_path is not None and b.legacy_fallback is False
    b = prompt_loader.load("drafting", "en-US", "KR", base_dir=base)
    assert b.text == "ENGLISH" and b.overlay_path is None


def test_loader_legacy_ko_only(tmp_path):
    base = _prompt_tree(tmp_path)
    b = prompt_loader.load("legacy_only", "ko-KR", "KR", base_dir=base)
    assert b.text == "LEGACY" and b.legacy_fallback is True
    with pytest.raises(FileNotFoundError) as e:
        prompt_loader.load("legacy_only", "en-US", "US", base_dir=base)
    assert "legacy_only" in str(e.value) and "en-US" in str(e.value)


def test_loader_missing_and_traversal(tmp_path):
    base = _prompt_tree(tmp_path)
    with pytest.raises(FileNotFoundError):
        prompt_loader.load("nope", "en-US", "US", base_dir=base)
    with pytest.raises(ValueError):
        prompt_loader.load("../secret", "en-US", "US", base_dir=base)


# --- migration ---

def test_migration_idempotent(conn, tmp_path):
    conn.execute("INSERT INTO works(title, logline, created_at, updated_at)"
                 " VALUES('w','','t','t')")
    conn.commit()
    dbmod._migrate(conn)
    dbmod._migrate(conn)
    row = conn.execute("SELECT market, writing_locale, platform FROM works"
                       ).fetchone()
    assert (row["market"], row["writing_locale"], row["platform"]) == (
        "KR", "ko-KR", "문피아")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(llm_calls)")]
    for c in ("market", "writing_locale", "prompt_feature", "prompt_version",
              "preset_id", "preset_version"):
        assert c in cols


def test_migration_legacy_db(tmp_path):
    legacy = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(legacy))
    raw.execute("CREATE TABLE works (id INTEGER PRIMARY KEY,"
                " author_id INTEGER, title TEXT NOT NULL,"
                " logline TEXT DEFAULT '', created_at TEXT NOT NULL)")
    raw.execute("INSERT INTO works(author_id, title, logline, created_at)"
                " VALUES(1,'old','L','t')")
    raw.commit()
    raw.close()
    c = dbmod.connect(legacy)
    row = c.execute("SELECT * FROM works").fetchone()
    assert row["title"] == "old" and row["logline"] == "L"
    assert row["market"] == "KR" and row["writing_locale"] == "ko-KR"
    assert row["platform"] == "문피아"
    assert c.execute("SELECT COUNT(*) c FROM works").fetchone()["c"] == 1
    c.close()
    # Reopen: stable, no duplicate migration.
    c2 = dbmod.connect(legacy)
    row2 = c2.execute("SELECT market, writing_locale FROM works").fetchone()
    assert (row2["market"], row2["writing_locale"]) == ("KR", "ko-KR")
    c2.close()
