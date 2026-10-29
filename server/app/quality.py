"""Quality gate (§6.5): scores + issues + proofread edits.

proofread_edits apply only when start_quote exists verbatim; application
saves a new revision. Gate: coherence >= COHERENCE_MIN and zero verify
errors (the error half is enforced in the VERIFY gating decision).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import citation, drafting
from .schemas import quality as sch

PROMPT = (Path(__file__).parent / "prompts" / "quality.txt").read_text(encoding="utf-8")
COHERENCE_MIN = 0.6


def run_quality(meta, conn: sqlite3.Connection, *, episode_no: int,
                revision_id: int, manuscript: str, outline: dict, work_id: int | None = None,
                max_tokens: int = 8000) -> dict:
    out = meta.chat_json(
        bundle="quality", episode=episode_no, system=PROMPT, work_id=work_id,
        user="다음 원고를 평가·교정하라. 스켈레톤: "
             '{"scores": {"immersion": number, "tension": number, '
             '"coherence": number}, "issues": [str], '
             '"proofread_edits": [{"start_quote": str, "replacement": str, '
             '"reason": str}]}\n'
             + json.dumps({"manuscript": manuscript,
                           "episode_outline": outline}, ensure_ascii=False),
        schema=sch.QualityOutput, max_tokens=max_tokens)
    scores = out.get("scores", {})
    issues = out.get("issues", [])
    text = manuscript
    applied = 0
    for e in out.get("proofread_edits", []):
        q = e.get("start_quote", "")
        if not q:
            continue
        check = citation.check_quote(text, q)
        if not check["ok"]:
            continue  # start_quote must exist verbatim
        text = text[:check["start"]] + e.get("replacement", "") + text[check["end"]:]
        applied += 1
    new_revision = None
    if applied:
        new_revision = drafting.save_revision(conn, episode_no, text)
    gate_pass = scores.get("coherence", 0) >= COHERENCE_MIN
    return {"scores": scores, "issues": issues, "edits_applied": applied,
            "new_revision": new_revision, "gate_pass": gate_pass,
            "manuscript": text}


def build_quality_fix(issues: list[str], scores: dict) -> str:
    lines = [f"품질 게이트 미달(coherence={scores.get('coherence', 0)}). "
             "다음 문제를 해소하도록 원고를 고쳐 써라:"]
    lines += [f"- {i}" for i in issues] or ["- (구체 이슈 없음, 전체 다듬기)"]
    return "\n".join(lines)
