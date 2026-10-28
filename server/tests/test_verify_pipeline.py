"""Phase 5: extraction→verification pipeline + VERIFY gating (fake MetaClient).

Deterministic proof of: citation-fail→deferred split, counter-evidence check,
error→DRAFT loop with fix instruction, deferred→NEEDS_AUTHOR, clean→READY.
"""
import pytest

from app import db as dbmod
from app import memory, state_machine
from app.schemas import drafting as dsch
from app.schemas import entity as esch
from app.schemas import verify as vsch


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(1,1,1,3,'{}','[]','approved')")
    for ep in (1, 2):
        c.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal)"
                  " VALUES(1,?,'T','G')", (ep,))
    memory.create_entity(c, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    yield c
    c.close()


MS = "서연은 달빛 서점의 주인이 되었다. " + "가" * 3000


class ScriptedMeta:
    """Routes canned outputs by bundle; validates through real schemas."""

    def __init__(self, entity_items, verdicts, manuscript=MS, quality=None):
        from app.schemas import quality as qsch
        self.entity_items = entity_items
        self.verdicts = verdicts
        self.manuscript = manuscript
        self.quality = quality or {
            "scores": {"immersion": 0.8, "tension": 0.8, "coherence": 0.9},
            "issues": [], "proofread_edits": []}
        self._qsch = qsch

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens):
        if bundle == "drafting":
            return dsch.DraftOutput.model_validate(
                {"manuscript": self.manuscript, "lookup_requests": []}).model_dump()
        if bundle == "entity":
            return esch.EntityOutput.model_validate(
                {"items": self.entity_items, "mentions": [],
                 "events": []}).model_dump()
        if bundle == "verify":
            import json as _json
            payload = _json.loads(user[user.index('{"proposal_items"'):])
            real_ids = [i["item_id"] for i in payload["proposal_items"]]
            verdicts = []
            for v, rid in zip(self.verdicts, real_ids):
                v = dict(v)
                v["item_id"] = rid
                verdicts.append(v)
            return vsch.VerifyOutput.model_validate(
                {"verdicts": verdicts}).model_dump()
        if bundle == "quality":
            return self._qsch.QualityOutput.model_validate(
                self.quality).model_dump()
        raise AssertionError(bundle)


def _item(entity_ref, op, path, new, quote):
    return {"entity_ref": entity_ref, "operation": op, "attribute_path": path,
            "new_value": new, "evidence_quote": quote}


def test_clean_run_reaches_ready(conn):
    meta = ScriptedMeta(
        [_item("ch_0001", "set", "직업", "서점주인", "달빛 서점의 주인이 되었다")],
        [{"item_id": 1, "verdict": "ok", "reason": "신규 설정"}])
    assert state_machine.advance(conn, meta, 1, design_id=1)["state"] == "DRAFT"
    out = state_machine.advance(conn, meta, 1)
    assert out["state"] == "VERIFY"
    assert out["verification"]["counts"] == {"ok": 1}
    out = state_machine.advance(conn, meta, 1)
    assert out["state"] == "READY_FOR_REVIEW"


def test_citation_failure_splits_to_deferred(conn):
    meta = ScriptedMeta(
        [_item("ch_0001", "add", "소지품", "검은 봉투",
               "서연은 검은 봉투를 품에 넣었다")],  # not in manuscript
        [])
    state_machine.advance(conn, meta, 1, design_id=1)
    out = state_machine.advance(conn, meta, 1)
    assert out["extraction"]["unverified"] == 1
    out = state_machine.advance(conn, meta, 1)
    assert out["state"] == "NEEDS_AUTHOR"  # deferred → author review
    items = memory.get_proposals(conn, 1)
    assert items[0]["validation"] == "deferred"


def test_error_loops_to_draft_with_fix(conn):
    meta = ScriptedMeta(
        [_item("ch_0001", "set", "나이", 25, "서연은 달빛 서점의 주인이 되었다")],
        [{"item_id": 1, "verdict": "error", "reason": "나이 충돌"}])
    state_machine.advance(conn, meta, 1, design_id=1)
    state_machine.advance(conn, meta, 1)
    out = state_machine.advance(conn, meta, 1)
    assert out["state"] == "DRAFT" and out["attempt"] == 1
    job = state_machine.get_job(conn, 1)
    assert "모순을 해소" in job["fix_instruction"]
    sets = conn.execute("SELECT status FROM change_proposals").fetchall()
    assert [s["status"] for s in sets] == ["discarded"]


def test_off_spec_operation_dropped(conn):
    meta = ScriptedMeta(
        [_item("ch_0001", "update", "나이", 26, "서연은 달빛 서점의 주인이 되었다")],
        [])
    state_machine.advance(conn, meta, 1, design_id=1)
    out = state_machine.advance(conn, meta, 1)
    assert out["extraction"]["items"] == 0


def test_events_commit_with_participants(conn):
    rev = conn.execute("INSERT INTO manuscript_revisions(episode_no, version, text,"
                       " created_at) VALUES(1,1,'t','t')").lastrowid
    sid = memory.store_proposal(
        conn, episode_no=1, revision_id=rev,
        items=[{"entity_id": "ch_0001", "operation": "set",
                "attribute_path": "직업", "new_value": "서점주인",
                "evidence_quote": "q", "validation": "ok"}],
        events=[{"summary": "서점이 문을 열었다", "story_time": "", "cause": "",
                 "consequence": "",
                 "participants": [{"entity_id": "ch_0001", "role": "주체"}]}])
    r = memory.commit_episode(conn, episode_no=1, revision_id=rev,
                              proposal_set_id=sid)
    assert r["committed"] is True
    evs = conn.execute("SELECT * FROM events").fetchall()
    assert len(evs) == 1 and evs[0]["summary"] == "서점이 문을 열었다"
    parts = conn.execute("SELECT * FROM event_participants").fetchall()
    assert [(p["entity_id"], p["role"]) for p in parts] == [("ch_0001", "주체")]


def test_unverified_counter_evidence_downgrades_error(conn):
    # Approved corpus EP1, then EP2 manuscript with a would-be error.
    meta1 = ScriptedMeta(
        [_item("ch_0001", "set", "나이", 26, "서연은 달빛 서점의 주인이 되었다")],
        [{"item_id": 1, "verdict": "ok", "reason": "신규"}])
    state_machine.advance(conn, meta1, 1, design_id=1)
    state_machine.advance(conn, meta1, 1)
    state_machine.advance(conn, meta1, 1)
    job = state_machine.get_job(conn, 1)
    memory.commit_episode(conn, episode_no=1,
                          revision_id=job["manuscript_revision_id"],
                          proposal_set_id=job["proposal_set_id"])
    meta2 = ScriptedMeta(
        [_item("ch_0001", "set", "나이", 25, "서연은 달빛 서점의 주인이 되었다")],
        [{"item_id": 2, "verdict": "error", "reason": "충돌 주장",
          "counter_evidence_quote": "원문에 없는 가짜 근거"}])
    state_machine.advance(conn, meta2, 2, design_id=1)
    out = state_machine.advance(conn, meta2, 2)
    assert out["verification"]["counts"] == {"deferred": 1}
