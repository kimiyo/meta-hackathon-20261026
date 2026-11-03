"""Phase 2: memory fixtures — age, unmentioned, recollection, unapproved,
homonym. No AI calls. Reconstruction must be deterministic."""
import json

import pytest

from app import consistency
from app import db as dbmod
from app import memory


@pytest.fixture()
def conn(tmp_path):
    c = dbmod.connect(tmp_path / "t.db")
    yield c
    c.close()


def _rev(conn, ep, ver=1, text="원문"):
    cur = conn.execute(
        "INSERT INTO manuscript_revisions(work_id, episode_no, version, text, created_at)"
        " VALUES(?,?,?,?,?)",
        (1, ep, ver, text, dbmod.now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def _commit(conn, ep, items):
    rev = _rev(conn, ep)
    sid = memory.store_proposal(conn, work_id=1, episode_no=ep, revision_id=rev, items=items)
    for it in memory.get_proposals(conn, ep):
        if it["proposal_set_id"] == sid and it["validation"] == "pending":
            memory.set_item_validation(conn, it["item_id"], "ok")
    return memory.commit_episode(conn, episode_no=ep, revision_id=rev,
                                 proposal_set_id=sid)


def test_age_anchor_and_deterministic_rebuild(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
                         name="서연", first_episode=1)
    r = _commit(conn, 3, [{"entity_id": "ch_0001", "operation": "set",
                           "attribute_path": "나이", "new_value": 26,
                           "evidence_quote": "스물여섯 생일"}])
    assert r == {"committed": True, "applied": 1, "entities": ["ch_0001"],
                   "aliases_linked": 0}
    s1 = memory.reconstruct_state(conn, "ch_0001", 6)
    s2 = memory.reconstruct_state(conn, "ch_0001", 6)
    assert s1 == s2 == {"나이": 26}
    snap = conn.execute("SELECT attributes_json FROM entity_snapshots"
                        " WHERE entity_id='ch_0001' AND episode_no=6").fetchone()
    assert snap is not None


def test_unmentioned_attribute_has_no_change(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
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
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
                         name="서연", first_episode=1)
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "직업", "new_value": "서점주인",
                       "evidence_quote": "서점주인 서연"}])
    rev = _rev(conn, 5)
    sid = memory.store_proposal(
        conn, work_id=1, episode_no=5, revision_id=rev,
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
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
                         name="서연", first_episode=1)
    rev = _rev(conn, 4)
    sid = memory.store_proposal(conn, work_id=1, episode_no=4, revision_id=rev, items=[
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
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
                         name="서연", first_episode=1)
    a1 = memory.add_alias_candidate(conn, work_id=1, entity_id="ch_0001", alias="장부지기",
                                    basis="4화 호칭")
    r1 = memory.resolve_alias(conn, a1, entity_id="ch_0001", basis="작가 확정")
    assert r1["status"] == "confirmed" and r1["new_entity"] is False
    a2 = memory.add_alias_candidate(conn, work_id=1, entity_id="ch_0001", alias="이서연",
                                    basis="동명이인 의심")
    r2 = memory.resolve_alias(conn, a2, entity_id=None, basis="별개 인물")
    assert r2["new_entity"] is True and r2["entity_id"] == "ch_0002"
    assert memory.get_entity(conn, "ch_0002")["canonical_name"] == "이서연"
    a3 = memory.add_alias_candidate(conn, work_id=1, entity_id="ch_0001", alias="오인",
                                    basis="오탐")
    assert memory.reject_alias(conn, a3)["status"] == "rejected"


def test_commit_is_idempotent(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
                         name="서연", first_episode=1)
    rev = _rev(conn, 2)
    sid = memory.store_proposal(conn, work_id=1, episode_no=2, revision_id=rev, items=[
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
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
                         name="서연", first_episode=1)
    s1 = memory.store_proposal(conn, work_id=1, episode_no=1, revision_id=rev1,
                               items=[dict(item)])
    s2 = memory.store_proposal(conn, work_id=1, episode_no=1, revision_id=rev2,
                               items=[dict(item)])
    assert memory.latest_pending_set(conn, 1)["proposal_set_id"] == s2
    assert memory.discard_all_pending(conn, 1) == 2
    assert memory.latest_pending_set(conn, 1) is None
    rows = conn.execute("SELECT status FROM change_proposals"
                        " WHERE proposal_set_id IN (?,?)", (s1, s2)).fetchall()
    assert {r["status"] for r in rows} == {"discarded"}


def test_delete_pending_event(conn):
    rev = _rev(conn, 1)
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
                         name="서연", first_episode=1)
    events = [{"summary": "사건A", "story_time": "", "cause": "",
               "consequence": "", "participants": []},
              {"summary": "사건B", "story_time": "", "cause": "",
               "consequence": "", "participants": []},
              {"summary": "사건C", "story_time": "", "cause": "",
               "consequence": "", "participants": []}]
    sid = memory.store_proposal(
        conn, work_id=1, episode_no=1, revision_id=rev,
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
    memory.create_entity(conn, entity_id="ch_0001", type_="character", work_id=1,
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
    sid = memory.store_proposal(conn, work_id=1, episode_no=3, revision_id=rev, items=[
        {"entity_id": "ch_0001", "operation": "set", "attribute_path": "나이",
         "new_value": 26, "evidence_quote": "스물여섯", "validation": "ok"}])
    assert memory.mark_stale_from(conn, 2) == 1
    items = [i for i in memory.get_proposals(conn, 3) if i["proposal_set_id"] == sid]
    assert items[0]["validation"] == "stale"


def test_reset_reopens_relations_closed_by_episode(conn):
    memory.create_entity(conn, entity_id="ch_0001", work_id=1,
                         type_="character", name="민준", first_episode=1)
    memory.create_entity(conn, entity_id="ch_0002", work_id=1,
                         type_="character", name="서연", first_episode=1)
    conn.execute(
        "INSERT INTO entity_relations(work_id, subject_id, predicate,"
        " object_id, valid_from_episode, valid_to_episode)"
        " VALUES(1,'ch_0001','상사','ch_0002',1,2)")
    conn.execute(
        "INSERT INTO entity_relations(work_id, subject_id, predicate,"
        " object_id, valid_from_episode) VALUES(1,'ch_0001','동료','ch_0002',2)")
    conn.commit()
    out = memory.reset_episode_memory(conn, 2, 1)
    assert out["relations"] == 1
    assert out["relations_reopened"] == 1
    rows = {r["predicate"]: r["valid_to_episode"] for r in conn.execute(
        "SELECT predicate, valid_to_episode FROM entity_relations")}
    assert rows == {"상사": None}
    # 참조가 남아 있어 설정은 유지된다.
    assert conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"] == 2


def test_reset_deletes_orphan_snapshots_at_any_episode(conn):
    memory.create_entity(conn, entity_id="ch_0001", work_id=1,
                         type_="character", name="민준", first_episode=1)
    conn.execute(
        "INSERT INTO entity_changes(entity_id, work_id, episode_no,"
        " manuscript_revision_id, kind, attribute_path, operation,"
        " committed_at) VALUES('ch_0001',1,1,1,'attribute','직업','create','t')")
    for ep in (0, 1, 2):
        conn.execute(
            "INSERT INTO entity_snapshots(entity_id, work_id, episode_no,"
            " attributes_json) VALUES('ch_0001',1,?,?)", (ep, "{}"))
    conn.commit()
    out = memory.reset_episode_memory(conn, 1, 1)
    assert out["entities"] == 1
    assert conn.execute(
        "SELECT COUNT(*) c FROM entity_snapshots").fetchone()["c"] == 0
    assert conn.execute(
        "SELECT COUNT(*) c FROM entities").fetchone()["c"] == 0


def test_commit_auto_clears_unique_candidate(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="민준", first_episode=1)
    memory.add_alias_candidate(conn, entity_id=None, work_id=1,
                               alias="민준", basis="t")
    r = _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                           "attribute_path": "직업", "new_value": "서점주인",
                           "evidence_quote": "서점"}])
    assert r["aliases_linked"] == 1
    # 자동 정리는 대기열에서 치울 뿐 확정 연결을 남기지 않는다.
    # 확정은 작가 판단 전용이라 이후의 새 인물 분리를 가리지 않는다.
    assert conn.execute(
        "SELECT COUNT(*) c FROM entity_aliases").fetchone()["c"] == 0
    assert memory.list_alias_candidates(conn, 1) == []


def test_confirmed_link_beats_explicit_new(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서도윤", first_episode=1)
    aid = memory.add_alias_candidate(conn, entity_id=None, work_id=1,
                                     alias="팀장님", basis="t")
    memory.resolve_alias(conn, aid, entity_id="ch_0001", basis="작가 확정")
    r = _commit(conn, 1, [{"entity_id": None, "candidate_name": "팀장님",
                           "explicit_new": True, "operation": "create",
                           "attribute_path": "직업", "new_value": "팀장",
                           "evidence_quote": "팀장님"}])
    assert r["entities"] == ["ch_0001"]
    assert memory.search_entities(conn, work_id=1, q="팀장님") == []
    hist = memory.entity_history(conn, "ch_0001")
    assert [(h["attribute_path"], json.loads(h["new_value"]))
            for h in hist] == [("직업", "팀장")]


def test_participant_uses_confirmed_alias(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서도윤", first_episode=1)
    aid = memory.add_alias_candidate(conn, entity_id=None, work_id=1,
                                     alias="팀장님", basis="t")
    memory.resolve_alias(conn, aid, entity_id="ch_0001", basis="작가 확정")
    rev = _rev(conn, 1)
    sid = memory.store_proposal(
        conn, work_id=1, episode_no=1, revision_id=rev, items=[],
        events=[{"summary": "사건A", "story_time": "", "cause": "",
                 "consequence": "", "evidence_quote": "팀장님",
                 "participants": [{"entity_id": None,
                                   "candidate_name": "팀장님",
                                   "role": "통보자"}]}])
    r = memory.commit_episode(conn, episode_no=1, revision_id=rev,
                              proposal_set_id=sid)
    assert r["committed"] is True
    assert memory.search_entities(conn, work_id=1, q="팀장님") == []
    row = conn.execute(
        "SELECT entity_id FROM event_participants").fetchone()
    assert row["entity_id"] == "ch_0001"


def test_later_explicit_split_after_autoclear(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서도윤", first_episode=1)
    memory.add_alias_candidate(conn, entity_id=None, work_id=1,
                               alias="서도윤", basis="t")
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "직업", "new_value": "팀장",
                       "evidence_quote": "서도윤"}])
    # 자동 정리 뒤에 모델이 진짜 새 인물을 선언하면 분리된다.
    r = _commit(conn, 2, [{"entity_id": None, "candidate_name": "서도윤",
                           "explicit_new": True, "operation": "create",
                           "attribute_path": "직업", "new_value": "신입",
                           "evidence_quote": "또 한 명의 서도윤"}])
    assert r["entities"] == ["ch_0002"]


def test_search_entities_content_and_asof(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서연", first_episode=1)
    memory.create_entity(conn, entity_id="ch_0002", type_="character",
                         work_id=1, name="민준", first_episode=2)
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "직업", "new_value": "서점주인",
                       "evidence_quote": "서점"}])
    _commit(conn, 2, [{"entity_id": "ch_0002", "operation": "set",
                       "attribute_path": "직업", "new_value": "점장",
                       "evidence_quote": "점장"}])
    got = memory.search_entities(conn, work_id=1, content="서점주인")
    assert [e["entity_id"] for e in got] == ["ch_0001"]
    assert memory.search_entities(conn, work_id=1, content="없는말") == []
    # 기준 회차와 함께 쓰면 그 회차까지의 내용만 본다.
    got = memory.search_entities(conn, work_id=1, content="점장",
                                 as_of_episode=1)
    assert got == []
    got = memory.search_entities(conn, work_id=1, content="점장",
                                 as_of_episode=2)
    assert [e["entity_id"] for e in got] == ["ch_0002"]


def test_search_entities_any_matches_name_id_content(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서도윤", first_episode=1)
    memory.create_entity(conn, entity_id="ch_0002", type_="character",
                         work_id=1, name="민준", first_episode=1)
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "직업", "new_value": "팀장",
                       "evidence_quote": "팀장"}])
    _commit(conn, 2, [{"entity_id": "ch_0002", "operation": "set",
                       "attribute_path": "직업", "new_value": "점장",
                       "evidence_quote": "점장"}])
    got = memory.search_entities(conn, work_id=1, any="서도")
    assert [e["entity_id"] for e in got] == ["ch_0001"]
    got = memory.search_entities(conn, work_id=1, any="ch_0002")
    assert [e["entity_id"] for e in got] == ["ch_0002"]
    got = memory.search_entities(conn, work_id=1, any="점장")
    assert [e["entity_id"] for e in got] == ["ch_0002"]
    # 기준 회차는 내용 매칭에도 적용된다.
    assert memory.search_entities(conn, work_id=1, any="점장",
                                  as_of_episode=1) == []
    got = memory.search_entities(conn, work_id=1, any="점장",
                                 as_of_episode=2)
    assert [e["entity_id"] for e in got] == ["ch_0002"]
    assert memory.search_entities(conn, work_id=1, any="없는말") == []


def test_entity_match_snippet_shows_window(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서도윤", first_episode=1)
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "직업",
                       "new_value": "가" * 30 + "팀장" + "나" * 30,
                       "evidence_quote": "긴 값"}])
    snip = memory.entity_match_snippet(conn, "ch_0001", "팀장")
    assert "팀장" in snip
    assert snip.startswith("…") and snip.endswith("…")
    # 이름으로만 매칭되면 발췌가 없다(이름 자체가 근거).
    assert memory.entity_match_snippet(conn, "ch_0001", "서도") == ""
    assert memory.entity_match_snippet(conn, "ch_0001", "") == ""


def test_search_entities_any_covers_screen_text(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서도윤", first_episode=1)
    memory.create_entity(conn, entity_id="ch_0002", type_="character",
                         work_id=1, name="민준", first_episode=1)
    _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                       "attribute_path": "직업", "new_value": "팀장",
                       "story_time": "2026-12-31",
                       "evidence_quote": "아침 7시 42분에 출입카드를 꺼냈다"}])
    conn.execute(
        "INSERT INTO entity_relations(work_id, subject_id, predicate,"
        " object_id, valid_from_episode) VALUES(1,'ch_0001','상사','ch_0002',1)")
    conn.execute(
        "INSERT INTO entity_relations(work_id, subject_id, predicate,"
        " object_id, valid_from_episode) VALUES(1,'ch_0001','동료','ch_0002',2)")
    conn.commit()
    # 근거 문장·시기로 검색된다.
    got = memory.search_entities(conn, work_id=1, any="아침 7시")
    assert [e["entity_id"] for e in got] == ["ch_0001"]
    got = memory.search_entities(conn, work_id=1, any="2026-12-31")
    assert [e["entity_id"] for e in got] == ["ch_0001"]
    # 관계 술어로는 양쪽 끝점이 검색된다.
    got = memory.search_entities(conn, work_id=1, any="상사")
    assert [e["entity_id"] for e in got] == ["ch_0001", "ch_0002"]
    # 관계 유효 구간도 기준 회차를 따른다.
    assert memory.search_entities(conn, work_id=1, any="동료",
                                  as_of_episode=1) == []
    got = memory.search_entities(conn, work_id=1, any="동료",
                                 as_of_episode=2)
    assert [e["entity_id"] for e in got] == ["ch_0001", "ch_0002"]
    # 상대 이름으로도 검색된다(민준 화면에 서도윤과의 관계가 보이므로).
    got = memory.search_entities(conn, work_id=1, any="서도윤")
    assert [e["entity_id"] for e in got] == ["ch_0001", "ch_0002"]


def test_entity_match_snippet_quote_and_null_old(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="서도윤", first_episode=1)
    conn.execute(
        "INSERT INTO entity_changes(entity_id, work_id, episode_no,"
        " manuscript_revision_id, kind, attribute_path, operation, old_value,"
        " new_value, story_time, evidence_quote, committed_at)"
        " VALUES('ch_0001',1,1,1,'attribute','근로관계','set','null',"
        " '\"2026-12-31부로 종료\"','2026-12-31','오늘부로 근로관계가 종료된다','t')")
    conn.commit()
    snip = memory.entity_match_snippet(conn, "ch_0001", "근로")
    assert "근로관계" in snip and "null" not in snip
    # 값에 없고 인용에만 있으면 인용 발췌를 보여준다.
    snip = memory.entity_match_snippet(conn, "ch_0001", "오늘부로")
    assert "오늘부로" in snip


def test_reset_clears_open_consistency_issues(conn):
    saved = consistency.add_issues(conn, 1, [
        {"title": "열린 이슈"}, {"title": "처리된 이슈"}], 1)["issues"]
    consistency.set_issue_status(conn, saved[1]["issue_id"], "fixed")
    counts = memory.reset_episode_memory(conn, 1, 1)
    assert counts["issues"] == 1
    rows = conn.execute(
        "SELECT title, status FROM consistency_issues").fetchall()
    assert [(r["title"], r["status"]) for r in rows] == [("처리된 이슈", "fixed")]


def test_commit_keeps_candidate_on_homonym(conn):
    memory.create_entity(conn, entity_id="ch_0001", type_="character",
                         work_id=1, name="민준", first_episode=1)
    memory.create_entity(conn, entity_id="ch_0002", type_="character",
                         work_id=1, name="민준", first_episode=1)
    memory.add_alias_candidate(conn, entity_id=None, work_id=1,
                               alias="민준", basis="t")
    r = _commit(conn, 1, [{"entity_id": "ch_0001", "operation": "set",
                           "attribute_path": "직업", "new_value": "서점주인",
                           "evidence_quote": "서점"}])
    assert r["aliases_linked"] == 0
    row = conn.execute("SELECT status FROM entity_aliases").fetchone()
    assert row["status"] == "candidate"
