"""Consistency analysis: issues, chat, memory draft (fake MetaClient)."""
import pytest

from app import consistency, db as dbmod
from app import drafting


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal)"
              " VALUES(1,1,'T','G')")
    c.execute("INSERT INTO episode_jobs(work_id, episode_no, design_id, state,"
              " attempt, updated_at) VALUES(1,1,1,'IN_REVIEW',0,'t')")
    c.commit()
    yield c
    c.close()


class IssueMeta:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens,
                  work_id=None):
        self.calls.append(user)
        return schema.model_validate(self.payloads.pop(0)).model_dump()


def test_add_and_list_issues(conn):
    saved = consistency.add_issues(conn, 1, [
        {"title": "능력 모순", "severity": "critical",
         "manuscript_quote": "그는 날았다"},
        {"title": "오타", "severity": "bogus"},
    ], 1)["issues"]
    assert len(saved) == 2
    assert consistency.open_count(conn, 1) == 2
    issues = consistency.list_issues(conn, 1)
    assert issues[1]["severity"] == "major"  # unknown severity normalized


def test_reset_review_clears_issues_and_analyses(conn):
    saved = consistency.add_issues(conn, 1, [
        {"title": "열린 이슈"}, {"title": "처리된 이슈"}], 1)["issues"]
    consistency.set_issue_status(conn, saved[1]["issue_id"], "fixed")
    consistency.record_analysis(
        conn, work_id=1, episode_no=1, revision_id=None,
        base_version=0, status="completed", coverage={}, counts={})
    out = consistency.reset_episode_review(conn, 1, 1)
    assert out == {"issues": 2, "analyses": 1}
    assert conn.execute(
        "SELECT COUNT(*) c FROM consistency_issues").fetchone()["c"] == 0
    assert consistency.latest_analysis(conn, 1, 1) is None


def _seed_manuscript(conn, text="첫 문장. 고칠 문장. 끝 문장."):
    cur = conn.execute(
        "INSERT INTO manuscript_revisions(work_id, episode_no, version, text,"
        " created_at) VALUES(1,1,1,?,'t')", (text,))
    conn.execute("UPDATE episode_jobs SET manuscript_revision_id=?"
                 " WHERE work_id=1 AND episode_no=1", (cur.lastrowid,))
    conn.commit()
    return cur.lastrowid


def test_fix_manuscript_issues_happy_path(conn):
    text = "첫 문장. 고칠 문장. 끝 문장."
    rev = _seed_manuscript(conn, text)
    [saved] = consistency.add_issues(conn, 1, [
        {"title": "모순", "manuscript_quote": "고칠 문장"}],
        1, revision_id=rev, manuscript=text)["issues"]
    meta = IssueMeta([{"manuscript": "첫 문장. 고친 문장. 끝 문장."}])
    out = consistency.fix_manuscript_issues(
        meta, conn, episode_no=1, work_id=1,
        issue_ids=[saved["issue_id"]])
    assert out["fixed_issue_ids"] == [saved["issue_id"]]
    job = conn.execute("SELECT state, return_reason FROM episode_jobs"
                       " WHERE work_id=1 AND episode_no=1").fetchone()
    assert job["state"] == "WRITING"
    assert "모순" in job["return_reason"]
    row = conn.execute("SELECT text, label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1"
                       " ORDER BY version DESC LIMIT 1").fetchone()
    assert row["text"] == "첫 문장. 고친 문장. 끝 문장."
    assert "AI 수정" in row["label"]
    assert conn.execute("SELECT status FROM consistency_issues"
                        " WHERE issue_id=?",
                        (saved["issue_id"],)).fetchone()["status"] == "fixed"


def test_fix_manuscript_issues_guards(conn):
    from app.errors import LLMStepError

    text = "첫 문장. 고칠 문장. 끝 문장."
    rev = _seed_manuscript(conn, text)
    [saved] = consistency.add_issues(conn, 1, [
        {"title": "모순", "manuscript_quote": "고칠 문장"}],
        1, revision_id=rev, manuscript=text)["issues"]
    # 리비전이 다르면 AI 호출 없이 거절한다.
    [stale] = consistency.add_issues(conn, 1, [{"title": "낡음"}], 1,
                                     revision_id=rev + 999)["issues"]
    meta = IssueMeta([{"manuscript": "x"}])
    with pytest.raises(ValueError, match="재분석"):
        consistency.fix_manuscript_issues(
            meta, conn, episode_no=1, work_id=1,
            issue_ids=[stale["issue_id"]])
    assert meta.calls == []
    # 빈 수정본이면 실패하고 상태는 그대로 둔다.
    meta = IssueMeta([{"manuscript": "   "}])
    with pytest.raises(LLMStepError):
        consistency.fix_manuscript_issues(
            meta, conn, episode_no=1, work_id=1,
            issue_ids=[saved["issue_id"]])
    assert conn.execute("SELECT state FROM episode_jobs"
                        " WHERE work_id=1 AND episode_no=1").fetchone()[
        "state"] == "IN_REVIEW"
    # 검토중이 아니면 거절한다.
    conn.execute("UPDATE episode_jobs SET state='WRITING'"
                 " WHERE work_id=1 AND episode_no=1")
    conn.commit()
    with pytest.raises(ValueError, match="검토중"):
        consistency.fix_manuscript_issues(
            IssueMeta([{"manuscript": "x"}]), conn, episode_no=1,
            work_id=1, issue_ids=[saved["issue_id"]])


def test_issue_status_flow(conn):
    [saved] = consistency.add_issues(conn, 1, [{"title": "x"}], 1)["issues"]
    out = consistency.set_issue_status(conn, saved["issue_id"], "fixed")
    assert out["status"] == "fixed" and out["resolved_at"]
    assert consistency.open_count(conn, 1) == 0
    out = consistency.set_issue_status(conn, saved["issue_id"], "resolved")
    assert out["status"] == "fixed"  # legacy alias normalized
    out = consistency.set_issue_status(conn, saved["issue_id"], "open")
    assert out["status"] == "open" and out["resolved_at"] is None
    with pytest.raises(ValueError, match="unknown status"):
        consistency.set_issue_status(conn, saved["issue_id"], "bogus")
    with pytest.raises(KeyError):
        consistency.set_issue_status(conn, 999, "resolved")


def test_analyze_skips_without_previous_memory(conn):
    rev = drafting.save_revision(conn, 1, 1, "그는 어제 죽었는데 오늘 웃었다")
    meta = IssueMeta([{"issues": []}])
    out = consistency.analyze_episode(meta, conn, episode_no=1,
                                      revision_id=rev["revision_id"],
                                      work_id=1)
    assert out["skipped"] is True and out["issues"] == []
    assert out["analysis"]["status"] == "skipped"
    assert meta.calls == []  # AI 호출 없음


def test_has_comparison_basis(conn):
    assert consistency.has_comparison_basis(conn, 1, 1) is False
    drafting.save_revision(conn, 1, 1, "첫 회차 원문")
    assert consistency.has_comparison_basis(conn, 1, 1) is False
    assert consistency.has_comparison_basis(conn, 2, 1) is True


def test_analyze_registers_issues(conn):
    drafting.save_revision(conn, 1, 1, "그는 어제 죽었다")
    rev = drafting.save_revision(conn, 1, 2, "그는 어제 죽었는데 오늘 웃었다")
    meta = IssueMeta([{
        "issues": [{"severity": "major", "title": "생사 모순",
                    "detail": "사망 후 등장", "manuscript_quote": "오늘 웃었다",
                    "evidence_episode": None, "evidence_quote": "",
                    "entity_id": ""}],
    }])
    out = consistency.analyze_episode(meta, conn, episode_no=2,
                                      revision_id=rev["revision_id"],
                                      work_id=1)
    assert len(out["issues"]) == 1
    assert consistency.open_count(conn, 2, 1) == 1
    assert "그는 어제 죽었다" in meta.calls[0]  # 직전화 원문 대조


def test_explained_issues_are_accepted(conn):
    [saved] = consistency.add_issues(conn, 1, [{
        "title": "성장", "verdict": "explained",
        "manuscript_quote": "이전보다 강해졌다"}], 1,
        manuscript="이전보다 강해졌다")["issues"]
    assert saved["status"] == "accepted"
    assert saved["resolved_at"] is not None
    assert consistency.open_count(conn, 1, 1) == 0


def test_chat_saves_messages_and_issues(conn):
    rev = drafting.save_revision(conn, 1, 1, "본문")
    meta = IssueMeta([{
        "reply": "모순 없음",
        "issues": [{"severity": "minor", "title": "표기",
                    "manuscript_quote": "본문"}],
    }])
    out = consistency.chat(meta, conn, episode_no=1,
                           revision_id=rev["revision_id"],
                           message="모순 있어?", work_id=1)
    assert out["reply"] == "모순 없음"
    assert len(out["issues"]) == 1
    msgs = consistency.list_messages(conn, 1)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    with pytest.raises(ValueError, match="blank"):
        consistency.chat(meta, conn, episode_no=1,
                         revision_id=rev["revision_id"], message="  ",
                         work_id=1)
