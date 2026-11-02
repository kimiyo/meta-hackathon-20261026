"""Phase 6: quality gate, approve/request-change, budget escalation."""
import pytest

from app import db as dbmod
from app import drafting, memory, quality, state_machine
from app.errors import BudgetExceeded
from tests.test_verify_pipeline import MS, ScriptedMeta, _item


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("INSERT INTO story_designs(id, work_id, version, scope_total_episodes,"
              " concept_json, sections_json, status) VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO episode_outlines(design_id, episode_no, title, goal)"
              " VALUES(1,1,'T','G')")
    memory.create_entity(c, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    yield c
    c.close()


class QFake:
    def __init__(self, payload):
        self.payload = payload

    def chat_json(self, **kw):
        from app.schemas import quality as qsch
        return qsch.QualityOutput.model_validate(self.payload).model_dump()


def _rev(conn, text):
    return conn.execute("INSERT INTO manuscript_revisions(episode_no, version, text,"
                        " created_at) VALUES(1,1,?,'t')", (text,)).lastrowid


def test_proofread_applies_and_gates(conn):
    text = "서연은 장부를 폈다." + "가" * 3000
    meta = QFake({"scores": {"immersion": 0.9, "tension": 0.9, "coherence": 0.9},
                  "issues": [],
                  "proofread_edits": [
                      {"start_quote": "장부를 폈다", "replacement": "장부를 펼쳤다",
                       "reason": "오타"},
                      {"start_quote": "없는 문장", "replacement": "x",
                       "reason": "무시됨"}]})
    out = quality.run_quality(meta, conn, episode_no=1, revision_id=_rev(conn, text),
                              manuscript=text, outline={"title": "T"})
    assert out["edits_applied"] == 1 and out["gate_pass"] is True
    assert "장부를 펼쳤다" in out["manuscript"]
    assert out["new_revision"]["version"] == 2


def test_low_coherence_fails_gate(conn):
    meta = QFake({"scores": {"immersion": 0.5, "tension": 0.5, "coherence": 0.2},
                  "issues": ["긴장 없음"], "proofread_edits": []})
    out = quality.run_quality(meta, conn, episode_no=1,
                              revision_id=_rev(conn, "가" * 3000),
                              manuscript="가" * 3000, outline={})
    assert out["gate_pass"] is False
    assert "긴장 없음" in quality.build_quality_fix(out["issues"], out["scores"])


def test_quality_fail_returns_to_draft(conn):
    meta = ScriptedMeta(
        [_item("ch_0001", "set", "직업", "서점주인", "달빛 서점의 주인이 되었다")],
        [{"item_id": 1, "verdict": "ok", "reason": "신규"}],
        quality={"scores": {"immersion": 0.5, "tension": 0.5, "coherence": 0.1},
                 "issues": ["흐름 끊김"], "proofread_edits": []})
    state_machine.advance(conn, meta, 1, design_id=1)
    state_machine.advance(conn, meta, 1)
    out = state_machine.advance(conn, meta, 1)
    assert out["state"] == "DRAFT"
    job = state_machine.get_job(conn, 1)
    assert "흐름 끊김" in job["fix_instruction"]


def test_approve_commits_atomically(conn):
    meta = ScriptedMeta(
        [_item("ch_0001", "set", "직업", "서점주인", "달빛 서점의 주인이 되었다")],
        [{"item_id": 1, "verdict": "ok", "reason": "신규"}])
    state_machine.advance(conn, meta, 1, design_id=1)
    state_machine.advance(conn, meta, 1)
    assert state_machine.advance(conn, meta, 1)["state"] == "READY_FOR_REVIEW"
    out = state_machine.approve_episode(conn, 1)
    assert out["state"] == "COMMITTED"
    assert out["commit"]["applied"] == 1
    assert memory.reconstruct_state(conn, "ch_0001", 1) == {"직업": "서점주인"}
    with pytest.raises(ValueError):
        state_machine.approve_episode(conn, 1)  # already committed


def test_request_change_returns_to_draft(conn):
    meta = ScriptedMeta(
        [_item("ch_0001", "set", "직업", "서점주인", "달빛 서점의 주인이 되었다")],
        [{"item_id": 1, "verdict": "ok", "reason": "신규"}])
    state_machine.advance(conn, meta, 1, design_id=1)
    state_machine.advance(conn, meta, 1)
    state_machine.advance(conn, meta, 1)
    out = state_machine.request_change(conn, meta, 1, "직업을 바꿔줘")
    assert out["state"] == "DRAFT"
    assert out["revision"]["version"] == 2  # immediate redraft
    job = state_machine.get_job(conn, 1)
    assert job["fix_instruction"] == ""  # consumed immediately
    assert job["proposal_set_id"] is None
    revs = conn.execute("SELECT COUNT(*) c FROM manuscript_revisions").fetchone()["c"]
    assert revs == 2
    hist = drafting.list_draft_requests(conn, 1)
    assert len(hist) == 1 and hist[0]["result_version"] == 2


def test_budget_exceeded_escalates(conn):
    class BrokeMeta:
        def chat_json(self, **kw):
            raise BudgetExceeded("over")

    out = state_machine.advance(conn, BrokeMeta(), 1, design_id=1)
    assert out["state"] == "NEEDS_AUTHOR"
    assert "budget" in out["last_error"]
