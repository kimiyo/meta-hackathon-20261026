"""EN-work runtime checks: prompts, labels, and errors in English.

File-level prompt parity lives in test_i18n_prompts.py; this module proves
the rewired call paths actually select the en-US text for a US/en-US work:
system prompts load from prompts/en-US, user-prompt builders emit no
Hangul, and version labels / user-facing errors use the English wording.
ko-KR behavior is pinned by the pre-existing suites (test_state_machine,
test_planning, ...), which keep passing unchanged.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app import auth as authmod
from app import budget
from app import db as dbmod
from app import design_nodes, design_threads, drafting, ideas, manuscripts
from app import memory, planning, prompt_loader, state_machine
from app.routes.deps import get_conn, get_current_author
from tests.test_drafting import FakeMeta as DraftFakeMeta
from tests.test_planning import FakeMeta as PlanFakeMeta

HANGUL_RE = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    c.execute("INSERT INTO works(id, title, created_at) VALUES(1,'w','t')")
    c.execute("UPDATE works SET market='US', writing_locale='en-US'"
              " WHERE id=1")
    c.execute("INSERT INTO story_designs(id, work_id, version,"
              " scope_total_episodes, concept_json, sections_json, status)"
              " VALUES(1,1,1,2,'{}','[]','approved')")
    c.execute("INSERT INTO episode_outlines(design_id, episode_no, title,"
              " goal) VALUES(1,1,'T','G')")
    c.commit()
    yield c
    c.close()


def _filler(words=5000):
    return ("word " * words).strip()


def _started(conn):
    return state_machine.start_writing(
        conn, DraftFakeMeta([_filler()]), 1, 1, design_id=1)


class CaptureMeta(DraftFakeMeta):
    def __init__(self, manuscripts):
        super().__init__(manuscripts)
        self.seen = {}

    def chat_json(self, **kw):
        self.seen = dict(kw)
        return super().chat_json(**kw)


def test_chat_revise_uses_en_prompts_and_labels(conn):
    _started(conn)
    meta = CaptureMeta(["revised manuscript ".ljust(50, "x")])
    out = state_machine.chat_revise(conn, meta, 1, 1,
                                    message="shorten the first line")
    assert out["revision"]["version"] == 2
    assert meta.seen["system"] == prompt_loader.load(
        "manuscript_chat", "en-US", "US").text
    assert meta.seen["writing_locale"] == "en-US"
    assert meta.seen["prompt_feature"] == "manuscript_chat"
    assert "author's instruction" in meta.seen["user"]
    assert not HANGUL_RE.search(meta.seen["system"])
    assert not HANGUL_RE.search(meta.seen["user"])
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1 AND version=2"
                       ).fetchone()
    assert row["label"] == "AI chat revision"


def test_chat_revise_en_errors(conn):
    _started(conn)
    with pytest.raises(ValueError, match="Enter a revision instruction"):
        state_machine.chat_revise(conn, DraftFakeMeta(["x"]), 1, 1,
                                  message="  ")


def test_redraft_en_labels(conn):
    _started(conn)
    conn.execute("UPDATE manuscript_docs SET draft_content='author draft'"
                 " WHERE work_id=1 AND episode_no=1")
    conn.commit()
    state_machine.redraft_episode(conn, DraftFakeMeta([_filler()]), 1, 1)
    labels = [r["label"] for r in conn.execute(
        "SELECT label FROM manuscript_revisions"
        " WHERE work_id=1 AND episode_no=1 ORDER BY version")]
    assert labels == ["AI draft", "Draft before AI redraft", "AI redraft"]


def test_drafting_en_provenance_and_directive(conn):
    meta = CaptureMeta([_filler()])
    out = drafting.draft_episode(meta, conn, design_id=1, work_id=1,
                                 episode_no=1)
    assert out["length_unit"] == "words"
    assert meta.seen["writing_locale"] == "en-US"
    assert meta.seen["market"] == "US"
    assert meta.seen["prompt_feature"] == "drafting"
    assert meta.seen["user"].startswith("Write the manuscript")
    assert not HANGUL_RE.search(meta.seen["system"])
    assert not HANGUL_RE.search(meta.seen["user"])
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1 AND version=1"
                       ).fetchone()
    assert row["label"] == "AI draft"


def test_planning_en_prompts(conn):
    meta = CaptureMeta([])
    seen = []
    orig = PlanFakeMeta.chat_json

    class SpyMeta(PlanFakeMeta):
        def chat_json(self, **kw):
            seen.append(kw)
            return orig(self, **kw)

    out = planning.generate_design(SpyMeta(2), conn, work_id=1,
                                   keywords="bookstore", total_episodes=2)
    assert out["outline_count"] == 2
    assert seen, "planning made no AI calls"
    en_system = prompt_loader.load("planning", "en-US", "US").text
    for kw in seen:
        assert kw["system"] == en_system
        assert not HANGUL_RE.search(kw["system"])
    # The concept call echoes only our English keywords; batch calls echo
    # the fake's Korean fixture data, so only the framing is asserted there.
    assert seen[0]["user"].startswith("Keywords:")
    assert not HANGUL_RE.search(seen[0]["user"])
    assert "Generate episode outlines" in seen[1]["user"]


def test_design_context_en_labels(conn):
    root = design_nodes.create_node(conn, 1, kind="work", title="W",
                                    logline="L", body={})
    node = design_nodes.create_node(conn, 1, kind="episode",
                                    parent_id=root["id"], title="EP1",
                                    logline="log", body={"episode_no": 1})
    ctx = design_threads.node_context(conn, node, "Author request: x",
                                      history=["Author: q"],
                                      writing_locale="en-US")
    for label in ("Work:", "Structure:", "Design tree:", "Selected node:",
                  "Logline:", "Detail:", "Ancestors:", "Children:",
                  "Siblings:", "Settings:", "[Included conversation]:"):
        assert label in ctx
    assert not HANGUL_RE.search(ctx)
    assert design_threads.ref_lines(conn, 1, ["@ZZZ"], "en-US") == [
        "- @ZZZ = (unknown)"]
    hist = design_threads.history_lines(
        [{"role": "user", "content": "q"},
         {"role": "assistant", "content": "a",
          "proposals": {"children": [{"kind": "scene", "title": "S"}],
                        "patch": {"title": "T"}},
          "applied": {"patch": False, "children": []}}],
        "en-US")
    assert hist[0].startswith("Author: ")
    assert "(This reply proposed:" in hist[1]
    assert "child [scene]S(pending)" in hist[1]
    assert "node patch(pending)" in hist[1]
    assert not HANGUL_RE.search("\n".join(hist))


def test_migration_tree_en_stub_titles():
    structure = {"use_part": False, "use_chapter": False,
                 "total_episodes": 2, "parts": []}
    tree, counts = ideas.build_migration_tree(structure, {"tree": []},
                                               "en-US")
    assert [n["title"] for n in tree] == ["Episode 1", "Episode 2"]
    assert counts["stub_episodes"] == 2
    tree_ko, _ = ideas.build_migration_tree(structure, {"tree": []})
    assert [n["title"] for n in tree_ko] == ["제1화", "제2화"]


def test_manuscript_labels_en(conn):
    _started(conn)
    job = {"work_id": 1, "episode_no": 1, "state": "WRITING"}
    row = manuscripts.ensure_doc_row(conn, 1, 1)
    manuscripts.create_version(conn, 1, 1, job, "T", row["revision"],
                               "req-1", "hand text " * 10, None)
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1 AND version=2"
                       ).fetchone()
    assert row["label"] == "Manual save"
    doc = manuscripts.ensure_doc_row(conn, 1, 1)
    manuscripts.restore_version(conn, 1, 1, job, "T", doc["revision"],
                                "req-2", "1")
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1 AND version=3"
                       ).fetchone()
    assert row["label"] == "v1 restored"


def test_submit_snapshot_label_en(conn):
    _started(conn)
    conn.execute("UPDATE manuscript_docs SET draft_content='author draft'"
                 " WHERE work_id=1 AND episode_no=1")
    conn.commit()
    state_machine.submit_for_review(conn, 1, 1)
    row = conn.execute("SELECT label FROM manuscript_revisions"
                       " WHERE work_id=1 AND episode_no=1"
                       " ORDER BY version DESC LIMIT 1").fetchone()
    assert row["label"] == "Submit snapshot"


def test_save_episode_memory_en_error(conn):
    rev = _started(conn)["revision"]["revision_id"]
    sid = memory.store_proposal(conn, episode_no=1, revision_id=rev, items=[],
                                mentions=[], events=[], work_id=1,
                                base_memory_version=0)
    memory.save_episode_memory(conn, episode_no=1, revision_id=rev,
                               proposal_set_id=sid, work_id=1,
                               writing_locale="en-US")
    sid2 = memory.store_proposal(conn, episode_no=1, revision_id=rev,
                                 items=[], mentions=[], events=[],
                                 work_id=1, base_memory_version=0)
    with pytest.raises(ValueError,
                       match="base memory changed; re-review is required"):
        memory.save_episode_memory(conn, episode_no=1, revision_id=rev,
                                   proposal_set_id=sid2, work_id=1,
                                   writing_locale="en-US")


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"

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
        seed, login_id="en-tester", pen_name="EN", password="pw1234"))
    seed.commit()
    seed.close()
    app.dependency_overrides[get_current_author] = lambda: author
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_style_route_lists_us_catalog_for_us_work(api_client):
    r = api_client.post("/api/v1/works", json={
        "title": "EN novel", "market": "US", "writing_locale": "en-US"})
    assert r.status_code == 200, r.text
    wid = r.json()["id"]
    r = api_client.get(f"/api/v1/works/{wid}/style")
    assert r.status_code == 200, r.text
    presets = r.json()["presets"]
    assert presets, "no presets listed"
    assert {p["market"] for p in presets} == {"US"}
    assert not any(HANGUL_RE.search(p["label"]) for p in presets)
    r = api_client.post("/api/v1/works", json={"title": "KR novel"})
    kr = r.json()["id"]
    r = api_client.get(f"/api/v1/works/{kr}/style")
    assert {p["market"] for p in r.json()["presets"]} == {"KR"}


def test_record_llm_call_persists_provenance(conn):
    """Regression: the recorder used to drop the six i18n columns."""
    budget.record_llm_call(conn, {
        "ts": "2026-11-04T00:00:00+00:00", "bundle": "idea_chat",
        "episode_no": None, "work_id": 1, "model": "m",
        "input_tokens": 1, "output_tokens": 2, "latency_ms": 3,
        "ok": 1, "error": "", "prompt_system": "s", "prompt_user": "u",
        "output_text": "o", "finish_reason": "stop",
        "market": "US", "writing_locale": "en-US",
        "prompt_feature": "ideas_chat", "prompt_version": "v1",
        "preset_id": "muse-spark-1.3", "preset_version": "v3"})
    row = conn.execute("SELECT market, writing_locale, prompt_feature,"
                       " prompt_version, preset_id, preset_version"
                       " FROM llm_calls WHERE work_id=1").fetchone()
    assert tuple(row) == ("US", "en-US", "ideas_chat", "v1",
                          "muse-spark-1.3", "v3")


def test_blurb_missing_material_error_en(api_client):
    r = api_client.post("/api/v1/works", json={
        "title": "EN novel", "market": "US", "writing_locale": "en-US"})
    wid = r.json()["id"]
    r = api_client.post(f"/api/v1/works/{wid}/blurb")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "no blurb material (need a logline, synopsis, or outlines)")
