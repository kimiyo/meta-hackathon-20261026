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
        conn, FakeMeta(["가" * 5000]), 1, ep, design_id=1)


def test_start_creates_writing_job(conn):
    out = _started(conn)
    assert out["state"] == "WRITING"
    assert out["revision"]["version"] == 1
    job = state_machine.get_job(conn, 1, 1)
    assert job["manuscript_revision_id"] == out["revision"]["revision_id"]


def test_start_twice_rejected(conn):
    _started(conn)
    with pytest.raises(ValueError, match="already started"):
        state_machine.start_writing(
            conn, FakeMeta(["가" * 5000]), 1, 1, design_id=1)


def test_start_unknown_design(conn):
    with pytest.raises(KeyError):
        state_machine.start_writing(
            conn, FakeMeta(["가" * 5000]), 1, 1, design_id=99)


def test_failed_start_sets_attention(conn):
    meta = FakeMeta(["짧다"] * 8)
    with pytest.raises(LLMStepError):
        state_machine.start_writing(conn, meta, 1, 1, design_id=1)
    job = state_machine.get_job(conn, 1, 1)
    assert job["state"] == "WRITING" and job["needs_attention"] == 1
    assert "manuscript_invalid" in job["last_error"]


def test_redraft_regenerates_manuscript(conn):
    _started(conn)
    out = state_machine.redraft_episode(conn, FakeMeta(["나" * 5000]), 1, 1)
    assert out["state"] == "WRITING"
    assert out["revision"]["version"] == 2
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1 AND version=2"
                       ).fetchone()
    assert row["label"] == "AI 재생성"
    job = state_machine.get_job(conn, 1, 1)
    assert job["manuscript_revision_id"] == out["revision"]["revision_id"]
    doc = conn.execute("SELECT draft_content FROM manuscript_docs"
                       " WHERE work_id=1 AND episode_no=1").fetchone()
    assert doc["draft_content"] == "나" * 5000


def test_redraft_keeps_unsaved_draft(conn):
    _started(conn)
    conn.execute("UPDATE manuscript_docs SET draft_content='사용자 초안'"
                 " WHERE work_id=1 AND episode_no=1")
    conn.commit()
    state_machine.redraft_episode(conn, FakeMeta(["나" * 5000]), 1, 1)
    labels = [r["label"] for r in conn.execute(
        "SELECT label FROM manuscript_revisions"
        " WHERE work_id=1 AND episode_no=1 ORDER BY version")]
    assert labels == ["AI 초안", "AI 재생성 전 초안", "AI 재생성"]


def test_redraft_rejected_unless_writing(conn):
    with pytest.raises(KeyError):
        state_machine.redraft_episode(conn, FakeMeta(["나" * 5000]), 1, 1)
    _started(conn)
    state_machine.submit_for_review(conn, 1, 1)
    with pytest.raises(ValueError, match="WRITING"):
        state_machine.redraft_episode(conn, FakeMeta(["나" * 5000]), 1, 1)


def test_chat_revise_saves_new_version(conn):
    _started(conn)
    out = state_machine.chat_revise(conn, FakeMeta(["고친 원고"]), 1, 1,
                                    message="첫 문장을 고쳐줘")
    assert out["state"] == "WRITING"
    assert out["revision"]["version"] == 2
    row = conn.execute("SELECT text, label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1 AND version=2"
                       ).fetchone()
    assert (row["text"], row["label"]) == ("고친 원고", "AI 채팅 수정")
    job = state_machine.get_job(conn, 1, 1)
    assert job["manuscript_revision_id"] == out["revision"]["revision_id"]
    doc = conn.execute("SELECT draft_content FROM manuscript_docs"
                       " WHERE work_id=1 AND episode_no=1").fetchone()
    assert doc["draft_content"] == "고친 원고"


def test_chat_revise_keeps_unsaved_draft(conn):
    _started(conn)
    conn.execute("UPDATE manuscript_docs SET draft_content='사용자 초안'"
                 " WHERE work_id=1 AND episode_no=1")
    conn.commit()
    state_machine.chat_revise(conn, FakeMeta(["고친 원고"]), 1, 1,
                              message="고쳐줘")
    labels = [r["label"] for r in conn.execute(
        "SELECT label FROM manuscript_revisions"
        " WHERE work_id=1 AND episode_no=1 ORDER BY version")]
    assert labels == ["AI 초안", "채팅 수정 전 초안", "AI 채팅 수정"]


def test_chat_revise_guards(conn):
    with pytest.raises(KeyError):
        state_machine.chat_revise(conn, FakeMeta(["x"]), 1, 1, message="m")
    _started(conn)
    with pytest.raises(ValueError, match="지시"):
        state_machine.chat_revise(conn, FakeMeta(["x"]), 1, 1, message="  ")
    state_machine.submit_for_review(conn, 1, 1)
    with pytest.raises(ValueError, match="WRITING"):
        state_machine.chat_revise(conn, FakeMeta(["x"]), 1, 1, message="m")


def test_submit_and_back(conn):
    _started(conn)
    out = state_machine.submit_for_review(conn, 1, 1)
    assert out["state"] == "IN_REVIEW"
    out = state_machine.back_to_writing(conn, 1, 1, "문장 다듬기 필요")
    assert out["state"] == "WRITING"
    assert state_machine.get_job(conn, 1, 1)["return_reason"] == "문장 다듬기 필요"


def test_submit_needs_manuscript(conn):
    conn.execute("INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
                 " attempt, updated_at) VALUES(1,1,1,'WRITING',0,'t')")
    conn.commit()
    with pytest.raises(ValueError, match="no manuscript"):
        state_machine.submit_for_review(conn, 1, 1)


def test_transitions_reject_wrong_states(conn):
    _started(conn)
    with pytest.raises(ValueError, match="back-to-writing needs"):
        state_machine.back_to_writing(conn, 1, 1)
    with pytest.raises(ValueError, match="publish needs"):
        state_machine.request_publish(conn, 1, 1)
    state_machine.submit_for_review(conn, 1, 1)
    with pytest.raises(ValueError, match="submit needs"):
        state_machine.submit_for_review(conn, 1, 1)


def test_publish_blocked_by_open_issues(conn):
    _started(conn)
    state_machine.submit_for_review(conn, 1, 1)
    consistency.add_issues(conn, 1, [{"title": "모순", "severity": "major"}], 1)
    with pytest.raises(ValueError, match="open consistency issues"):
        state_machine.request_publish(conn, 1, 1)
    assert state_machine.get_job(conn, 1, 1)["state"] == "IN_REVIEW"


def _store_proposal(conn, episode_no, revision_id, items):
    from app import memory
    return memory.store_proposal(
        conn, episode_no=episode_no, revision_id=revision_id, items=items,
        work_id=1,
        base_memory_version=memory.current_memory_version(conn, 1))


def test_publish_needs_extraction(conn):
    rev = drafting.save_revision(conn, 1, 1, "확정 원고")
    conn.execute("INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,1,'IN_REVIEW',0,?,'t')", (rev["revision_id"],))
    conn.commit()
    with pytest.raises(ValueError, match="설정 추출을 먼저 실행"):
        state_machine.request_publish(conn, 1, 1)


def test_publish_rejects_stale_extraction(conn):
    rev1 = drafting.save_revision(conn, 1, 1, "구원고")
    _store_proposal(conn, 1, rev1["revision_id"], [{
        "operation": "create", "candidate_name": "서연",
        "evidence_quote": "구원고"}])
    rev2 = drafting.save_revision(conn, 1, 1, "새원고")
    conn.execute("INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,1,'IN_REVIEW',0,?,'t')", (rev2["revision_id"],))
    conn.commit()
    with pytest.raises(ValueError, match="재추출이 필요"):
        state_machine.request_publish(conn, 1, 1)


def _in_review(conn, revision_id):
    conn.execute("INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,1,'IN_REVIEW',0,?,'t')", (revision_id,))
    conn.commit()


def test_publish_auto_commits_fresh_pending(conn):
    from app import memory
    rev = drafting.save_revision(conn, 1, 1, "서연은 서점주인이다")
    _store_proposal(conn, 1, rev["revision_id"], [{
        "operation": "create", "candidate_name": "서연",
        "attribute_path": "직업", "new_value": "서점주인",
        "evidence_quote": "서연은 서점주인이다"}])
    _in_review(conn, rev["revision_id"])
    out = state_machine.request_publish(conn, 1, 1)
    assert out["state"] == "PUBLISHING"
    assert out["commit"]["applied"] == 1
    assert memory.get_entity(conn, out["commit"]["entities"][0])
    row = conn.execute("SELECT approval_status FROM manuscript_revisions"
                       " WHERE revision_id=?", (rev["revision_id"],)).fetchone()
    assert row["approval_status"] == "approved"


def test_publish_with_saved_memory(conn):
    from app import memory
    rev = drafting.save_revision(conn, 1, 1, "서연은 서점주인이다")
    set_id = _store_proposal(conn, 1, rev["revision_id"], [{
        "operation": "create", "candidate_name": "서연",
        "attribute_path": "직업", "new_value": "서점주인",
        "evidence_quote": "서연은 서점주인이다"}])
    memory.save_episode_memory(conn, episode_no=1,
                               revision_id=rev["revision_id"],
                               proposal_set_id=set_id)
    _in_review(conn, rev["revision_id"])
    out = state_machine.request_publish(conn, 1, 1)
    assert out["state"] == "PUBLISHING"
    assert out["commit"].get("already_saved") is True
    row = conn.execute("SELECT COUNT(*) c FROM entity_changes"
                       " WHERE episode_no=1").fetchone()
    assert row["c"] == 1  # no double apply


def test_save_supersedes_stale_memory(conn):
    from app import memory
    rev1 = drafting.save_revision(conn, 1, 1, "서연은 기사다")
    set1 = _store_proposal(conn, 1, rev1["revision_id"], [{
        "operation": "create", "candidate_name": "서연",
        "attribute_path": "직업", "new_value": "기사",
        "evidence_quote": "서연은 기사다"}])
    memory.save_episode_memory(conn, episode_no=1,
                               revision_id=rev1["revision_id"],
                               proposal_set_id=set1)
    rev2 = drafting.save_revision(conn, 1, 1, "서연은 서점주인이다")
    set2 = _store_proposal(conn, 1, rev2["revision_id"], [{
        "operation": "set", "entity_id": "ch_0001",
        "attribute_path": "직업", "new_value": "서점주인",
        "evidence_quote": "서연은 서점주인이다"}])
    out = memory.save_episode_memory(conn, episode_no=1,
                                     revision_id=rev2["revision_id"],
                                     proposal_set_id=set2)
    assert out["committed"] is True
    # Official reads use the active run only; old facts stay as history.
    assert memory.reconstruct_state(conn, "ch_0001", 1) == {"직업": "서점주인"}
    row = conn.execute("SELECT COUNT(*) c FROM entity_changes"
                       " WHERE episode_no=1").fetchone()
    assert row["c"] == 2
    s1 = conn.execute("SELECT status FROM change_proposals"
                      " WHERE proposal_set_id=?", (set1,)).fetchone()
    assert s1["status"] == "superseded"


def test_story_time_flows_to_history(conn):
    from app import memory
    rev = drafting.save_revision(conn, 1, 1, "오늘부로 종료다")
    set_id = _store_proposal(conn, 1, rev["revision_id"], [{
        "operation": "set", "candidate_name": "서연",
        "attribute_path": "근로관계", "new_value": "2026-12-31부로 종료",
        "story_time": "2026-12-31", "evidence_quote": "오늘부로 종료다"}])
    memory.save_episode_memory(conn, episode_no=1,
                               revision_id=rev["revision_id"],
                               proposal_set_id=set_id)
    ent = memory.search_entities(conn, q="서연")[0]
    hist = memory.entity_history(conn, ent["entity_id"])
    assert hist[0]["story_time"] == "2026-12-31"
    assert memory.reconstruct_state(conn, ent["entity_id"], 1) == {
        "근로관계": "2026-12-31부로 종료"}


def test_delete_pending_item(conn):
    from app import memory
    rev = drafting.save_revision(conn, 1, 1, "서연은 서점주인이다")
    set_id = _store_proposal(conn, 1, rev["revision_id"], [
        {"operation": "create", "candidate_name": "서연",
         "evidence_quote": "서연은 서점주인이다"},
        {"operation": "create", "candidate_name": "민준",
         "evidence_quote": "서연은 서점주인이다"}])
    items = [i for i in memory.get_proposals(conn, 1)
             if i["proposal_set_id"] == set_id]
    out = memory.delete_pending_item(conn, items[0]["item_id"])
    assert out["deleted"] == items[0]["item_id"]
    rest = [i for i in memory.get_proposals(conn, 1)
            if i["proposal_set_id"] == set_id]
    assert len(rest) == 1
    with pytest.raises(KeyError):
        memory.delete_pending_item(conn, 999999)


def test_delete_committed_item_rejected(conn):
    from app import memory
    rev = drafting.save_revision(conn, 1, 1, "서연은 서점주인이다")
    set_id = _store_proposal(conn, 1, rev["revision_id"], [{
        "operation": "create", "candidate_name": "서연",
        "evidence_quote": "서연은 서점주인이다"}])
    memory.save_episode_memory(conn, episode_no=1,
                               revision_id=rev["revision_id"],
                               proposal_set_id=set_id)
    item = memory.get_proposals(conn, 1)[0]
    with pytest.raises(ValueError, match="cannot be deleted"):
        memory.delete_pending_item(conn, item["item_id"])


def test_extract_empty_result_is_successful_empty(conn):
    from app import extraction
    from tests.test_consistency import IssueMeta
    rev = drafting.save_revision(conn, 1, 1, "본문")
    meta = IssueMeta([{"items": [], "mentions": [], "events": []}])
    out = extraction.extract_changes(meta, conn, episode_no=1,
                                     revision_id=rev["revision_id"],
                                     manuscript="본문", work_id=1)
    assert out["empty"] is True
    row = conn.execute("SELECT status FROM change_proposals").fetchone()
    assert row["status"] == "pending"  # 성공한 빈 실행으로 저장


def test_extract_inherits_client_default_max_tokens(conn):
    from app import extraction

    class CapMeta:
        def __init__(self):
            self.seen = []

        def chat_json(self, *, bundle, episode, system, user, schema,
                      max_tokens, work_id=None):
            self.seen.append(max_tokens)
            return schema.model_validate(
                {"items": [], "mentions": [], "events": []}).model_dump()

    rev = drafting.save_revision(conn, 1, 1, "본문")
    meta = CapMeta()
    extraction.extract_changes(meta, conn, episode_no=1,
                               revision_id=rev["revision_id"],
                               manuscript="본문", work_id=1)
    extraction.extract_changes(meta, conn, episode_no=1,
                               revision_id=rev["revision_id"],
                               manuscript="본문", work_id=1,
                               max_tokens=1234)
    assert meta.seen == [None, 1234]


def test_extract_passes_author_instruction(conn):
    from app import extraction
    from tests.test_consistency import IssueMeta
    rev = drafting.save_revision(conn, 1, 1, "본문")
    meta = IssueMeta([{"items": [{"entity_ref": "new:서연",
                                  "operation": "create",
                                  "evidence_quote": "본문"}],
                       "mentions": [], "events": []}])
    extraction.extract_changes(meta, conn, episode_no=1,
                               revision_id=rev["revision_id"],
                               manuscript="본문", work_id=1,
                               author_instruction="직업을 고쳐줘")
    assert "직업을 고쳐줘" in meta.calls[0]


def test_submit_snapshots_dirty_draft(conn):
    from app import manuscripts
    rev = drafting.save_revision(conn, 1, 1, "원고 v1")
    conn.execute("INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
                 " attempt, manuscript_revision_id, updated_at)"
                 " VALUES(1,1,1,'WRITING',0,?,'t')", (rev["revision_id"],))
    conn.commit()
    job = state_machine.get_job(conn, 1, 1)
    doc = manuscripts.get_doc(conn, 1, 1, job, "T")
    manuscripts.save_draft(conn, 1, 1, job, "T", doc["revision"], "손질된 원고")
    out = state_machine.submit_for_review(conn, 1, 1)
    assert out["state"] == "IN_REVIEW"
    assert out.get("snapshot") is True
    fresh = state_machine.get_job(conn, 1, 1)
    assert fresh["manuscript_revision_id"] != rev["revision_id"]
    row = conn.execute("SELECT text, label FROM manuscript_revisions"
                       " WHERE revision_id=?",
                       (fresh["manuscript_revision_id"],)).fetchone()
    assert (row["text"], row["label"]) == ("손질된 원고", "제출 스냅샷")


def test_submit_without_draft_changes_has_no_snapshot(conn):
    _started(conn)
    out = state_machine.submit_for_review(conn, 1, 1)
    assert out["state"] == "IN_REVIEW"
    assert "snapshot" not in out


def test_jobs_by_state(conn):
    _started(conn)
    state_machine.submit_for_review(conn, 1, 1)
    rows = state_machine.jobs_by_state(conn, 1, "IN_REVIEW")
    assert [r["episode_no"] for r in rows] == [1]
    assert rows[0]["title"] == "T"
    with pytest.raises(ValueError, match="unknown state"):
        state_machine.jobs_by_state(conn, 1, "DRAFT")
