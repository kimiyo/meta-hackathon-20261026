"""Drafting pipeline (§6.2): outline+states+style → manuscript revision.

The server validates length (±30% of target), required/forbidden phrases,
executes lookup_requests, and stores revisions. The AI never touches the DB.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import memory
from .db import now_iso
from .errors import LLMStepError
from .schemas import drafting as sch

PROMPT = (Path(__file__).parent / "prompts" / "drafting.txt").read_text(encoding="utf-8")

DEFAULT_MIN, DEFAULT_MAX = 3000, 5000


def get_style(conn: sqlite3.Connection, work_id: int) -> dict:
    row = conn.execute("SELECT * FROM style_policies WHERE work_id=?",
                       (work_id,)).fetchone()
    if row:
        return dict(row)
    conn.execute(
        "INSERT INTO style_policies(work_id, length_target, updated_at)"
        " VALUES(?,?,?)",
        (work_id, f"{DEFAULT_MIN}-{DEFAULT_MAX}", now_iso()))
    conn.commit()
    return {"work_id": work_id, "pov": "", "diction": "",
            "length_target": f"{DEFAULT_MIN}-{DEFAULT_MAX}", "feedback": ""}


def parse_length_target(target: str) -> tuple[int, int]:
    m = re.search(r"(\d+)\s*[-~]\s*(\d+)", target or "")
    if not m:
        return DEFAULT_MIN, DEFAULT_MAX
    return int(m.group(1)), int(m.group(2))


def get_outline(conn: sqlite3.Connection, design_id: int, episode_no: int) -> dict:
    row = conn.execute("SELECT * FROM episode_outlines WHERE design_id=? AND episode_no=?",
                       (design_id, episode_no)).fetchone()
    if not row:
        raise KeyError(f"outline {design_id}/{episode_no} not found")
    o = dict(row)
    for k in ("events_json", "char_changes_json", "foreshadowing_json",
              "links_json", "constraints_json"):
        o[k[:-5]] = json.loads(o.pop(k))
    return o


def collect_entity_states(conn, episode_no: int) -> list[dict]:
    """Approved-state snapshots of all entities as of the previous episode."""
    states = []
    for ent in memory.search_entities(conn):
        attrs = memory.reconstruct_state(conn, ent["entity_id"], episode_no - 1)
        states.append({"entity_id": ent["entity_id"], "name": ent["canonical_name"],
                       "type": ent["type"], "attributes": attrs})
    return states


def execute_lookups(conn, requests: list[dict], episode_no: int) -> list[dict]:
    """Run lookup_requests against committed data only."""
    results = []
    for r in requests:
        kind = r.get("kind", "")
        if kind == "entity_state" and r.get("entity_id"):
            eid = r["entity_id"]
            ep = r.get("episode") or (episode_no - 1)
            if memory.get_entity(conn, eid):
                results.append({"kind": kind, "entity_id": eid, "episode": ep,
                                "attributes": memory.reconstruct_state(conn, eid, ep)})
            else:
                results.append({"kind": kind, "entity_id": eid, "error": "not_found"})
        elif kind == "event_search":
            q = r.get("query", "")
            rows = conn.execute(
                "SELECT * FROM events WHERE summary LIKE ? OR cause LIKE ?"
                " OR consequence LIKE ? ORDER BY episode_no LIMIT 5",
                (f"%{q}%", f"%{q}%", f"%{q}%")).fetchall()
            results.append({"kind": kind, "query": q,
                            "events": [dict(x) for x in rows]})
        elif kind == "mention_search":
            q = r.get("query", "")
            rows = conn.execute(
                "SELECT * FROM entity_mentions WHERE quote LIKE ?"
                " ORDER BY episode_no LIMIT 5", (f"%{q}%",)).fetchall()
            results.append({"kind": kind, "query": q,
                            "mentions": [dict(x) for x in rows]})
        else:
            results.append({"kind": kind, "error": "unsupported_lookup"})
    return results


def validate_manuscript(text: str, constraints: list[str],
                        length_target: str) -> list[str]:
    """Return a list of violations (empty = pass). Pure function."""
    problems = []
    lo, hi = parse_length_target(length_target)
    lo30, hi30 = int(lo * 0.7), int(hi * 1.3)
    n = len(text)
    if not (lo30 <= n <= hi30):
        problems.append(f"length {n} outside ±30% of {lo}-{hi} ({lo30}-{hi30})")
    for c in constraints or []:
        if c.startswith("필수:"):
            phrase = c[len("필수:"):].strip()
            if phrase and phrase not in text:
                problems.append(f"missing required phrase: {phrase}")
        elif c.startswith("금지:"):
            phrase = c[len("금지:"):].strip()
            if phrase and phrase in text:
                problems.append(f"forbidden phrase present: {phrase}")
    return problems


def save_revision(conn, episode_no: int, text: str) -> dict:
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM manuscript_revisions"
                       " WHERE episode_no=?", (episode_no,)).fetchone()
    version = row["v"] + 1
    conn.execute("UPDATE manuscript_revisions SET approval_status='superseded'"
                 " WHERE episode_no=? AND approval_status='draft'", (episode_no,))
    cur = conn.execute(
        "INSERT INTO manuscript_revisions(episode_no, version, text, created_at)"
        " VALUES(?,?,?,?)", (episode_no, version, text, now_iso()))
    conn.commit()
    return {"revision_id": cur.lastrowid, "version": version,
            "episode_no": episode_no}


def draft_episode(meta, conn, *, design_id: int, work_id: int, episode_no: int,
                  manuscript_draft: str = "", author_instruction: str = "",
                  max_tokens: int = 8000) -> dict:
    """Run drafting (with at most one lookup-injection round) and save revision."""
    outline = get_outline(conn, design_id, episode_no)
    style = get_style(conn, work_id)
    states = collect_entity_states(conn, episode_no)
    base_input = {
        "episode_outline": outline,
        "entity_states": states,
        "style_policy": style,
    }
    if manuscript_draft:
        base_input["manuscript_draft"] = manuscript_draft
    if author_instruction:
        base_input["author_instruction"] = author_instruction

    out = meta.chat_json(
        bundle="drafting", episode=episode_no, system=PROMPT, work_id=work_id,
        user="다음 입력으로 원고를 써라. 스켈레톤: "
             '{"manuscript": str, "lookup_requests": [...]}\n'
             + json.dumps(base_input, ensure_ascii=False),
        schema=sch.DraftOutput, max_tokens=max_tokens)

    lookups = out.get("lookup_requests") or []
    if lookups:
        results = execute_lookups(conn, lookups, episode_no)
        out = meta.chat_json(
            bundle="drafting", episode=episode_no, system=PROMPT, work_id=work_id,
            user="조회 결과를 반영해 원고를 확정하라. 스켈레톤: "
                 '{"manuscript": str, "lookup_requests": []}\n원고 초안: '
                 + out["manuscript"][:4000]
                 + "\n조회 결과: " + json.dumps(results, ensure_ascii=False),
            schema=sch.DraftOutput, max_tokens=max_tokens)

    problems = validate_manuscript(out["manuscript"], outline.get("constraints", []),
                                   style["length_target"])
    if problems:
        raise LLMStepError("drafting", episode_no, "manuscript_invalid",
                           "; ".join(problems))
    rev = save_revision(conn, episode_no, out["manuscript"])
    return {**rev, "length": len(out["manuscript"]),
            "lookups_executed": len(lookups)}
