"""Top-down design tree: Work/Part/Chapter/Episode/Scene nodes.

Node ids carry a kind prefix (N/P/C/E/S) plus a zero-padded sequence that is
unique per kind across all designs (id is a global primary key), e.g. E001.
Episode nodes sync into episode_outlines so the existing writing pipeline
keeps working unchanged.
"""
from __future__ import annotations

import json
import sqlite3

from .db import now_iso, touch_work

KINDS = {"work": "N", "part": "P", "chapter": "C", "episode": "E", "scene": "S"}


def _row(conn, sql, args=()) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def _public(row: dict) -> dict:
    row = dict(row)
    try:
        row["body"] = json.loads(row.pop("body_json"))
    except ValueError:
        row["body"] = {}
    return row


def allocate_id(conn: sqlite3.Connection, design_id: int, kind: str) -> str:
    # id is a global PRIMARY KEY and node lookup by id is global, so the
    # sequence must run per kind across all designs. (design_id is kept
    # for caller compatibility.) LENGTH first keeps ordering correct
    # past 999, where zero-padding no longer sorts lexically.
    prefix = KINDS[kind]
    top = conn.execute(
        "SELECT id FROM design_nodes WHERE kind=?"
        " ORDER BY LENGTH(id) DESC, id DESC LIMIT 1", (kind,)).fetchone()
    n = 0
    if top:
        try:
            n = int(top["id"][len(prefix):])
        except ValueError:
            n = 0
    return f"{prefix}{n + 1:03d}"


def _design_or_raise(conn, design_id: int) -> dict:
    d = _row(conn, "SELECT * FROM story_designs WHERE id=?", (design_id,))
    if not d:
        raise KeyError(f"design {design_id} not found")
    return d


def _next_episode_no(conn, design_id: int) -> int:
    top = conn.execute(
        "SELECT COALESCE(MAX(episode_no),0) m FROM episode_outlines"
        " WHERE design_id=?", (design_id,)).fetchone()
    return top["m"] + 1


def create_node(conn: sqlite3.Connection, design_id: int, *, kind: str,
                parent_id: str | None = None, title: str = "",
                logline: str = "", body: dict | None = None,
                _skip_commit: bool = False) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}")
    design = _design_or_raise(conn, design_id)
    if parent_id is not None:
        parent = _row(conn, "SELECT * FROM design_nodes WHERE id=?",
                      (parent_id,))
        if not parent:
            raise KeyError(f"node {parent_id} not found")
        if parent["design_id"] != design_id:
            raise ValueError("parent belongs to another design")
    body = dict(body or {})
    if kind == "episode" and "episode_no" not in body:
        body["episode_no"] = _next_episode_no(conn, design_id)
    sib = conn.execute(
        "SELECT COALESCE(MAX(sort_order),-1) m FROM design_nodes"
        " WHERE design_id=? AND COALESCE(parent_id,'')=COALESCE(?, '')",
        (design_id, parent_id)).fetchone()
    node_id = allocate_id(conn, design_id, kind)
    ts = now_iso()
    conn.execute(
        "INSERT INTO design_nodes(id, design_id, parent_id, kind, title,"
        " logline, body_json, sort_order, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (node_id, design_id, parent_id, kind, title.strip(), logline,
         json.dumps(body, ensure_ascii=False), sib["m"] + 1, ts))
    if kind == "episode":
        _sync_episode_outline(conn, design_id, node_id, title.strip(),
                              logline, body)
    touch_work(conn, design["work_id"])
    if not _skip_commit:
        conn.commit()
    return _public(_row(conn, "SELECT * FROM design_nodes WHERE id=?",
                        (node_id,)))


def _sync_episode_outline(conn, design_id: int, node_id: str, title: str,
                          logline: str, body: dict) -> None:
    """Mirror one Episode node into episode_outlines (upsert)."""
    ep_no = body.get("episode_no")
    if not isinstance(ep_no, int) or ep_no < 1:
        raise ValueError("episode node needs a positive body.episode_no")
    vals = {
        "title": title,
        "goal": body.get("goal", logline),
        "events_json": json.dumps(body.get("events", []), ensure_ascii=False),
        "char_changes_json": json.dumps(body.get("char_changes", []),
                                        ensure_ascii=False),
        "foreshadowing_json": json.dumps(body.get("foreshadowing", []),
                                         ensure_ascii=False),
        "links_json": json.dumps(body.get("links", {}), ensure_ascii=False),
        "constraints_json": json.dumps(body.get("constraints", []),
                                       ensure_ascii=False),
    }
    conn.execute(
        "INSERT INTO episode_outlines(design_id, episode_no, title, goal,"
        " events_json, char_changes_json, foreshadowing_json, links_json,"
        " constraints_json) VALUES(?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(design_id, episode_no) DO UPDATE SET"
        " title=excluded.title, goal=excluded.goal,"
        " events_json=excluded.events_json,"
        " char_changes_json=excluded.char_changes_json,"
        " foreshadowing_json=excluded.foreshadowing_json,"
        " links_json=excluded.links_json,"
        " constraints_json=excluded.constraints_json",
        (design_id, ep_no, vals["title"], vals["goal"], vals["events_json"],
         vals["char_changes_json"], vals["foreshadowing_json"],
         vals["links_json"], vals["constraints_json"]))


def list_nodes(conn: sqlite3.Connection, design_id: int, *,
               include_archived: bool = False,
               q: str | None = None) -> list[dict]:
    _design_or_raise(conn, design_id)
    sql = "SELECT * FROM design_nodes WHERE design_id=?"
    args: list = [design_id]
    if not include_archived:
        sql += " AND archived=0"
    if q:
        sql += " AND (title LIKE ? OR logline LIKE ? OR id LIKE ?)"
        args += [f"%{q}%"] * 3
    sql += " ORDER BY sort_order, id"
    return [_public(dict(r)) for r in conn.execute(sql, args)]


def get_node(conn: sqlite3.Connection, node_id: str) -> dict | None:
    row = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    return _public(row) if row else None


def get_node_with_children(conn: sqlite3.Connection,
                           node_id: str) -> dict | None:
    node = get_node(conn, node_id)
    if not node:
        return None
    kids = [_public(dict(r)) for r in conn.execute(
        "SELECT * FROM design_nodes WHERE parent_id=? AND archived=0"
        " ORDER BY sort_order, id", (node_id,))]
    node["children"] = [
        {"id": k["id"], "kind": k["kind"], "title": k["title"],
         "logline": k["logline"]} for k in kids]
    return node


def update_node(conn: sqlite3.Connection, node_id: str,
                fields: dict) -> dict:
    node = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    if not node:
        raise KeyError(f"node {node_id} not found")
    allowed = {"title", "logline", "body", "parent_id", "sort_order"}
    sets, args = [], []
    body = json.loads(node["body_json"])
    new_parent = node["parent_id"]
    if "parent_id" in fields and fields["parent_id"] != node["parent_id"]:
        new_parent = fields["parent_id"]
        if new_parent is not None:
            if new_parent == node_id:
                raise ValueError("node cannot be its own parent")
            parent = _row(conn, "SELECT * FROM design_nodes WHERE id=?",
                          (new_parent,))
            if not parent:
                raise KeyError(f"node {new_parent} not found")
            if parent["design_id"] != node["design_id"]:
                raise ValueError("parent belongs to another design")
            # Cycle guard: new parent must not descend from this node.
            seen = new_parent
            while seen is not None:
                if seen == node_id:
                    raise ValueError("move would create a cycle")
                prow = _row(conn,
                            "SELECT parent_id FROM design_nodes WHERE id=?",
                            (seen,))
                seen = prow["parent_id"] if prow else None
        sets.append("parent_id=?")
        args.append(new_parent)
    if "title" in fields:
        sets.append("title=?")
        args.append(fields["title"].strip())
    if "logline" in fields:
        sets.append("logline=?")
        args.append(fields["logline"])
    if "body" in fields:
        if not isinstance(fields["body"], dict):
            raise ValueError("body must be an object")
        body = fields["body"]
        sets.append("body_json=?")
        args.append(json.dumps(body, ensure_ascii=False))
    if "sort_order" in fields:
        sets.append("sort_order=?")
        args.append(int(fields["sort_order"]))
    if not sets:
        raise ValueError("no valid node fields")
    sets.append("updated_at=?")
    args += [now_iso(), node_id]
    conn.execute(f"UPDATE design_nodes SET {', '.join(sets)} WHERE id=?", args)
    fresh = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    if fresh["kind"] == "episode":
        _sync_episode_outline(conn, fresh["design_id"], node_id,
                              fresh["title"], fresh["logline"], body)
    design = _row(conn, "SELECT work_id FROM story_designs WHERE id=?",
                  (node["design_id"],))
    if design:
        touch_work(conn, design["work_id"])
    conn.commit()
    return _public(fresh)


def set_archived(conn: sqlite3.Connection, node_id: str,
                 archived: bool) -> dict:
    node = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    if not node:
        raise KeyError(f"node {node_id} not found")
    conn.execute("UPDATE design_nodes SET archived=?, updated_at=?"
                 " WHERE id=?", (1 if archived else 0, now_iso(), node_id))
    conn.commit()
    return _public(_row(conn, "SELECT * FROM design_nodes WHERE id=?",
                        (node_id,)))
