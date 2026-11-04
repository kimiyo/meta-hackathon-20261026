"""Entity memory: reconstruction, snapshots, idempotent commit, invalidation.

Reads use committed rows of the ACTIVE run only. The program owns all
writes; AI output arrives as proposal items and is applied only through
commit/save. Memory is scoped per work (work_id).

Fact kinds: attribute | state | relation | rule | event.
Narrative modes: narration | dialogue | rumor | conjecture | hypothesis |
negation | dream | recollection | lie. NON_STATE_MODES never change the
reconstructed state (§4): they are kept as history/events only.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from .db import now_iso

OPERATIONS = ("create", "set", "add", "remove", "end_relation", "no_change")
KINDS = ("attribute", "state", "relation", "rule", "event")
MODES = ("narration", "dialogue", "rumor", "conjecture", "hypothesis",
         "negation", "dream", "recollection", "lie")
NON_STATE_MODES = frozenset({"dream", "conjecture", "hypothesis", "lie",
                             "recollection"})
TYPE_PREFIX = {"character": "ch", "location": "loc"}


def _j(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def _u(text: str):
    if text in ("", None):
        return ""
    try:
        return json.loads(text)
    except ValueError:
        return text


def norm_kind(kind: str) -> str:
    return kind if kind in KINDS else "attribute"


def norm_mode(mode: str) -> str:
    return mode if mode in MODES else "narration"


def next_entity_id(conn: sqlite3.Connection, type_: str) -> str:
    """Globally unique IDs (counter is global; scoping is via work_id)."""
    prefix = TYPE_PREFIX.get(type_, "en")
    row = conn.execute(
        "SELECT entity_id FROM entities WHERE entity_id LIKE ? ORDER BY entity_id DESC LIMIT 1",
        (f"{prefix}_%",),
    ).fetchone()
    n = int(row["entity_id"].split("_")[1]) + 1 if row else 1
    return f"{prefix}_{n:04d}"


def create_entity(conn, *, entity_id, work_id, type_, name, first_episode,
                  description="", _commit=True):
    conn.execute(
        "INSERT INTO entities(entity_id, work_id, type, canonical_name,"
        " first_episode, description) VALUES(?,?,?,?,?,?)",
        (entity_id, work_id, type_, name, first_episode, description),
    )
    if _commit:
        conn.commit()
    return entity_id


def get_entity(conn, entity_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    return dict(row) if row else None


def search_entities(conn, type_: str | None = None, q: str | None = None,
                    work_id: int | None = None,
                    as_of_episode: int | None = None,
                    content: str | None = None,
                    any: str | None = None) -> list[dict]:
    sql = "SELECT * FROM entities WHERE 1=1"
    args: list = []
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    if as_of_episode is not None:
        sql += " AND first_episode<=?"
        args.append(as_of_episode)
    if type_:
        sql += " AND type=?"
        args.append(type_)
    if q:
        sql += " AND (canonical_name LIKE ? OR entity_id LIKE ? OR description LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if content:
        like = f"%{content}%"
        conds = ["c.entity_id=entities.entity_id",
                 "(c.attribute_path LIKE ? OR c.new_value LIKE ? OR c.old_value LIKE ?)"]
        cargs: list = [like, like, like]
        if as_of_episode is not None:
            conds.append("c.episode_no<=?")
            cargs.append(as_of_episode)
        sql += " AND EXISTS (SELECT 1 FROM entity_changes c WHERE " + " AND ".join(conds) + ")"
        args += cargs
    if any:
        like = f"%{any}%"
        aconds = ["canonical_name LIKE ?", "entity_id LIKE ?",
                  "description LIKE ?", "type LIKE ?",
                  "lifecycle_status LIKE ?"]
        aargs: list = [like] * 5
        cconds = ["c.entity_id=entities.entity_id",
                  "(c.attribute_path LIKE ? OR c.new_value LIKE ?"
                  " OR c.old_value LIKE ? OR c.story_time LIKE ?"
                  " OR c.evidence_quote LIKE ?)"]
        cargs2: list = [like] * 5
        if as_of_episode is not None:
            cconds.append("c.episode_no<=?")
            cargs2.append(as_of_episode)
        aconds.append("EXISTS (SELECT 1 FROM entity_changes c WHERE "
                      + " AND ".join(cconds) + ")")
        args += aargs + cargs2
        for side, other in (('subject_id', 'object_id'),
                            ('object_id', 'subject_id')):
            rconds = [f"r.{side}=entities.entity_id",
                      "(r.predicate LIKE ? OR o.canonical_name LIKE ?)"]
            rargs: list = [like, like]
            if as_of_episode is not None:
                rconds.append("r.valid_from_episode<=?")
                rconds.append("(r.valid_to_episode IS NULL OR r.valid_to_episode>?)")
                rargs += [as_of_episode, as_of_episode]
            aconds.append("EXISTS (SELECT 1 FROM entity_relations r"
                          f" JOIN entities o ON o.entity_id=r.{other} WHERE "
                          + " AND ".join(rconds) + ")")
            args += rargs
        sql += " AND (" + " OR ".join(aconds) + ")"
    sql += " ORDER BY entity_id"
    return [dict(r) for r in conn.execute(sql, args)]


def _snippet_window(text: str, query: str, window: int = 24) -> str:
    """발췌: 매칭 위치 앞뒤 window자씩 잘라 …로 표시한다."""
    i = text.lower().find(query.lower())
    if i < 0:
        return ""
    start = max(0, i - window)
    end = min(len(text), i + len(query) + window)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


def _show_value(value) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def entity_match_snippet(conn, entity_id: str, query: str,
                         as_of_episode: int | None = None) -> str:
    """통합 검색 근거 발췌: 매칭된 변경·인용·설명·관계를 앞뒤 문맥과 함께 반환한다.

    이름·ID로만 매칭되면 빈 문자열(이름 자체가 근거). 기준 회차가 있으면
    그 회차까지의 변경·유효 관계에서만 찾는다.
    """
    q = (query or "").strip()
    if not q:
        return ""
    sql = ("SELECT attribute_path, old_value, new_value, story_time,"
           " evidence_quote FROM entity_changes"
           " WHERE entity_id=? AND (attribute_path LIKE ?"
           " OR new_value LIKE ? OR old_value LIKE ? OR story_time LIKE ?"
           " OR evidence_quote LIKE ?)")
    args: list = [entity_id] + [f"%{q}%"] * 5
    if as_of_episode is not None:
        sql += " AND episode_no<=?"
        args.append(as_of_episode)
    sql += " ORDER BY episode_no, change_id"
    for r in conn.execute(sql, args):
        old = _show_value(_u(r["old_value"]))
        new = _show_value(_u(r["new_value"]))
        texts = [f"{r['attribute_path']}: {new}" if not old else
                 f"{r['attribute_path']}: {old} → {new}",
                 r["evidence_quote"] or "", r["story_time"] or ""]
        for t in texts:
            snip = _snippet_window(t, q)
            if snip:
                return snip
    row = conn.execute("SELECT description FROM entities WHERE entity_id=?",
                       (entity_id,)).fetchone()
    if row and row["description"]:
        snip = _snippet_window(row["description"], q)
        if snip:
            return snip
    rsql = ("SELECT r.predicate, r.subject_id, s.canonical_name AS subj,"
            " o.canonical_name AS obj FROM entity_relations r"
            " JOIN entities s ON s.entity_id=r.subject_id"
            " JOIN entities o ON o.entity_id=r.object_id"
            " WHERE (r.subject_id=? OR r.object_id=?)"
            " AND (r.predicate LIKE ? OR s.canonical_name LIKE ?"
            " OR o.canonical_name LIKE ?)")
    rargs: list = [entity_id, entity_id] + [f"%{q}%"] * 3
    if as_of_episode is not None:
        rsql += " AND r.valid_from_episode<=? AND (r.valid_to_episode IS NULL OR r.valid_to_episode>?)"
        rargs += [as_of_episode, as_of_episode]
    rsql += " ORDER BY r.valid_from_episode, r.relation_id"
    for r in conn.execute(rsql, rargs):
        if r["subject_id"] == entity_id:
            text = f"→ {r['obj']} · {r['predicate']}"
        else:
            text = f"← {r['subj']} · {r['predicate']}"
        snip = _snippet_window(text, q)
        if snip:
            return snip
    return ""


# -- reconstruction ----------------------------------------------------
def _apply_operation(attrs: dict, operation: str, path: str, new_value) -> dict:
    """Apply one change to an attribute dict. Pure function (deterministic)."""
    if operation == "no_change":
        return attrs
    if operation == "end_relation":
        rels = list(attrs.get("relations", []))
        attrs["relations"] = [r for r in rels if not (
            isinstance(r, dict) and r.get("predicate") == path and
            r.get("object_id") == (new_value or {}).get("object_id"))]
        return attrs
    keys = [k for k in path.split(".") if k]
    if not keys:
        return attrs
    node = attrs
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    leaf = keys[-1]
    if operation in ("create", "set"):
        node[leaf] = new_value
    elif operation == "add":
        cur = node.get(leaf, [])
        node[leaf] = (cur if isinstance(cur, list) else [cur]) + [new_value]
    elif operation == "remove":
        cur = node.get(leaf)
        if isinstance(cur, list):
            node[leaf] = [v for v in cur if v != new_value]
        elif cur == new_value:
            node.pop(leaf, None)
    return attrs


def reconstruct_state(conn, entity_id: str, as_of_episode: int,
                      work_id: int | None = None) -> dict:
    """State at end of episode N from ACTIVE committed sets only.

    Snapshots are a cache, never the source: on any doubt the caller can
    delete the snapshot row and this rebuilds by replaying history.
    """
    ent = get_entity(conn, entity_id)
    if ent is None:
        return {}
    if work_id is not None and ent["work_id"] != work_id:
        return {}
    row = conn.execute(
        "SELECT attributes_json FROM entity_snapshots WHERE entity_id=? AND episode_no=?",
        (entity_id, as_of_episode),
    ).fetchone()
    if row:
        return json.loads(row["attributes_json"])
    attrs: dict = {}
    changes = conn.execute(
        "SELECT c.operation, c.attribute_path, c.new_value, c.narrative_mode"
        " FROM entity_changes c"
        " JOIN change_proposals s ON s.proposal_set_id=c.proposal_set_id"
        " WHERE c.entity_id=? AND c.episode_no<=? AND s.status='committed'"
        " ORDER BY c.episode_no, c.change_id",
        (entity_id, as_of_episode),
    ).fetchall()
    for ch in changes:
        if ch["narrative_mode"] in NON_STATE_MODES:
            continue  # dreams, guesses, lies, recollections stay history-only
        _apply_operation(attrs, ch["operation"], ch["attribute_path"], _u(ch["new_value"]))
    ver = current_memory_version(conn, ent["work_id"])
    conn.execute(
        "INSERT OR REPLACE INTO entity_snapshots(entity_id, work_id, episode_no,"
        " memory_version, attributes_json) VALUES(?,?,?,?,?)",
        (entity_id, ent["work_id"], as_of_episode, ver, _j(attrs)),
    )
    conn.commit()
    return attrs


def entity_history(conn, entity_id: str, from_ep: int | None = None,
                   to_ep: int | None = None) -> list[dict]:
    sql = ("SELECT * FROM entity_changes WHERE entity_id=?")
    args: list = [entity_id]
    if from_ep is not None:
        sql += " AND episode_no>=?"
        args.append(from_ep)
    if to_ep is not None:
        sql += " AND episode_no<=?"
        args.append(to_ep)
    sql += " ORDER BY episode_no, change_id"
    return [dict(r) for r in conn.execute(sql, args)]


def committed_rules(conn, work_id: int, episode_no: int) -> list[dict]:
    """Active rule-kind facts (machine-checkable constraints)."""
    rows = conn.execute(
        "SELECT c.*, e.canonical_name FROM entity_changes c"
        " JOIN change_proposals s ON s.proposal_set_id=c.proposal_set_id"
        " JOIN entities e ON e.entity_id=c.entity_id"
        " WHERE c.work_id=? AND c.episode_no<=? AND c.kind='rule'"
        " AND s.status='committed' ORDER BY c.episode_no, c.change_id",
        (work_id, episode_no)).fetchall()
    return [dict(r) for r in rows]


def active_relations(conn, work_id: int, episode_no: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM entity_relations WHERE work_id=?"
        " AND valid_from_episode<=? AND (valid_to_episode IS NULL"
        " OR valid_to_episode>?) ORDER BY relation_id",
        (work_id, episode_no, episode_no)).fetchall()
    return [dict(r) for r in rows]


def confirmed_aliases(conn, entity_id: str) -> list[str]:
    rows = conn.execute("SELECT alias FROM entity_aliases"
                        " WHERE entity_id=? AND status='confirmed'"
                        " ORDER BY alias_id", (entity_id,)).fetchall()
    return [r["alias"] for r in rows]


def find_entities_by_name_or_alias(conn, work_id: int,
                                   name: str) -> list[dict]:
    """같은 작품에서 이름·확정 별명이 일치하는 개체 (§6 관련 검색).

    미확정 후보 별명은 합치지 않는다. 합치면 동명이인이 섞인다.
    """
    if not (name or "").strip():
        return []
    rows = conn.execute(
        "SELECT * FROM entities WHERE work_id=? AND canonical_name=?",
        (work_id, name)).fetchall()
    out = [dict(r) for r in rows]
    seen = {e["entity_id"] for e in out}
    for r in conn.execute(
            "SELECT e.* FROM entity_aliases a"
            " JOIN entities e ON e.entity_id=a.entity_id"
            " WHERE a.work_id=? AND a.alias=? AND a.status='confirmed'",
            (work_id, name)):
        if r["entity_id"] not in seen:
            seen.add(r["entity_id"])
            out.append(dict(r))
    return out


def related_entities(conn, work_id: int, entity_id: str,
                     episode_no: int) -> dict:
    """개체별 정밀 대조용 관련 개체 묶음 (§6 2단계).

    - relations: 관계 엣지로 연결된 개체 (양방향, 유효 구간 내)
    - co_participants: 같은 확정 사건에 함께 등장한 개체
    - shared_rules: 같은 규칙 술어를 공유하는 개체
    모두 확정된 것만 쓴다. 임시안끼리는 비교하지 않는다.
    """
    rels = [r for r in active_relations(conn, work_id, episode_no)
            if r["subject_id"] == entity_id or r["object_id"] == entity_id]
    rel_peers = [{"entity_id": r["object_id"] if r["subject_id"] == entity_id
                  else r["subject_id"],
                  "predicate": r["predicate"],
                  "outgoing": r["subject_id"] == entity_id} for r in rels]
    co_rows = conn.execute(
        "SELECT DISTINCT p2.entity_id, e.summary, e.episode_no"
        " FROM event_participants p1"
        " JOIN event_participants p2 ON p2.event_id=p1.event_id"
        " JOIN events e ON e.event_id=p1.event_id"
        " WHERE p1.entity_id=? AND p2.entity_id!=?"
        " AND e.work_id=? AND e.episode_no<?"
        " ORDER BY e.episode_no DESC LIMIT 20",
        (entity_id, entity_id, work_id, episode_no)).fetchall()
    my_rules = {r["attribute_path"] for r in committed_rules(conn, work_id,
                                                             episode_no)
                if r["entity_id"] == entity_id}
    shared = []
    if my_rules:
        for r in committed_rules(conn, work_id, episode_no):
            if r["entity_id"] != entity_id and \
                    r["attribute_path"] in my_rules:
                shared.append({"entity_id": r["entity_id"],
                               "predicate": r["attribute_path"]})
    return {"relations": rel_peers,
            "co_participants": [dict(r) for r in co_rows],
            "shared_rules": shared}


# -- narrative units ----------------------------------------------------
_UNIT_SPLIT = re.compile(r"\n\s*\n")


def ensure_units(conn, work_id: int, episode_no: int, revision_id: int,
                 text: str) -> list[dict]:
    """Split a revision into paragraph units (offset coordinates, §2).

    Offsets are Python str indices into the revision text (== UTF-16
    offsets for BMP text, which covers Korean). Idempotent per revision.
    """
    rows = conn.execute(
        "SELECT * FROM narrative_units WHERE manuscript_revision_id=?"
        " ORDER BY unit_index", (revision_id,)).fetchall()
    if rows:
        return [dict(r) for r in rows]
    units: list[dict] = []
    pos = 0
    idx = 0
    for m in _UNIT_SPLIT.finditer(text):
        end = m.start()
        if text[pos:end].strip():
            units.append((idx, pos, end))
            idx += 1
        pos = m.end()
    if text[pos:].strip():
        units.append((idx, pos, len(text)))
    if not units and text.strip():
        units.append((0, 0, len(text)))
    for i, s, e in units:
        h = hashlib.sha1(text[s:e].encode("utf-8")).hexdigest()[:16]
        conn.execute(
            "INSERT INTO narrative_units(work_id, episode_no,"
            " manuscript_revision_id, unit_index, start_offset, end_offset,"
            " text_hash) VALUES(?,?,?,?,?,?,?)",
            (work_id, episode_no, revision_id, i, s, e, h))
    conn.commit()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM narrative_units WHERE manuscript_revision_id=?"
        " ORDER BY unit_index", (revision_id,))]


def unit_index_for_offset(units: list[dict], offset: int | None) -> int | None:
    if offset is None:
        return None
    for u in units:
        if u["start_offset"] <= offset < u["end_offset"]:
            return u["unit_index"]
    return units[-1]["unit_index"] if units else None


# -- memory versions ------------------------------------------------------
def current_memory_version(conn, work_id: int) -> int:
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM memory_versions"
                       " WHERE work_id=?", (work_id,)).fetchone()
    return row["v"]


def _bump_memory_version(conn, work_id: int, episode_no: int,
                         proposal_set_id: int) -> int:
    ver = current_memory_version(conn, work_id) + 1
    conn.execute("INSERT INTO memory_versions(work_id, version, episode_no,"
                 " proposal_set_id, created_at) VALUES(?,?,?,?,?)",
                 (work_id, ver, episode_no, proposal_set_id, now_iso()))
    return ver


# -- proposals -----------------------------------------------------------
def store_proposal(conn, *, episode_no, revision_id, items, mentions=(),
                   events=(), work_id=0, base_memory_version=0,
                   model_version="", prompt_version="", units_total=0,
                   unit_statuses=()) -> int:
    cur = conn.execute(
        "INSERT INTO change_proposals(work_id, episode_no, manuscript_revision_id,"
        " base_memory_version, model_version, prompt_version, units_total,"
        " unit_statuses_json, events_json, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (work_id, episode_no, revision_id, base_memory_version,
         model_version, prompt_version, units_total,
         _j(list(unit_statuses)), _j(list(events)), now_iso()),
    )
    set_id = cur.lastrowid
    for it in items:
        conn.execute(
            "INSERT INTO change_proposal_items(proposal_set_id, entity_id,"
            " candidate_name, explicit_new, kind, operation, attribute_path,"
            " old_value, new_value, standard_value, story_time, narrative_mode,"
            " unit_index, evidence_quote, evidence_start, evidence_end,"
            " validation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (set_id, it.get("entity_id"), it.get("candidate_name", ""),
             1 if it.get("explicit_new") else 0,
             norm_kind(it.get("kind", "attribute") or "attribute"),
             it["operation"], it.get("attribute_path", ""),
             _j(it.get("old_value", "")) if it.get("old_value", "") != "" else "",
             _j(it.get("new_value", "")) if it.get("new_value", "") != "" else "",
             _j(it.get("standard_value", "")) if it.get("standard_value", "") != "" else "",
             it.get("story_time", ""),
             norm_mode(it.get("narrative_mode", "narration") or "narration"),
             it.get("unit_index"), it.get("evidence_quote", ""),
             it.get("evidence_start"), it.get("evidence_end"),
             it.get("validation", "pending")),
        )
    for m in mentions:
        conn.execute(
            "INSERT INTO entity_mentions(entity_id, work_id, episode_no,"
            " manuscript_revision_id, unit_index, quote, start_offset,"
            " end_offset) VALUES(?,?,?,?,?,?,?,?)",
            (m["entity_id"], work_id, episode_no, revision_id,
             m.get("unit_index"), m["quote"],
             m.get("start_offset"), m.get("end_offset")),
        )
    conn.commit()
    return set_id


def record_failed_run(conn, *, episode_no, revision_id, work_id=0,
                      base_memory_version=0, model_version="",
                      prompt_version="", units_total=0, error="") -> int:
    cur = conn.execute(
        "INSERT INTO change_proposals(work_id, episode_no, manuscript_revision_id,"
        " base_memory_version, model_version, prompt_version, units_total,"
        " error, status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (work_id, episode_no, revision_id, base_memory_version,
         model_version, prompt_version, units_total, error[:1000],
         "failed", now_iso()),
    )
    conn.commit()
    return cur.lastrowid


def get_proposals(conn, episode_no: int,
                  work_id: int | None = None) -> list[dict]:
    sql = ("SELECT s.proposal_set_id, s.status AS set_status,"
           " s.manuscript_revision_id, i.* FROM change_proposals s"
           " JOIN change_proposal_items i ON i.proposal_set_id=s.proposal_set_id"
           " WHERE s.episode_no=?")
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND s.work_id=?"
        args.append(work_id)
    sql += " ORDER BY s.proposal_set_id DESC, i.item_id"
    return [dict(r) for r in conn.execute(sql, args)]


def get_proposal_events(conn, proposal_set_id: int) -> list[dict]:
    row = conn.execute("SELECT events_json FROM change_proposals WHERE proposal_set_id=?",
                       (proposal_set_id,)).fetchone()
    if not row or not row["events_json"]:
        return []
    return json.loads(row["events_json"])


def set_item_validation(conn, item_id: int, validation: str, reason: str = "",
                        counter_quote: str = "", _commit: bool = True) -> None:
    conn.execute("UPDATE change_proposal_items SET validation=?, reason=?,"
                 " counter_quote=? WHERE item_id=?",
                 (validation, reason[:500], counter_quote[:1000], item_id))
    if _commit:
        conn.commit()


def discard_proposal_set(conn, set_id: int) -> None:
    conn.execute("UPDATE change_proposals SET status='discarded' WHERE proposal_set_id=?",
                 (set_id,))
    conn.commit()


def delete_pending_item(conn, item_id: int) -> dict:
    """임시 변경안의 항목 1건을 삭제한다. 확정분은 삭제 불가."""
    row = conn.execute(
        "SELECT i.item_id, s.episode_no, s.status"
        " FROM change_proposal_items i"
        " JOIN change_proposals s ON s.proposal_set_id=i.proposal_set_id"
        " WHERE i.item_id=?", (item_id,)).fetchone()
    if not row:
        raise KeyError(f"item {item_id} not found")
    if row["status"] != "pending":
        raise ValueError("committed items cannot be deleted")
    conn.execute("DELETE FROM change_proposal_items WHERE item_id=?",
                 (item_id,))
    conn.commit()
    return {"episode_no": row["episode_no"], "deleted": item_id}


def delete_pending_event(conn, proposal_set_id: int, index: int) -> dict:
    """임시 변경안의 사건 1건을 삭제한다. 확정분은 삭제 불가."""
    row = conn.execute("SELECT * FROM change_proposals WHERE proposal_set_id=?",
                       (proposal_set_id,)).fetchone()
    if not row:
        raise KeyError(f"proposal set {proposal_set_id} not found")
    if row["status"] != "pending":
        raise ValueError("committed events cannot be deleted")
    events = json.loads(row["events_json"] or "[]")
    if index < 0 or index >= len(events):
        raise IndexError(f"event {index} not found")
    events.pop(index)
    conn.execute("UPDATE change_proposals SET events_json=? WHERE proposal_set_id=?",
                 (_j(events), proposal_set_id))
    conn.commit()
    return {"proposal_set_id": proposal_set_id, "deleted": index,
            "events": len(events)}


def delete_pending_event_participant(conn, proposal_set_id: int, index: int,
                                     pindex: int) -> dict:
    """임시 변경안의 사건 참여자 1명을 제외한다. 확정분은 변경 불가."""
    row = conn.execute("SELECT * FROM change_proposals WHERE proposal_set_id=?",
                       (proposal_set_id,)).fetchone()
    if not row:
        raise KeyError(f"proposal set {proposal_set_id} not found")
    if row["status"] != "pending":
        raise ValueError("committed events cannot be changed")
    events = json.loads(row["events_json"] or "[]")
    if index < 0 or index >= len(events):
        raise IndexError(f"event {index} not found")
    parts = events[index].get("participants", [])
    if pindex < 0 or pindex >= len(parts):
        raise IndexError(f"participant {pindex} not found")
    parts.pop(pindex)
    conn.execute("UPDATE change_proposals SET events_json=? WHERE proposal_set_id=?",
                 (_j(events), proposal_set_id))
    conn.commit()
    return {"proposal_set_id": proposal_set_id, "event": index,
            "removed": pindex, "participants": len(parts)}


def discard_pending_for_revision(conn, episode_no: int, revision_id: int) -> None:
    """Drop stale pending sets before a fresh extraction on the same text."""
    conn.execute("UPDATE change_proposals SET status='discarded'"
                 " WHERE episode_no=? AND manuscript_revision_id=?"
                 " AND status='pending'",
                 (episode_no, revision_id))
    conn.commit()


def discard_all_pending(conn, episode_no: int,
                        work_id: int | None = None) -> int:
    """재추출 대체 의미: 해당 회차의 임시 변경안을 모두 버린다."""
    sql = ("UPDATE change_proposals SET status='discarded'"
           " WHERE episode_no=? AND status='pending'")
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount


def discard_pending_except(conn, episode_no: int, keep_id: int,
                           work_id: int | None = None) -> int:
    """완성 후 교체(§5.3): 새 실행을 살리고 나머지 pending을 버린다."""
    sql = ("UPDATE change_proposals SET status='discarded'"
           " WHERE episode_no=? AND status='pending' AND proposal_set_id!=?")
    args: list = [episode_no, keep_id]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount


def get_pending_set_for_revision(conn, episode_no: int, revision_id: int,
                                work_id: int | None = None) -> dict | None:
    sql = ("SELECT * FROM change_proposals WHERE episode_no=?"
           " AND manuscript_revision_id=? AND status='pending'")
    args: list = [episode_no, revision_id]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    sql += " ORDER BY proposal_set_id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def latest_pending_set(conn, episode_no: int,
                       work_id: int | None = None) -> dict | None:
    sql = ("SELECT * FROM change_proposals WHERE episode_no=?"
           " AND status='pending'")
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    sql += " ORDER BY proposal_set_id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def latest_set(conn, episode_no: int,
               work_id: int | None = None) -> dict | None:
    """Newest run of any status (for basis display incl. failed)."""
    sql = "SELECT * FROM change_proposals WHERE episode_no=?"
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    sql += " ORDER BY proposal_set_id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def has_any_proposal(conn, episode_no: int,
                     work_id: int | None = None) -> bool:
    sql = "SELECT 1 FROM change_proposals WHERE episode_no=?"
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    row = conn.execute(sql + " LIMIT 1", args).fetchone()
    return row is not None


def approve_proposal_set(conn, set_id: int, _commit: bool = True) -> None:
    """작가 승인: pending 항목을 ok로 확정한다.

    deferred/error는 제외 유지되어 절대 커밋되지 않는다.
    """
    conn.execute("UPDATE change_proposal_items SET validation='ok'"
                 " WHERE proposal_set_id=? AND validation='pending'",
                 (set_id,))
    if _commit:
        conn.commit()


def latest_committed_set(conn, episode_no: int,
                         work_id: int | None = None) -> dict | None:
    """Newest committed set of the episode (any manuscript revision)."""
    sql = ("SELECT * FROM change_proposals WHERE episode_no=?"
           " AND status='committed'")
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    sql += " ORDER BY proposal_set_id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None

def get_committed_set_for_revision(conn, episode_no: int, revision_id: int,
                                  work_id: int | None = None) -> dict | None:
    sql = ("SELECT * FROM change_proposals WHERE episode_no=?"
           " AND manuscript_revision_id=? AND status='committed'")
    args: list = [episode_no, revision_id]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    sql += " ORDER BY proposal_set_id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def approve_manuscript_revision(conn, revision_id: int,
                                _commit: bool = True) -> None:
    conn.execute("UPDATE manuscript_revisions SET approval_status='approved'"
                 " WHERE revision_id=?", (revision_id,))
    if _commit:
        conn.commit()


def save_episode_memory(conn, *, episode_no: int, revision_id: int,
                        proposal_set_id: int,
                        work_id: int | None = None,
                        writing_locale: str = "ko-KR") -> dict:
    """검토 확정: 새 변경안을 원자적으로 공식 기억으로 저장한다(§5).

    같은 회차의 이전 확정 실행은 superseded로만 바꾸고, 그 사실들은
    이력으로 남긴다. 공식 조회는 활성(committed) 실행만 사용한다.
    기준 기억(base_memory_version)이 바뀌었으면 저장하지 않고 재검토를
    요구한다.
    """
    pset = conn.execute("SELECT * FROM change_proposals WHERE proposal_set_id=?",
                        (proposal_set_id,)).fetchone()
    if not pset:
        raise KeyError(f"proposal set {proposal_set_id} not found")
    if pset["status"] != "pending":
        raise ValueError(f"proposal set {proposal_set_id} is {pset['status']}")
    wid = work_id if work_id is not None else pset["work_id"]
    if current_memory_version(conn, wid) != (pset["base_memory_version"] or 0):
        if writing_locale == "en-US":
            raise ValueError("base memory changed; re-review is required")
        raise ValueError("기준 기억이 바뀌어 재검토가 필요합니다")
    try:
        conn.execute("UPDATE change_proposals SET status='superseded'"
                     " WHERE episode_no=? AND work_id=? AND status='committed'",
                     (episode_no, wid))
        approve_proposal_set(conn, proposal_set_id, _commit=False)
        version = _bump_memory_version(conn, wid, episode_no, proposal_set_id)
        result = commit_episode(conn, episode_no=episode_no,
                                revision_id=revision_id,
                                proposal_set_id=proposal_set_id,
                                work_id=wid, memory_version=version,
                                writing_locale=writing_locale,
                                _commit=False)
        for eid in result["entities"]:
            conn.execute("DELETE FROM entity_snapshots WHERE entity_id=?"
                         " AND episode_no>=?", (eid, episode_no))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    for eid in result["entities"]:
        reconstruct_state(conn, eid, episode_no, wid)
    return {**result, "memory_version": version}


def _entity_ref_count(conn, entity_id: str) -> int:
    n = 0
    n += conn.execute("SELECT COUNT(*) c FROM entity_changes"
                      " WHERE entity_id=?", (entity_id,)).fetchone()["c"]
    n += conn.execute("SELECT COUNT(*) c FROM event_participants"
                      " WHERE entity_id=?", (entity_id,)).fetchone()["c"]
    n += conn.execute("SELECT COUNT(*) c FROM entity_relations"
                      " WHERE subject_id=? OR object_id=?",
                      (entity_id, entity_id)).fetchone()["c"]
    n += conn.execute("SELECT COUNT(*) c FROM entity_mentions"
                      " WHERE entity_id=?", (entity_id,)).fetchone()["c"]
    n += conn.execute("SELECT COUNT(*) c FROM entity_aliases"
                      " WHERE entity_id=?", (entity_id,)).fetchone()["c"]
    return n


def committed_summary(conn, episode_no: int, work_id: int) -> dict:
    """이번 화에 확정 저장된 설정 규모(초기화 확인용)."""
    ch = conn.execute("SELECT COUNT(*) c FROM entity_changes"
                      " WHERE episode_no=? AND work_id=?",
                      (episode_no, work_id)).fetchone()["c"]
    ev = conn.execute("SELECT COUNT(*) c FROM events"
                      " WHERE episode_no=? AND work_id=?",
                      (episode_no, work_id)).fetchone()["c"]
    revs = [r["revision_id"] for r in conn.execute(
        "SELECT revision_id FROM manuscript_revisions"
        " WHERE episode_no=? AND work_id=?", (episode_no, work_id))]
    ac = 0
    if revs:
        q = ",".join("?" for _ in revs)
        ac = conn.execute(
            f"SELECT COUNT(*) c FROM entity_aliases WHERE work_id=?"
            f" AND manuscript_revision_id IN ({q})",
            (work_id, *revs)).fetchone()["c"]
    return {"changes": ch, "events": ev, "aliases": ac}


def reset_episode_memory(conn, episode_no: int, work_id: int) -> dict:
    """이번 화의 설정 기억을 모두 지운다(재추출의 전반전).

    지우는 것: 이 화의 추출 실행 전체(pending 포함)와 그 항목,
    확정 변경, 사건·참여자, 언급, 이 화에서 열린 관계, 이 화 출처의
    별명(확정·기각·후보), 참조가 끊긴 고아 설정과 그 별명, 이 화의 분석
    기록. 이 화에서 닫힌 관계는 다시 열고, 원고 승인 상태도 되돌린다.
    스냅샷은 캐시라서 이 화 이후분(>=N)을 지우면 조회 시 재구축된다.
    유지하는 것: 다른 화, 원고 본문, 일관성 이슈·대화.
    """
    counts = {"sets": 0, "changes": 0, "events": 0, "mentions": 0,
              "relations": 0, "relations_reopened": 0, "aliases": 0,
              "analyses": 0, "entities": 0}
    with conn:
        ref_ids: set[str] = set()
        for table in ("entity_changes", "entity_mentions"):
            ref_ids.update(
                r["entity_id"] for r in conn.execute(
                    f"SELECT DISTINCT entity_id FROM {table}"
                    " WHERE episode_no=? AND work_id=?",
                    (episode_no, work_id)))
        ev_ids = [r["event_id"] for r in conn.execute(
            "SELECT event_id FROM events WHERE episode_no=? AND work_id=?",
            (episode_no, work_id))]
        if ev_ids:
            q = ",".join("?" for _ in ev_ids)
            ref_ids.update(
                r["entity_id"] for r in conn.execute(
                    f"SELECT DISTINCT entity_id FROM event_participants"
                    f" WHERE event_id IN ({q})", ev_ids))
        ref_ids.update(
            r["eid"] for r in conn.execute(
                "SELECT subject_id AS eid FROM entity_relations"
                " WHERE work_id=? AND valid_from_episode=?"
                " UNION SELECT object_id FROM entity_relations"
                " WHERE work_id=? AND valid_from_episode=?",
                (work_id, episode_no, work_id, episode_no)))
        alias_revs = [r["revision_id"] for r in conn.execute(
            "SELECT revision_id FROM manuscript_revisions"
            " WHERE episode_no=? AND work_id=?", (episode_no, work_id))]
        if alias_revs:
            aq = ",".join("?" for _ in alias_revs)
            ref_ids.update(
                r["entity_id"] for r in conn.execute(
                    f"SELECT DISTINCT entity_id FROM entity_aliases"
                    f" WHERE work_id=? AND entity_id IS NOT NULL"
                    f" AND manuscript_revision_id IN ({aq})",
                    (work_id, *alias_revs)))
        ref_ids.discard(None)

        set_ids = [r["proposal_set_id"] for r in conn.execute(
            "SELECT proposal_set_id FROM change_proposals"
            " WHERE episode_no=? AND work_id=?",
            (episode_no, work_id))]
        if set_ids:
            q = ",".join("?" for _ in set_ids)
            conn.execute(
                f"DELETE FROM change_proposal_items WHERE proposal_set_id IN ({q})",
                set_ids)
            cur = conn.execute(
                f"DELETE FROM change_proposals WHERE proposal_set_id IN ({q})",
                set_ids)
            counts["sets"] = cur.rowcount
        cur = conn.execute("DELETE FROM entity_changes"
                           " WHERE episode_no=? AND work_id=?",
                           (episode_no, work_id))
        counts["changes"] = cur.rowcount
        if ev_ids:
            q = ",".join("?" for _ in ev_ids)
            conn.execute(f"DELETE FROM event_participants WHERE event_id IN ({q})",
                         ev_ids)
            cur = conn.execute(f"DELETE FROM events WHERE event_id IN ({q})",
                               ev_ids)
            counts["events"] = cur.rowcount
        cur = conn.execute("DELETE FROM entity_mentions"
                           " WHERE episode_no=? AND work_id=?",
                           (episode_no, work_id))
        counts["mentions"] = cur.rowcount
        cur = conn.execute("DELETE FROM entity_relations"
                           " WHERE work_id=? AND valid_from_episode=?",
                           (work_id, episode_no))
        counts["relations"] = cur.rowcount
        cur = conn.execute("UPDATE entity_relations SET valid_to_episode=NULL"
                           " WHERE work_id=? AND valid_to_episode=?",
                           (work_id, episode_no))
        counts["relations_reopened"] = cur.rowcount
        revs = [r["revision_id"] for r in conn.execute(
            "SELECT revision_id FROM manuscript_revisions"
            " WHERE episode_no=? AND work_id=?", (episode_no, work_id))]
        if revs:
            q = ",".join("?" for _ in revs)
            cur = conn.execute(
                f"DELETE FROM entity_aliases WHERE work_id=?"
                f" AND manuscript_revision_id IN ({q})",
                (work_id, *revs))
            counts["aliases"] = cur.rowcount
        conn.execute("DELETE FROM entity_snapshots"
                     " WHERE work_id=? AND episode_no>=?",
                     (work_id, episode_no))
        conn.execute("UPDATE manuscript_revisions SET approval_status='draft'"
                     " WHERE episode_no=? AND work_id=?"
                     " AND approval_status='approved'", (episode_no, work_id))
        cur = conn.execute("DELETE FROM consistency_analyses"
                           " WHERE episode_no=? AND work_id=?",
                           (episode_no, work_id))
        counts["analyses"] = cur.rowcount
        # 판단 근거인 기억이 사라지므로 이슈도 함께 지운다.
        cur = conn.execute("DELETE FROM consistency_issues"
                           " WHERE episode_no=? AND work_id=?",
                           (episode_no, work_id))
        counts["issues"] = cur.rowcount
        for eid in sorted(ref_ids):
            if _entity_ref_count(conn, eid) > 0:
                continue
            cur = conn.execute("DELETE FROM entity_aliases WHERE entity_id=?",
                               (eid,))
            counts["aliases"] += cur.rowcount
            # 스냅샷은 캐시: 고아는 전 회차분(0화 포함)을 함께 지운다.
            conn.execute("DELETE FROM entity_snapshots WHERE entity_id=?",
                         (eid,))
            cur = conn.execute(
                "DELETE FROM entities WHERE entity_id=? AND work_id=?",
                (eid, work_id))
            counts["entities"] += cur.rowcount
    return counts


# -- commit (idempotent) ---------------------------------------------------
def is_committed(conn, episode_no: int, revision_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM change_proposals WHERE episode_no=? AND manuscript_revision_id=?"
        " AND status='committed'",
        (episode_no, revision_id),
    ).fetchone()
    return row is not None


def _commit_resolve(conn, work_id: int, entity_id: str | None,
                    candidate: str, explicit_new: bool,
                    new_ids: dict[str, str], episode_no: int,
                    writing_locale: str = "ko-KR") -> str:
    """Stable identity (§5.5): reuse the resolved entity whenever possible.

    A confirmed link is always an author decision, so it wins over both
    the model's explicit-new mark and same-name matching. A new entity
    is created only for explicit-new candidates without a link, or for
    names unknown to this work.
    """
    if entity_id:
        ent = get_entity(conn, entity_id)
        if ent and (work_id is None or ent["work_id"] == work_id):
            return entity_id
    name = candidate or ("Unnamed" if writing_locale == "en-US" else "이름없음")
    if name in new_ids:
        return new_ids[name]
    if work_id is not None:
        row = conn.execute(
            "SELECT a.entity_id FROM entity_aliases a"
            " JOIN entities e ON e.entity_id=a.entity_id"
            " WHERE a.work_id=? AND a.alias=? AND a.status='confirmed'",
            (work_id, name)).fetchone()
        if row and row["entity_id"]:
            new_ids[name] = row["entity_id"]
            return row["entity_id"]
    if not explicit_new and work_id is not None:
        row = conn.execute("SELECT entity_id FROM entities"
                           " WHERE work_id=? AND canonical_name=?",
                           (work_id, name)).fetchone()
        if row:
            new_ids[name] = row["entity_id"]
            return row["entity_id"]
    eid = next_entity_id(conn, "character")
    create_entity(conn, entity_id=eid, work_id=work_id or 0, type_="character",
                  name=name, first_episode=episode_no, _commit=False)
    new_ids[name] = eid
    return eid


def _relation_object(value) -> tuple[str, str]:
    """Return (object_name, detail) from a relation item value."""
    if isinstance(value, dict):
        return (value.get("object") or value.get("object_name") or "",
                value.get("detail") or "")
    if isinstance(value, str):
        return value, ""
    return "", ""


def _auto_link_aliases(conn, work_id: int) -> int:
    """저장 후 정리: 같은 이름의 설정이 딱 1건이면 후보를 대기열에서 치운다.

    확정 연결은 작가 판단 전용으로 남겨 둔다. 자동 정리가 확정 행을 만들면
    이후 화의 진짜 새 인물(new:) 분리를 가려 버리기 때문이다.
    동명이인(2건 이상)이나 설정 없는 이름은 작가 판단이 필요해서 남긴다.
    """
    if not work_id:
        return 0
    linked = 0
    cands = conn.execute(
        "SELECT alias_id, alias FROM entity_aliases"
        " WHERE work_id=? AND status='candidate'", (work_id,)).fetchall()
    for c in cands:
        rows = conn.execute(
            "SELECT entity_id FROM entities"
            " WHERE work_id=? AND canonical_name=?",
            (work_id, c["alias"])).fetchall()
        if len(rows) != 1:
            continue
        conn.execute(
            "DELETE FROM entity_aliases WHERE alias_id=?",
            (c["alias_id"],))
        linked += 1
    return linked


def commit_episode(conn, *, episode_no, revision_id, proposal_set_id,
                   work_id: int | None = None, memory_version: int = 0,
                   writing_locale: str = "ko-KR",
                   _commit: bool = True) -> dict:
    """Apply approved proposal items. Idempotent on (episode, revision)."""
    if is_committed(conn, episode_no, revision_id):
        return {"committed": False, "reason": "already_committed"}
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM change_proposal_items WHERE proposal_set_id=? ORDER BY item_id",
        (proposal_set_id,)).fetchall()]

    def _apply() -> tuple[int, set[str], dict[str, str]]:
        applied = 0
        touched: set[str] = set()
        new_ids: dict[str, str] = {}
        wid = work_id
        if wid is None:
            srow = conn.execute("SELECT work_id FROM change_proposals"
                                " WHERE proposal_set_id=?",
                                (proposal_set_id,)).fetchone()
            wid = srow["work_id"] if srow else 0
        for it in items:
            if it["validation"] not in ("ok", "change"):
                continue  # error|deferred|pending never commit
            if it["operation"] == "create" or it["entity_id"] is None:
                entity_id = _commit_resolve(
                    conn, wid, it["entity_id"], it["candidate_name"],
                    bool(it["explicit_new"]), new_ids, episode_no,
                    writing_locale)
            else:
                entity_id = it["entity_id"]
                ent = get_entity(conn, entity_id)
                if ent is None or (wid and ent["work_id"] != wid):
                    entity_id = _commit_resolve(
                        conn, wid, None, it["candidate_name"] or "",
                        False, new_ids, episode_no, writing_locale)
            conn.execute(
                "INSERT INTO entity_changes(entity_id, work_id, episode_no,"
                " manuscript_revision_id, proposal_set_id, memory_version,"
                " kind, attribute_path, operation, old_value, new_value,"
                " standard_value, story_time, narrative_mode, evidence_quote,"
                " evidence_start, evidence_end, committed_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (entity_id, wid, episode_no, revision_id, proposal_set_id,
                 memory_version, it["kind"], it["attribute_path"],
                 it["operation"], it["old_value"], it["new_value"],
                 it.get("standard_value", ""), it["story_time"],
                 it.get("narrative_mode", "narration") or "narration",
                 it["evidence_quote"], it["evidence_start"],
                 it["evidence_end"], now_iso()),
            )
            applied += 1
            touched.add(entity_id)
            if it["kind"] == "relation":
                _commit_relation(conn, wid, entity_id, episode_no,
                                 revision_id, it, new_ids)
            elif it["operation"] == "end_relation":
                _close_relation(conn, wid, entity_id, episode_no, it)
        for ev in get_proposal_events(conn, proposal_set_id):
            if ev.get("validation") == "deferred":
                continue
            cur = conn.execute(
                "INSERT INTO events(work_id, episode_no, summary, story_time,"
                " cause, consequence, evidence_revision_id, evidence_quote,"
                " evidence_start, evidence_end) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (wid, episode_no, ev.get("summary", ""),
                 ev.get("story_time", ""), ev.get("cause", ""),
                 ev.get("consequence", ""), revision_id,
                 ev.get("evidence_quote", ""), ev.get("evidence_start"),
                 ev.get("evidence_end")))
            for p in ev.get("participants", []):
                pid = p.get("entity_id")
                if not pid and p.get("candidate_name"):
                    pid = _participant_resolve(conn, wid, episode_no,
                                               p["candidate_name"], new_ids)
                elif pid:
                    ent = get_entity(conn, pid)
                    if ent is None or (wid and ent["work_id"] != wid):
                        pid = None
                if pid:
                    conn.execute(
                        "INSERT OR IGNORE INTO event_participants(event_id,"
                        " entity_id, role) VALUES(?,?,?)",
                        (cur.lastrowid, pid, p.get("role", "")))
        conn.execute(
            "UPDATE change_proposals SET status='committed' WHERE proposal_set_id=?",
            (proposal_set_id,),
        )
        conn.execute(
            "UPDATE manuscript_revisions SET approval_status='approved'"
            " WHERE revision_id=?",
            (revision_id,),
        )
        linked = _auto_link_aliases(conn, wid)
        return applied, touched, new_ids, linked

    if _commit:
        with conn:
            applied, touched, _, linked = _apply()
    else:
        applied, touched, _, linked = _apply()
    for eid in touched:
        conn.execute("DELETE FROM entity_snapshots WHERE entity_id=? AND episode_no>=?",
                     (eid, episode_no))
    if _commit:
        conn.commit()
    for eid in touched:
        wid = work_id
        if wid is None:
            ent = get_entity(conn, eid)
            wid = ent["work_id"] if ent else 0
        reconstruct_state(conn, eid, episode_no, wid)
    return {"committed": True, "applied": applied, "entities": sorted(touched), "aliases_linked": linked}


def _participant_resolve(conn, work_id: int, episode_no: int, name: str,
                         new_ids: dict[str, str]) -> str | None:
    """Link an event participant name (author link first, else a new entity)."""
    if not name:
        return None
    if name in new_ids:
        return new_ids[name]
    row = conn.execute(
        "SELECT a.entity_id FROM entity_aliases a"
        " JOIN entities e ON e.entity_id=a.entity_id"
        " WHERE a.work_id=? AND a.alias=? AND a.status='confirmed'",
        (work_id, name)).fetchone()
    if row and row["entity_id"]:
        return row["entity_id"]
    row = conn.execute("SELECT entity_id FROM entities"
                       " WHERE work_id=? AND canonical_name=?",
                       (work_id, name)).fetchone()
    if row:
        return row["entity_id"]
    eid = next_entity_id(conn, "character")
    create_entity(conn, entity_id=eid, work_id=work_id, type_="character",
                  name=name, first_episode=episode_no, _commit=False)
    new_ids[name] = eid
    return eid


def _commit_relation(conn, work_id: int, subject_id: str, episode_no: int,
                     revision_id: int, it, new_ids: dict[str, str]) -> None:
    """Write a relation-kind fact to entity_relations (directed, §2)."""
    obj_name, _detail = _relation_object(_u(it["new_value"]))
    if not obj_name:
        return
    obj_id = _participant_resolve(conn, work_id, episode_no, obj_name, new_ids)
    if not obj_id:
        return
    dup = conn.execute(
        "SELECT 1 FROM entity_relations WHERE subject_id=? AND predicate=?"
        " AND object_id=? AND valid_to_episode IS NULL",
        (subject_id, it["attribute_path"], obj_id)).fetchone()
    if dup:
        return
    conn.execute(
        "INSERT INTO entity_relations(work_id, subject_id, predicate, object_id,"
        " valid_from_episode, story_time, evidence_revision_id, evidence_quote,"
        " evidence_start, evidence_end) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (work_id, subject_id, it["attribute_path"], obj_id, episode_no,
         it["story_time"], revision_id, it["evidence_quote"],
         it["evidence_start"], it["evidence_end"]))


def _close_relation(conn, work_id: int, subject_id: str, episode_no: int,
                    it) -> None:
    """End matching relations: valid during [from, to), ended this episode."""
    obj_name, _detail = _relation_object(_u(it["new_value"]))
    sql = ("UPDATE entity_relations SET valid_to_episode=?"
           " WHERE subject_id=? AND predicate=? AND valid_to_episode IS NULL")
    args: list = [episode_no, subject_id, it["attribute_path"]]
    if obj_name:
        row = conn.execute("SELECT entity_id FROM entities"
                           " WHERE work_id=? AND canonical_name=?",
                           (work_id, obj_name)).fetchone()
        if row:
            sql += " AND object_id=?"
            args.append(row["entity_id"])
    conn.execute(sql, args)


# -- aliases ---------------------------------------------------------------
def add_alias_candidate(conn, *, entity_id, work_id, alias, basis="",
                        revision_id=None, quote="", start=None, end=None,
                        _commit=True) -> int:
    cur = conn.execute(
        "INSERT INTO entity_aliases(work_id, entity_id, alias, basis,"
        " manuscript_revision_id, quote, start_offset, end_offset)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (work_id, entity_id, alias, basis, revision_id, quote, start, end),
    )
    if _commit:
        conn.commit()
    return cur.lastrowid


def list_alias_candidates(conn, work_id: int,
                          episode_no: int | None = None) -> list[dict]:
    del episode_no  # reserved: candidates are work-level until linked
    rows = conn.execute(
        "SELECT a.*, e.canonical_name AS entity_name FROM entity_aliases a"
        " LEFT JOIN entities e ON e.entity_id=a.entity_id"
        " WHERE a.work_id=? AND a.status='candidate' ORDER BY a.alias_id",
        (work_id,)).fetchall()
    return [dict(r) for r in rows]


def resolve_alias(conn, alias_id: int, *, entity_id: str | None,
                  basis: str) -> dict:
    """Author judgment: confirm link (entity_id) or register new entity (None)."""
    row = conn.execute("SELECT * FROM entity_aliases WHERE alias_id=?",
                       (alias_id,)).fetchone()
    if not row:
        raise KeyError(f"alias {alias_id} not found")
    if row["status"] != "candidate":
        return {"alias_id": alias_id, "status": row["status"], "repeated": True}
    if entity_id is None:
        new_id = next_entity_id(conn, "character")
        create_entity(conn, entity_id=new_id, work_id=row["work_id"],
                      type_="character", name=row["alias"], first_episode=0)
        conn.execute(
            "UPDATE entity_aliases SET entity_id=?, status='confirmed', basis=?"
            " WHERE alias_id=?",
            (new_id, basis, alias_id),
        )
        conn.commit()
        return {"alias_id": alias_id, "status": "confirmed", "entity_id": new_id,
                "new_entity": True}
    conn.execute(
        "UPDATE entity_aliases SET entity_id=?, status='confirmed', basis=?"
        " WHERE alias_id=?",
        (entity_id, basis, alias_id),
    )
    conn.commit()
    return {"alias_id": alias_id, "status": "confirmed", "entity_id": entity_id,
            "new_entity": False}


def reject_alias(conn, alias_id: int, basis: str = "") -> dict:
    conn.execute("UPDATE entity_aliases SET status='rejected', basis=? WHERE alias_id=?",
                 (basis, alias_id))
    conn.commit()
    return {"alias_id": alias_id, "status": "rejected"}


# -- invalidation ------------------------------------------------------------
def mark_stale_from(conn, episode_no: int,
                    work_id: int | None = None) -> int:
    """Mark later-episode pending validations stale (§5.9, broad).

    An earlier episode's fix invalidates every later pending proposal of
    the same work until re-extracted.
    """
    sql = ("UPDATE change_proposal_items SET validation='stale'"
           " WHERE proposal_set_id IN (SELECT proposal_set_id FROM change_proposals"
           " WHERE episode_no>? AND status='pending'")
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    sql += ")"
    cur = conn.execute(sql, args)
    conn.commit()
    return cur.rowcount