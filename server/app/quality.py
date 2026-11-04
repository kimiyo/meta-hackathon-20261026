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
                revision_id: int, manuscript: str, outline: dict, work_id: int,
                max_tokens: int | None = None,
                writing_locale: str | None = None,
                market: str | None = None) -> dict:
    from . import meta_client as metac

    prompt, writing_locale, market = metac.feature_prompt(
        conn, work_id, "quality", writing_locale, market)
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    if writing_locale == "en-US":
        directive = ("Evaluate and proofread the following manuscript."
                     " Skeleton: "
                     '{"scores": {"immersion": number, "tension": number, '
                     '"coherence": number}, "issues": [str], '
                     '"proofread_edits": [{"start_quote": str, '
                     '"replacement": str, "reason": str}]}\n')
    else:
        directive = ("다음 원고를 평가·교정하라. 스켈레톤: "
                     '{"scores": {"immersion": number, "tension": number, '
                     '"coherence": number}, "issues": [str], '
                     '"proofread_edits": [{"start_quote": str, '
                     '"replacement": str, "reason": str}]}\n')
    out = metac.chat_with_provenance(
        meta, bundle="quality", episode=episode_no, system=prompt.text,
        work_id=work_id,
        user=directive + json.dumps({"manuscript": manuscript,
                                     "episode_outline": outline},
                                    ensure_ascii=False),
        schema=sch.QualityOutput, max_tokens=max_tokens,
        writing_locale=writing_locale, market=market,
        prompt_feature="quality",
        preset_id=preset_id, preset_version=preset_version)
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
        label = ("AI proofread" if writing_locale == "en-US" else "AI 교정")
        new_revision = drafting.save_revision(conn, work_id, episode_no, text,
                                             label=label)
    gate_pass = scores.get("coherence", 0) >= COHERENCE_MIN
    return {"scores": scores, "issues": issues, "edits_applied": applied,
            "new_revision": new_revision, "gate_pass": gate_pass,
            "manuscript": text}


def build_quality_fix(issues: list[str], scores: dict,
                      writing_locale: str = "ko-KR") -> str:
    if writing_locale == "en-US":
        lines = [f"Quality gate missed"
                 f" (coherence={scores.get('coherence', 0)})."
                 " Rewrite the manuscript to resolve:"]
        lines += [f"- {i}" for i in issues] or ["- (no specifics;"
                                                " polish overall)"]
    else:
        lines = [f"품질 게이트 미달(coherence={scores.get('coherence', 0)}). "
                 "다음 문제를 해소하도록 원고를 고쳐 써라:"]
        lines += [f"- {i}" for i in issues] or ["- (구체 이슈 없음, 전체 다듬기)"]
    return "\n".join(lines)
