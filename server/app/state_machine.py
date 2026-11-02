"""Episode state machine: WRITING → IN_REVIEW → PUBLISHING.

집필중 → 검토중 → 발간중. No automatic gates: the author moves the
episode forward, sends it back for fixes, or requests publishing.
Errors (budget, LLM failures) are not states; they set needs_attention
with last_error so the author can see and retry.
"""
from __future__ import annotations

import sqlite3

from . import drafting, memory
from .db import now_iso
from .errors import BudgetExceeded, LLMStepError

STATES = ("WRITING", "IN_REVIEW", "PUBLISHING")


def get_job(conn: sqlite3.Connection, episode_no: int) -> dict | None:
    row = conn.execute("SELECT * FROM episode_jobs WHERE episode_no=?",
                       (episode_no,)).fetchone()
    return dict(row) if row else None


def _save_job(conn, job: dict) -> None:
    conn.execute(
        "INSERT INTO episode_jobs(episode_no, design_id, state, attempt,"
        " manuscript_revision_id, proposal_set_id, last_error, fix_instruction,"
        " quality_json, needs_attention, return_reason, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(episode_no) DO UPDATE SET state=excluded.state,"
        " attempt=excluded.attempt, manuscript_revision_id=excluded.manuscript_revision_id,"
        " proposal_set_id=excluded.proposal_set_id, last_error=excluded.last_error,"
        " fix_instruction=excluded.fix_instruction,"
        " quality_json=excluded.quality_json,"
        " needs_attention=excluded.needs_attention,"
        " return_reason=excluded.return_reason, updated_at=excluded.updated_at",
        (job["episode_no"], job["design_id"], job["state"], job["attempt"],
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


def start_writing(conn, meta, episode_no: int, *, design_id: int,
                max_tokens: int = 8000) -> dict:
    """미시작 → 집필중: AI 초안을 생성하고 집필을 시작한다."""
    job = get_job(conn, episode_no)
    if job is not None:
        raise ValueError(f"episode {episode_no} already started"
                         f" ({job['state']})")
    design = conn.execute("SELECT * FROM story_designs WHERE id=?",
                          (design_id,)).fetchone()
    if not design:
        raise KeyError(f"design {design_id} not found")
    job = {"episode_no": episode_no, "design_id": design_id,
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


def submit_for_review(conn, episode_no: int) -> dict:
    """집필중 → 검토중: 작가의 검토를 요청한다."""
    job = get_job(conn, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "WRITING":
        raise ValueError(f"submit needs WRITING, got {job['state']}")
    if not job.get("manuscript_revision_id"):
        raise ValueError("no manuscript to review yet")
    job["state"] = "IN_REVIEW"
    job["return_reason"] = ""
    job["needs_attention"] = 0
    job["last_error"] = ""
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job)}


def back_to_writing(conn, episode_no: int, reason: str = "") -> dict:
    """검토중 → 집필중: 수정이 필요해 집필 화면으로 되돌린다."""
    job = get_job(conn, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "IN_REVIEW":
        raise ValueError(f"back-to-writing needs IN_REVIEW, got {job['state']}")
    job["state"] = "WRITING"
    job["return_reason"] = (reason or "")[:500]
    job["needs_attention"] = 0
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job)}


def request_publish(conn, episode_no: int) -> dict:
    """검토중 → 발간중: 원고를 확정하고 기억을 반영한다.

    열린(open) 일관성 이슈가 남아 있으면 차단된다.
    """
    from . import consistency, manuscripts

    job = get_job(conn, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "IN_REVIEW":
        raise ValueError(f"publish needs IN_REVIEW, got {job['state']}")
    open_issues = consistency.open_count(conn, episode_no)
    if open_issues:
        raise ValueError(f"{open_issues} open consistency issues remain")
    try:
        if job.get("proposal_set_id"):
            result = memory.commit_episode(
                conn, episode_no=episode_no,
                revision_id=job["manuscript_revision_id"],
                proposal_set_id=job["proposal_set_id"])
        else:
            result = memory.commit_manuscript_only(
                conn, episode_no=episode_no,
                revision_id=job["manuscript_revision_id"])
    except Exception:
        job["needs_attention"] = 1
        job["last_error"] = "publish commit failed"
        _save_job(conn, job)
        raise
    memory.mark_stale_from(conn, episode_no)
    job["state"] = "PUBLISHING"
    job["last_error"] = ""
    job["needs_attention"] = 0
    _save_job(conn, job)
    manuscripts.snapshot_approval(conn, episode_no,
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
