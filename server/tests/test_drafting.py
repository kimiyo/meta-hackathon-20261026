"""Phase 4: drafting validation, lookups, revisions (fake MetaClient)."""
import json

import pytest

from app import db as dbmod
from app import drafting, memory
from app.errors import LLMStepError


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


class FakeMeta:
    def __init__(self, manuscripts):
        self.manuscripts = list(manuscripts)
        self.calls = []

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens,
                  work_id=None, **_extra):
        self.calls.append(user)
        m = self.manuscripts.pop(0)
        if isinstance(m, tuple):
            text, lookups = m
        else:
            text, lookups = m, []
        return schema.model_validate(
            {"manuscript": text, "lookup_requests": lookups}).model_dump()


def test_draft_saves_revision(conn):
    meta = FakeMeta(["가" * 5000])
    out = drafting.draft_episode(meta, conn, design_id=1, work_id=1, episode_no=1)
    assert out["version"] == 1 and out["length"] == 5000
    out2 = drafting.draft_episode(meta := FakeMeta(["나" * 5000]), conn,
                                  design_id=1, work_id=1, episode_no=1)
    assert out2["version"] == 2
    rows = conn.execute("SELECT version, approval_status FROM manuscript_revisions"
                        " WHERE episode_no=1 ORDER BY version").fetchall()
    assert [(r["version"], r["approval_status"]) for r in rows] == \
        [(1, "superseded"), (2, "draft")]


def test_length_and_constraints_validated(conn):
    assert drafting.validate_manuscript("짧다", [], "3000-5000") != []
    ok = drafting.validate_manuscript("달빛 " + "가" * 3000, ["필수:달빛"],
                                      "3000-5000")
    assert ok == []
    bad = drafting.validate_manuscript("가" * 3000, ["필수:달빛"], "3000-5000")
    assert any("missing required" in p for p in bad)
    bad2 = drafting.validate_manuscript("살인 " + "가" * 3000, ["금지:살인"],
                                        "3000-5000")
    assert any("forbidden" in p for p in bad2)
    with pytest.raises(LLMStepError, match="manuscript_invalid"):
        drafting.draft_episode(FakeMeta(["짧다"] * 4), conn, design_id=1,
                               work_id=1, episode_no=1)


def test_draft_retries_length_miss_with_feedback(conn):
    meta = FakeMeta(["가" * 2544, "가" * 5000])
    out = drafting.draft_episode(meta, conn, design_id=1, work_id=1,
                                 episode_no=1)
    assert out["version"] == 1 and len(meta.calls) == 2
    assert "[분량 교정]" in meta.calls[1] and "2544" in meta.calls[1]


def test_draft_gives_up_after_three_retries(conn):
    meta = FakeMeta(["짧다"] * 4)
    with pytest.raises(LLMStepError, match="manuscript_invalid"):
        drafting.draft_episode(meta, conn, design_id=1, work_id=1,
                               episode_no=1)
    assert len(meta.calls) == 4
    rows = conn.execute("SELECT * FROM manuscript_revisions").fetchall()
    assert rows == []


def test_draft_no_retry_on_schema_error(conn):
    class FailMeta(FakeMeta):
        def chat_json(self, **kw):
            self.calls.append(kw["user"])
            raise LLMStepError("drafting", 1, "schema_error", "bad json")

    meta = FailMeta([])
    with pytest.raises(LLMStepError, match="schema_error"):
        drafting.draft_episode(meta, conn, design_id=1, work_id=1,
                               episode_no=1)
    assert len(meta.calls) == 1


def test_lookup_round_executes_and_injects(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서연", first_episode=1)
    rev = conn.execute("INSERT INTO manuscript_revisions(work_id, episode_no, version, text,"
                       " created_at) VALUES(1,1,1,'x','t')").lastrowid
    sid = memory.store_proposal(conn, episode_no=1, revision_id=rev, work_id=1,
                                items=[
        {"entity_id": "ch_0001", "operation": "set", "attribute_path": "나이",
         "new_value": 26, "evidence_quote": "q", "validation": "ok"}])
    memory.commit_episode(conn, episode_no=1, revision_id=rev, proposal_set_id=sid)
    conn.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal)"
                 " VALUES(1,2,'T','G')")
    conn.commit()
    meta = FakeMeta([
        ("초안 " + "가" * 5000,
         [{"kind": "entity_state", "entity_id": "ch_0001"}]),
        ("확정 " + "나" * 5000, []),
    ])
    out = drafting.draft_episode(meta, conn, design_id=1, work_id=1, episode_no=2)
    assert out["lookups_executed"] == 1
    assert len(meta.calls) == 2
    assert "26" in meta.calls[1]  # injected lookup result
    row = conn.execute("SELECT text FROM manuscript_revisions WHERE episode_no=2"
                       ).fetchone()
    assert row["text"].startswith("확정")


def test_execute_lookups_kinds(conn):
    conn.execute("INSERT INTO events(work_id, episode_no, summary)"
                 " VALUES(1,1,'서점 화재')")
    conn.commit()
    res = drafting.execute_lookups(conn, [
        {"kind": "event_search", "query": "화재"},
        {"kind": "mention_search", "query": "없음"},
        {"kind": "bogus"},
        {"kind": "entity_state", "entity_id": "ch_9999"},
    ], 2)
    assert res[0]["events"][0]["summary"] == "서점 화재"
    assert res[1]["mentions"] == []
    assert res[2]["error"] == "unsupported_lookup"
    assert res[3]["error"] == "not_found"

def test_default_length_target_meets_platform_minimum(conn):
    assert drafting.get_style(conn, 1)["length_target"] == "5000-7000"
    assert drafting.parse_length_target("") == (5000, 7000)
