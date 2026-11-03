"""§9 eval set: paired violation/normal cases (no AI calls) + pipeline behaviors."""
import json
from pathlib import Path

import pytest

from app import checks, citation, db as dbmod, memory

CASES_PATH = Path(__file__).resolve().parents[2] / "evals" / "cases.json"
CASES = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    yield c
    c.close()


def _rev(conn, ep, text):
    row = conn.execute("SELECT COALESCE(MAX(version),0)+1 v"
                       " FROM manuscript_revisions WHERE episode_no=?",
                       (ep,)).fetchone()
    cur = conn.execute(
        "INSERT INTO manuscript_revisions(work_id, episode_no, version, text, created_at)"
        " VALUES(?,?,?,?,?)", (1, ep, row["v"], text, dbmod.now_iso()))
    conn.commit()
    return cur.lastrowid


def _build(conn, case):
    name_to_id = {}
    for e in case.get("entities", []):
        eid = memory.next_entity_id(conn, e.get("type", "character"))
        memory.create_entity(conn, entity_id=eid, work_id=1,
                             type_=e.get("type", "character"),
                             name=e["name"], first_episode=1)
        name_to_id[e["name"]] = eid

    def _item(src):
        return {"entity_id": name_to_id.get(src.get("name", "")),
                "kind": src.get("kind", "attribute"),
                "operation": src.get("operation", "set"),
                "attribute_path": src.get("attribute_path", ""),
                "new_value": src.get("new_value", ""),
                "standard_value": src.get("standard_value", ""),
                "story_time": src.get("story_time", ""),
                "narrative_mode": src.get("narrative_mode", "narration"),
                "evidence_quote": src.get("evidence_quote", "")}

    rev1 = _rev(conn, 1, case["manuscript_ep1"])
    if case.get("committed"):
        sid = memory.store_proposal(
            conn, episode_no=1, revision_id=rev1,
            items=[_item(i) for i in case["committed"]], work_id=1,
            base_memory_version=memory.current_memory_version(conn, 1))
        memory.save_episode_memory(conn, episode_no=1, revision_id=rev1,
                                   proposal_set_id=sid, work_id=1)
    rev2 = _rev(conn, 2, case["manuscript_ep2"])
    events = []
    for ev in case.get("pending_events", []):
        parts = []
        for p in ev.get("participants", []):
            eid = name_to_id.get(p.get("name", ""))
            parts.append({"entity_id": eid,
                          "candidate_name": "" if eid else p.get("name", ""),
                          "role": p.get("role", "")})
        events.append({"summary": ev.get("summary", ""),
                       "story_time": ev.get("story_time", ""),
                       "narrative_mode": ev.get("narrative_mode",
                                                "narration"),
                       "evidence_quote": ev.get("evidence_quote", ""),
                       "participants": parts})
    sid2 = memory.store_proposal(
        conn, episode_no=2, revision_id=rev2,
        items=[_item(i) for i in case.get("pending_items", [])],
        events=events, work_id=1,
        base_memory_version=memory.current_memory_version(conn, 1))
    return sid2


def _quoted(manuscript, quote):
    return citation.check_quote(manuscript, quote)["ok"]


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_eval_case(conn, case):
    # Case honesty: every quote must come from its own manuscript.
    for it in case.get("committed", []):
        assert _quoted(case["manuscript_ep1"], it["evidence_quote"]), it
    for it in case.get("pending_items", []):
        assert _quoted(case["manuscript_ep2"], it["evidence_quote"]), it
    for ev in case.get("pending_events", []):
        assert _quoted(case["manuscript_ep2"], ev["evidence_quote"]), ev
    sid2 = _build(conn, case)
    out = checks.run_deterministic_checks(conn, 1, 2, sid2)
    assert all(r["status"] == "completed" for r in out["ran"])
    got = [c for c in out["candidates"]
           if c["check"] == case["expect"]["check"]]
    assert len(got) == case["expect"]["count"]
    if "verdict" in case["expect"]:
        assert got and all(c["verdict"] == case["expect"]["verdict"]
                           for c in got)


# -- pipeline behaviors (no AI) -------------------------------------------
def _commit_items(conn, ep, text, items, work_id=1):
    rev = _rev(conn, ep, text)
    sid = memory.store_proposal(
        conn, episode_no=ep, revision_id=rev, items=items, work_id=work_id,
        base_memory_version=memory.current_memory_version(conn, work_id))
    return memory.save_episode_memory(conn, episode_no=ep, revision_id=rev,
                                      proposal_set_id=sid, work_id=work_id)


def test_stable_id_on_reextract_and_save(conn):
    memory.create_entity(conn, entity_id="ch_0001", work_id=1,
                         type_="character", name="서연", first_episode=1)
    _commit_items(conn, 1, "서연 등장", [
        {"entity_id": "ch_0001", "operation": "set",
         "attribute_path": "직업", "new_value": "기사",
         "evidence_quote": "서연 등장"}])
    # Re-extract hitting the same bare name must reuse ch_0001.
    out = _commit_items(conn, 2, "서연 재등장", [
        {"entity_id": None, "candidate_name": "서연",
         "operation": "create", "attribute_path": "직업",
         "new_value": "기사", "evidence_quote": "서연 재등장"}])
    assert out["entities"] == ["ch_0001"]
    assert len(memory.search_entities(conn, work_id=1)) == 1


def test_explicit_new_splits_homonym(conn):
    memory.create_entity(conn, entity_id="ch_0001", work_id=1,
                         type_="character", name="서연", first_episode=1)
    out = _commit_items(conn, 1, "또 한 명의 서연", [
        {"entity_id": None, "candidate_name": "서연", "explicit_new": True,
         "operation": "create", "attribute_path": "이름",
         "new_value": "서연", "evidence_quote": "또 한 명의 서연"}])
    assert out["entities"] == ["ch_0002"]
    assert len(memory.search_entities(conn, work_id=1)) == 2


def test_work_scoping_isolates_names(conn):
    out1 = _commit_items(conn, 1, "서연 등장", [
        {"candidate_name": "서연", "operation": "create",
         "attribute_path": "이름", "new_value": "서연",
         "evidence_quote": "서연 등장"}], work_id=1)
    out2 = _commit_items(conn, 1, "서연 등장", [
        {"candidate_name": "서연", "operation": "create",
         "attribute_path": "이름", "new_value": "서연",
         "evidence_quote": "서연 등장"}], work_id=2)
    assert out1["entities"] != out2["entities"]
    assert [e["entity_id"] for e in memory.search_entities(conn, work_id=1)] \
        == out1["entities"]
    assert [e["entity_id"] for e in memory.search_entities(conn, work_id=2)] \
        == out2["entities"]


def test_non_state_modes_stay_history_only(conn):
    memory.create_entity(conn, entity_id="ch_0001", work_id=1,
                         type_="character", name="서연", first_episode=1)
    _commit_items(conn, 1, "꿈 이야기", [
        {"entity_id": "ch_0001", "operation": "set",
         "attribute_path": "직업", "new_value": "왕",
         "narrative_mode": "dream", "evidence_quote": "꿈 이야기"}])
    assert memory.reconstruct_state(conn, "ch_0001", 1, 1) == {}
    assert len(memory.entity_history(conn, "ch_0001")) == 1


def test_relation_commit_and_close(conn):
    out = _commit_items(conn, 1, "서연이 민준을 의심한다", [
        {"candidate_name": "서연", "kind": "relation", "operation": "set",
         "attribute_path": "의심", "new_value": {"object": "민준"},
         "evidence_quote": "서연이 민준을 의심한다"}])
    rels = memory.active_relations(conn, 1, 1)
    assert len(rels) == 1
    assert rels[0]["predicate"] == "의심"
    subj = out["entities"][0]
    assert rels[0]["subject_id"] == subj
    obj = memory.search_entities(conn, q="민준", work_id=1)[0]["entity_id"]
    assert rels[0]["object_id"] == obj
    assert memory.active_relations(conn, 1, 2) != []
    _commit_items(conn, 2, "의심이 풀렸다", [
        {"entity_id": subj, "operation": "end_relation",
         "attribute_path": "의심", "new_value": {"object": "민준"},
         "evidence_quote": "의심이 풀렸다"}])
    assert memory.active_relations(conn, 1, 2) == []


def test_save_blocks_stale_base(conn):
    rev = _rev(conn, 1, "원고")
    sid = memory.store_proposal(conn, episode_no=1, revision_id=rev, items=[
        {"candidate_name": "서연", "operation": "create",
         "attribute_path": "이름", "new_value": "서연",
         "evidence_quote": "원고"}],
        work_id=1, base_memory_version=0)
    memory.save_episode_memory(conn, episode_no=1, revision_id=rev,
                               proposal_set_id=sid, work_id=1)
    sid2 = memory.store_proposal(conn, episode_no=1, revision_id=rev, items=[
        {"candidate_name": "민준", "operation": "create",
         "attribute_path": "이름", "new_value": "민준",
         "evidence_quote": "원고"}],
        work_id=1, base_memory_version=0)  # pinned before the first save
    with pytest.raises(ValueError, match="재검토"):
        memory.save_episode_memory(conn, episode_no=1, revision_id=rev,
                                   proposal_set_id=sid2, work_id=1)


def test_units_and_evidence_index(conn):
    text = "첫 문단이다.\n\n두 번째 문단이다."
    rev = _rev(conn, 1, text)
    units = memory.ensure_units(conn, 1, 1, rev, text)
    assert len(units) == 2
    assert units[0]["text_hash"] != units[1]["text_hash"]
    assert memory.unit_index_for_offset(units, 1) == 0
    assert memory.unit_index_for_offset(units, len("첫 문단이다.\n\n")) == 1
    assert memory.unit_index_for_offset(units, None) is None
    # Idempotent per revision.
    again = memory.ensure_units(conn, 1, 1, rev, text)
    assert [u["unit_id"] for u in again] == [u["unit_id"] for u in units]
