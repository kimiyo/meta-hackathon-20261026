"""Consistency review routes: analyze, chat, issues."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import consistency
from ..errors import LLMStepError
from .deps import get_conn, get_current_author, require_episode_job
from .works import get_meta

router = APIRouter(prefix="/api/v1", tags=["consistency"])


def _job_or_404(conn, episode_no, author_id):
    job = require_episode_job(conn, episode_no, author_id)
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    return job


@router.post("/episodes/{episode_no}/consistency/analyze")
def analyze(episode_no: int,
            conn: sqlite3.Connection = Depends(get_conn),
            meta=Depends(get_meta),
            author: dict = Depends(get_current_author)):
    job = _job_or_404(conn, episode_no, author["id"])
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (job["design_id"],)).fetchone()
    try:
        return consistency.analyze_episode(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            work_id=design["work_id"] if design else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


class ChatIn(BaseModel):
    message: str = ""


@router.get("/episodes/{episode_no}/consistency/messages")
def messages(episode_no: int,
             conn: sqlite3.Connection = Depends(get_conn),
             author: dict = Depends(get_current_author)):
    _job_or_404(conn, episode_no, author["id"])
    return {"episode_no": episode_no,
            "messages": consistency.list_messages(conn, episode_no)}


@router.post("/episodes/{episode_no}/consistency/chat")
def chat(episode_no: int, body: ChatIn,
         conn: sqlite3.Connection = Depends(get_conn),
         meta=Depends(get_meta),
         author: dict = Depends(get_current_author)):
    job = _job_or_404(conn, episode_no, author["id"])
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (job["design_id"],)).fetchone()
    try:
        return consistency.chat(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            message=body.message,
            work_id=design["work_id"] if design else None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.get("/episodes/{episode_no}/consistency/issues")
def issues(episode_no: int, status: str | None = None,
           conn: sqlite3.Connection = Depends(get_conn),
           author: dict = Depends(get_current_author)):
    _job_or_404(conn, episode_no, author["id"])
    return {"episode_no": episode_no,
            "issues": consistency.list_issues(conn, episode_no, status)}


class IssueStatusIn(BaseModel):
    status: str = "open"


@router.patch("/consistency/issues/{issue_id}")
def issue_status(issue_id: int, body: IssueStatusIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    try:
        return consistency.set_issue_status(conn, issue_id, body.status)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))

