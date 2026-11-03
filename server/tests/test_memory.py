"""Phase 2: memory fixtures — age, unmentioned, recollection, unapproved,
homonym. No AI calls. Reconstruction must be deterministic."""
import pytest

from app import db as dbmod
from app import memory


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    yield c
    c.close()


def _rev(conn, ep, ver=1, text="원문"):
    cur = conn.execute(
        "INSERT INTO manuscript_revisions(episode_no, version, text, created_at)"
        " VALUES(?,?,?,?)",
        (ep, ver, text, dbmod.now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def _commit(conn, ep, items):
    rev = _rev(conn, ep)
    sid = memory.store_proposal(conn, episode_no=ep, revision_id=rev, items=items)
    for it in memory.get_proposals(conn, ep):
        if it["proposal_set_id"] == sid and it["validation"] == "pending":
            memory.set_item_validation(conn, it["item_id"], "ok")
    return memory.commit_episode(conn, episode_no=ep, revision_id=rev,
                                 proposal_set_id=sid)


def test_age_anchor_and_deterministic_rebuild(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    r = _commit(conn, 3, [{"entity_id": "ch_0001", "operation": "set",
                           "attribute_path": "나이", "new_value": 26,
                           "evidence_quote": "스물여섯 생일"}])
    assert r == {"committed": True, "applied": 1, "entities": ["ch_0001"]}
    s1 = memory.reconstruct_state(conn, "ch_0001", 6)
    s2 = memory.reconstruct_state(conn, "ch_0001", 6)
    assert s1 == s2 == {"나이": 26}
    snap = conn.execute("SELECT attributes_json FROM entity_snapshots"
                        " WHERE entity_id='ch_0001' AND episode_no=6").fetchone()
    assert snap is not None


def test_unmentioned_attribute_has_no_change(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    _commit(conn, 3, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "나이", "new_value": 26,
                       "evidence_quote": "스물여섯"}])
    state = memory.reconstruct_state(conn, "ch_0001", 6)
    assert "소지품" not in state
    rows = conn.execute("SELECT * FROM entity_changes WHERE attribute_path LIKE '%소지품%'"
                        ).fetchall()
    assert rows == []


def test_recollection_does_not_change_state(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "직업", "new_value": "서점주인",
                       "evidence_quote": "서점주인 서연"}])
    rev = _rev(conn, 5)
    sid = memory.store_proposal(
        conn, episode_no=5, revision_id=rev,
        items=[{"entity_id": "ch_0001", "operation": "no_change",
                "attribute_path": "", "evidence_quote": "옛날을 회상했다"}],
        mentions=[{"entity_id": "ch_0001", "quote": "옛날을 회상했다"}])
    for it in memory.get_proposals(conn, 5):
        if it["proposal_set_id"] == sid:
            memory.set_item_validation(conn, it["item_id"], "ok")
    memory.commit_episode(conn, episode_no=5, revision_id=rev, proposal_set_id=sid)
    assert memory.reconstruct_state(conn, "ch_0001", 5) == {"직업": "서점주인"}
    m = conn.execute("SELECT * FROM entity_mentions WHERE episode_no=5").fetchall()
    assert len(m) == 1  # observation recorded, state untouched


def test_unapproved_items_never_commit(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    rev = _rev(conn, 4)
    sid = memory.store_proposal(conn, episode_no=4, revision_id=rev, items=[
        {"entity_id": "ch_0001", "operation": "set", "attribute_path": "나이",
         "new_value": 30, "evidence_quote": "서른", "validation": "error"},
        {"entity_id": "ch_0001", "operation": "set", "attribute_path": "직업",
         "new_value": "추정", "evidence_quote": "어쩌면", "validation": "deferred"},
        {"entity_id": "ch_0001", "operation": "set", "attribute_path": "별명",
         "new_value": "장부지기", "evidence_quote": "장부지기"},
    ])
    # Leave third item pending; only ok/change commit.
    r = memory.commit_episode(conn, episode_no=4, revision_id=rev, proposal_set_id=sid)
    assert r["applied"] == 0
    assert memory.reconstruct_state(conn, "ch_0001", 4) == {}


def test_homonym_alias_flow(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    a1 = memory.add_alias_candidate(conn, entity_id="ch_0001", alias="장부지기",
                                    basis="4화 호칭")
    r1 = memory.resolve_alias(conn, a1, entity_id="ch_0001", basis="작가 확정")
    assert r1["status"] == "confirmed" and r1["new_entity"] is False
    a2 = memory.add_alias_candidate(conn, entity_id="ch_0001", alias="이서연",
                                    basis="동명이인 의심")
    r2 = memory.resolve_alias(conn, a2, entity_id=None, basis="별개 인물")
    assert r2["new_entity"] is True and r2["entity_id"] == "ch_0002"
    assert memory.get_entity(conn, "ch_0002")["canonical_name"] == "이서연"
    a3 = memory.add_alias_candidate(conn, entity_id="ch_0001", alias="오인",
                                    basis="오탐")
    assert memory.reject_alias(conn, a3)["status"] == "rejected"


def test_commit_is_idempotent(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    rev = _rev(conn, 2)
    sid = memory.store_proposal(conn, episode_no=2, revision_id=rev, items=[
        {"entity_id": "ch_0001", "operation": "set", "attribute_path": "나이",
         "new_value": 26, "evidence_quote": "스물여섯"}])
    for it in memory.get_proposals(conn, 2):
        memory.set_item_validation(conn, it["item_id"], "ok")
    first = memory.commit_episode(conn, episode_no=2, revision_id=rev,
                                  proposal_set_id=sid)
    second = memory.commit_episode(conn, episode_no=2, revision_id=rev,
                                   proposal_set_id=sid)
    assert first["committed"] is True
    assert second == {"committed": False, "reason": "already_committed"}
    n = conn.execute("SELECT COUNT(*) c FROM entity_changes").fetchone()["c"]
    assert n == 1


def test_discard_all_pending_drops_every_revision(conn):
    rev1 = _rev(conn, 1, ver=1)
    rev2 = _rev(conn, 1, ver=2)
    item = {"entity_id": "ch_0001", "operation": "set",
            "attribute_path": "나이", "new_value": 26,
            "evidence_quote": "스물여섯"}
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    s1 = memory.store_proposal(conn, episode_no=1, revision_id=rev1,
                               items=[dict(item)])
    s2 = memory.store_proposal(conn, episode_no=1, revision_id=rev2,
                               items=[dict(item)])
    assert memory.latest_pending_set(conn, 1)["proposal_set_id"] == s2
    assert memory.discard_all_pending(conn, 1) == 2
    assert memory.latest_pending_set(conn, 1) is None
    rows = conn.execute("SELECT status FROM change_proposals"
                        " WHERE proposal_set_id IN (?,?)", (s1, s2)).fetchall()
    assert {r["status"] for r in rows} == {"discarded"}


def test_delete_pending_event(conn):
    rev = _rev(conn, 1)
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    events = [{"summary": "사건A", "story_time": "", "cause": "",
               "consequence": "", "participants": []},
              {"summary": "사건B", "story_time": "", "cause": "",
               "consequence": "", "participants": []},
              {"summary": "사건C", "story_time": "", "cause": "",
               "consequence": "", "participants": []}]
    sid = memory.store_proposal(
        conn, episode_no=1, revision_id=rev,
        items=[{"entity_id": "ch_0001", "operation": "set",
                "attribute_path": "나이", "new_value": 26,
                "evidence_quote": "스물여섯"}],
        events=events)
    out = memory.delete_pending_event(conn, sid, 1)
    assert out == {"proposal_set_id": sid, "deleted": 1, "events": 2}
    assert [e["summary"] for e in memory.get_proposal_events(conn, sid)] == [
        "사건A", "사건C"]
    with pytest.raises(IndexError):
        memory.delete_pending_event(conn, sid, 5)
    for it in memory.get_proposals(conn, 1):
        if it["proposal_set_id"] == sid:
            memory.set_item_validation(conn, it["item_id"], "ok")
    memory.commit_episode(conn, episode_no=1, revision_id=rev,
                          proposal_set_id=sid)
    with pytest.raises(ValueError, match="committed"):
        memory.delete_pending_event(conn, sid, 0)


def test_history_and_invalidation(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         name="서연", first_episode=1)
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "나이", "new_value": 25,
                       "evidence_quote": "스물다섯"}])
    _commit(conn, 2, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "나이", "new_value": 26,
                       "evidence_quote": "스물여섯"}])
    hist = memory.entity_history(conn, "ch_0001")
    assert [h["episode_no"] for h in hist] == [1, 2]
    rev = _rev(conn, 3)
    sid = memory.store_proposal(conn, episode_no=3, revision_id=rev, items=[
        {"entity_id": "ch_0001", "operation": "set", "attribute_path": "나이",
         "new_value": 26, "evidence_quote": "스물여섯", "validation": "ok"}])
    assert memory.mark_stale_from(conn, 2) == 1
    items = [i for i in memory.get_proposals(conn, 3) if i["proposal_set_id"] == sid]
    assert items[0]["validation"] == "stale"
