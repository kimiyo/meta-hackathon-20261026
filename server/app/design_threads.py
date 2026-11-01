"""Design partner threads: per-node conversations with history inclusion.

Mirrors the ideas chat rule: the client sends an explicit include_messages
id list, the server filters it to this thread, and the current question is
always included. Assistant proposals persist with the message together with
per-item applied flags, so reloaded conversations render digests like
"(이 답변의 제안: 하위 [scene]장면1(반영됨))" instead of re-proposing.
"""
from __future__ import annotations

import json
import sqlite3

from . import design_nodes, ideas
from .db import find_work, now_iso

TITLE_LEN = 20
HISTORY_CLIP = 2000


def _row(conn, sql, args=()) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def _public_thread(row: dict, node_code: str | None,
                   snippet: str | None = None) -> dict:
    return {"id": row["id"], "design_id": row["design_id"],
            "node_id": row["node_id"], "node_code": node_code,
            "title": row["title"], "created_at": row["created_at"],
            "updated_at": row["updated_at"], "snippet": snippet}


def _public_message(row: dict) -> dict:
    try:
        proposals = json.loads(row.get("proposals_json") or "{}")
    except ValueError:
        proposals = {}
    try:
        applied = json.loads(row.get("applied_json") or "{}")
    except ValueError:
        applied = {}
    applied = {"patch": bool(applied.get("patch")),
               "children": [int(i) for i in applied.get("children", [])]}
    return {"id": row["id"], "thread_id": row["thread_id"],
            "role": row["role"], "content": row["content"],
            "proposals": {"children": proposals.get("children", []),
                          "patch": proposals.get("patch", {})},
            "applied": applied, "created_at": row["created_at"]}


def list_threads(conn: sqlite3.Connection, design_id: int, *,
                 node_id: str | None = None, q: str | None = None) -> list[dict]:
    sql = ("SELECT t.*, n.kind, n.seq FROM design_threads t"
           " JOIN design_nodes n ON n.id=t.node_id"
           " WHERE t.design_id=?")
    args: list = [design_id]
    if node_id is not None:
        sql += " AND t.node_id=?"
        args.append(node_id)
    if q:
        sql += (" AND (t.title LIKE ? OR EXISTS (SELECT 1 FROM design_messages m"
                " WHERE m.thread_id=t.id AND m.content LIKE ?))")
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY t.updated_at DESC, t.id DESC"
    out = []
    for r in conn.execute(sql, args):
        row = dict(r)
        snippet = None
        if q:
            hit = conn.execute(
                "SELECT content FROM design_messages"
                " WHERE thread_id=? AND content LIKE ? ORDER BY id LIMIT 1",
                (row["id"], f"%{q}%")).fetchone()
            snippet = (hit["content"][:60] if hit else row["title"][:60]) or None
        out.append(_public_thread(
            row, design_nodes.node_code(row["kind"], row["seq"]), snippet))
    return out


def create_thread(conn: sqlite3.Connection, design_id: int,
                  node_id: str, title: str = "") -> dict:
    node = design_nodes.get_node(conn, node_id)
    if not node:
        raise KeyError(f"node {node_id} not found")
    if node["design_id"] != design_id:
        raise ValueError("node belongs to another design")
    ts = now_iso()
    cur = conn.execute(
        "INSERT INTO design_threads(design_id, node_id, title, created_at,"
        " updated_at) VALUES(?,?,?,?,?)",
        (design_id, node_id, title.strip(), ts, ts))
    conn.commit()
    row = _row(conn, "SELECT * FROM design_threads WHERE id=?",
               (cur.lastrowid,))
    return _public_thread(row, node["code"])


def get_thread(conn: sqlite3.Connection, thread_id: int) -> dict | None:
    return _row(conn, "SELECT * FROM design_threads WHERE id=?",
                (thread_id,))


def list_messages(conn: sqlite3.Connection, thread_id: int,
                  limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM design_messages WHERE thread_id=? ORDER BY id"
        " LIMIT ?", (thread_id, limit)).fetchall()
    return [_public_message(dict(r)) for r in rows]


def add_message(conn: sqlite3.Connection, thread_id: int, role: str,
                content: str, proposals: dict | None = None) -> dict:
    if role not in ("user", "assistant"):
        raise ValueError("role must be user or assistant")
    cur = conn.execute(
        "INSERT INTO design_messages(thread_id, role, content,"
        " proposals_json, created_at) VALUES(?,?,?,?,?)",
        (thread_id, role, content,
         json.dumps(proposals or {}, ensure_ascii=False), now_iso()))
    if role == "user" and not (get_thread(conn, thread_id) or {}).get("title"):
        conn.execute("UPDATE design_threads SET title=? WHERE id=?",
                     (content.strip()[:TITLE_LEN], thread_id))
    conn.execute("UPDATE design_threads SET updated_at=? WHERE id=?",
                 (now_iso(), thread_id))
    conn.commit()
    return _public_message(_row(
        conn, "SELECT * FROM design_messages WHERE id=?",
        (cur.lastrowid,)))


def mark_applied(conn: sqlite3.Connection, message_id: int, *,
                 patch: bool | None = None,
                 child: int | None = None) -> dict:
    row = _row(conn, "SELECT * FROM design_messages WHERE id=?",
               (message_id,))
    if not row:
        raise KeyError(f"design message {message_id} not found")
    msg = _public_message(row)
    applied = {"patch": msg["applied"]["patch"],
               "children": list(msg["applied"]["children"])}
    if patch is not None:
        applied["patch"] = bool(patch)
    if child is not None:
        kids = msg["proposals"]["children"]
        if (not isinstance(child, int) or isinstance(child, bool)
                or not 0 <= child < len(kids)):
            raise ValueError(f"child index {child} out of range")
        if child not in applied["children"]:
            applied["children"].append(child)
    conn.execute("UPDATE design_messages SET applied_json=? WHERE id=?",
                 (json.dumps(applied, ensure_ascii=False), message_id))
    conn.commit()
    msg["applied"] = applied
    return msg


def proposal_digest(msg: dict) -> str:
    """One-line applied-state summary for history context."""
    props = msg.get("proposals") or {}
    applied = msg.get("applied") or {}
    kids = props.get("children") or []
    done = set(applied.get("children") or [])
    parts = [f"하위 [{c.get('kind')}]"
             f"{c.get('title', '')}"
             f"({'반영됨' if i in done else '미반영'})"
             for i, c in enumerate(kids)]
    if props.get("patch"):
        parts.append(
            f"노드수정({'반영됨' if applied.get('patch') else '미반영'})")
    if not parts:
        return ""
    return "(이 답변의 제안: " + ", ".join(parts) + ")"


def ref_lines(conn: sqlite3.Connection, design_id: int,
              refs: list[str]) -> list[str]:
    """Resolve @ codes to node one-liners; unknowns stay listed."""
    lines = []
    for ref in refs:
        nid = design_nodes.resolve_node_id(conn, design_id, ref)
        if nid is None:
            lines.append(f"- {ref} = (알 수 없음)")
            continue
        n = design_nodes.get_node(conn, nid)
        lines.append(f"- {ref} = {n['full_code']}({n['kind']})"
                     f" {n['title']}: {n['logline']}")
    return lines


def node_context(conn: sqlite3.Connection, node: dict, extra: str = "",
                 history: list[str] | None = None) -> str:
    design = conn.execute("SELECT * FROM story_designs WHERE id=?",
                          (node["design_id"],)).fetchone()
    work = find_work(conn, design["work_id"]) if design else None
    root_code = design_nodes.design_root_code(conn, node["design_id"])
    chain, seen = [], node.get("parent_id")
    while seen is not None and len(chain) < 8:
        prow = conn.execute("SELECT id, kind, seq, title, logline, parent_id"
                            " FROM design_nodes WHERE id=?", (seen,)).fetchone()
        if not prow:
            break
        pcode = design_nodes.node_code(prow["kind"], prow["seq"])
        chain.append(f"{pcode}({prow['kind']}) {prow['title']}:"
                     f" {prow['logline']}")
        seen = prow["parent_id"]
    kids = conn.execute(
        "SELECT kind, seq, title, logline FROM design_nodes"
        " WHERE parent_id=? AND archived=0 ORDER BY sort_order, id LIMIT 30",
        (node["id"],)).fetchall()
    sibs = conn.execute(
        "SELECT kind, seq, title FROM design_nodes WHERE design_id=?"
        " AND COALESCE(parent_id,'')=COALESCE(?,'') AND id!=?"
        " ORDER BY sort_order, id LIMIT 20",
        (node["design_id"], node.get("parent_id"), node["id"])).fetchall()
    settings = ideas.list_settings(conn, work["id"])[:20] if work else []
    code_of = lambda r: design_nodes.node_code(r["kind"], r["seq"])
    parts = [
        f"작품: {work['title'] if work else '?'}"
        f" / 구조: {work['structure_json'] if work else '{}'}",
        f"설계 트리: {root_code or '?'}"
        " (이 트리 안에서는 E001 같은 짧은 코드로 지칭한다)",
        f"선택 노드: {node.get('code') or node['id']}({node['kind']})"
        f" {node['title']}",
        f"줄거리: {node['logline']}",
        f"상세: {json.dumps(node['body'], ensure_ascii=False)[:1000]}",
        "상위: " + (" > ".join(reversed(chain)) if chain else "(없음)"),
        "하위:",
    ]
    parts += [f"- {code_of(k)}({k['kind']}) {k['title']}: {k['logline']}"
              for k in kids] or ["(없음)"]
    parts += ["형제:"] + [
        f"- {code_of(s)}({s['kind']}) {s['title']}" for s in sibs] or ["(없음)"]
    parts += ["설정정보:"] + [
        f"- [{s['category']}] {s['title']}: {s['summary']}"
        for s in settings] or ["(없음)"]
    if history is not None:
        parts += ["", "[포함된 대화]:"] + (history or ["(없음)"])
    if extra:
        parts += ["", extra]
    return "\n".join(parts)[:8000]


def history_lines(messages: list[dict]) -> list[str]:
    """Render included messages for the prompt, with proposal digests."""
    lines = []
    for m in messages:
        who = "질문" if m["role"] == "user" else "답변"
        content = (m["content"] or "")[:HISTORY_CLIP]
        digest = proposal_digest(m) if m["role"] == "assistant" else ""
        lines.append(f"{who}: {content}{digest}")
    return lines
