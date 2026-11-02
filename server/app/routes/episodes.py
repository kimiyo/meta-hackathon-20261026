"""Episode execution routes (§5.2)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import manuscripts, state_machine
from ..db import find_work, touch_work
from ..errors import LLMStepError
from .deps import (get_conn, get_current_author, require_design,
                   require_episode_job)
from .works import get_meta

router = APIRouter(prefix="/api/v1", tags=["episodes"])


class AdvanceIn(BaseModel):
    design_id: int | None = None


def _mark_writing(conn: sqlite3.Connection, episode_no: int,
                  design_id: int | None) -> None:
    """Flag the owning work as past design once writing starts."""
    if design_id is None:
        job = state_machine.get_job(conn, episode_no)
        design_id = job.get("design_id") if job else None
    if design_id is None:
        return
    row = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                       (design_id,)).fetchone()
    if row:
        touch_work(conn, row["work_id"], stage="writing")
        conn.commit()


@router.post("/episodes/{episode_no}/advance")
def advance_episode(episode_no: int, body: AdvanceIn,
                    conn: sqlite3.Connection = Depends(get_conn),
                    meta=Depends(get_meta),
                    author: dict = Depends(get_current_author)):
    job = state_machine.get_job(conn, episode_no)
    if job is None:
        if body.design_id is not None:
            require_design(conn, body.design_id, author["id"])
    else:
        wanted = (body.design_id if body.design_id is not None
                  else job["design_id"])
        d = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                         (job["design_id"],)).fetchone()
        w = find_work(conn, d["work_id"]) if d else None
        owned = bool(w and w.get("author_id") == author["id"])
        if wanted != job["design_id"] or not owned:
            raise HTTPException(
                409, f"episode {episode_no} is already being written"
                f" in design {job['design_id']}")
    try:
        out = state_machine.advance(conn, meta, episode_no,
                                    design_id=body.design_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    _mark_writing(conn, episode_no, body.design_id)
    return out


@router.post("/episodes/{episode_no}/revert")
def revert_episode(episode_no: int,
                   conn: sqlite3.Connection = Depends(get_conn),
                   author: dict = Depends(get_current_author)):
    job = require_episode_job(conn, episode_no, author["id"])
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    try:
        return state_machine.revert_to_draft(conn, episode_no)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.get("/episodes/{episode_no}")
def get_episode(episode_no: int,
                conn: sqlite3.Connection = Depends(get_conn),
                author: dict = Depends(get_current_author)):
    job = require_episode_job(conn, episode_no, author["id"])
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    revs = conn.execute(
        "SELECT revision_id, version, approval_status, LENGTH(text) AS length"
        " FROM manuscript_revisions WHERE episode_no=? ORDER BY version",
        (episode_no,)).fetchall()
    verification = None
    if job.get("proposal_set_id"):
        from .. import memory
        items = [i for i in memory.get_proposals(conn, episode_no)
                 if i["proposal_set_id"] == job["proposal_set_id"]]
        counts: dict[str, int] = {}
        for i in items:
            counts[i["validation"]] = counts.get(i["validation"], 0) + 1
        verification = {"proposal_set_id": job["proposal_set_id"],
                        "counts": counts, "items": len(items)}
    import json as _json
    quality = None
    if job.get("quality_json"):
        try:
            quality = _json.loads(job["quality_json"]) or None
        except ValueError:
            quality = None
    return {"episode_no": episode_no, **state_machine._public(job),
            "revisions": [dict(r) for r in revs],
            "verification": verification, "quality": quality}


class ChangeIn(BaseModel):
    instruction: str = ""
    full: bool = False
    base: str = "draft"


@router.post("/episodes/{episode_no}/request-change")
def request_change(episode_no: int, body: ChangeIn,
                   conn: sqlite3.Connection = Depends(get_conn),
                   meta=Depends(get_meta),
                   author: dict = Depends(get_current_author)):
    require_episode_job(conn, episode_no, author["id"])
    try:
        return state_machine.request_change(conn, meta, episode_no,
                                            body.instruction, full=body.full,
                                            base_source=body.base)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.get("/episodes/{episode_no}/draft-requests")
def list_draft_requests(episode_no: int,
                        conn: sqlite3.Connection = Depends(get_conn),
                        author: dict = Depends(get_current_author)):
    from .. import drafting

    job = require_episode_job(conn, episode_no, author["id"])
    if not job:
        return {"episode_no": episode_no, "requests": []}
    return {"episode_no": episode_no,
            "requests": drafting.list_draft_requests(conn, episode_no)}


@router.post("/episodes/{episode_no}/approve")
def approve_episode(episode_no: int,
                    conn: sqlite3.Connection = Depends(get_conn),
                    author: dict = Depends(get_current_author)):
    require_episode_job(conn, episode_no, author["id"])
    try:
        return state_machine.approve_episode(conn, episode_no)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.get("/designs/{design_id}/episodes")
def design_episodes(design_id: int,
                    conn: sqlite3.Connection = Depends(get_conn),
                    author: dict = Depends(get_current_author)):
    """Outlines joined with jobs (for the writing monitor)."""
    require_design(conn, design_id, author["id"])
    outlines = conn.execute(
        "SELECT episode_no, title FROM episode_outlines WHERE design_id=?"
        " ORDER BY episode_no", (design_id,)).fetchall()
    if not outlines:
        raise HTTPException(404, f"design {design_id} not found")
    jobs = {r["episode_no"]: dict(r) for r in conn.execute(
        "SELECT * FROM episode_jobs WHERE design_id=?", (design_id,))}
    return {"design_id": design_id, "episodes": [
        {"episode_no": o["episode_no"], "title": o["title"],
         "job": ({k: jobs[o["episode_no"]][k] for k in
                 ("state", "attempt", "manuscript_revision_id",
                  "proposal_set_id", "last_error")}
                 if o["episode_no"] in jobs else None)}
        for o in outlines]}


def _episode_title(conn: sqlite3.Connection, job: dict) -> str:
    row = conn.execute(
        "SELECT title FROM episode_outlines WHERE design_id=? AND episode_no=?",
        (job["design_id"], job["episode_no"])).fetchone()
    return row["title"] if row else ""


def _manuscript_error(action):
    try:
        return action()
    except manuscripts.ManuscriptLocked as e:
        raise HTTPException(423, str(e))
    except manuscripts.RevisionConflict as e:
        raise HTTPException(409, {"code": "REVISION_CONFLICT",
                                  "message": str(e),
                                  "latestRevision": e.latest})
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


class ManuscriptDraftIn(BaseModel):
    expectedRevision: int
    content: str = ""


class ManuscriptVersionIn(BaseModel):
    expectedRevision: int
    requestId: str
    content: str = ""
    label: str | None = None


class ManuscriptRestoreIn(BaseModel):
    expectedRevision: int
    requestId: str
    sourceVersionId: str


@router.get("/episodes/{episode_no}/manuscript-doc")
def get_manuscript_doc(episode_no: int,
                       conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    job = require_episode_job(conn, episode_no, author["id"])
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    job = dict(job)
    return {"document": manuscripts.get_doc(
        conn, episode_no, job, _episode_title(conn, job))}


@router.patch("/episodes/{episode_no}/manuscript-draft")
def patch_manuscript_draft(episode_no: int, body: ManuscriptDraftIn,
                           conn: sqlite3.Connection = Depends(get_conn),
                           author: dict = Depends(get_current_author)):
    job = require_episode_job(conn, episode_no, author["id"])
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    job = dict(job)
    doc = _manuscript_error(lambda: manuscripts.save_draft(
        conn, episode_no, job, _episode_title(conn, job),
        body.expectedRevision, body.content))
    return {"document": doc}


@router.post("/episodes/{episode_no}/manuscript-versions")
def post_manuscript_version(episode_no: int, body: ManuscriptVersionIn,
                            conn: sqlite3.Connection = Depends(get_conn),
                            author: dict = Depends(get_current_author)):
    job = require_episode_job(conn, episode_no, author["id"])
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    job = dict(job)
    doc = _manuscript_error(lambda: manuscripts.create_version(
        conn, episode_no, job, _episode_title(conn, job),
        body.expectedRevision, body.requestId, body.content, body.label))
    return {"document": doc}


@router.post("/episodes/{episode_no}/manuscript-restore")
def post_manuscript_restore(episode_no: int, body: ManuscriptRestoreIn,
                            conn: sqlite3.Connection = Depends(get_conn),
                            author: dict = Depends(get_current_author)):
    job = require_episode_job(conn, episode_no, author["id"])
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    job = dict(job)
    doc = _manuscript_error(lambda: manuscripts.restore_version(
        conn, episode_no, job, _episode_title(conn, job),
        body.expectedRevision, body.requestId, body.sourceVersionId))
    return {"document": doc}


@router.get("/episodes/{episode_no}/manuscript")
def get_manuscript(episode_no: int, version: int | None = None,
                   conn: sqlite3.Connection = Depends(get_conn),
                   author: dict = Depends(get_current_author)):
    require_episode_job(conn, episode_no, author["id"])
    if version is None:
        row = conn.execute(
            "SELECT * FROM manuscript_revisions WHERE episode_no=?"
            " ORDER BY version DESC LIMIT 1", (episode_no,)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM manuscript_revisions WHERE episode_no=? AND version=?",
            (episode_no, version)).fetchone()
    if not row:
        raise HTTPException(404, f"manuscript EP{episode_no} v{version} not found")
    return dict(row)
