"""3-state machine: WRITING → IN_REVIEW → PUBLISHING (fake MetaClient)."""
import pytest

from app import consistency, db as dbmod
from app import drafting, state_machine
from app.errors import LLMStepError
from tests.test_drafting import FakeMeta


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal)"
              " VALUES(1,1,'T','G')")
    c.commit()
    yield c
    c.close()


def _started(conn, ep=1):
    return state_machine.start_writing(
        conn, FakeMeta(["가" * 5000]), ep, design_id=1)


def test_start_creates_writing_job(conn):
    out = _started(conn)
    assert out["state"] == "WRITING"
    assert out["revision"]["version"] == 1
    job = state_machine.get_job(conn, 1)
    assert job["manuscript_revision_id"] == out["revision"]["revision_id"]


def test_start_twice_rejected(conn):
    _started(conn)
    with pytest.raises(ValueError, match="already started"):
        state_machine.start_writing(
            conn, FakeMeta(["가" * 5000]), 1, design_id=1)


def test_start_unknown_design(conn):
    with pytest.raises(KeyError):
        state_machine.start_writing(
            conn, FakeMeta(["가" * 5000]), 1, design_id=99)


def test_failed_start_sets_attention(conn):
    meta = FakeMeta(["짧다"] * 8)
    with pytest.raises(LLMStepError):
        state_machine.start_writing(conn, meta, 1, design_id=1)
    job = state_machine.get_job(conn, 1)
    assert job["state"] == "WRITING" and job["needs_attention"] == 1
    assert "manuscript_invalid" in job["last_error"]


def test_submit_and_back(conn):
    _started(conn)
    out = state_machine.submit_for_review(conn, 1)
    assert out["state"] == "IN_REVIEW"
    out = state_machine.back_to_writing(conn, 1, "문장 다듬기 필요")
    assert out["state"] == "WRITING"
    assert state_machine.get_job(conn, 1)["return_reason"] == "문장 다듬기 필요"


def test_submit_needs_manuscript(conn):
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state,"
                 " attempt, updated_at) VALUES(1,1,'WRITING',0,'t')")
    conn.commit()
    with pytest.raises(ValueError, match="no manuscript"):
        state_machine.submit_for_review(conn, 1)


def test_transitions_reject_wrong_states(conn):
    _started(conn)
    with pytest.raises(ValueError, match="back-to-writing needs"):
        state_machine.back_to_writing(conn, 1)
    with pytest.raises(ValueError, match="publish needs"):
        state_machine.request_publish(conn, 1)
    state_machine.submit_for_review(conn, 1)
    with pytest.raises(ValueError, match="submit needs"):
        state_machine.submit_for_review(conn, 1)


def test_publish_blocked_by_open_issues(conn):
    _started(conn)
    state_machine.submit_for_review(conn, 1)
    consistency.add_issues(conn, 1, [{"title": "모순", "severity": "major"}])
    with pytest.raises(ValueError, match="open consistency issues"):
        state_machine.request_publish(conn, 1)
    assert state_machine.get_job(conn, 1)["state"] == "IN_REVIEW"


def _store_proposal(conn, episode_no, revision_id, items):
    from app import memory
    return memory.store_proposal(conn, episode_no=episode_no,
                                 revision_id=revision_id, items=items)


def test_publish_needs_extraction(conn):
    rev = drafting.save_revision(conn, 1, "확정 원고")
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,'IN_REVIEW',0,?,'t')", (rev["revision_id"],))
    conn.commit()
    with pytest.raises(ValueError, match="Entity 추출을 먼저 실행"):
        state_machine.request_publish(conn, 1)


def test_publish_rejects_stale_extraction(conn):
    rev1 = drafting.save_revision(conn, 1, "구원고")
    _store_proposal(conn, 1, rev1["revision_id"], [{
        "operation": "create", "candidate_name": "서연",
        "evidence_quote": "구원고"}])
    rev2 = drafting.save_revision(conn, 1, "새원고")
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,'IN_REVIEW',0,?,'t')", (rev2["revision_id"],))
    conn.commit()
    with pytest.raises(ValueError, match="재추출이 필요"):
        state_machine.request_publish(conn, 1)


def test_publish_approves_manuscript_and_memory(conn):
    from app import memory
    rev = drafting.save_revision(conn, 1, "서연은 서점주인이다")
    _store_proposal(conn, 1, rev["revision_id"], [{
        "operation": "create", "candidate_name": "서연",
        "attribute_path": "직업", "new_value": "서점주인",
        "evidence_quote": "서연은 서점주인이다"}])
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,'IN_REVIEW',0,?,'t')", (rev["revision_id"],))
    conn.commit()
    out = state_machine.request_publish(conn, 1)
    assert out["state"] == "PUBLISHING"
    assert out["commit"]["applied"] == 1
    assert memory.get_entity(conn, out["commit"]["entities"][0])
    row = conn.execute("SELECT approval_status FROM manuscript_revisions"
                       " WHERE revision_id=?", (rev["revision_id"],)).fetchone()
    assert row["approval_status"] == "approved"


def test_submit_snapshots_dirty_draft(conn):
    from app import manuscripts
    rev = drafting.save_revision(conn, 1, "원고 v1")
    conn.execute("INSERT INTO episode_jobs(episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,'WRITING',0,?,'t')", (rev["revision_id"],))
    conn.commit()
    job = state_machine.get_job(conn, 1)
    doc = manuscripts.get_doc(conn, 1, job, "T")
    manuscripts.save_draft(conn, 1, job, "T", doc["revision"], "손질된 원고")
    out = state_machine.submit_for_review(conn, 1)
    assert out["state"] == "IN_REVIEW"
    assert out.get("snapshot") is True
    fresh = state_machine.get_job(conn, 1)
    assert fresh["manuscript_revision_id"] != rev["revision_id"]
    row = conn.execute("SELECT text, label FROM manuscript_revisions"
                       " WHERE revision_id=?",
                       (fresh["manuscript_revision_id"],)).fetchone()
    assert (row["text"], row["label"]) == ("손질된 원고", "제출 스냅샷")


def test_submit_without_draft_changes_has_no_snapshot(conn):
    _started(conn)
    out = state_machine.submit_for_review(conn, 1)
    assert out["state"] == "IN_REVIEW"
    assert "snapshot" not in out


def test_jobs_by_state(conn):
    _started(conn)
    state_machine.submit_for_review(conn, 1)
    rows = state_machine.jobs_by_state(conn, 1, "IN_REVIEW")
    assert [r["episode_no"] for r in rows] == [1]
    assert rows[0]["title"] == "T"
    with pytest.raises(ValueError, match="unknown state"):
        state_machine.jobs_by_state(conn, 1, "DRAFT")
