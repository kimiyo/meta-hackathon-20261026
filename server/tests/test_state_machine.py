"""Phase 4: state machine PLAN→DRAFT (fake MetaClient, no AI calls)."""
import pytest

from app import db as dbmod
from app import drafting, state_machine
from app.errors import LLMStepError
from tests.test_drafting import FakeMeta


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(2,1,2,2,'{}','[]','draft')")
    c.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal)"
              " VALUES(1,1,'T','G')")
    c.commit()
    yield c
    c.close()


def test_plan_to_draft(conn):
    out = state_machine.advance(conn, FakeMeta(["가" * 5000]), 1, design_id=1)
    assert out["state"] == "DRAFT"
    assert out["revision"]["version"] == 1
    job = state_machine.get_job(conn, 1)
    assert job["manuscript_revision_id"] == out["revision"]["revision_id"]


def test_draft_design_advances_without_approval(conn):
    conn.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal)"
                 " VALUES(2,1,'T','G')")
    conn.commit()
    out = state_machine.advance(conn, FakeMeta(["가" * 5000]), 1, design_id=2)
    assert out["state"] == "DRAFT"


def test_repeat_failed_advance_consumes_attempt(conn):
    meta = FakeMeta(["짧다"] * 8)
    for _ in range(2):
        with pytest.raises(LLMStepError):
            state_machine.advance(conn, meta, 1, design_id=1)
    job = state_machine.get_job(conn, 1)
    assert job["state"] == "PLAN" and job["attempt"] == 2


def test_invalid_manuscript_records_error_and_retries(conn):
    meta = FakeMeta(["짧다"] * 4)
    with pytest.raises(LLMStepError, match="manuscript_invalid"):
        state_machine.advance(conn, meta, 1, design_id=1)
    job = state_machine.get_job(conn, 1)
    assert job["state"] == "PLAN" and job["attempt"] == 1
    assert "manuscript_invalid" in job["last_error"]


def _job_with_proposals(conn, state, attempt):
    rev = drafting.save_revision(conn, 1, "가" * 5000)
    conn.execute("INSERT INTO change_proposals(episode_no,"
                 " manuscript_revision_id, created_at) VALUES(1,?,'t')",
                 (rev["revision_id"],))
    sid = conn.execute("SELECT last_insert_rowid() i").fetchone()["i"]
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, proposal_set_id,"
                 " last_error, updated_at)"
                 " VALUES(1,1,?,?,?,?,?,'t')",
                 (state, attempt, rev["revision_id"], sid, "err"))
    conn.commit()
    return sid


def test_revert_verify_discards_set_and_keeps_attempt(conn):
    sid = _job_with_proposals(conn, "VERIFY", 1)
    out = state_machine.revert_to_draft(conn, 1)
    assert out["state"] == "DRAFT" and out["attempt"] == 1
    job = state_machine.get_job(conn, 1)
    assert job["proposal_set_id"] is None and job["last_error"] == ""
    row = conn.execute("SELECT status FROM change_proposals"
                       " WHERE proposal_set_id=?", (sid,)).fetchone()
    assert row["status"] == "discarded"


def test_revert_needs_author_restarts_budget(conn):
    _job_with_proposals(conn, "NEEDS_AUTHOR", 3)
    out = state_machine.revert_to_draft(conn, 1)
    assert out["state"] == "DRAFT" and out["attempt"] == 0


def test_revert_rejects_other_states(conn):
    with pytest.raises(KeyError):
        state_machine.revert_to_draft(conn, 9)
    for st in ("DRAFT", "READY_FOR_REVIEW", "COMMITTED"):
        conn.execute("INSERT OR REPLACE INTO episode_jobs(episode_no,"
                     " design_id, state, attempt, updated_at)"
                     " VALUES(1,1,?,0,'t')", (st,))
        conn.commit()
        with pytest.raises(ValueError, match="revert needs"):
            state_machine.revert_to_draft(conn, 1)


def test_retry_limit_escalates_to_needs_author(conn):
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state, attempt,"
                 " updated_at) VALUES(1,1,'PLAN',3,'t')")
    conn.commit()
    out = state_machine.advance(conn, FakeMeta(["가" * 3000]), 1)
    assert out["state"] == "NEEDS_AUTHOR"
