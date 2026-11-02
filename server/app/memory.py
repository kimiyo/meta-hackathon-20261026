"""Entity memory: reconstruction, snapshots, idempotent commit, invalidation.

Reads use committed rows only. The program owns all writes; AI output arrives
as proposal items and is applied only through commit_episode().
"""
from __future__ import annotations

import json
import sqlite3

from .db import now_iso

OPERATIONS = ("create", "set", "add", "remove", "end_relation", "no_change")
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


def next_entity_id(conn: sqlite3.Connection, type_: str) -> str:
    prefix = TYPE_PREFIX.get(type_, "en")
    row = conn.execute(
        "SELECT entity_id FROM entities WHERE entity_id LIKE ? ORDER BY entity_id DESC LIMIT 1",
        (f"{prefix}_%",),
    ).fetchone()
    n = int(row["entity_id"].split("_")[1]) + 1 if row else 1
    return f"{prefix}_{n:04d}"


def create_entity(conn, *, entity_id, type_, name, first_episode, description="",
                _commit=True):
    conn.execute(
        "INSERT INTO entities(entity_id, type, canonical_name, first_episode, description)"
        " VALUES(?,?,?,?,?)",
        (entity_id, type_, name, first_episode, description),
    )
    if _commit:
        conn.commit()
    return entity_id


def get_entity(conn, entity_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM entities WHERE entity_id=?", (entity_id,)).fetchone()
    return dict(row) if row else None


def search_entities(conn, type_: str | None = None, q: str | None = None) -> list[dict]:
    sql = "SELECT * FROM entities WHERE 1=1"
    args: list = []
    if type_:
        sql += " AND type=?"
        args.append(type_)
    if q:
        sql += " AND (canonical_name LIKE ? OR entity_id LIKE ? OR description LIKE ?)"
        args += [f"%{q}%", f"%{q}%", f"%{q}%"]
    sql += " ORDER BY entity_id"
    return [dict(r) for r in conn.execute(sql, args)]


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


def reconstruct_state(conn, entity_id: str, as_of_episode: int) -> dict:
    """State at end of episode N: snapshot(N) or replay of committed changes."""
    row = conn.execute(
        "SELECT attributes_json FROM entity_snapshots WHERE entity_id=? AND episode_no=?",
        (entity_id, as_of_episode),
    ).fetchone()
    if row:
        return json.loads(row["attributes_json"])
    attrs: dict = {}
    changes = conn.execute(
        "SELECT operation, attribute_path, new_value FROM entity_changes"
        " WHERE entity_id=? AND episode_no<=? ORDER BY episode_no, change_id",
        (entity_id, as_of_episode),
    ).fetchall()
    for ch in changes:
        _apply_operation(attrs, ch["operation"], ch["attribute_path"], _u(ch["new_value"]))
    conn.execute(
        "INSERT OR REPLACE INTO entity_snapshots(entity_id, episode_no, attributes_json)"
        " VALUES(?,?,?)",
        (entity_id, as_of_episode, _j(attrs)),
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


# -- proposals -----------------------------------------------------------
def store_proposal(conn, *, episode_no, revision_id, items, mentions=(),
                   events=()) -> int:
    cur = conn.execute(
        "INSERT INTO change_proposals(episode_no, manuscript_revision_id, created_at,"
        " events_json) VALUES(?,?,?,?)",
        (episode_no, revision_id, now_iso(), _j(list(events))),
    )
    set_id = cur.lastrowid
    for it in items:
        conn.execute(
            "INSERT INTO change_proposal_items(proposal_set_id, entity_id, candidate_name,"
            " operation, attribute_path, old_value, new_value, story_time, evidence_quote,"
            " evidence_start, evidence_end, validation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (set_id, it.get("entity_id"), it.get("candidate_name", ""),
             it["operation"], it.get("attribute_path", ""),
             _j(it.get("old_value", "")) if it.get("old_value", "") != "" else "",
             _j(it.get("new_value", "")) if it.get("new_value", "") != "" else "",
             it.get("story_time", ""), it.get("evidence_quote", ""),
             it.get("evidence_start"), it.get("evidence_end"),
             it.get("validation", "pending")),
        )
    for m in mentions:
        conn.execute(
            "INSERT INTO entity_mentions(entity_id, episode_no, manuscript_revision_id,"
            " quote, start_offset, end_offset) VALUES(?,?,?,?,?,?)",
            (m["entity_id"], episode_no, revision_id, m["quote"],
             m.get("start_offset"), m.get("end_offset")),
        )
    conn.commit()
    return set_id


def get_proposals(conn, episode_no: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.proposal_set_id, s.status AS set_status, s.manuscript_revision_id,"
        " i.* FROM change_proposals s"
        " JOIN change_proposal_items i ON i.proposal_set_id=s.proposal_set_id"
        " WHERE s.episode_no=? ORDER BY s.proposal_set_id DESC, i.item_id",
        (episode_no,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_proposal_events(conn, proposal_set_id: int) -> list[dict]:
    row = conn.execute("SELECT events_json FROM change_proposals WHERE proposal_set_id=?",
                       (proposal_set_id,)).fetchone()
    if not row or not row["events_json"]:
        return []
    return json.loads(row["events_json"])


def set_item_validation(conn, item_id: int, validation: str, reason: str = "",
                      counter_quote: str = "") -> None:
    conn.execute("UPDATE change_proposal_items SET validation=?, reason=?,"
                 " counter_quote=? WHERE item_id=?",
                 (validation, reason[:500], counter_quote[:1000], item_id))
    conn.commit()


def discard_proposal_set(conn, set_id: int) -> None:
    conn.execute("UPDATE change_proposals SET status='discarded' WHERE proposal_set_id=?",
                 (set_id,))
    conn.commit()


def discard_pending_for_revision(conn, episode_no: int, revision_id: int) -> None:
    """Drop stale pending sets before a fresh extraction on the same text."""
    conn.execute("UPDATE change_proposals SET status='discarded'"
                 " WHERE episode_no=? AND manuscript_revision_id=?"
                 " AND status='pending'",
                 (episode_no, revision_id))
    conn.commit()


def get_pending_set_for_revision(conn, episode_no: int,
                                revision_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM change_proposals WHERE episode_no=?"
        " AND manuscript_revision_id=? AND status='pending'"
        " ORDER BY proposal_set_id DESC LIMIT 1",
        (episode_no, revision_id)).fetchone()
    return dict(row) if row else None


def latest_pending_set(conn, episode_no: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM change_proposals WHERE episode_no=?"
        " AND status='pending' ORDER BY proposal_set_id DESC LIMIT 1",
        (episode_no,)).fetchone()
    return dict(row) if row else None


def has_any_proposal(conn, episode_no: int) -> bool:
    row = conn.execute("SELECT 1 FROM change_proposals WHERE episode_no=?"
                       " LIMIT 1", (episode_no,)).fetchone()
    return row is not None


def approve_proposal_set(conn, set_id: int) -> None:
    """작가 승인: pending 항목을 ok로 확정한다.

    deferred/error는 제외 유지되어 절대 커밋되지 않는다.
    """
    conn.execute("UPDATE change_proposal_items SET validation='ok'"
                 " WHERE proposal_set_id=? AND validation='pending'",
                 (set_id,))
    conn.commit()


def get_committed_set_for_revision(conn, episode_no: int,
                                  revision_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM change_proposals WHERE episode_no=?"
        " AND manuscript_revision_id=? AND status='committed'"
        " ORDER BY proposal_set_id DESC LIMIT 1",
        (episode_no, revision_id)).fetchone()
    return dict(row) if row else None


def approve_manuscript_revision(conn, revision_id: int) -> None:
    conn.execute("UPDATE manuscript_revisions SET approval_status='approved'"
                 " WHERE revision_id=?", (revision_id,))
    conn.commit()


def save_episode_memory(conn, *, episode_no: int, revision_id: int,
                       proposal_set_id: int) -> dict:
    """검토 확정: 미발간 회차의 기존 기억을 교체하고 새 변경안을 확정한다.

    같은 회차의 낡은 기억(이전 원고 기준)은 삭제 후 다시 쓰므로
    초안 기반 사실이 공식 기억에 남지 않는다. 호출자는 원고 버전이
    최신인지(신선도) 미리 확인해야 한다.
    """
    conn.execute("DELETE FROM event_participants WHERE event_id IN"
                 " (SELECT event_id FROM events WHERE episode_no=?)",
                 (episode_no,))
    conn.execute("DELETE FROM events WHERE episode_no=?", (episode_no,))
    conn.execute("DELETE FROM entity_changes WHERE episode_no=?",
                 (episode_no,))
    conn.execute("DELETE FROM entity_snapshots WHERE episode_no>=?",
                 (episode_no,))
    conn.execute("UPDATE change_proposals SET status='superseded'"
                 " WHERE episode_no=? AND status='committed'",
                 (episode_no,))
    conn.commit()
    approve_proposal_set(conn, proposal_set_id)
    return commit_episode(conn, episode_no=episode_no, revision_id=revision_id,
                          proposal_set_id=proposal_set_id)


# -- commit (idempotent) ---------------------------------------------------
def is_committed(conn, episode_no: int, revision_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM change_proposals WHERE episode_no=? AND manuscript_revision_id=?"
        " AND status='committed'",
        (episode_no, revision_id),
    ).fetchone()
    return row is not None


def commit_episode(conn, *, episode_no, revision_id, proposal_set_id) -> dict:
    """Atomically apply approved proposal items. Idempotent on (episode, revision)."""
    if is_committed(conn, episode_no, revision_id):
        return {"committed": False, "reason": "already_committed"}
    items = conn.execute(
        "SELECT * FROM change_proposal_items WHERE proposal_set_id=? ORDER BY item_id",
        (proposal_set_id,),
    ).fetchall()
    applied = 0
    touched: set[str] = set()
    new_ids: dict[str, str] = {}  # candidate_name → entity_id (one set, one entity)
    with conn:  # atomic transaction
        for it in items:
            if it["validation"] not in ("ok", "change"):
                continue  # error|deferred|pending never commit
            entity_id = it["entity_id"]
            if it["operation"] == "create" or entity_id is None:
                name = it["candidate_name"] or "이름없음"
                if name in new_ids:
                    entity_id = new_ids[name]
                else:
                    entity_id = next_entity_id(conn, "character")
                    create_entity(conn, entity_id=entity_id, type_="character",
                                  name=name, first_episode=episode_no,
                                  _commit=False)
                    new_ids[name] = entity_id
            conn.execute(
                "INSERT INTO entity_changes(entity_id, episode_no, manuscript_revision_id,"
                " attribute_path, operation, old_value, new_value, story_time,"
                " evidence_quote, committed_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (entity_id, episode_no, revision_id, it["attribute_path"],
                 it["operation"], it["old_value"], it["new_value"],
                 it["story_time"], it["evidence_quote"], now_iso()),
            )
            applied += 1
            touched.add(entity_id)
        for ev in get_proposal_events(conn, proposal_set_id):
            cur = conn.execute(
                "INSERT INTO events(episode_no, summary, story_time, cause, consequence)"
                " VALUES(?,?,?,?,?)",
                (episode_no, ev.get("summary", ""), ev.get("story_time", ""),
                 ev.get("cause", ""), ev.get("consequence", "")))
            for p in ev.get("participants", []):
                pid = p.get("entity_id") or new_ids.get(p.get("candidate_name", ""))
                if pid:
                    conn.execute(
                        "INSERT OR IGNORE INTO event_participants(event_id, entity_id, role)"
                        " VALUES(?,?,?)", (cur.lastrowid, pid, p.get("role", "")))
        conn.execute(
            "UPDATE change_proposals SET status='committed' WHERE proposal_set_id=?",
            (proposal_set_id,),
        )
        conn.execute(
            "UPDATE manuscript_revisions SET approval_status='approved'"
            " WHERE revision_id=?",
            (revision_id,),
        )
    for eid in touched:
        # Refresh snapshot caches at this episode (recompute, don't trust cache).
        conn.execute("DELETE FROM entity_snapshots WHERE entity_id=? AND episode_no>=?",
                     (eid, episode_no))
    conn.commit()
    for eid in touched:
        reconstruct_state(conn, eid, episode_no)
    return {"committed": True, "applied": applied, "entities": sorted(touched)}


# -- aliases ---------------------------------------------------------------
def add_alias_candidate(conn, *, entity_id, alias, basis="") -> int:
    cur = conn.execute(
        "INSERT INTO entity_aliases(entity_id, alias, basis) VALUES(?,?,?)",
        (entity_id, alias, basis),
    )
    conn.commit()
    return cur.lastrowid


def resolve_alias(conn, alias_id: int, *, entity_id: str | None, basis: str) -> dict:
    """Author judgment: confirm link (entity_id) or register new entity (None)."""
    row = conn.execute("SELECT * FROM entity_aliases WHERE alias_id=?",
                       (alias_id,)).fetchone()
    if not row:
        raise KeyError(f"alias {alias_id} not found")
    if row["status"] != "candidate":
        return {"alias_id": alias_id, "status": row["status"], "repeated": True}
    if entity_id is None:
        new_id = next_entity_id(conn, "character")
        create_entity(conn, entity_id=new_id, type_="character",
                      name=row["alias"], first_episode=0)
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
def mark_stale_from(conn, episode_no: int) -> int:
    """Mark later-episode validations stale after an earlier episode is fixed.

    MVP scope: the next episode only (episode_no+1).
    """
    cur = conn.execute(
        "UPDATE change_proposal_items SET validation='stale'"
        " WHERE proposal_set_id IN (SELECT proposal_set_id FROM change_proposals"
        " WHERE episode_no=?)",
        (episode_no + 1,),
    )
    conn.commit()
    return cur.rowcount
