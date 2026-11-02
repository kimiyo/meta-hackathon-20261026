"""Drafting pipeline (§6.2): outline+states+style → manuscript revision.

The server validates length (±30% of target), required/forbidden phrases,
executes lookup_requests, and stores revisions. The AI never touches the DB.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import memory, refs, style_presets
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
    if not row["style_preset"] or row["style_preset"] not in style_presets.PRESETS:
        # Legacy row (pre-preset): adopt the default, keep any custom voice.
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
    row = conn.execute("SELECT length_target, style_preset FROM style_policies"
                       " WHERE work_id=?", (work_id,)).fetchone()
    if row is None:
        key, _ = style_presets.resolve_preset(None)
        return {"length_min": DEFAULT_MIN, "length_max": DEFAULT_MAX,
                "is_default": True, "preset": key}
    lo, hi = parse_length_target(row["length_target"])
    key, _ = style_presets.resolve_preset(row["style_preset"])
    return {"length_min": lo, "length_max": hi, "is_default": False,
            "preset": key}


def set_style_preset(conn: sqlite3.Connection, work_id: int,
                     key: str) -> dict:
    """Switch the writing voice (preset values replace pov/diction/feedback).

    Applies to drafts started after the save, like the length target."""
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
            "UPDATE style_policies SET style_preset=?, pov=?, diction=?,"
            " feedback=?, updated_at=? WHERE work_id=?",
            (key, preset["pov"], preset["diction"], preset["feedback"],
             now_iso(), work_id))
        lo, hi = parse_length_target(row["length_target"])
    conn.commit()
    return {"length_min": lo, "length_max": hi, "is_default": False,
            "preset": key}


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


REF_CONTEXT_BUDGET = 12000  # chars of @-resolved context per redraft


def expand_line_refs(text: str,
                     tokens: list[str]) -> tuple[list[str], list[str]]:
    """Turn @L tokens into quoted excerpts. Returns (quotes, rest).

    Line numbers are 1-based over text.split("\n") — the exact split the
    writing screen renders, so displayed and server numbers always match.
    Out-of-range pins raise ValueError: quoting wrong lines silently
    would be worse than refusing."""
    lines = (text or "").split("\n")
    quotes, rest = [], []
    for tok in tokens:
        rng = refs.parse_line_token(tok)
        if rng is None:
            rest.append(tok)
            continue
        start, end = rng
        if not (1 <= start <= end <= len(lines)):
            raise ValueError(
                f"line ref @{tok} out of range "
                f"(manuscript has {len(lines)} lines)")
        excerpt = "\n".join(lines[start - 1:end])
        quotes.append(f"[원고 L{start}-{end}]\n{excerpt}")
    return quotes, rest


def log_draft_request(conn: sqlite3.Connection, episode_no: int, *,
                      instruction: str, resolved: list[str],
                      unknown: list[str], full: bool,
                      base_version: int | None,
                      result_version: int | None = None,
                      error: str = "") -> int:
    """Append one chat-history row (attempts and failures alike)."""
    cur = conn.execute(
        "INSERT INTO draft_requests(episode_no, instruction, refs_json,"
        " unknown_json, full_regen, base_version, result_version, error,"
        " created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (episode_no, instruction, json.dumps(resolved, ensure_ascii=False),
         json.dumps(unknown, ensure_ascii=False), 1 if full else 0,
         base_version, result_version, error, now_iso()))
    conn.commit()
    return cur.lastrowid


def list_draft_requests(conn: sqlite3.Connection,
                        episode_no: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM draft_requests WHERE episode_no=? ORDER BY id",
        (episode_no,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["refs"] = json.loads(d.pop("refs_json"))
        d["unknown"] = json.loads(d.pop("unknown_json"))
        d["full"] = bool(d.pop("full_regen"))
        out.append(d)
    return out


def redraft_episode(meta, conn, *, design_id: int, work_id: int,
                    episode_no: int, base_text: str,
                    base_version: int | None, instruction: str,
                    full: bool = False, max_tokens: int = 8000) -> dict:
    """Author-driven redraft: instruction + @ context → new revision.

    full=False revises base_text (manuscript_draft); full=True writes
    fresh from the outline. @Title/@E001 tokens resolve to setting/node
    text, @L7/@L12-15 pins to quoted manuscript lines. Unknown @ tokens
    are reported, never fatal."""
    instruction = (instruction or "").strip()
    if not instruction and not full:
        raise ValueError(
            "instruction must not be blank (or request full regeneration)")
    if full and not instruction:
        instruction = "원고 전체를 새로 써라."
    tokens = refs.parse_mentions(instruction)
    line_toks = [t for t in tokens if refs.parse_line_token(t) is not None]
    ref_toks = [t for t in tokens if refs.parse_line_token(t) is None]
    if line_toks and (full or not base_text):
        raise ValueError(
            "@L line pins need a base manuscript (partial redraft only)")
    setting_lines, node_lines, unknown = refs.resolve_refs(
        conn, work_id, ref_toks)
    quotes, _ = expand_line_refs(base_text, line_toks)
    context = "\n".join(setting_lines + node_lines + quotes)
    if len(context) > REF_CONTEXT_BUDGET:
        context = context[:REF_CONTEXT_BUDGET] + "\n[이하 생략]"
    composed = instruction
    if context:
        composed += "\n\n[참조 자료]\n" + context
    rev = draft_episode(meta, conn, design_id=design_id, work_id=work_id,
                        episode_no=episode_no,
                        manuscript_draft="" if full else base_text,
                        author_instruction=composed, max_tokens=max_tokens,
                        revision_label="AI 전체 재생성" if full else "AI 부분 수정")
    resolved = [t for t in ref_toks if t not in unknown]
    return {**rev, "base_version": base_version,
            "resolved_refs": resolved, "unknown_refs": unknown,
            "line_quotes": len(quotes)}


def save_revision(conn, episode_no: int, text: str, label: str = "") -> dict:
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM manuscript_revisions"
                       " WHERE episode_no=?", (episode_no,)).fetchone()
    version = row["v"] + 1
    conn.execute("UPDATE manuscript_revisions SET approval_status='superseded'"
                 " WHERE episode_no=? AND approval_status='draft'", (episode_no,))
    cur = conn.execute(
        "INSERT INTO manuscript_revisions(episode_no, version, text, label, created_at)"
        " VALUES(?,?,?,?,?)", (episode_no, version, text, label, now_iso()))
    conn.commit()
    from . import manuscripts  # lazy: manuscripts imports drafting lazily too
    manuscripts.sync_doc_after_revision(conn, episode_no, cur.lastrowid)
    return {"revision_id": cur.lastrowid, "version": version,
            "episode_no": episode_no}


def draft_episode(meta, conn, *, design_id: int, work_id: int, episode_no: int,
                  manuscript_draft: str = "", author_instruction: str = "",
                  max_tokens: int = 8000, revision_label: str = "AI 초안") -> dict:
    """Run drafting (with at most one lookup-injection round) and save revision."""
    outline = get_outline(conn, design_id, episode_no)
    style = get_style(conn, work_id)
    _, style_hi = parse_length_target(style["length_target"])
    out_budget = draft_output_budget(style_hi, floor=max_tokens)
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
        schema=sch.DraftOutput, max_tokens=out_budget)

    lookups = out.get("lookup_requests") or []
    if lookups:
        results = execute_lookups(conn, lookups, episode_no)
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
    rev = save_revision(conn, episode_no, out["manuscript"], label=revision_label)
    return {**rev, "length": len(out["manuscript"]),
            "lookups_executed": len(lookups)}
