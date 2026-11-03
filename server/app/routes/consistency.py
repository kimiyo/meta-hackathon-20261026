"""Consistency review routes: analyze, chat, issues, analyses."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import consistency
from .. import meta_client as metac
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


def _locale(conn: sqlite3.Connection,
            work_id: int | None) -> tuple[str, str]:
    """(writing_locale, market) for a work; ko-KR/KR fallback."""
    market, locale = metac.work_locale(conn, work_id)
    return locale, market


@router.post("/episodes/{episode_no}/consistency/analyze")
def analyze(episode_no: int, work: str,
            conn: sqlite3.Connection = Depends(get_conn),
            meta=Depends(get_meta),
            author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    writing_locale, market = _locale(conn, job["work_id"])
    try:
        return consistency.analyze_episode(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            work_id=job["work_id"],
            writing_locale=writing_locale, market=market)
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.post("/episodes/{episode_no}/consistency/reset")
def reset_review(episode_no: int, work: str,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    """일관성 점검 초기화: 이슈 전체와 분석 기록을 지운다(기억은 유지)."""
    job = _owned_job(conn, work, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        if _locale(conn, job["work_id"])[0] == "en-US":
            raise HTTPException(
                400, "review reset is only allowed while in review")
        raise HTTPException(400, "일관성 점검 초기화는 검토중 상태에서만 가능합니다")
    return {"episode_no": episode_no,
            **consistency.reset_episode_review(conn, episode_no,
                                               job["work_id"])}


class FixIn(BaseModel):
    issue_ids: list[int] = []


@router.post("/episodes/{episode_no}/manuscript-fix")
def manuscript_fix(episode_no: int, body: FixIn, work: str,
                   conn: sqlite3.Connection = Depends(get_conn),
                   meta=Depends(get_meta),
                   author: dict = Depends(get_current_author)):
    """선택된 열린 이슈를 AI가 고친 새 원고 버전으로 저장하고 집필중으로 되돌린다."""
    job = _owned_job(conn, work, episode_no, author["id"])
    writing_locale, market = _locale(conn, job["work_id"])
    if job["state"] != "IN_REVIEW":
        if writing_locale == "en-US":
            raise HTTPException(
                400, "manuscript fixes are only allowed while in review")
        raise HTTPException(400, "원고 수정은 검토중 상태에서만 가능합니다")
    try:
        return consistency.fix_manuscript_issues(
            meta, conn, episode_no=episode_no, work_id=job["work_id"],
            issue_ids=body.issue_ids,
            writing_locale=writing_locale, market=market)
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


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
    writing_locale, market = _locale(conn, job["work_id"])
    try:
        return consistency.chat(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            message=body.message, work_id=job["work_id"],
            writing_locale=writing_locale, market=market)
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
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
