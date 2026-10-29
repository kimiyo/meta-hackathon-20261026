"""Phase 3: planning contracts (valid/invalid samples) + orchestration with
a fake MetaClient (no AI calls). Covers 10-split merge and validation."""
import pytest

from app import db as dbmod
from app import planning
from app.errors import LLMStepError
from app.schemas import planning as sch


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    yield c
    c.close()


def _concept():
    return {"concept": {"title_candidates": ["달빛 서점"], "genre": "판타지",
                        "tone": "서정적", "theme": "기억", "ending": "장서를 닫는다"},
            "sections": [{"range": "1-10", "summary": "시작"}],
            "warnings": []}


def _batch(start, end):
    return {"episode_outlines": [
        {"episode_no": n, "title": f"제목{n}", "goal": f"목표{n}",
         "events": [f"사건{n}"], "char_changes": [], "foreshadowing": [],
         "links": {}, "constraints": []} for n in range(start, end + 1)],
        "warnings": []}


class FakeMeta:
    def __init__(self, total):
        self.total = total
        self.calls = 0

    def chat_json(self, *, bundle, episode, system, user, schema, max_tokens,
                  work_id=None):
        self.calls += 1
        if schema is sch.ConceptOutput:
            return sch.ConceptOutput.model_validate(_concept()).model_dump()
        m = self.calls - 1  # batch index (concept call was #1)
        start = (m - 1) * 10 + 1
        end = min(start + 9, self.total)
        return sch.OutlineBatch.model_validate(
            _batch(start, end)).model_dump()


def test_concept_schema_valid_and_invalid():
    ok = sch.ConceptOutput.model_validate(_concept())
    assert ok.concept.genre == "판타지"
    with pytest.raises(Exception):
        sch.ConceptOutput.model_validate({"concept": {"genre": 123}})
    with pytest.raises(Exception):
        sch.OutlineBatch.model_validate({"episodes": []})  # wrong key


def test_generate_design_merges_batches(conn):
    conn.execute("INSERT INTO works(title, created_at) VALUES('w', 't')")
    meta = FakeMeta(25)
    out = planning.generate_design(meta, conn, work_id=1, keywords="서점",
                                   total_episodes=25)
    assert out["outline_count"] == 25
    assert meta.calls == 1 + 3  # concept + 3 batches
    d = planning.get_design(conn, out["design_id"])
    assert d["version"] == 1
    assert [o["episode_no"] for o in d["outlines"]] == list(range(1, 26))
    assert d["outlines"][0]["events"] == ["사건1"]


def test_generate_design_rejects_gap(conn):
    conn.execute("INSERT INTO works(title, created_at) VALUES('w', 't')")

    class Gappy(FakeMeta):
        def chat_json(self, **kw):
            out = super().chat_json(**kw)
            if kw["schema"] is sch.OutlineBatch:
                out["episode_outlines"] = out["episode_outlines"][:5]
            return out

    with pytest.raises(LLMStepError, match="outline_count_mismatch"):
        planning.generate_design(Gappy(12), conn, work_id=1, keywords="서점",
                                 total_episodes=12)


def test_approve_and_patch_outline(conn):
    conn.execute("INSERT INTO works(title, created_at) VALUES('w', 't')")
    out = planning.generate_design(FakeMeta(2), conn, work_id=1, keywords="서점",
                                   total_episodes=2)
    assert planning.approve_design(conn, out["design_id"])["status"] == "approved"
    planning.update_outline(conn, out["design_id"], 1, {"title": "새제목"})
    d = planning.get_design(conn, out["design_id"])
    assert d["outlines"][0]["title"] == "새제목"
    assert d["status"] == "approved"
    with pytest.raises(KeyError):
        planning.update_outline(conn, out["design_id"], 99, {"title": "x"})
