"""Episode execution routes (§5.2)."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import state_machine
from ..errors import LLMStepError
from .deps import get_conn
from .works import get_meta

router = APIRouter(prefix="/api/v1", tags=["episodes"])


class AdvanceIn(BaseModel):
    design_id: int | None = None


@router.post("/episodes/{episode_no}/advance")
def advance_episode(episode_no: int, body: AdvanceIn,
                    conn: sqlite3.Connection = Depends(get_conn),
                    meta=Depends(get_meta)):
    try:
        return state_machine.advance(conn, meta, episode_no,
                                     design_id=body.design_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except NotImplementedError as e:
        raise HTTPException(501, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.get("/episodes/{episode_no}")
def get_episode(episode_no: int,
                conn: sqlite3.Connection = Depends(get_conn)):
    job = state_machine.get_job(conn, episode_no)
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
    return {"episode_no": episode_no, **state_machine._public(job),
            "revisions": [dict(r) for r in revs],
            "verification": verification}


@router.get("/episodes/{episode_no}/manuscript")
def get_manuscript(episode_no: int, version: int | None = None,
                   conn: sqlite3.Connection = Depends(get_conn)):
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
