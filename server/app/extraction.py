"""Entity extraction pipeline (§6.3 / §3): manuscript → proposal items.

Server post-processing: narrative units, resolve entity_ref (existing ID
or new:name) within the work, register alias candidates for unresolvable
refs, citation-check every evidence_quote. Failed citations become
deferred (근거 미확인). A genuinely empty result is a successful empty
run, not an error; failures are recorded as failed runs by the caller.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import citation, memory
from .schemas import entity as sch

PROMPT = (Path(__file__).parent / "prompts" / "entity.txt").read_text(encoding="utf-8")
PROMPT_VERSION = "entity-v2"


def _prev_states(conn: sqlite3.Connection, episode_no: int,
                 work_id: int) -> list[dict]:
    states = []
    for ent in memory.search_entities(conn, work_id=work_id):
        aliases = [a["alias"] for a in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id=? AND status='confirmed'",
            (ent["entity_id"],))]
        states.append({
            "entity_id": ent["entity_id"], "name": ent["canonical_name"],
            "type": ent["type"],
            "attributes": memory.reconstruct_state(conn, ent["entity_id"],
                                                   episode_no - 1, work_id),
            "aliases": aliases,
        })
    return states


def _committed_rules(conn: sqlite3.Connection, episode_no: int,
                     work_id: int) -> list[dict]:
    out = []
    for r in memory.committed_rules(conn, work_id, episode_no - 1):
        out.append({
            "entity_id": r["entity_id"], "name": r["canonical_name"],
            "predicate": r["attribute_path"],
            "standard_value": memory._u(r["standard_value"]),
            "display": memory._u(r["new_value"]),
            "story_time": r["story_time"], "episode_no": r["episode_no"],
        })
    return out


def _resolve_ref(conn, ref: str,
                 work_id: int) -> tuple[str | None, str, bool]:
    """Return (entity_id, candidate_name, explicit_new).

    Unknown refs become new candidates. Name matching is scoped to the
    work so two works never merge.
    """
    if not ref:
        return None, "", False
    if ref.startswith("new:"):
        return None, ref[len("new:"):], True
    ent = memory.get_entity(conn, ref)
    if ent and (work_id is None or ent["work_id"] == work_id):
        return ref, "", False
    # Bare name: try canonical/alias match in this work, else new candidate.
    row = conn.execute("SELECT entity_id FROM entities"
                       " WHERE work_id=? AND canonical_name=?",
                       (work_id, ref)).fetchone()
    if row:
        return row["entity_id"], "", False
    row = conn.execute("SELECT entity_id FROM entity_aliases"
                       " WHERE work_id=? AND alias=? AND status='confirmed'",
                       (work_id, ref)).fetchone()
    if row and row["entity_id"]:
        return row["entity_id"], "", False
    return None, ref, False


def extract_changes(meta, conn: sqlite3.Connection, *, episode_no: int,
                    revision_id: int, manuscript: str, work_id: int,
                    base_memory_version: int = 0, model_version: str = "",
                    max_tokens: int | None = None,
                    author_instruction: str = "",
                    writing_locale: str | None = None,
                    market: str | None = None) -> dict:
    from . import meta_client as metac

    prompt, writing_locale, market = metac.feature_prompt(
        conn, work_id, "entity", writing_locale, market)
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    units = memory.ensure_units(conn, work_id, episode_no, revision_id,
                                manuscript)
    payload: dict = {"manuscript": manuscript,
                     "prev_states": _prev_states(conn, episode_no, work_id),
                     "committed_rules": _committed_rules(conn, episode_no,
                                                         work_id)}
    if writing_locale == "en-US":
        directive = "Extract change items from the following input."
        skel = " Skeleton: "
        if (author_instruction or "").strip():
            payload["author_instruction"] = author_instruction.strip()
            directive = ("Apply the author's revision request"
                         " (author_instruction) first, then re-extract change"
                         " items from the following input.")
    else:
        directive = "다음 입력에서 변경 항목을 추출하라."
        skel = " 스켈레톤: "
        if (author_instruction or "").strip():
            payload["author_instruction"] = author_instruction.strip()
            directive = ("작가의 수정 요청(author_instruction)을 최우선 반영해"
                         " 다음 입력에서 변경 항목을 다시 추출하라.")
    out = metac.chat_with_provenance(
        meta, bundle="entity", episode=episode_no, system=prompt.text,
        work_id=work_id,
        user=directive + skel
             + '{"items": [{"entity_ref": str, "candidate_name": str, '
             '"kind": str, "operation": str, '
             '"attribute_path": str, "old_value": any, "new_value": any, '
             '"standard_value": any, "story_time": str, '
             '"narrative_mode": str, "evidence_quote": str}], '
             '"mentions": [{"entity_ref": str, "quote": str}], '
             '"events": [{"summary": str, "story_time": str, '
             '"narrative_mode": str, "cause": str, "consequence": str, '
             '"evidence_quote": str, '
             '"participants": [{"entity_ref": str, "role": str}]}]}\n'
             + json.dumps(payload, ensure_ascii=False),
        schema=sch.EntityOutput, max_tokens=max_tokens,
        writing_locale=writing_locale, market=market,
        prompt_feature="entity", prompt_version=PROMPT_VERSION,
        preset_id=preset_id, preset_version=preset_version)

    items = []
    dropped = 0
    for it in out.get("items", []):
        if it.get("operation") not in memory.OPERATIONS:
            dropped += 1  # drop off-spec operations (e.g. "update")
            continue
        entity_id, candidate, explicit = _resolve_ref(
            conn, it.get("entity_ref", ""), work_id)
        if entity_id is None and not candidate:
            candidate = it.get("candidate_name") or it.get("entity_ref", "")
        check = citation.check_quote(manuscript, it.get("evidence_quote", ""))
        items.append({
            "entity_id": entity_id,
            "candidate_name": candidate or it.get("candidate_name", ""),
            "explicit_new": explicit,
            "kind": memory.norm_kind(it.get("kind", "attribute") or "attribute"),
            "operation": it["operation"],
            "attribute_path": it.get("attribute_path", ""),
            "old_value": it.get("old_value", ""),
            "new_value": it.get("new_value", ""),
            "standard_value": it.get("standard_value", ""),
            "story_time": it.get("story_time", ""),
            "narrative_mode": memory.norm_mode(
                it.get("narrative_mode", "narration") or "narration"),
            "unit_index": memory.unit_index_for_offset(units, check["start"]),
            "evidence_quote": it.get("evidence_quote", ""),
            "evidence_start": check["start"],
            "evidence_end": check["end"],
            "validation": "pending" if check["ok"] else "deferred",
        })
    mentions = []
    for m in out.get("mentions", []):
        entity_id, candidate, _exp = _resolve_ref(
            conn, m.get("entity_ref", ""), work_id)
        check = citation.check_quote(manuscript, m.get("quote", ""))
        if not check["ok"]:
            continue  # unverified mention quotes are dropped
        if entity_id is None:
            memory.add_alias_candidate(
                conn, entity_id=None, work_id=work_id,
                alias=candidate or m.get("entity_ref", ""),
                basis=f"EP{episode_no} mention", revision_id=revision_id,
                quote=m.get("quote", ""), start=check["start"],
                end=check["end"])
            continue  # mention rows require a resolved entity
        mentions.append({"entity_id": entity_id, "quote": m["quote"],
                         "unit_index": memory.unit_index_for_offset(
                             units, check["start"]),
                         "start_offset": check["start"],
                         "end_offset": check["end"]})
    events = []
    for ev in out.get("events", []):
        if not ev.get("summary"):
            continue
        parts = []
        for p in ev.get("participants", []):
            eid, cand, _exp = _resolve_ref(conn, p.get("entity_ref", ""),
                                           work_id)
            parts.append({"entity_id": eid, "candidate_name": cand,
                          "role": p.get("role", "")})
        check = citation.check_quote(manuscript, ev.get("evidence_quote", ""))
        events.append({"summary": ev.get("summary", ""),
                       "story_time": ev.get("story_time", ""),
                       "narrative_mode": memory.norm_mode(
                           ev.get("narrative_mode", "narration") or "narration"),
                       "cause": ev.get("cause", ""),
                       "consequence": ev.get("consequence", ""),
                       "evidence_quote": ev.get("evidence_quote", ""),
                       "evidence_start": check["start"],
                       "evidence_end": check["end"],
                       "unit_index": memory.unit_index_for_offset(
                           units, check["start"]),
                       "validation": "pending" if check["ok"] else "deferred",
                       "participants": parts})
    # A genuinely empty result is a successful empty run (§5.7).
    set_id = memory.store_proposal(
        conn, episode_no=episode_no, revision_id=revision_id, items=items,
        mentions=mentions, events=events, work_id=work_id,
        base_memory_version=base_memory_version, model_version=model_version,
        prompt_version=PROMPT_VERSION, units_total=len(units),
        unit_statuses=[{"status": "completed", "units": len(units)}])
    verified = sum(1 for i in items if i["validation"] == "pending")
    return {"proposal_set_id": set_id, "items": len(items), "verified": verified,
            "unverified": len(items) - verified, "mentions": len(mentions),
            "events": len(events), "dropped": dropped,
            "empty": not items and not events,
            "raw_items": len(out.get("items", []))}
