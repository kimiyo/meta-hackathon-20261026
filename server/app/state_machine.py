"""Episode state machine (§7). The program performs transitions;
the LLM never decides the next state.

MVP: PLAN → DRAFT → VERIFY → READY_FOR_REVIEW → AUTHOR_APPROVED → COMMITTED,
with VERIFY→DRAFT loops and NEEDS_AUTHOR escalation. Phase 4 wires PLAN→DRAFT;
later phases add the remaining transitions.
"""
from __future__ import annotations

import sqlite3

from . import drafting, extraction, memory, verification
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
        " manuscript_revision_id, proposal_set_id, last_error, fix_instruction,"
        " updated_at) VALUES(?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(episode_no) DO UPDATE SET state=excluded.state,"
        " attempt=excluded.attempt, manuscript_revision_id=excluded.manuscript_revision_id,"
        " proposal_set_id=excluded.proposal_set_id, last_error=excluded.last_error,"
        " fix_instruction=excluded.fix_instruction, updated_at=excluded.updated_at",
        (job["episode_no"], job["design_id"], job["state"], job["attempt"],
         job.get("manuscript_revision_id"), job.get("proposal_set_id"),
         job.get("last_error", ""), job.get("fix_instruction", ""), now_iso()))
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
    if state == "PLAN":
        return _run_draft(conn, meta, job, max_tokens)
    if state == "DRAFT":
        return _run_verify_batch(conn, meta, job, max_tokens)
    if state == "VERIFY":
        return _gate_verify(conn, job, retry_limit)
    if state in ("READY_FOR_REVIEW", "AUTHOR_APPROVED", "COMMITTED", "NEEDS_AUTHOR"):
        return {"episode_no": episode_no, **_public(job), "note": "no forward step"}
    raise ValueError(f"unknown state {state}")


def _run_draft(conn, meta, job: dict, max_tokens: int) -> dict:
    episode_no = job["episode_no"]
    try:
        design = conn.execute("SELECT * FROM story_designs WHERE id=?",
                              (job["design_id"],)).fetchone()
        rev = drafting.draft_episode(
            meta, conn, design_id=job["design_id"],
            work_id=design["work_id"], episode_no=episode_no,
            author_instruction=job.get("fix_instruction", "") or "",
            max_tokens=max_tokens)
    except LLMStepError as e:
        job["attempt"] += 1  # failed step stays in state, consumes retry
        job["last_error"] = f"{e.reason}: {e.detail}"[:300]
        _save_job(conn, job)
        raise
    job["state"] = "DRAFT"
    job["manuscript_revision_id"] = rev["revision_id"]
    job["last_error"] = ""
    job["fix_instruction"] = ""
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job), "revision": rev}


def _run_verify_batch(conn, meta, job: dict, max_tokens: int) -> dict:
    """DRAFT→VERIFY: extraction → proposals → verification in one batch."""
    episode_no = job["episode_no"]
    rev_row = conn.execute("SELECT * FROM manuscript_revisions WHERE revision_id=?",
                           (job["manuscript_revision_id"],)).fetchone()
    # Discard any stale pending set for this revision before regenerating.
    for ps in conn.execute(
            "SELECT proposal_set_id FROM change_proposals"
            " WHERE episode_no=? AND manuscript_revision_id=? AND status='pending'",
            (episode_no, job["manuscript_revision_id"])):
        memory.discard_proposal_set(conn, ps["proposal_set_id"])
    try:
        ext = extraction.extract_changes(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"], manuscript=rev_row["text"],
            max_tokens=max_tokens)
        outline = drafting.get_outline(conn, job["design_id"], episode_no)
        ver = verification.verify_proposals(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            proposal_set_id=ext["proposal_set_id"], manuscript=rev_row["text"],
            world_rules=outline.get("constraints", []), max_tokens=max_tokens)
    except LLMStepError as e:
        job["attempt"] += 1
        job["last_error"] = f"{e.reason}: {e.detail}"[:300]
        _save_job(conn, job)
        raise
    job["state"] = "VERIFY"
    job["proposal_set_id"] = ext["proposal_set_id"]
    job["last_error"] = ""
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job),
            "extraction": ext, "verification": ver}


def _gate_verify(conn, job: dict, retry_limit: int) -> dict:
    """VERIFY resting-state decision (no AI): error→DRAFT, else READY/NEEDS_AUTHOR.
    Phase 6 adds the quality gate to the pass branch."""
    episode_no = job["episode_no"]
    items = [i for i in memory.get_proposals(conn, episode_no)
             if i["proposal_set_id"] == job["proposal_set_id"]]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["validation"]] = counts.get(i["validation"], 0) + 1
    if counts.get("error", 0) > 0:
        memory.discard_proposal_set(conn, job["proposal_set_id"])
        job["fix_instruction"] = verification.build_fix_instruction(
            conn, episode_no, job["proposal_set_id"])
        job["proposal_set_id"] = None
        job["attempt"] += 1
        job["state"] = "DRAFT"
        job["last_error"] = f"verify errors: {counts.get('error', 0)}, redrafting"
        _save_job(conn, job)
        return {"episode_no": episode_no, **_public(job), "counts": counts}
    if counts.get("deferred", 0) > 0 or job["attempt"] >= retry_limit:
        job["state"] = "NEEDS_AUTHOR"
        job["last_error"] = ("deferred items need author review"
                             if counts.get("deferred", 0) > 0
                             else f"retry limit {retry_limit} reached")
        _save_job(conn, job)
        return {"episode_no": episode_no, **_public(job), "counts": counts}
    job["state"] = "READY_FOR_REVIEW"
    job["last_error"] = ""
    _save_job(conn, job)
    return {"episode_no": episode_no, **_public(job), "counts": counts}


def _public(job: dict) -> dict:
    return {"state": job["state"], "attempt": job["attempt"],
            "manuscript_revision_id": job.get("manuscript_revision_id"),
            "proposal_set_id": job.get("proposal_set_id"),
            "last_error": job.get("last_error", "")}
