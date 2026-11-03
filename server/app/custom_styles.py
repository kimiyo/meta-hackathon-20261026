"""User-defined writing styles: named voice reusable across novels.

System presets (style_presets.PRESETS) stay read-only. A user copies one,
edits the voice, and saves it under their own name; works then apply either
a system preset key or a custom style id. Application snapshots the voice
into style_policies (like presets): editing a custom style later does not
rewrite works already using it, and deleting one resets those works to the
default preset.
"""
from __future__ import annotations

import sqlite3

from . import style_presets
from .db import now_iso

NAME_MAX = 40


class DuplicateName(Exception):
    pass


def _clean_name(name) -> str:
    if not isinstance(name, str):
        raise ValueError("name must be a string")
    name = name.strip()
    if not name:
        raise ValueError("name must not be blank")
    if len(name) > NAME_MAX:
        raise ValueError(f"name must be ≤{NAME_MAX} chars")
    return name


def _clean_voice(value, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def list_custom(conn: sqlite3.Connection, author_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM custom_styles WHERE author_id=? ORDER BY name, id",
        (author_id,))]


def get_custom(conn: sqlite3.Connection, author_id: int,
               style_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM custom_styles WHERE id=? AND author_id=?",
                       (style_id, author_id)).fetchone()
    return dict(row) if row else None


def create_custom(conn: sqlite3.Connection, author_id: int, name: str,
                  pov: str = "", diction: str = "",
                  feedback: str = "") -> dict:
    name = _clean_name(name)
    pov = _clean_voice(pov, "pov")
    diction = _clean_voice(diction, "diction")
    feedback = _clean_voice(feedback, "feedback")
    ts = now_iso()
    try:
        cur = conn.execute(
            "INSERT INTO custom_styles(author_id, name, pov, diction, feedback,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            (author_id, name, pov, diction, feedback, ts, ts))
    except sqlite3.IntegrityError as e:
        if "UNIQUE" not in str(e).upper():
            raise
        raise DuplicateName(f"style name already exists: {name}")
    conn.commit()
    return get_custom(conn, author_id, cur.lastrowid)


def update_custom(conn: sqlite3.Connection, author_id: int, style_id: int, *,
                  name: str | None = None, pov: str | None = None,
                  diction: str | None = None,
                  feedback: str | None = None) -> dict:
    row = get_custom(conn, author_id, style_id)
    if row is None:
        raise KeyError(f"style {style_id} not found")
    sets, args = [], []
    if name is not None:
        sets.append("name=?")
        args.append(_clean_name(name))
    if pov is not None:
        sets.append("pov=?")
        args.append(_clean_voice(pov, "pov"))
    if diction is not None:
        sets.append("diction=?")
        args.append(_clean_voice(diction, "diction"))
    if feedback is not None:
        sets.append("feedback=?")
        args.append(_clean_voice(feedback, "feedback"))
    if not sets:
        raise ValueError("no valid style fields")
    sets.append("updated_at=?")
    args += [now_iso(), style_id, author_id]
    try:
        conn.execute(f"UPDATE custom_styles SET {', '.join(sets)}"
                     " WHERE id=? AND author_id=?", args)
    except sqlite3.IntegrityError as e:
        if "UNIQUE" not in str(e).upper():
            raise
        raise DuplicateName(f"style name already exists: {name}")
    conn.commit()
    return get_custom(conn, author_id, style_id)


def delete_custom(conn: sqlite3.Connection, author_id: int,
                  style_id: int) -> dict:
    row = get_custom(conn, author_id, style_id)
    if row is None:
        raise KeyError(f"style {style_id} not found")
    key, preset = style_presets.resolve_preset(None)
    cur = conn.execute(
        "UPDATE style_policies SET custom_style_id=NULL, style_preset=?,"
        " pov=?, diction=?, feedback=?, updated_at=?"
        " WHERE custom_style_id=?",
        (key, preset["pov"], preset["diction"], preset["feedback"],
         now_iso(), style_id))
    reset = cur.rowcount
    conn.execute("DELETE FROM custom_styles WHERE id=? AND author_id=?",
                 (style_id, author_id))
    conn.commit()
    return {"deleted": style_id, "works_reset": reset}


def apply_custom(conn: sqlite3.Connection, work_id: int, author_id: int,
                 style_id: int) -> dict:
    """Point a work at a custom style (voice snapshotted, like presets).

    Custom styles carry voice only; the length target and its unit stay
    on the work and are passed through unchanged (unit follows the work
    market default, see drafting.effective_length_unit).
    """
    from . import drafting

    row = get_custom(conn, author_id, style_id)
    if row is None:
        raise KeyError(f"style {style_id} not found")
    prow = conn.execute("SELECT length_target FROM style_policies"
                        " WHERE work_id=?", (work_id,)).fetchone()
    if prow is None:
        conn.execute(
            "INSERT INTO style_policies(work_id, length_target, pov, diction,"
            " feedback, style_preset, custom_style_id, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (work_id, f"{drafting.DEFAULT_MIN}-{drafting.DEFAULT_MAX}",
             row["pov"], row["diction"], row["feedback"], "", style_id,
             now_iso()))
        lo, hi = drafting.DEFAULT_MIN, drafting.DEFAULT_MAX
    else:
        conn.execute(
            "UPDATE style_policies SET custom_style_id=?, style_preset='',"
            " pov=?, diction=?, feedback=?, updated_at=? WHERE work_id=?",
            (style_id, row["pov"], row["diction"], row["feedback"],
             now_iso(), work_id))
        lo, hi = drafting.parse_length_target(prow["length_target"])
    conn.commit()
    unit = drafting.effective_length_unit(conn, work_id)
    return {"length_min": lo, "length_max": hi, "length_unit": unit,
            "is_default": False, "preset": None, "custom_style_id": style_id,
            "custom_style_name": row["name"]}
