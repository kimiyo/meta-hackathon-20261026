"""Consistency review routes: analyze, chat, issues, analyses."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import consistency
from ..errors import LLMStepError
from .deps import (get_conn, get_current_author, require_episode_job,
                   require_work)
from .works import get_meta

router = APIRouter(prefix="/api/v1", tags=["consistency"])


def _owned_job(conn, work: str, episode_no, author_id):
    wid = require_work(conn, work, author_id)["id"]
    job = require_episode_job(conn, wid, episode_no, author_id)
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    return job


@router.post("/episodes/{episode_no}/consistency/analyze")
def analyze(episode_no: int, work: str,
            conn: sqlite3.Connection = Depends(get_conn),
            meta=Depends(get_meta),
            author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    try:
        return consistency.analyze_episode(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            work_id=job["work_id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.post("/episodes/{episode_no}/consistency/reset")
def reset_review(episode_no: int, work: str,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    """일관성 점검 초기화: 열린 이슈와 분석 기록을 지운다(기억은 유지)."""
    job = _owned_job(conn, work, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "일관성 점검 초기화는 검토중 상태에서만 가능합니다")
    return {"episode_no": episode_no,
            **consistency.reset_episode_review(conn, episode_no,
                                               job["work_id"])}


class ChatIn(BaseModel):
    message: str = ""


@router.get("/episodes/{episode_no}/consistency/messages")
def messages(episode_no: int, work: str,
             conn: sqlite3.Connection = Depends(get_conn),
             author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    return {"episode_no": episode_no,
            "messages": consistency.list_messages(conn, episode_no,
                                                  work_id=job["work_id"])}


@router.post("/episodes/{episode_no}/consistency/chat")
def chat(episode_no: int, body: ChatIn, work: str,
         conn: sqlite3.Connection = Depends(get_conn),
         meta=Depends(get_meta),
         author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    try:
        return consistency.chat(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            message=body.message, work_id=job["work_id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.get("/episodes/{episode_no}/consistency/issues")
def issues(episode_no: int, work: str, status: str | None = None,
           conn: sqlite3.Connection = Depends(get_conn),
           author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    return {"episode_no": episode_no,
            "issues": consistency.list_issues(conn, episode_no, status,
                                              job["work_id"])}


@router.get("/episodes/{episode_no}/consistency/analyses/latest")
def latest_analysis(episode_no: int, work: str,
                    conn: sqlite3.Connection = Depends(get_conn),
                    author: dict = Depends(get_current_author)):
    """최근 분석 완료 상태(오류 수와 별도, §7 분석 완료 표시)."""
    job = _owned_job(conn, work, episode_no, author["id"])
    return {"episode_no": episode_no,
            "analysis": consistency.latest_analysis(conn, episode_no,
                                                    job["work_id"])}


class IssueStatusIn(BaseModel):
    status: str = "open"


@router.patch("/consistency/issues/{issue_id}")
def issue_status(issue_id: int, body: IssueStatusIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    row = conn.execute("SELECT work_id FROM consistency_issues"
                       " WHERE issue_id=?", (issue_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"issue {issue_id} not found")
    require_work(conn, row["work_id"], author["id"])
    try:
        return consistency.set_issue_status(conn, issue_id, body.status)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
