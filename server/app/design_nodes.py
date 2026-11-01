"""Top-down design tree: Work/Part/Chapter/Episode/Scene nodes.

Node ids carry a kind prefix (N/P/C/E/S) plus a zero-padded sequence that is
unique per kind across all designs (id is a global primary key), e.g. E026.
The user-facing identity is the per-design code: P/C/E/S restart at 001 in
each tree (e.g. E001) while the work root keeps a global N number, so the
full code N002-E001 is unique across works. Episode nodes sync into
episode_outlines so the existing writing pipeline keeps working unchanged.
"""
from __future__ import annotations

import json
import re
import sqlite3

from .db import now_iso, touch_work

KINDS = {"work": "N", "part": "P", "chapter": "C", "episode": "E", "scene": "S"}


def _row(conn, sql, args=()) -> dict | None:
    r = conn.execute(sql, args).fetchone()
    return dict(r) if r else None


def node_code(kind: str, seq: int) -> str:
    return f"{KINDS[kind]}{seq:03d}"


def design_root_code(conn: sqlite3.Connection, design_id: int) -> str | None:
    r = _row(conn,
             "SELECT seq FROM design_nodes"
             " WHERE design_id=? AND kind='work' AND parent_id IS NULL",
             (design_id,))
    if not r or not isinstance(r["seq"], int) or r["seq"] <= 0:
        return None
    return node_code("work", r["seq"])


def _public(row: dict, root_code: str | None = None) -> dict:
    row = dict(row)
    try:
        row["body"] = json.loads(row.pop("body_json"))
    except ValueError:
        row["body"] = {}
    seq = row.get("seq")
    code = (node_code(row["kind"], seq)
            if isinstance(seq, int) and seq > 0 else None)
    row["code"] = code
    if row["kind"] == "work":
        row["full_code"] = code
    else:
        row["full_code"] = (f"{root_code}-{code}"
                            if root_code and code else None)
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


def allocate_seq(conn: sqlite3.Connection, design_id: int, kind: str) -> int:
    """Next code number: per design+kind, except work roots (global N)."""
    if kind == "work":
        top = conn.execute(
            "SELECT COALESCE(MAX(seq),0) m FROM design_nodes"
            " WHERE kind='work'").fetchone()
    else:
        top = conn.execute(
            "SELECT COALESCE(MAX(seq),0) m FROM design_nodes"
            " WHERE design_id=? AND kind=?", (design_id, kind)).fetchone()
    return (top["m"] or 0) + 1


_CODE_RE = re.compile(r"^([NPCES])(\d{1,6})$")
_FULL_RE = re.compile(r"^(N\d{1,6})-([NPCES]\d{1,6})$")
_PREFIX_KIND = {v: k for k, v in KINDS.items()}


def resolve_node_id(conn: sqlite3.Connection, design_id: int,
                    ref: str) -> str | None:
    """Resolve a short code (E001), full code (N002-E001), or legacy id.

    Short codes win over legacy ids: inside one tree E001 always means
    that tree's first episode, even though another tree owns the global
    id E001. Everything is constrained to this design, so a full code
    from another tree resolves to nothing here.
    """
    ref = (ref or "").strip().lstrip("@").upper()
    m = _FULL_RE.match(ref)
    if m:
        if design_root_code(conn, design_id) != m.group(1):
            return None
        ref = m.group(2)
    m = _CODE_RE.match(ref)
    if m:
        hit = _row(conn,
                   "SELECT id FROM design_nodes"
                   " WHERE design_id=? AND kind=? AND seq=?",
                   (design_id, _PREFIX_KIND[m.group(1)], int(m.group(2))))
        return hit["id"] if hit else None
    hit = _row(conn,
               "SELECT id FROM design_nodes WHERE design_id=? AND id=?",
               (design_id, ref))
    return hit["id"] if hit else None


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
    seq = allocate_seq(conn, design_id, kind)
    ts = now_iso()
    conn.execute(
        "INSERT INTO design_nodes(id, design_id, parent_id, kind, title,"
        " logline, body_json, sort_order, updated_at, seq)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (node_id, design_id, parent_id, kind, title.strip(), logline,
         json.dumps(body, ensure_ascii=False), sib["m"] + 1, ts, seq))
    if kind == "episode":
        _sync_episode_outline(conn, design_id, node_id, title.strip(),
                              logline, body)
    touch_work(conn, design["work_id"])
    if not _skip_commit:
        conn.commit()
    row = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    if kind == "work" and parent_id is None:
        root_code = node_code(kind, seq)
    else:
        root_code = design_root_code(conn, design_id)
    return _public(row, root_code)


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
        ors = ["title LIKE ?", "logline LIKE ?", "id LIKE ?"]
        args += [f"%{q}%"] * 3
        hit = resolve_node_id(conn, design_id, q)
        if hit:
            ors.append("id=?")
            args.append(hit)
        sql += " AND (" + " OR ".join(ors) + ")"
    sql += " ORDER BY sort_order, id"
    root_code = design_root_code(conn, design_id)
    return [_public(dict(r), root_code) for r in conn.execute(sql, args)]


def get_node(conn: sqlite3.Connection, node_id: str) -> dict | None:
    row = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    if not row:
        return None
    return _public(row, design_root_code(conn, row["design_id"]))


def get_node_with_children(conn: sqlite3.Connection,
                           node_id: str) -> dict | None:
    node = get_node(conn, node_id)
    if not node:
        return None
    root_code = design_root_code(conn, node["design_id"])
    kids = [_public(dict(r), root_code) for r in conn.execute(
        "SELECT * FROM design_nodes WHERE parent_id=? AND archived=0"
        " ORDER BY sort_order, id", (node_id,))]
    node["children"] = [
        {"id": k["id"], "kind": k["kind"], "title": k["title"],
         "logline": k["logline"], "code": k["code"],
         "full_code": k["full_code"]} for k in kids]
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
        # Merge, not replace: patch callers (AI proposals) send partial
        # bodies without episode_no; replacing would wipe the stored
        # number and trip the outline sync below.
        body = {**body, **fields["body"]}
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
    return _public(fresh, design_root_code(conn, fresh["design_id"]))


def set_archived(conn: sqlite3.Connection, node_id: str,
                 archived: bool) -> dict:
    node = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    if not node:
        raise KeyError(f"node {node_id} not found")
    conn.execute("UPDATE design_nodes SET archived=?, updated_at=?"
                 " WHERE id=?", (1 if archived else 0, now_iso(), node_id))
    conn.commit()
    fresh = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    return _public(fresh, design_root_code(conn, fresh["design_id"]))


def delete_node_subtree(conn: sqlite3.Connection, node_id: str) -> dict:
    """Hard-delete a node with all descendants.

    Removes owned node settings plus the design-scoped episode outlines
    and jobs of deleted episodes. Partner threads on deleted nodes go
    too (with their messages). Manuscript revisions stay: they are
    history keyed globally by episode_no, unsafe to cascade. The root
    node cannot be deleted.
    """
    node = _node_or_raise(conn, node_id)
    if node["parent_id"] is None:
        raise ValueError("cannot delete the root node")
    rows = conn.execute(
        "SELECT id, parent_id, kind, body_json FROM design_nodes"
        " WHERE design_id=?", (node["design_id"],)).fetchall()
    by_parent: dict = {}
    by_id = {}
    for r in rows:
        by_parent.setdefault(r["parent_id"], []).append(r["id"])
        by_id[r["id"]] = r
    ids = [node_id]
    seen = {node_id}
    queue = [node_id]
    while queue:
        for cid in by_parent.get(queue.pop(), []):
            if cid not in seen:
                seen.add(cid)
                ids.append(cid)
                queue.append(cid)
    ep_nos = []
    for did in ids:
        r = by_id.get(did)
        if not r or r["kind"] != "episode":
            continue
        try:
            ep_no = (json.loads(r["body_json"] or "{}") or {}).get(
                "episode_no")
        except ValueError:
            continue
        if isinstance(ep_no, int) and ep_no >= 1:
            ep_nos.append(ep_no)
    q = ",".join("?" * len(ids))
    conn.execute(f"DELETE FROM node_settings WHERE node_id IN ({q})", ids)
    tids = [r[0] for r in conn.execute(
        f"SELECT id FROM design_threads WHERE node_id IN ({q})", ids)]
    if tids:
        tq = ",".join("?" * len(tids))
        conn.execute(
            f"DELETE FROM design_messages WHERE thread_id IN ({tq})", tids)
        conn.execute(
            f"DELETE FROM design_threads WHERE id IN ({tq})", tids)
    if ep_nos:
        eq = ",".join("?" * len(ep_nos))
        conn.execute(
            f"DELETE FROM episode_outlines WHERE design_id=? AND episode_no IN ({eq})",
            [node["design_id"], *ep_nos])
        conn.execute(
            f"DELETE FROM episode_jobs WHERE design_id=? AND episode_no IN ({eq})",
            [node["design_id"], *ep_nos])
    conn.execute(f"DELETE FROM design_nodes WHERE id IN ({q})", ids)
    _touch_node_work(conn, node)
    conn.commit()
    return {"deleted": ids, "threads": len(tids)}


# ---- 노드별 설정 (scoped canon) ----
#
# 각 노드는 임의의 설정(인물·세계관·사건·기타)을 가질 수 있다. 설정의
# 적용 범위는 해당 노드와 모든 자손이며, 같은 코드가 조상·자손에 함께
# 있으면 가장 가까운 정의가 이긴다(자식 우선). 코드는 디자인별 S번호로
# 자동 발급해서 우연한 중복이 생기지 않으며, 명시 코드는 조상의 코드를
# 가리킬 때만 허용된다(override 연결 고리).

NODE_SETTING_CATEGORIES = ("character", "world", "event", "misc")


def _node_or_raise(conn: sqlite3.Connection, node_id: str) -> dict:
    n = _row(conn, "SELECT * FROM design_nodes WHERE id=?", (node_id,))
    if not n:
        raise KeyError(f"node {node_id} not found")
    return n


def _touch_node_work(conn: sqlite3.Connection, node: dict) -> None:
    design = _row(conn, "SELECT work_id FROM story_designs WHERE id=?",
                  (node["design_id"],))
    if design:
        touch_work(conn, design["work_id"])


def _valid_setting_code(code) -> bool:
    return (isinstance(code, str) and len(code) >= 4
            and code[0] == "S" and code[1:].isdigit())


def allocate_node_setting_code(conn: sqlite3.Connection,
                               node_id: str) -> str:
    """Next per-design S-number (S001, S002, ...). Never reused."""
    node = _node_or_raise(conn, node_id)
    top = conn.execute(
        "SELECT s.code code FROM node_settings s"
        " JOIN design_nodes n ON n.id=s.node_id"
        " WHERE n.design_id=? AND s.code LIKE 'S%'"
        " ORDER BY LENGTH(s.code) DESC, s.code DESC LIMIT 1",
        (node["design_id"],)).fetchone()
    n = 0
    if top and top["code"]:
        try:
            n = int(top["code"][1:])
        except ValueError:
            n = 0
    return f"S{n + 1:03d}"


def _ancestor_has_code(conn: sqlite3.Connection, node: dict,
                       code: str) -> bool:
    seen = {node["id"]}
    parent_id = node["parent_id"]
    guard = 0
    while parent_id is not None and guard < 64:
        guard += 1
        if parent_id in seen:
            break
        seen.add(parent_id)
        hit = conn.execute(
            "SELECT id FROM node_settings WHERE node_id=? AND code=?",
            (parent_id, code)).fetchone()
        if hit:
            return True
        prow = conn.execute(
            "SELECT parent_id FROM design_nodes WHERE id=?",
            (parent_id,)).fetchone()
        parent_id = prow["parent_id"] if prow else None
    return False


def list_node_settings(conn: sqlite3.Connection, node_id: str) -> list[dict]:
    _node_or_raise(conn, node_id)
    return [dict(r) for r in conn.execute(
        "SELECT * FROM node_settings WHERE node_id=? ORDER BY id",
        (node_id,))]


def list_design_node_settings(conn: sqlite3.Connection,
                              design_id: int) -> list[dict]:
    """All node settings in a design (for tree bulk display)."""
    _design_or_raise(conn, design_id)
    return [dict(r) for r in conn.execute(
        "SELECT s.* FROM node_settings s"
        " JOIN design_nodes n ON n.id=s.node_id"
        " WHERE n.design_id=? ORDER BY s.node_id, s.id",
        (design_id,))]


def create_node_setting(conn: sqlite3.Connection, node_id: str, *,
                        category: str = "misc", title: str = "",
                        summary: str = "", detail: str = "",
                        code: str | None = None) -> dict:
    """Create a setting on a node. Pass an ancestor's code to override it."""
    node = _node_or_raise(conn, node_id)
    if category not in NODE_SETTING_CATEGORIES:
        raise ValueError(
            f"category must be one of {NODE_SETTING_CATEGORIES}")
    if code is None:
        code = allocate_node_setting_code(conn, node_id)
    else:
        if not _valid_setting_code(code):
            raise ValueError("code must look like S001")
        dup = conn.execute(
            "SELECT id FROM node_settings WHERE node_id=? AND code=?",
            (node_id, code)).fetchone()
        if dup:
            raise ValueError(f"code {code} already used on node {node_id}")
        if not _ancestor_has_code(conn, node, code):
            raise ValueError(
                f"code {code} is not defined on any ancestor"
                f" of node {node_id}")
    cur = conn.execute(
        "INSERT INTO node_settings(node_id, code, category, title, summary,"
        " detail, updated_at) VALUES(?,?,?,?,?,?,?)",
        (node_id, code, category, title.strip(), summary, detail,
         now_iso()))
    _touch_node_work(conn, node)
    conn.commit()
    return _row(conn, "SELECT * FROM node_settings WHERE id=?",
                (cur.lastrowid,))


def update_node_setting(conn: sqlite3.Connection, setting_id: int,
                        fields: dict) -> dict:
    # code는 override 연결 고리라서 변경 불가다.
    allowed = {"category", "title", "summary", "detail"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "category" and v not in NODE_SETTING_CATEGORIES:
            raise ValueError(
                f"category must be one of {NODE_SETTING_CATEGORIES}")
        sets.append(f"{k}=?")
        args.append(v.strip() if k == "title" and isinstance(v, str) else v)
    if not sets:
        raise ValueError("no valid node-setting fields")
    row = _row(conn, "SELECT * FROM node_settings WHERE id=?",
               (setting_id,))
    if not row:
        raise KeyError(f"node setting {setting_id} not found")
    sets.append("updated_at=?")
    args += [now_iso(), setting_id]
    conn.execute(f"UPDATE node_settings SET {', '.join(sets)} WHERE id=?",
                 args)
    node = _row(conn, "SELECT * FROM design_nodes WHERE id=?",
                (row["node_id"],))
    if node:
        _touch_node_work(conn, node)
    conn.commit()
    return _row(conn, "SELECT * FROM node_settings WHERE id=?",
                (setting_id,))


def delete_node_setting(conn: sqlite3.Connection,
                        setting_id: int) -> dict:
    row = _row(conn, "SELECT * FROM node_settings WHERE id=?",
               (setting_id,))
    if not row:
        raise KeyError(f"node setting {setting_id} not found")
    conn.execute("DELETE FROM node_settings WHERE id=?", (setting_id,))
    node = _row(conn, "SELECT * FROM design_nodes WHERE id=?",
                (row["node_id"],))
    if node:
        _touch_node_work(conn, node)
    conn.commit()
    return {"deleted": setting_id}


def effective_node_settings(conn: sqlite3.Connection,
                            node_id: str) -> dict:
    """Own settings plus inherited winners from unarchived ancestors.

    Nearest definition of a code wins; farther ones are shadowed.
    Archived ancestors contribute nothing but do not sever the chain.
    `overridden` lists ancestor codes shadowed by this node's own rows.
    """
    node = _node_or_raise(conn, node_id)
    root_code = design_root_code(conn, node["design_id"])
    own = [dict(r) for r in conn.execute(
        "SELECT * FROM node_settings WHERE node_id=? ORDER BY id",
        (node_id,))]
    own_codes = {s["code"] for s in own}
    seen = set(own_codes)
    inherited, overridden = [], []
    seen_nodes = {node_id}
    parent_id = node["parent_id"]
    guard = 0
    while parent_id is not None and guard < 64:
        guard += 1
        if parent_id in seen_nodes:
            break
        seen_nodes.add(parent_id)
        parent = _row(conn, "SELECT * FROM design_nodes WHERE id=?",
                      (parent_id,))
        if not parent:
            break
        parent_id = parent["parent_id"]
        if parent["archived"]:
            continue
        for s in conn.execute(
                "SELECT * FROM node_settings WHERE node_id=? ORDER BY id",
                (parent["id"],)):
            s = dict(s)
            if s["code"] in seen:
                if s["code"] in own_codes and s["code"] not in overridden:
                    overridden.append(s["code"])
                continue
            seen.add(s["code"])
            s["source_node"] = _public(parent, root_code)["full_code"]
            s["source_kind"] = parent["kind"]
            s["source_title"] = parent["title"]
            inherited.append(s)
    return {"own": own, "inherited": inherited, "overridden": overridden}
