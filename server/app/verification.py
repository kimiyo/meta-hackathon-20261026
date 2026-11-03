"""Continuity verification pipeline (§6.4): proposal items → verdicts.

Verdicts: ok | error | change | deferred. Counter-evidence quotes are
citation-checked against approved manuscripts; an error resting on
unverified counter-evidence is downgraded to deferred (never used as
conflict evidence).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import citation, memory
from .schemas import verify as sch

PROMPT = (Path(__file__).parent / "prompts" / "verify.txt").read_text(encoding="utf-8")
VERDICTS = ("ok", "error", "change", "deferred")


def _approved_corpus(conn: sqlite3.Connection, episode_no: int,
                   work_id: int) -> str:
    rows = conn.execute(
        "SELECT text FROM manuscript_revisions WHERE work_id=? AND episode_no<=?"
        " AND approval_status='approved' ORDER BY episode_no, version",
        (work_id, episode_no)).fetchall()
    return "\n".join(r["text"] for r in rows)


def _excerpt(text: str, start: int | None, end: int | None, window: int = 200) -> str:
    if start is None or end is None:
        return text[: window * 2]
    return text[max(0, start - window): end + window]


def verify_proposals(meta, conn: sqlite3.Connection, *, episode_no: int,
                     revision_id: int, proposal_set_id: int, manuscript: str,
                     world_rules: list[str] | None = None, work_id: int | None = None,
                     max_tokens: int = 8000,
                     writing_locale: str | None = None,
                     market: str | None = None) -> dict:
    items = [i for i in memory.get_proposals(conn, episode_no, work_id)
             if i["proposal_set_id"] == proposal_set_id]
    # Skip citation-failed items: they stay deferred, never judged.
    judgeable = [i for i in items if i["validation"] != "deferred"]
    if not judgeable:
        return {"verdicts": {}, "counts": {"deferred": len(items)}}

    approved_states = []
    for ent in memory.search_entities(conn, work_id=work_id):
        ev_rows = conn.execute(
            "SELECT attribute_path, evidence_quote FROM entity_changes"
            " WHERE entity_id=? AND episode_no<? AND evidence_quote<>''"
            " ORDER BY episode_no DESC, change_id DESC",
            (ent["entity_id"], episode_no)).fetchall()
        evidence = {}
        for r in ev_rows:
            evidence.setdefault(r["attribute_path"], r["evidence_quote"])
        approved_states.append({
            "entity_id": ent["entity_id"], "name": ent["canonical_name"],
            "attributes": memory.reconstruct_state(conn, ent["entity_id"],
                                                   episode_no - 1, work_id),
            "evidence": evidence})
    payload_items = [{
        "item_id": i["item_id"], "entity_id": i["entity_id"],
        "candidate_name": i["candidate_name"], "kind": i["kind"],
        "operation": i["operation"], "attribute_path": i["attribute_path"],
        "old_value": i["old_value"], "new_value": i["new_value"],
        "standard_value": i.get("standard_value", ""),
        "story_time": i["story_time"],
        "narrative_mode": i.get("narrative_mode", "narration"),
        "evidence_quote": i["evidence_quote"],
    } for i in judgeable]
    from . import meta_client as metac

    prompt, writing_locale, market = metac.feature_prompt(
        conn, work_id, "verify", writing_locale, market)
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    if writing_locale == "en-US":
        directive = ("Judge the following change proposals. Skeleton: "
                     '{"verdicts": [{"item_id": int, "verdict": str, '
                     '"reason": str, "counter_evidence_quote": str}]}\n')
    else:
        directive = ("다음 변경안을 판정하라. 스켈레톤: "
                     '{"verdicts": [{"item_id": int, "verdict": str, '
                     '"reason": str, "counter_evidence_quote": str}]}\n')
    out = metac.chat_with_provenance(
        meta, bundle="verify", episode=episode_no, system=prompt.text,
        work_id=work_id,
        user=directive + json.dumps({
            "proposal_items": payload_items,
            "approved_states": approved_states,
            "world_rules": world_rules or [],
            "manuscript_excerpts": [
                {"item_id": i["item_id"],
                 "excerpt": _excerpt(manuscript, i["evidence_start"],
                                     i["evidence_end"])}
                for i in judgeable],
        }, ensure_ascii=False),
        schema=sch.VerifyOutput, max_tokens=max_tokens,
        writing_locale=writing_locale, market=market,
        prompt_feature="verify",
        preset_id=preset_id, preset_version=preset_version)

    corpus = _approved_corpus(conn, episode_no, work_id or 0)
    counts: dict[str, int] = {}
    applied = {}
    by_id = {i["item_id"]: i for i in judgeable}
    for v in out.get("verdicts", []):
        if v.get("item_id") not in by_id:
            continue
        verdict = v.get("verdict", "")
        if verdict not in VERDICTS:
            verdict = "deferred"
        counter = v.get("counter_evidence_quote", "")
        if verdict == "error" and counter:
            if not citation.check_quote(corpus, counter)["ok"]:
                verdict = "deferred"  # unverified counter-evidence: no conflict use
        memory.set_item_validation(conn, v["item_id"], verdict,
                                   v.get("reason", ""), counter)
        counts[verdict] = counts.get(verdict, 0) + 1
        applied[v["item_id"]] = {"verdict": verdict,
                                 "reason": v.get("reason", "")}
    # Items the model skipped stay pending → treat as deferred for gating.
    for i in judgeable:
        if i["item_id"] not in applied:
            memory.set_item_validation(conn, i["item_id"], "deferred")
            counts["deferred"] = counts.get("deferred", 0) + 1
    skipped = len(items) - len(judgeable)
    if skipped:
        counts["deferred"] = counts.get("deferred", 0) + skipped
    return {"verdicts": applied, "counts": counts}


def build_fix_instruction(conn: sqlite3.Connection, episode_no: int,
                          proposal_set_id: int,
                          writing_locale: str = "ko-KR") -> str:
    """Correction instruction for the VERIFY→DRAFT loop (error items only)."""
    items = [i for i in memory.get_proposals(conn, episode_no)
             if i["proposal_set_id"] == proposal_set_id
             and i["validation"] == "error"]
    if writing_locale == "en-US":
        lines = ["Rewrite the manuscript to resolve the following"
                 " contradictions. Approved existing canon takes precedence:"]
        tag = "Problem sentence"
    else:
        lines = ["다음 모순을 해소하도록 원고를 고쳐 써라. 승인된 기존 설정을 우선하라:"]
        tag = "문제 문장"
    for i in items:
        lines.append(
            f"- [{i['entity_id'] or i['candidate_name']} {i['attribute_path']}] "
            f"{tag}: {i['evidence_quote']}")
    return "\n".join(lines)
