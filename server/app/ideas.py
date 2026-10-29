"""Ideas/structure stage: chat threads, synopsis doc, settings, handoff.

The program owns storage, locking, and handoff seeding; the AI only
generates replies and proposals (see routes/ideas.py). Nothing here imports
httpx/openai: all AI calls go through MetaClient in the route layer.
"""
from __future__ import annotations

import json
import sqlite3

from . import design_nodes
from .db import now_iso, touch_work

SETTING_CATEGORIES = ("character", "world", "event", "part", "misc")
SETTING_STATUS = ("draft", "applied")


def _row(conn, sql, args=()) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


# ---------------------------------------------------------------- threads

def list_threads(conn: sqlite3.Connection, work_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM idea_threads WHERE work_id=? ORDER BY id",
        (work_id,))]


def create_thread(conn: sqlite3.Connection, work_id: int,
                  title: str = "") -> dict:
    cur = conn.execute(
        "INSERT INTO idea_threads(work_id, title, created_at)"
        " VALUES(?,?,?)", (work_id, title.strip(), now_iso()))
    touch_work(conn, work_id)
    conn.commit()
    return _row(conn, "SELECT * FROM idea_threads WHERE id=?",
                (cur.lastrowid,))


def get_thread(conn: sqlite3.Connection, thread_id: int) -> dict | None:
    return _row(conn, "SELECT * FROM idea_threads WHERE id=?", (thread_id,))


def _public_message(m: dict) -> dict:
    m["refs"] = json.loads(m.pop("refs_json"))
    m["choices"] = json.loads(m.pop("choices_json"))
    try:
        m["proposals"] = json.loads(m.pop("proposals_json") or "{}")
    except ValueError:
        m["proposals"] = {}
    if not isinstance(m["proposals"], dict):
        m["proposals"] = {}
    return m


def list_messages(conn: sqlite3.Connection, thread_id: int,
                  limit: int = 100) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM idea_messages WHERE thread_id=? ORDER BY id LIMIT ?",
        (thread_id, limit)).fetchall()
    return [_public_message(dict(r)) for r in rows]


def add_message(conn: sqlite3.Connection, thread_id: int, role: str,
                content: str, refs: list | None = None,
                choices: list | None = None,
                proposals: dict | None = None) -> dict:
    if role not in ("user", "assistant"):
        raise ValueError("role must be user|assistant")
    cur = conn.execute(
        "INSERT INTO idea_messages(thread_id, role, content, refs_json,"
        " choices_json, proposals_json, created_at) VALUES(?,?,?,?,?,?,?)",
        (thread_id, role, content,
         json.dumps(refs or [], ensure_ascii=False),
         json.dumps(choices or [], ensure_ascii=False),
         json.dumps(proposals or {}, ensure_ascii=False), now_iso()))
    conn.commit()
    m = _row(conn, "SELECT * FROM idea_messages WHERE id=?", (cur.lastrowid,))
    return _public_message(m)


# ---------------------------------------------------------------- synopsis

def get_synopsis(conn: sqlite3.Connection, work_id: int) -> dict:
    row = _row(conn, "SELECT * FROM story_synopsis WHERE work_id=?", (work_id,))
    if row:
        return row
    return {"work_id": work_id, "content": "", "version": 0,
            "updated_at": ""}


def update_synopsis(conn: sqlite3.Connection, work_id: int,
                    content: str) -> dict:
    cur = get_synopsis(conn, work_id)
    version = cur["version"] + 1
    ts = now_iso()
    conn.execute(
        "INSERT INTO story_synopsis(work_id, content, version, updated_at)"
        " VALUES(?,?,?,?)"
        " ON CONFLICT(work_id) DO UPDATE SET content=excluded.content,"
        " version=excluded.version, updated_at=excluded.updated_at",
        (work_id, content, version, ts))
    conn.execute(
        "INSERT INTO synopsis_revisions(work_id, version, content, created_at)"
        " VALUES(?,?,?,?)", (work_id, version, content, ts))
    touch_work(conn, work_id)
    conn.commit()
    return get_synopsis(conn, work_id)


def list_synopsis_revisions(conn: sqlite3.Connection,
                            work_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT work_id, version, content, created_at FROM synopsis_revisions"
        " WHERE work_id=? ORDER BY version DESC", (work_id,))]


# ---------------------------------------------------------------- settings

def list_settings(conn: sqlite3.Connection, work_id: int,
                  category: str | None = None) -> list[dict]:
    if category:
        rows = conn.execute(
            "SELECT * FROM setting_entries WHERE work_id=? AND category=?"
            " ORDER BY id", (work_id, category)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM setting_entries WHERE work_id=? ORDER BY id",
            (work_id,)).fetchall()
    return [dict(r) for r in rows]


def allocate_setting_code(conn: sqlite3.Connection, work_id: int) -> str:
    """Next per-work D-number (D001, D002, ...). Never reused."""
    top = conn.execute(
        "SELECT code FROM setting_entries WHERE work_id=? AND code LIKE 'D%'"
        " ORDER BY code DESC LIMIT 1", (work_id,)).fetchone()
    n = 0
    if top and top["code"]:
        try:
            n = int(top["code"][1:])
        except ValueError:
            n = 0
    return f"D{n + 1:03d}"


def create_setting(conn: sqlite3.Connection, work_id: int, *,
                   category: str = "misc", title: str = "",
                   summary: str = "", detail: str = "",
                   status: str = "draft") -> dict:
    if category not in SETTING_CATEGORIES:
        raise ValueError(f"category must be one of {SETTING_CATEGORIES}")
    if status not in SETTING_STATUS:
        raise ValueError(f"status must be one of {SETTING_STATUS}")
    cur = conn.execute(
        "INSERT INTO setting_entries(work_id, code, category, title, summary,"
        " detail, status, updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (work_id, allocate_setting_code(conn, work_id), category,
         title.strip(), summary, detail, status, now_iso()))
    touch_work(conn, work_id)
    conn.commit()
    return _row(conn, "SELECT * FROM setting_entries WHERE id=?",
                (cur.lastrowid,))


def update_setting(conn: sqlite3.Connection, setting_id: int,
                   fields: dict) -> dict:
    allowed = {"category", "title", "summary", "detail", "status"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "category" and v not in SETTING_CATEGORIES:
            raise ValueError(f"category must be one of {SETTING_CATEGORIES}")
        if k == "status" and v not in SETTING_STATUS:
            raise ValueError(f"status must be one of {SETTING_STATUS}")
        sets.append(f"{k}=?")
        args.append(v.strip() if k == "title" and isinstance(v, str) else v)
    if not sets:
        raise ValueError("no valid setting fields")
    row = _row(conn, "SELECT * FROM setting_entries WHERE id=?", (setting_id,))
    if not row:
        raise KeyError(f"setting {setting_id} not found")
    sets.append("updated_at=?")
    args += [now_iso(), setting_id]
    conn.execute(f"UPDATE setting_entries SET {', '.join(sets)} WHERE id=?",
                 args)
    touch_work(conn, row["work_id"])
    conn.commit()
    return _row(conn, "SELECT * FROM setting_entries WHERE id=?",
                (setting_id,))


def delete_setting(conn: sqlite3.Connection, setting_id: int) -> dict:
    row = _row(conn, "SELECT * FROM setting_entries WHERE id=?", (setting_id,))
    if not row:
        raise KeyError(f"setting {setting_id} not found")
    conn.execute("DELETE FROM setting_entries WHERE id=?", (setting_id,))
    touch_work(conn, row["work_id"])
    conn.commit()
    return {"deleted": setting_id}


# ---------------------------------------------------------------- structure

STRUCTURE_DEFAULTS = {"use_part": False, "use_chapter": False,
                      "total_episodes": 25, "parts": []}
PARTS_MAX = 20


def parse_structure(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    out = dict(STRUCTURE_DEFAULTS)
    out.update({k: data[k] for k in STRUCTURE_DEFAULTS if k in data})
    if not isinstance(out.get("parts"), list):
        out["parts"] = []
    out["parts"] = [{"title": str(p.get("title", ""))[:200],
                     "summary": str(p.get("summary", ""))[:2000]}
                    for p in out["parts"] if isinstance(p, dict)][:PARTS_MAX]
    return out


def update_structure(conn: sqlite3.Connection, work_id: int,
                     structure: dict) -> dict:
    """Replace the work's structure decision (ideas phase only)."""
    work = _row(conn, "SELECT * FROM works WHERE id=?", (work_id,))
    if not work:
        raise KeyError(f"work {work_id} not found")
    if work["ideas_locked"]:
        raise ValueError("ideas handed off (read-only)")
    total = structure.get("total_episodes", 25)
    if not isinstance(total, int) or isinstance(total, bool) \
            or not 1 <= total <= 200:
        raise ValueError("total_episodes must be 1..200")
    parts = structure.get("parts", [])
    if not isinstance(parts, list) or len(parts) > PARTS_MAX:
        raise ValueError(f"parts must be a list of ≤{PARTS_MAX}")
    for p in parts:
        if not isinstance(p, dict):
            raise ValueError("each part must be {title, summary}")
    out = {"use_part": bool(structure.get("use_part", False)),
           "use_chapter": bool(structure.get("use_chapter", False)),
           "total_episodes": total,
           "parts": [{"title": str(p.get("title", ""))[:200],
                      "summary": str(p.get("summary", ""))[:2000]}
                     for p in parts]}
    conn.execute("UPDATE works SET structure_json=? WHERE id=?",
                 (json.dumps(out, ensure_ascii=False), work_id))
    touch_work(conn, work_id)
    conn.commit()
    return out


# ---------------------------------------------------------------- handoff

def build_migration_tree(structure: dict, plan: dict) -> tuple[list, dict]:
    """Pure planning step shared by the preview and the migration.

    Returns (tree, counts) where tree is a sanitized nested list of
    {kind, title, summary, episode_no, stub, children} and counts holds
    parts/chapters/episodes/stub_episodes. Nodes whose kind the structure
    disables are lifted (their children attach to the grandparent).
    Episodes 1..total each appear exactly once; gaps become stub episodes
    under the last part or chapter (else top level).
    """
    total = structure.get("total_episodes", 0)
    allowed = {"episode"}
    if structure.get("use_part"):
        allowed.add("part")
    if structure.get("use_chapter"):
        allowed.add("chapter")
    counts = {"parts": 0, "chapters": 0, "episodes": 0, "stub_episodes": 0}
    used: set[int] = set()
    tree: list = []
    state: dict = {"last_container": None, "placed": 0}

    def _next_free() -> int:
        n = 1
        while n in used:
            n += 1
        return n

    def _walk(raw_nodes: list, out: list) -> None:
        for raw in raw_nodes:
            if state["placed"] >= 400:
                return
            n = _sanitize_plan_node(raw)
            kind = n["kind"]
            if kind == "episode":
                ep_no = n["episode_no"]
                if not isinstance(ep_no, int) or isinstance(ep_no, bool) \
                        or not 1 <= ep_no <= total or ep_no in used:
                    ep_no = _next_free()
                if ep_no > total:
                    continue
                out.append({"kind": "episode",
                            "title": n["title"] or f"제{ep_no}화",
                            "summary": n["summary"], "episode_no": ep_no,
                            "stub": False, "children": []})
                used.add(ep_no)
                counts["episodes"] += 1
                state["placed"] += 1
                _walk(n["children"], out)
            elif kind in allowed:
                node = {"kind": kind, "title": n["title"],
                        "summary": n["summary"], "episode_no": 0,
                        "stub": False, "children": []}
                out.append(node)
                counts["parts" if kind == "part" else "chapters"] += 1
                state["last_container"] = node
                state["placed"] += 1
                _walk(n["children"], node["children"])
            else:
                # Disabled or unknown kind: lift children to this parent.
                _walk(n["children"], out)

    raw_tree = plan.get("tree") or [] if isinstance(plan, dict) else []
    _walk(raw_tree, tree)
    if isinstance(total, int) and not isinstance(total, bool) \
            and 1 <= total <= 200:
        dest = state["last_container"]["children"] \
            if state["last_container"] is not None else tree
        for ep_no in range(1, total + 1):
            if ep_no in used:
                continue
            dest.append({"kind": "episode", "title": f"제{ep_no}화",
                         "summary": "", "episode_no": ep_no,
                         "stub": True, "children": []})
            counts["episodes"] += 1
            counts["stub_episodes"] += 1
    return tree, counts


def _sanitize_plan_node(raw: dict) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    kids = raw.get("children") or []
    return {"kind": str(raw.get("kind", "")),
            "title": str(raw.get("title", "")).strip()[:80],
            "summary": str(raw.get("summary", "")).strip()[:2000],
            "episode_no": raw.get("episode_no", 0),
            "children": [k for k in kids if isinstance(k, dict)]}


def handoff_migrate(conn: sqlite3.Connection, work_id: int,
                    plan: dict) -> dict:
    """Build the design tree from an AI migration plan, then lock ideas.

    plan is {"tree": [{kind, title, summary, episode_no, children}]}.
    Planning is shared with the preview via build_migration_tree, so the
    confirmed result always matches what was previewed.
    """
    work = _row(conn, "SELECT * FROM works WHERE id=?", (work_id,))
    if not work:
        raise KeyError(f"work {work_id} not found")
    if work["ideas_locked"]:
        raise ValueError("ideas already handed off (read-only)")
    structure = parse_structure(work["structure_json"])
    total = structure["total_episodes"]
    if not 1 <= total <= 200:
        raise ValueError("total_episodes must be 1..200")
    syn = get_synopsis(conn, work_id)
    settings = list_settings(conn, work_id)
    excerpt = (syn["content"] or "")[:500]
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM story_designs"
                       " WHERE work_id=?", (work_id,)).fetchone()
    version = row["v"] + 1
    cur = conn.execute(
        "INSERT INTO story_designs(work_id, version, scope_total_episodes,"
        " concept_json, sections_json) VALUES(?,?,?,?,?)",
        (work_id, version, total,
         json.dumps({"title_candidates": [work["title"]], "genre": "",
                     "tone": "", "theme": excerpt, "ending": ""},
                    ensure_ascii=False),
         json.dumps([], ensure_ascii=False)))
    design_id = cur.lastrowid
    root = design_nodes.create_node(
        conn, design_id, kind="work", title=work["title"], logline=excerpt,
        body={"structure": structure, "synopsis": syn["content"] or "",
              "settings": [{"code": s["code"], "category": s["category"],
                            "title": s["title"], "summary": s["summary"],
                            "detail": s["detail"]} for s in settings]},
        _skip_commit=True)
    tree, counts = build_migration_tree(structure, plan)

    def _create(nodes: list, parent_id: str) -> None:
        for n in nodes:
            body = {"episode_no": n["episode_no"]} \
                if n["kind"] == "episode" else {}
            node = design_nodes.create_node(
                conn, design_id, kind=n["kind"], parent_id=parent_id,
                title=n["title"], logline=n["summary"], body=body,
                _skip_commit=True)
            _create(n["children"], node["id"])

    _create(tree, root["id"])
    conn.execute("UPDATE works SET ideas_locked=1, stage='design',"
                 " updated_at=? WHERE id=?", (now_iso(), work_id))
    conn.commit()
    return {"design_id": design_id, "version": version,
            "root_node": root, "structure": structure, "counts": counts}
