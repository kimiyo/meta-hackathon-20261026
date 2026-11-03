"""Drafting pipeline (§6.2): outline+states+style → manuscript revision.

The server validates length (±30% of target), required/forbidden phrases,
executes lookup_requests, and stores revisions. The AI never touches the DB.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import memory, style_presets
from .db import now_iso
from .errors import LLMStepError
from .schemas import drafting as sch

PROMPT = (Path(__file__).parent / "prompts" / "drafting.txt").read_text(encoding="utf-8")

DEFAULT_MIN, DEFAULT_MAX = 5000, 7000
LENGTH_MIN_LIMIT = 1
LENGTH_MAX_LIMIT = 10000

# Output budget scales with the length target: validation accepts up to
# hi*1.3 chars, muse-spark-1.3 measured ~1.1 tokens/char (EP1: 3842
# chars / 4280 out tokens), and max_tokens also covers reasoning.
# ceil(hi * 39/20): 1.3 validation overshoot x 1.5 tokens/char.
# Integer math keeps exact values (no float ceil surprises).
BUDGET_NUM, BUDGET_DEN = 39, 20


def draft_output_budget(length_max: int, floor: int = 8000) -> int:
    """Ceiling-only output budget for a drafting call. Larger targets
    need a larger ceiling; actual spend is unchanged when text is short."""
    return max(floor,
               (length_max * BUDGET_NUM + BUDGET_DEN - 1) // BUDGET_DEN)


def get_style(conn: sqlite3.Connection, work_id: int) -> dict:
    row = conn.execute("SELECT * FROM style_policies WHERE work_id=?",
                       (work_id,)).fetchone()
    if row is None:
        key, preset = style_presets.resolve_preset(None)
        conn.execute(
            "INSERT INTO style_policies(work_id, length_target, pov, diction,"
            " feedback, style_preset, updated_at) VALUES(?,?,?,?,?,?,?)",
            (work_id, f"{DEFAULT_MIN}-{DEFAULT_MAX}", preset["pov"],
             preset["diction"], preset["feedback"], key, now_iso()))
        conn.commit()
        row = conn.execute("SELECT * FROM style_policies WHERE work_id=?",
                           (work_id,)).fetchone()
        return dict(row)
    if row["custom_style_id"] is None and (
            not row["style_preset"]
            or row["style_preset"] not in style_presets.PRESETS):
        # Legacy row (pre-preset): adopt the default, keep any custom voice.
        # Rows pointing at a user style keep their snapshotted voice.
        key, preset = style_presets.resolve_preset(None)
        conn.execute(
            "UPDATE style_policies SET style_preset=?,"
            " pov=COALESCE(NULLIF(pov,''),?),"
            " diction=COALESCE(NULLIF(diction,''),?),"
            " feedback=COALESCE(NULLIF(feedback,''),?), updated_at=?"
            " WHERE work_id=?",
            (key, preset["pov"], preset["diction"], preset["feedback"],
             now_iso(), work_id))
        conn.commit()
        row = conn.execute("SELECT * FROM style_policies WHERE work_id=?",
                           (work_id,)).fetchone()
    return dict(row)


def get_length_range(conn: sqlite3.Connection, work_id: int) -> dict:
    """Effective style for display. Creates no row."""
    row = conn.execute("SELECT length_target, style_preset, custom_style_id"
                       " FROM style_policies WHERE work_id=?",
                       (work_id,)).fetchone()
    if row is None:
        key, _ = style_presets.resolve_preset(None)
        return {"length_min": DEFAULT_MIN, "length_max": DEFAULT_MAX,
                "is_default": True, "preset": key,
                "custom_style_id": None, "custom_style_name": None}
    lo, hi = parse_length_target(row["length_target"])
    if row["custom_style_id"] is not None:
        custom = conn.execute("SELECT name FROM custom_styles WHERE id=?",
                              (row["custom_style_id"],)).fetchone()
        if custom is not None:
            return {"length_min": lo, "length_max": hi, "is_default": False,
                    "preset": None, "custom_style_id": row["custom_style_id"],
                    "custom_style_name": custom["name"]}
        # Orphaned pointer (style deleted out of band): fall through to the key.
    key, _ = style_presets.resolve_preset(row["style_preset"])
    return {"length_min": lo, "length_max": hi, "is_default": False,
            "preset": key, "custom_style_id": None, "custom_style_name": None}


def set_style_preset(conn: sqlite3.Connection, work_id: int,
                     key: str) -> dict:
    """Switch the writing voice (preset values replace pov/diction/feedback).

    Applies to drafts started after the save, like the length target.
    Clears any custom-style pointer."""
    if key not in style_presets.PRESETS:
        raise ValueError(f"unknown style preset: {key}")
    preset = style_presets.PRESETS[key]
    row = conn.execute("SELECT length_target FROM style_policies"
                       " WHERE work_id=?", (work_id,)).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO style_policies(work_id, length_target, pov, diction,"
            " feedback, style_preset, updated_at) VALUES(?,?,?,?,?,?,?)",
            (work_id, f"{DEFAULT_MIN}-{DEFAULT_MAX}", preset["pov"],
             preset["diction"], preset["feedback"], key, now_iso()))
        lo, hi = DEFAULT_MIN, DEFAULT_MAX
    else:
        conn.execute(
            "UPDATE style_policies SET style_preset=?, custom_style_id=NULL,"
            " pov=?, diction=?, feedback=?, updated_at=? WHERE work_id=?",
            (key, preset["pov"], preset["diction"], preset["feedback"],
             now_iso(), work_id))
        lo, hi = parse_length_target(row["length_target"])
    conn.commit()
    return {"length_min": lo, "length_max": hi, "is_default": False,
            "preset": key, "custom_style_id": None, "custom_style_name": None}


def set_length_target(conn: sqlite3.Connection, work_id: int,
                      lo: int, hi: int) -> dict:
    """Validate and upsert the length target (other style columns kept).

    Applies to drafts started after the save; existing revisions keep
    the target they were validated against (no retroactive change)."""
    for name, v in (("length_min", lo), ("length_max", hi)):
        if not isinstance(v, int) or isinstance(v, bool):
            raise ValueError(f"{name} must be an integer")
    if not (LENGTH_MIN_LIMIT <= lo <= hi <= LENGTH_MAX_LIMIT):
        raise ValueError(
            f"need {LENGTH_MIN_LIMIT}<=min<=max<={LENGTH_MAX_LIMIT},"
            f" got {lo}-{hi}")
    row = conn.execute("SELECT id FROM style_policies WHERE work_id=?",
                       (work_id,)).fetchone()
    if row is None:
        conn.execute("INSERT INTO style_policies(work_id, length_target,"
                   " updated_at) VALUES(?,?,?)",
                   (work_id, f"{lo}-{hi}", now_iso()))
    else:
        conn.execute("UPDATE style_policies SET length_target=?,"
                   " updated_at=? WHERE work_id=?",
                   (f"{lo}-{hi}", now_iso(), work_id))
    conn.commit()
    return {"length_min": lo, "length_max": hi, "is_default": False}


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


def collect_entity_states(conn, episode_no: int,
                          work_id: int | None = None) -> list[dict]:
    """Approved-state snapshots of all entities as of the previous episode."""
    states = []
    for ent in memory.search_entities(conn, work_id=work_id):
        attrs = memory.reconstruct_state(conn, ent["entity_id"], episode_no - 1,
                                         work_id)
        states.append({"entity_id": ent["entity_id"], "name": ent["canonical_name"],
                       "type": ent["type"], "attributes": attrs})
    return states


def execute_lookups(conn, requests: list[dict], episode_no: int,
                    work_id: int | None = None) -> list[dict]:
    """Run lookup_requests against committed data only."""
    results = []
    for r in requests:
        kind = r.get("kind", "")
        if kind == "entity_state" and r.get("entity_id"):
            eid = r["entity_id"]
            ep = r.get("episode") or (episode_no - 1)
            ent = memory.get_entity(conn, eid)
            if ent and (work_id is None or ent["work_id"] == work_id):
                results.append({"kind": kind, "entity_id": eid, "episode": ep,
                                "attributes": memory.reconstruct_state(
                                    conn, eid, ep, work_id)})
            else:
                results.append({"kind": kind, "entity_id": eid, "error": "not_found"})
        elif kind == "event_search":
            q = r.get("query", "")
            sql = ("SELECT * FROM events WHERE (summary LIKE ? OR cause LIKE ?"
                   " OR consequence LIKE ?)")
            args: list = [f"%{q}%", f"%{q}%", f"%{q}%"]
            if work_id is not None:
                sql += " AND work_id=?"
                args.append(work_id)
            rows = conn.execute(sql + " ORDER BY episode_no LIMIT 5",
                                args).fetchall()
            results.append({"kind": kind, "query": q,
                            "events": [dict(x) for x in rows]})
        elif kind == "mention_search":
            q = r.get("query", "")
            sql = "SELECT * FROM entity_mentions WHERE quote LIKE ?"
            args = [f"%{q}%"]
            if work_id is not None:
                sql += " AND work_id=?"
                args.append(work_id)
            rows = conn.execute(sql + " ORDER BY episode_no LIMIT 5",
                                args).fetchall()
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


def save_revision(conn, work_id: int, episode_no: int, text: str,
                  label: str = "") -> dict:
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM manuscript_revisions"
                       " WHERE work_id=? AND episode_no=?",
                       (work_id, episode_no)).fetchone()
    version = row["v"] + 1
    conn.execute("UPDATE manuscript_revisions SET approval_status='superseded'"
                 " WHERE work_id=? AND episode_no=? AND approval_status='draft'",
                 (work_id, episode_no))
    cur = conn.execute(
        "INSERT INTO manuscript_revisions(work_id, episode_no, version, text, label, created_at)"
        " VALUES(?,?,?,?,?,?)", (work_id, episode_no, version, text, label, now_iso()))
    conn.commit()
    from . import manuscripts  # lazy: manuscripts imports drafting lazily too
    manuscripts.sync_doc_after_revision(conn, work_id, episode_no, cur.lastrowid)
    return {"revision_id": cur.lastrowid, "version": version,
            "episode_no": episode_no}


DRAFT_MAX_RETRIES = 3  # manuscript_invalid retries inside one request


def _retry_feedback(detail: str) -> str:
    """Tell the next attempt what missed (length numbers when present)."""
    m = re.search(r"length (\d+) outside .* of (\d+)-(\d+) \((\d+)-(\d+)\)",
                  detail)
    if m:
        n, lo, hi, lo30, hi30 = (int(g) for g in m.groups())
        direction = "미달" if n < lo30 else "초과"
        return ("\n\n[분량 교정] 직전 원고는 " + str(n) + "자로 목표(" + str(lo)
                + "-" + str(hi) + "자, 허용 " + str(lo30) + "-" + str(hi30)
                + "자)에 " + direction + "했다. 분량을 맞춰 원고 전체를 다시 써라.")
    return "\n\n[검증 교정] 직전 원고가 기각됐다: " + detail + " 문제를 고쳐 다시 써라."


def draft_episode(meta, conn, *, design_id: int, work_id: int, episode_no: int,
                  manuscript_draft: str = "", author_instruction: str = "",
                  max_tokens: int = 8000, revision_label: str = "AI 초안",
                  max_retries: int = DRAFT_MAX_RETRIES) -> dict:
    """Run drafting, retrying manuscript_invalid up to max_retries.

    Only validation failures retry (with the miss fed back); transport and
    schema errors fail fast. Retries stay inside the request: the episode's
    attempt budget is untouched (the caller counts the request once)."""
    feedback = ""
    for n in range(max_retries + 1):
        try:
            return _draft_once(
                meta, conn, design_id=design_id, work_id=work_id,
                episode_no=episode_no, manuscript_draft=manuscript_draft,
                author_instruction=author_instruction + feedback,
                max_tokens=max_tokens, revision_label=revision_label)
        except LLMStepError as e:
            if e.reason != "manuscript_invalid" or n >= max_retries:
                raise
            feedback = _retry_feedback(e.detail)
    raise AssertionError("unreachable")


def _draft_once(meta, conn, *, design_id: int, work_id: int, episode_no: int,
                 manuscript_draft: str = "", author_instruction: str = "",
                 max_tokens: int = 8000, revision_label: str = "AI 초안") -> dict:
    """Run drafting (with at most one lookup-injection round) and save revision."""
    outline = get_outline(conn, design_id, episode_no)
    style = get_style(conn, work_id)
    _, style_hi = parse_length_target(style["length_target"])
    out_budget = draft_output_budget(style_hi, floor=max_tokens)
    states = collect_entity_states(conn, episode_no, work_id)
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
        schema=sch.DraftOutput, max_tokens=out_budget)

    lookups = out.get("lookup_requests") or []
    if lookups:
        results = execute_lookups(conn, lookups, episode_no, work_id)
        out = meta.chat_json(
            bundle="drafting", episode=episode_no, system=PROMPT, work_id=work_id,
            user="조회 결과를 반영해 원고를 확정하라. 스켈레톤: "
                 '{"manuscript": str, "lookup_requests": []}\n원고 초안: '
                 + out["manuscript"][:4000]
                 + "\n조회 결과: " + json.dumps(results, ensure_ascii=False),
            schema=sch.DraftOutput, max_tokens=out_budget)

    problems = validate_manuscript(out["manuscript"], outline.get("constraints", []),
                                   style["length_target"])
    if problems:
        raise LLMStepError("drafting", episode_no, "manuscript_invalid",
                           "; ".join(problems))
    rev = save_revision(conn, work_id, episode_no, out["manuscript"],
                        label=revision_label)
    return {**rev, "length": len(out["manuscript"]),
            "lookups_executed": len(lookups)}
