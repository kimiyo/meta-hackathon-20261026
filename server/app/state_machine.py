"""Episode state machine: WRITING → IN_REVIEW → PUBLISHING.

집필중 → 검토중 → 발간중. No automatic gates: the author moves the
episode forward, sends it back for fixes, or requests publishing.
Errors (budget, LLM failures) are not states; they set needs_attention
with last_error so the author can see and retry.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import drafting, memory
from .db import now_iso
from .errors import BudgetExceeded, LLMStepError
from .schemas import drafting as dsch

CHAT_PROMPT = (Path(__file__).parent / "prompts" / "manuscript_chat.txt").read_text(encoding="utf-8")

STATES = ("WRITING", "IN_REVIEW", "PUBLISHING")


def get_job(conn: sqlite3.Connection, work_id: int,
            episode_no: int) -> dict | None:
    row = conn.execute("SELECT * FROM episode_jobs WHERE work_id=? AND episode_no=?",
                       (work_id, episode_no)).fetchone()
    return dict(row) if row else None


def _save_job(conn, job: dict) -> None:
    conn.execute(
        "INSERT INTO episode_jobs(work_id, episode_no, design_id, state, attempt,"
        " manuscript_revision_id, proposal_set_id, last_error, fix_instruction,"
        " quality_json, needs_attention, return_reason, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(work_id, episode_no) DO UPDATE SET state=excluded.state,"
        " attempt=excluded.attempt, manuscript_revision_id=excluded.manuscript_revision_id,"
        " proposal_set_id=excluded.proposal_set_id, last_error=excluded.last_error,"
        " fix_instruction=excluded.fix_instruction,"
        " quality_json=excluded.quality_json,"
        " needs_attention=excluded.needs_attention,"
        " return_reason=excluded.return_reason, updated_at=excluded.updated_at",
        (job["work_id"], job["episode_no"], job["design_id"], job["state"],
         job["attempt"],
         job.get("manuscript_revision_id"), job.get("proposal_set_id"),
         job.get("last_error", ""), job.get("fix_instruction", ""),
         job.get("quality_json", "{}"),
         job.get("needs_attention", 0), job.get("return_reason", ""),
         now_iso()))
    conn.commit()


def _fail(conn, job: dict, message: str) -> dict:
    job["needs_attention"] = 1
    job["last_error"] = message[:300]
    _save_job(conn, job)
    return {"episode_no": job["episode_no"], **_public(job)}


def start_writing(conn, meta, work_id: int, episode_no: int, *,
                design_id: int, max_tokens: int = 8000) -> dict:
    """미시작 → 집필중: AI 초안을 생성하고 집필을 시작한다."""
    job = get_job(conn, work_id, episode_no)
    if job is not None:
        raise ValueError(f"episode {episode_no} already started"
                         f" ({job['state']})")
    design = conn.execute("SELECT * FROM story_designs WHERE id=?",
                          (design_id,)).fetchone()
    if not design:
        raise KeyError(f"design {design_id} not found")
    if design["work_id"] != work_id:
        raise ValueError(f"design {design_id} is not in work {work_id}")
    job = {"work_id": work_id, "episode_no": episode_no, "design_id": design_id,
           "state": "WRITING", "attempt": 0, "manuscript_revision_id": None,
           "proposal_set_id": None, "last_error": "",
           "needs_attention": 0, "return_reason": ""}
    try:
        rev = drafting.draft_episode(
            meta, conn, design_id=design_id,
            work_id=design["work_id"], episode_no=episode_no,
            max_tokens=max_tokens)
    except BudgetExceeded as e:
        job["last_error"] = f"budget_exceeded: {e}"[:300]
        job["needs_attention"] = 1
        _save_job(conn, job)
        return {"episode_no": episode_no, **_public(job)}
    except LLMStepError as e:
        job["last_error"] = f"{e.reason}: {e.detail}"[:300]
        job["needs_attention"] = 1
        _save_job(conn, job)
        raise
    job["manuscript_revision_id"] = rev["revision_id"]
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job), "revision": rev}


def redraft_episode(conn, meta, work_id: int, episode_no: int,
                    max_tokens: int = 8000) -> dict:
    """집필중 원고 재생성: AI가 처음부터 다시 쓰고 새 버전으로 저장한다.

    버전으로 저장되지 않은 작업 초안이 있으면 "AI 재생성 전 초안"으로
    먼저 보관한다.
    """
    from . import manuscripts

    job = get_job(conn, work_id, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "WRITING":
        raise ValueError(f"redraft needs WRITING, got {job['state']}")
    title_row = conn.execute(
        "SELECT title FROM episode_outlines WHERE design_id=? AND episode_no=?",
        (job["design_id"], episode_no)).fetchone()
    title = title_row["title"] if title_row else ""
    doc_row = manuscripts.ensure_doc_row(conn, work_id, episode_no)
    latest = conn.execute(
        "SELECT text FROM manuscript_revisions WHERE work_id=? AND episode_no=?"
        " ORDER BY version DESC LIMIT 1", (work_id, episode_no)).fetchone()
    if doc_row["draft_content"].strip() and (
            not latest or doc_row["draft_content"] != latest["text"]):
        manuscripts.create_version(
            conn, work_id, episode_no, job, title, doc_row["revision"],
            f"redraft-keep-{episode_no}-{doc_row['revision']}",
            doc_row["draft_content"], "AI 재생성 전 초안")
    try:
        rev = drafting.draft_episode(
            meta, conn, design_id=job["design_id"], work_id=work_id,
            episode_no=episode_no, max_tokens=max_tokens,
            revision_label="AI 재생성")
    except BudgetExceeded as e:
        job["last_error"] = f"budget_exceeded: {e}"[:300]
        job["needs_attention"] = 1
        _save_job(conn, job)
        raise
    except LLMStepError as e:
        job["last_error"] = f"{e.reason}: {e.detail}"[:300]
        job["needs_attention"] = 1
        _save_job(conn, job)
        raise
    job["manuscript_revision_id"] = rev["revision_id"]
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job), "revision": rev}


def chat_revise(conn, meta, work_id: int, episode_no: int, *,
                message: str, include_draft: bool = True,
                max_tokens: int = 16000) -> dict:
    """집필중 채팅 수정: 지시대로 고친 새 버전을 저장한다(상태 유지)."""
    from . import manuscripts

    job = get_job(conn, work_id, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "WRITING":
        raise ValueError(f"chat revise needs WRITING, got {job['state']}")
    instruction = (message or "").strip()
    if not instruction:
        raise ValueError("수정 지시를 입력하세요")
    title_row = conn.execute(
        "SELECT title FROM episode_outlines WHERE design_id=? AND episode_no=?",
        (job["design_id"], episode_no)).fetchone()
    title = title_row["title"] if title_row else ""
    doc_row = manuscripts.ensure_doc_row(conn, work_id, episode_no)
    latest = conn.execute(
        "SELECT text FROM manuscript_revisions WHERE work_id=? AND episode_no=?"
        " ORDER BY version DESC LIMIT 1", (work_id, episode_no)).fetchone()
    draft = doc_row["draft_content"]
    latest_text = latest["text"] if latest else ""
    if include_draft and draft.strip():
        base = draft
    elif latest_text.strip():
        base = latest_text
    elif draft.strip():
        base = draft
    else:
        raise ValueError("수정할 원고가 없습니다")
    if base == draft and draft.strip() and draft != latest_text:
        manuscripts.create_version(
            conn, work_id, episode_no, job, title, doc_row["revision"],
            f"chat-keep-{episode_no}-{doc_row['revision']}",
            draft, "채팅 수정 전 초안")
    try:
        out = meta.chat_json(
            bundle="manuscript-chat", episode=episode_no,
            system=CHAT_PROMPT, work_id=work_id,
            user="작가 지시와 현재 원고다. 스켈레톤: "
                 '{"manuscript": str}\n'
                 + json.dumps({"episode_no": episode_no,
                               "instruction": instruction,
                               "manuscript": base}, ensure_ascii=False),
            schema=dsch.DraftOutput, max_tokens=max_tokens)
    except BudgetExceeded as e:
        job["last_error"] = f"budget_exceeded: {e}"[:300]
        job["needs_attention"] = 1
        _save_job(conn, job)
        raise
    except LLMStepError as e:
        job["last_error"] = f"{e.reason}: {e.detail}"[:300]
        job["needs_attention"] = 1
        _save_job(conn, job)
        raise
    revised = (out.get("manuscript") or "")
    if not revised.strip():
        raise LLMStepError("manuscript-chat", episode_no, "empty",
                           "AI가 빈 원고를 반환했습니다")
    rev = drafting.save_revision(conn, work_id, episode_no, revised,
                                 label="AI 채팅 수정")
    job["manuscript_revision_id"] = rev["revision_id"]
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job), "revision": rev}


def submit_for_review(conn, work_id: int, episode_no: int) -> dict:
    """집필중 → 검토중: 작가의 검토를 요청한다.

    작업 초안이 최신 버전과 다르면 제출 스냅샷을 먼저 남겨
    검토·추출·발간 대상 원고를 일치시킨다.
    """
    from . import manuscripts

    job = get_job(conn, work_id, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "WRITING":
        raise ValueError(f"submit needs WRITING, got {job['state']}")
    if not job.get("manuscript_revision_id"):
        raise ValueError("no manuscript to review yet")
    title_row = conn.execute(
        "SELECT title FROM episode_outlines WHERE design_id=? AND episode_no=?",
        (job["design_id"], episode_no)).fetchone()
    snapshot = manuscripts.snapshot_draft_for_submit(
        conn, work_id, episode_no, job, title_row["title"] if title_row else "")
    job = get_job(conn, work_id, episode_no)
    job["state"] = "IN_REVIEW"
    job["return_reason"] = ""
    job["needs_attention"] = 0
    job["last_error"] = ""
    _save_job(conn, job)
    out = {"episode_no": episode_no, **_public(job)}
    if snapshot is not None:
        out["snapshot"] = True
    return out


def back_to_writing(conn, work_id: int, episode_no: int,
                    reason: str = "") -> dict:
    """검토중 → 집필중: 수정이 필요해 집필 화면으로 되돌린다."""
    job = get_job(conn, work_id, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "IN_REVIEW":
        raise ValueError(f"back-to-writing needs IN_REVIEW, got {job['state']}")
    job["state"] = "WRITING"
    job["return_reason"] = (reason or "")[:500]
    job["needs_attention"] = 0
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job)}


def request_publish(conn, work_id: int, episode_no: int) -> dict:
    """검토중 → 발간중: 원고를 확정하고 기억을 잠근다.

    열린(open) 일관성 이슈가 남아 있거나, 현재 원고 기준의 저장된
    기억·신선한 추출이 없으면 차단된다. 저장은 검토 단계의 설정
    저장이 원칙이며, 미저장 신선 추출만 있으면 여기서 자동 확정한다.
    """
    from . import consistency, manuscripts

    job = get_job(conn, work_id, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "IN_REVIEW":
        raise ValueError(f"publish needs IN_REVIEW, got {job['state']}")
    rev_id = job["manuscript_revision_id"]
    open_issues = consistency.open_count(conn, episode_no, work_id)
    if open_issues:
        raise ValueError(f"{open_issues} open consistency issues remain")
    saved = memory.get_committed_set_for_revision(conn, episode_no, rev_id,
                                                  work_id)
    pset = None
    if saved is None:
        pset = memory.get_pending_set_for_revision(conn, episode_no, rev_id,
                                                   work_id)
        if pset is None:
            if memory.has_any_proposal(conn, episode_no, work_id):
                raise ValueError("원고가 바뀌어 설정 재추출이 필요합니다")
            raise ValueError("설정 추출을 먼저 실행하세요")
    try:
        if saved is not None:
            memory.approve_manuscript_revision(conn, rev_id)
            job["proposal_set_id"] = saved["proposal_set_id"]
            result = {"committed": True, "already_saved": True,
                      "proposal_set_id": saved["proposal_set_id"]}
        else:
            result = memory.save_episode_memory(
                conn, episode_no=episode_no, revision_id=rev_id,
                proposal_set_id=pset["proposal_set_id"], work_id=work_id)
            job["proposal_set_id"] = pset["proposal_set_id"]
    except Exception:
        job["needs_attention"] = 1
        job["last_error"] = "publish commit failed"
        _save_job(conn, job)
        raise
    memory.mark_stale_from(conn, episode_no, work_id)
    job["state"] = "PUBLISHING"
    job["last_error"] = ""
    job["needs_attention"] = 0
    _save_job(conn, job)
    manuscripts.snapshot_approval(conn, work_id, episode_no,
                                job["manuscript_revision_id"])
    return {"episode_no": episode_no, **_public(job), "commit": result}


def jobs_by_state(conn, design_id: int, state: str) -> list[dict]:
    if state not in STATES:
        raise ValueError(f"unknown state {state}")
    rows = conn.execute(
        "SELECT j.*, o.title FROM episode_jobs j"
        " LEFT JOIN episode_outlines o ON o.design_id=j.design_id"
        " AND o.episode_no=j.episode_no"
        " WHERE j.design_id=? AND j.state=? ORDER BY j.episode_no",
        (design_id, state)).fetchall()
    return [dict(r) for r in rows]


def _public(job: dict) -> dict:
    return {"state": job["state"], "attempt": job["attempt"],
            "manuscript_revision_id": job.get("manuscript_revision_id"),
            "proposal_set_id": job.get("proposal_set_id"),
            "last_error": job.get("last_error", ""),
            "needs_attention": job.get("needs_attention", 0),
            "return_reason": job.get("return_reason", "")}
