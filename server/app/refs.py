"""@ mention parsing and resolution (ideas chat + draft redraft share this).

Tokens: @Title (setting_entries title), @E001 (design node code/id).
@L12-15 line pins are parsed here but expanded by drafting (it owns the
manuscript text). Unknown tokens stay listed so the UI can report them;
they are never an error.
"""
from __future__ import annotations

import re
import sqlite3

from . import design_nodes

_MENTION_RE = re.compile(r"@([^\s@,]+)")
_TRAILING_PUNCT = ".,!?;:)]}\">'"
_LINE_RE = re.compile(r"[lL](\d+)(?:\s*[-~]\s*(\d+))?$")


def parse_mentions(text: str) -> list[str]:
    """@ tokens in order, deduplicated (mirrors ideas.js parseMentions)."""
    out = []
    for m in _MENTION_RE.finditer(text or ""):
        tok = m.group(1).rstrip(_TRAILING_PUNCT)
        if tok and tok not in out:
            out.append(tok)
    return out


def parse_line_token(tok: str) -> tuple[int, int] | None:
    """@L7 → (7, 7), @L12-15 → (12, 15); None when not a line token."""
    m = _LINE_RE.fullmatch((tok or "").strip())
    if not m:
        return None
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    return (start, end) if start <= end else (end, start)


def resolve_work_node(conn: sqlite3.Connection, work_id: int,
                      ref: str) -> dict | None:
    """Resolve a node code/id across the work's designs, latest first."""
    designs = conn.execute(
        "SELECT id FROM story_designs WHERE work_id=? ORDER BY id DESC",
        (work_id,)).fetchall()
    for d in designs:
        nid = design_nodes.resolve_node_id(conn, d["id"], ref)
        if nid is not None:
            return design_nodes.get_node(conn, nid)
    return None


def resolve_refs(conn: sqlite3.Connection, work_id: int, refs: list[str],
                 skip_setting_ids: set[int] | None = None) -> tuple[
                     list[str], list[str], list[str]]:
    """Resolve @ tokens to full settings / design nodes. Unknown stay listed."""
    skip_setting_ids = skip_setting_ids if skip_setting_ids is not None else set()
    setting_lines, node_lines, unknown = [], [], []
    for ref in refs:
        s = conn.execute(
            "SELECT * FROM setting_entries WHERE work_id=? AND title=?",
            (work_id, ref)).fetchone()
        if s is not None:
            if s["id"] not in skip_setting_ids:
                setting_lines.append(
                    f"- [{s['category']}] {s['title']}: {s['summary']}\n"
                    f"  {s['detail']}")
                skip_setting_ids.add(s["id"])
            continue
        n = resolve_work_node(conn, work_id, ref)
        if n is not None:
            node_lines.append(
                f"- {n['full_code']}({n['kind']}) {n['title']}: {n['logline']}")
        else:
            unknown.append(ref)
    return setting_lines, node_lines, unknown
