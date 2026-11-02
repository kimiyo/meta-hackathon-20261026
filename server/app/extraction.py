"""Entity extraction pipeline (§6.3): manuscript → proposal items + mentions.

Server post-processing: resolve entity_ref (existing ID or new:name),
register alias candidates for unresolvable refs, citation-check every
evidence_quote. Failed citations become deferred (근거 미확인).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import citation, memory
from .schemas import entity as sch

PROMPT = (Path(__file__).parent / "prompts" / "entity.txt").read_text(encoding="utf-8")


def _prev_states(conn: sqlite3.Connection, episode_no: int) -> list[dict]:
    states = []
    for ent in memory.search_entities(conn):
        aliases = [a["alias"] for a in conn.execute(
            "SELECT alias FROM entity_aliases WHERE entity_id=? AND status='confirmed'",
            (ent["entity_id"],))]
        states.append({
            "entity_id": ent["entity_id"], "name": ent["canonical_name"],
            "type": ent["type"],
            "attributes": memory.reconstruct_state(conn, ent["entity_id"],
                                                   episode_no - 1),
            "aliases": aliases,
        })
    return states


def _resolve_ref(conn, ref: str) -> tuple[str | None, str]:
    """Return (entity_id, candidate_name). Unknown refs become new candidates."""
    if not ref:
        return None, ""
    if ref.startswith("new:"):
        return None, ref[len("new:"):]
    if memory.get_entity(conn, ref):
        return ref, ""
    # Bare name: try canonical/alias match, else treat as new candidate.
    row = conn.execute("SELECT entity_id FROM entities WHERE canonical_name=?",
                       (ref,)).fetchone()
    if row:
        return row["entity_id"], ""
    row = conn.execute("SELECT entity_id FROM entity_aliases WHERE alias=?",
                       (ref,)).fetchone()
    if row and row["entity_id"]:
        return row["entity_id"], ""
    return None, ref


def extract_changes(meta, conn: sqlite3.Connection, *, episode_no: int,
                    revision_id: int, manuscript: str, work_id: int | None = None,
                    max_tokens: int = 8000,
                    author_instruction: str = "") -> dict:
    payload: dict = {"manuscript": manuscript,
                     "prev_states": _prev_states(conn, episode_no)}
    directive = "다음 입력에서 변경 항목을 추출하라."
    if (author_instruction or "").strip():
        payload["author_instruction"] = author_instruction.strip()
        directive = ("작가의 수정 요청(author_instruction)을 최우선 반영해"
                     " 다음 입력에서 변경 항목을 다시 추출하라.")
    out = meta.chat_json(
        bundle="entity", episode=episode_no, system=PROMPT, work_id=work_id,
        user=directive + " 스켈레톤: "
             '{"items": [{"entity_ref": str, "candidate_name": str, "operation": str, '
             '"attribute_path": str, "old_value": any, "new_value": any, '
             '"story_time": str, "evidence_quote": str}], '
             '"mentions": [{"entity_ref": str, "quote": str}], '
             '"events": [{"summary": str, "story_time": str, "cause": str, '
             '"consequence": str, '
             '"participants": [{"entity_ref": str, "role": str}]}]}\n'
             + json.dumps(payload, ensure_ascii=False),
        schema=sch.EntityOutput, max_tokens=max_tokens)

    items = []
    dropped = 0
    for it in out.get("items", []):
        if it.get("operation") not in memory.OPERATIONS:
            dropped += 1  # drop off-spec operations (e.g. "update")
            continue
        entity_id, candidate = _resolve_ref(conn, it.get("entity_ref", ""))
        if entity_id is None and not candidate:
            candidate = it.get("candidate_name") or it.get("entity_ref", "")
        check = citation.check_quote(manuscript, it.get("evidence_quote", ""))
        items.append({
            "entity_id": entity_id,
            "candidate_name": candidate or it.get("candidate_name", ""),
            "operation": it["operation"],
            "attribute_path": it.get("attribute_path", ""),
            "old_value": it.get("old_value", ""),
            "new_value": it.get("new_value", ""),
            "story_time": it.get("story_time", ""),
            "evidence_quote": it.get("evidence_quote", ""),
            "evidence_start": check["start"],
            "evidence_end": check["end"],
            "validation": "pending" if check["ok"] else "deferred",
        })
    mentions = []
    for m in out.get("mentions", []):
        entity_id, candidate = _resolve_ref(conn, m.get("entity_ref", ""))
        check = citation.check_quote(manuscript, m.get("quote", ""))
        if not check["ok"]:
            continue  # unverified mention quotes are dropped
        if entity_id is None:
            memory.add_alias_candidate(conn, entity_id=None,
                                       alias=candidate or m.get("entity_ref", ""),
                                       basis=f"EP{episode_no} mention")
            continue  # mention rows require a resolved entity
        mentions.append({"entity_id": entity_id, "quote": m["quote"],
                         "start_offset": check["start"],
                         "end_offset": check["end"]})
    events = []
    for ev in out.get("events", []):
        if not ev.get("summary"):
            continue
        parts = []
        for p in ev.get("participants", []):
            eid, cand = _resolve_ref(conn, p.get("entity_ref", ""))
            parts.append({"entity_id": eid, "candidate_name": cand,
                          "role": p.get("role", "")})
        events.append({"summary": ev.get("summary", ""),
                       "story_time": ev.get("story_time", ""),
                       "cause": ev.get("cause", ""),
                       "consequence": ev.get("consequence", ""),
                       "participants": parts})
    if not items and not events:
        raise ValueError("추출 결과가 비어 있습니다. 다시 실행해 주세요.")
    set_id = memory.store_proposal(conn, episode_no=episode_no,
                                   revision_id=revision_id, items=items,
                                   mentions=mentions, events=events)
    verified = sum(1 for i in items if i["validation"] == "pending")
    return {"proposal_set_id": set_id, "items": len(items), "verified": verified,
            "unverified": len(items) - verified, "mentions": len(mentions),
            "events": len(events), "dropped": dropped,
            "raw_items": len(out.get("items", []))}
