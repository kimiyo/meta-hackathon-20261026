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


def test_analyze_registers_issues(conn):
    rev = drafting.save_revision(conn, 1, 1, "그는 어제 죽었는데 오늘 웃었다")
    meta = IssueMeta([{
        "issues": [{"severity": "major", "title": "생사 모순",
                    "detail": "사망 후 등장", "manuscript_quote": "오늘 웃었다",
                    "evidence_episode": None, "evidence_quote": "",
                    "entity_id": ""}],
    }])
    out = consistency.analyze_episode(meta, conn, episode_no=1,
                                      revision_id=rev["revision_id"],
                                      work_id=1)
    assert len(out["issues"]) == 1
    assert consistency.open_count(conn, 1) == 1


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
