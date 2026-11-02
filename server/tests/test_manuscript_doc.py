"""Manuscript documents: draft autosave, versions, restore, locks."""
import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import db as dbmod
from app import drafting, manuscripts, state_machine
from app.routes.deps import get_conn, get_current_author
from tests.test_drafting import FakeMeta


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal,"
              " constraints_json) VALUES(1,1,'T','G','[]')")
    c.execute("INSERT INTO episode_jobs(episode_no, design_id, state, attempt,"
              " updated_at) VALUES(1,1,'DRAFT',0,'t')")
    c.commit()
    yield c
    c.close()


def _job(conn):
    return state_machine.get_job(conn, 1)


def test_migrate_adds_label_to_old_shape_db(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(str(p))
    c.execute("CREATE TABLE works(id INTEGER PRIMARY KEY, title TEXT,"
              " created_at TEXT, updated_at TEXT, uid TEXT)")
    c.execute("CREATE TABLE setting_entries(id INTEGER PRIMARY KEY,"
              " work_id INTEGER)")
    c.execute("CREATE TABLE manuscript_revisions("
              "revision_id INTEGER PRIMARY KEY, episode_no INTEGER NOT NULL,"
              " version INTEGER NOT NULL, text TEXT NOT NULL,"
              " approval_status TEXT NOT NULL DEFAULT 'draft',"
              " created_at TEXT NOT NULL, UNIQUE (episode_no, version))")
    c.commit()
    c.close()
    # Full connect path: DDL keeps old tables, _migrate adds the column.
    conn = dbmod.connect(p)
    try:
        cols = [r[1] for r in
                conn.execute("PRAGMA table_info(manuscript_revisions)")]
        assert "label" in cols
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"manuscript_docs", "idempotency_keys"} <= tables
    finally:
        conn.close()


def test_doc_empty_shape(conn):
    doc = manuscripts.get_doc(conn, 1, _job(conn), "T")
    assert doc["episodeId"] == "1" and doc["title"] == "T"
    assert doc["revision"] == 0 and doc["draft"] == ""
    assert doc["versions"] == [] and doc["currentVersionId"] is None
    assert doc["approvedVersionId"] is None and doc["state"] == "writing"


def test_save_draft_bumps_revision_and_conflicts(conn):
    job = _job(conn)
    doc = manuscripts.save_draft(conn, 1, job, "T", 0, "초안 일")
    assert doc["revision"] == 1 and doc["draft"] == "초안 일"
    same = manuscripts.save_draft(conn, 1, job, "T", 1, "초안 일")
    assert same["revision"] == 1  # unchanged content: no bump
    with pytest.raises(manuscripts.RevisionConflict) as e:
        manuscripts.save_draft(conn, 1, job, "T", 0, "늦은 쓰기")
    assert e.value.latest == 1
    assert manuscripts.get_doc(conn, 1, job, "T")["draft"] == "초안 일"


def test_save_draft_normalizes_newlines(conn):
    doc = manuscripts.save_draft(conn, 1, _job(conn), "T", 0, "a\r\nb\rc")
    assert doc["draft"] == "a\nb\nc"


def test_save_draft_rejects_bad_types(conn):
    with pytest.raises(ValueError):
        manuscripts.save_draft(conn, 1, _job(conn), "T", "1", "x")
    with pytest.raises(ValueError):
        manuscripts.save_draft(conn, 1, _job(conn), "T", True, "x")
    with pytest.raises(ValueError):
        manuscripts.save_draft(conn, 1, _job(conn), "T", 0, None)


def test_create_version_snapshots_and_adopts(conn):
    job = _job(conn)
    manuscripts.save_draft(conn, 1, job, "T", 0, "손글 원고")
    doc = manuscripts.create_version(conn, 1, job, "T", 1, "req-1",
                                     "손글 원고", "손 저장")
    assert [v["number"] for v in doc["versions"]] == [1]
    assert doc["versions"][0]["label"] == "손 저장"
    assert doc["currentVersionId"] == doc["versions"][0]["id"]
    assert doc["draft"] == "손글 원고" and doc["revision"] == 2
    fresh = state_machine.get_job(conn, 1)
    assert fresh["state"] == "DRAFT"
    assert fresh["manuscript_revision_id"] == int(doc["currentVersionId"])


def test_create_version_defaults_label(conn):
    doc = manuscripts.create_version(conn, 1, _job(conn), "T", 0, "req-1",
                                     "본문")
    assert doc["versions"][0]["label"] == "수동 저장"


def test_create_version_idempotent_retry(conn):
    job = _job(conn)
    first = manuscripts.create_version(conn, 1, job, "T", 0, "req-9", "본문")
    again = manuscripts.create_version(conn, 1, job, "T", 0, "req-9", "본문")
    assert again == first
    rows = conn.execute("SELECT COUNT(*) c FROM manuscript_revisions"
                        " WHERE episode_no=1").fetchone()
    assert rows["c"] == 1


def test_create_version_same_id_different_body_conflicts(conn):
    job = _job(conn)
    manuscripts.create_version(conn, 1, job, "T", 0, "req-9", "본문")
    with pytest.raises(manuscripts.RevisionConflict):
        manuscripts.create_version(conn, 1, job, "T", 1, "req-9", "다른 본문")
    rows = conn.execute("SELECT COUNT(*) c FROM manuscript_revisions"
                        " WHERE episode_no=1").fetchone()
    assert rows["c"] == 1


def test_restore_creates_copy_and_keeps_history(conn):
    job = _job(conn)
    v1 = manuscripts.create_version(conn, 1, job, "T", 0, "r1", "첫 본문")
    id1 = v1["currentVersionId"]
    v2 = manuscripts.create_version(conn, 1, job, "T", 1, "r2", "둘째 본문")
    assert len(v2["versions"]) == 2
    doc = manuscripts.restore_version(conn, 1, job, "T", 2, "r3", id1)
    assert [v["number"] for v in doc["versions"]] == [1, 2, 3]
    assert doc["versions"][2]["content"] == "첫 본문"
    assert doc["versions"][2]["label"] == "v1 복원"
    assert doc["draft"] == "첫 본문"
    assert doc["versions"][1]["content"] == "둘째 본문"  # history intact


def test_restore_unknown_source_raises_key_error(conn):
    with pytest.raises(KeyError):
        manuscripts.restore_version(conn, 1, _job(conn), "T", 0, "r1", "999")
    with pytest.raises(ValueError):
        manuscripts.restore_version(conn, 1, _job(conn), "T", 0, "r1", "nope")


def test_locked_job_rejects_writes_but_reads(conn):
    conn.execute("UPDATE episode_jobs SET state='COMMITTED' WHERE episode_no=1")
    conn.commit()
    job = _job(conn)
    assert manuscripts.get_doc(conn, 1, job, "T")["state"] == "approved"
    with pytest.raises(manuscripts.ManuscriptLocked):
        manuscripts.save_draft(conn, 1, job, "T", 0, "x")
    with pytest.raises(manuscripts.ManuscriptLocked):
        manuscripts.create_version(conn, 1, job, "T", 0, "r", "x")
    with pytest.raises(manuscripts.ManuscriptLocked):
        manuscripts.restore_version(conn, 1, job, "T", 0, "r", "1")


def test_ai_revision_syncs_draft_and_label(conn):
    rev = drafting.save_revision(conn, 1, "AI 원고", label="AI 초안")
    assert rev["version"] == 1
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE revision_id=?", (rev["revision_id"],)).fetchone()
    assert row["label"] == "AI 초안"
    doc = manuscripts.get_doc(conn, 1, _job(conn), "T")
    assert doc["draft"] == "AI 원고" and doc["revision"] == 1
    assert doc["currentVersionId"] == str(rev["revision_id"])


def test_redraft_uses_working_draft_as_base(conn):
    drafting.save_revision(conn, 1, "AI 원고 " + "가" * 5000, label="AI 초안")
    job = _job(conn)
    manuscripts.save_draft(conn, 1, job, "T", 1, "손글 수정 " + "나" * 5000)
    meta = FakeMeta(["새 원고 " + "다" * 5000])
    out = state_machine.request_change(conn, meta, 1, "마무리만 다듬어줘")
    assert out["state"] == "DRAFT"
    assert "손글 수정" in meta.calls[0]  # draft, not the AI revision
    assert "AI 원고" not in meta.calls[0]


def test_redraft_version_base_ignores_working_draft(conn):
    drafting.save_revision(conn, 1, "AI 원고 " + "가" * 5000, label="AI 초안")
    job = _job(conn)
    manuscripts.save_draft(conn, 1, job, "T", 1, "손글 수정 " + "나" * 5000)
    meta = FakeMeta(["새 원고 " + "다" * 5000])
    out = state_machine.request_change(conn, meta, 1, "다듬어줘",
                                       base_source="version")
    assert out["state"] == "DRAFT"
    assert "AI 원고" in meta.calls[0]
    assert "손글 수정" not in meta.calls[0]
    with pytest.raises(ValueError, match="base must be"):
        state_machine.request_change(conn, meta, 1, "x", base_source="bogus")


def test_approve_snapshots_approved_version(conn):
    rev = drafting.save_revision(conn, 1, "확정 원고", label="AI 초안")
    conn.execute("INSERT INTO change_proposals(episode_no, manuscript_revision_id,"
                 " created_at) VALUES(1,?,'t')", (rev["revision_id"],))
    set_id = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
    conn.execute("UPDATE episode_jobs SET state='READY_FOR_REVIEW',"
                 " manuscript_revision_id=?, proposal_set_id=? WHERE episode_no=1",
                 (rev["revision_id"], set_id))
    conn.commit()
    out = state_machine.approve_episode(conn, 1)
    assert out["state"] == "COMMITTED"
    doc = manuscripts.get_doc(conn, 1, _job(conn), "T")
    assert doc["state"] == "approved"
    assert doc["approvedVersionId"] == str(rev["revision_id"])


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "msdoc.db"

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
        seed, login_id="scribe", pen_name="서기", password="pw1234"))
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: author
    yield TestClient(app), db_path
    app.dependency_overrides.clear()


def _mkjob(c, db_path):
    w = c.post("/api/v1/works", json={"title": "w"}).json()
    conn = dbmod.connect(db_path)
    work = conn.execute("SELECT id FROM works WHERE uid=?",
                        (w["uid"],)).fetchone()
    conn.execute("INSERT INTO story_designs(work_id, version, scope_total_episodes,"
                 " concept_json, sections_json, status)"
                 " VALUES(?,1,2,'{}','[]','approved')", (work["id"],))
    did = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
    conn.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal,"
                 " constraints_json) VALUES(? ,1,'제1화','G','[]')", (did,))
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state, attempt,"
                 " updated_at) VALUES(1,?, 'DRAFT',0,'t')", (did,))
    conn.commit()
    conn.close()


def test_doc_api_roundtrip_and_errors(client):
    c, db_path = client
    _mkjob(c, db_path)
    r = c.get("/api/v1/episodes/1/manuscript-doc")
    assert r.status_code == 200, r.text
    assert r.json()["document"]["revision"] == 0
    r = c.patch("/api/v1/episodes/1/manuscript-draft",
                json={"expectedRevision": 0, "content": "초안"})
    assert r.status_code == 200, r.text
    assert r.json()["document"]["revision"] == 1
    r = c.patch("/api/v1/episodes/1/manuscript-draft",
                json={"expectedRevision": 0, "content": "늦은"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "REVISION_CONFLICT"
    r = c.post("/api/v1/episodes/1/manuscript-versions",
               json={"expectedRevision": 1, "requestId": "web-1",
                     "content": "초안", "label": "웹 저장"})
    assert r.status_code == 200, r.text
    doc = r.json()["document"]
    assert doc["versions"][0]["label"] == "웹 저장"
    vid = doc["currentVersionId"]
    r = c.post("/api/v1/episodes/1/manuscript-restore",
               json={"expectedRevision": doc["revision"], "requestId": "web-2",
                     "sourceVersionId": vid})
    assert r.status_code == 200, r.text
    assert len(r.json()["document"]["versions"]) == 2
    r = c.post("/api/v1/episodes/1/manuscript-restore",
               json={"expectedRevision": 0, "requestId": "web-3",
                     "sourceVersionId": "4242"})
    assert r.status_code == 404, r.text


def test_doc_api_locked_returns_423(client):
    c, db_path = client
    _mkjob(c, db_path)
    conn = dbmod.connect(db_path)
    conn.execute("UPDATE episode_jobs SET state='COMMITTED' WHERE episode_no=1")
    conn.commit()
    conn.close()
    r = c.get("/api/v1/episodes/1/manuscript-doc")
    assert r.json()["document"]["state"] == "approved"
    r = c.patch("/api/v1/episodes/1/manuscript-draft",
                json={"expectedRevision": 0, "content": "x"})
    assert r.status_code == 423, r.text


def test_doc_api_requires_job(client):
    c, _ = client
    r = c.get("/api/v1/episodes/77/manuscript-doc")
    assert r.status_code == 404
