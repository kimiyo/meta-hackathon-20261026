"""Episode state machine (§7). The program performs transitions;
the LLM never decides the next state.

MVP: PLAN → DRAFT → VERIFY → READY_FOR_REVIEW → AUTHOR_APPROVED → COMMITTED,
with VERIFY→DRAFT loops and NEEDS_AUTHOR escalation. Phase 4 wires PLAN→DRAFT;
later phases add the remaining transitions.
"""
from __future__ import annotations

import sqlite3

from . import drafting
from .db import now_iso
from .errors import LLMStepError

STATES = ("PLAN", "DRAFT", "VERIFY", "READY_FOR_REVIEW", "AUTHOR_APPROVED",
          "COMMITTED", "NEEDS_AUTHOR")


def get_job(conn: sqlite3.Connection, episode_no: int) -> dict | None:
    row = conn.execute("SELECT * FROM episode_jobs WHERE episode_no=?",
                       (episode_no,)).fetchone()
    return dict(row) if row else None


def _save_job(conn, job: dict) -> None:
    conn.execute(
        "INSERT INTO episode_jobs(episode_no, design_id, state, attempt,"
        " manuscript_revision_id, proposal_set_id, last_error, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)"
        " ON CONFLICT(episode_no) DO UPDATE SET state=excluded.state,"
        " attempt=excluded.attempt, manuscript_revision_id=excluded.manuscript_revision_id,"
        " proposal_set_id=excluded.proposal_set_id, last_error=excluded.last_error,"
        " updated_at=excluded.updated_at",
        (job["episode_no"], job["design_id"], job["state"], job["attempt"],
         job.get("manuscript_revision_id"), job.get("proposal_set_id"),
         job.get("last_error", ""), now_iso()))
    conn.commit()


def advance(conn, meta, episode_no: int, *, design_id: int | None = None,
            max_tokens: int = 8000, retry_limit: int = 3) -> dict:
    """Move one step forward. Not idempotent: repeats consume retry budget."""
    job = get_job(conn, episode_no)
    if job is None:
        if design_id is None:
            raise ValueError("design_id required on first advance")
        design = conn.execute("SELECT * FROM story_designs WHERE id=?",
                              (design_id,)).fetchone()
        if not design:
            raise KeyError(f"design {design_id} not found")
        if design["status"] != "approved":
            raise LLMStepError("drafting", episode_no, "design_not_approved",
                               f"design {design_id} status={design['status']}")
        job = {"episode_no": episode_no, "design_id": design_id, "state": "PLAN",
               "attempt": 0, "manuscript_revision_id": None,
               "proposal_set_id": None, "last_error": ""}
    if job["attempt"] >= retry_limit and job["state"] not in ("COMMITTED",):
        job["state"] = "NEEDS_AUTHOR"
        job["last_error"] = f"retry limit {retry_limit} reached"
        _save_job(conn, job)
        return {"episode_no": episode_no, **_public(job)}

    state = job["state"]
    if state in ("PLAN", "DRAFT"):
        # (Re)run drafting; a repeat call consumes one attempt.
        if state == "DRAFT":
            job["attempt"] += 1
        try:
            design = conn.execute("SELECT * FROM story_designs WHERE id=?",
                                  (job["design_id"],)).fetchone()
            rev = drafting.draft_episode(
                meta, conn, design_id=job["design_id"],
                work_id=design["work_id"], episode_no=episode_no,
                max_tokens=max_tokens)
        except LLMStepError as e:
            job["attempt"] += 1
            job["last_error"] = f"{e.reason}: {e.detail}"[:300]
            _save_job(conn, job)
            raise
        job["state"] = "DRAFT"
        job["manuscript_revision_id"] = rev["revision_id"]
        job["last_error"] = ""
        _save_job(conn, job)
        return {"episode_no": episode_no, **_public(job), "revision": rev}
    if state == "VERIFY":
        raise NotImplementedError("VERIFY transition lands in Phase 5")
    if state in ("READY_FOR_REVIEW", "AUTHOR_APPROVED", "COMMITTED", "NEEDS_AUTHOR"):
        return {"episode_no": episode_no, **_public(job), "note": "no forward step"}
    raise ValueError(f"unknown state {state}")


def _public(job: dict) -> dict:
    return {"state": job["state"], "attempt": job["attempt"],
            "manuscript_revision_id": job.get("manuscript_revision_id"),
            "proposal_set_id": job.get("proposal_set_id"),
            "last_error": job.get("last_error", "")}
