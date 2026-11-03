"""Episode execution routes: start → review → publish.

Episode identity is (work, episode_no): every /episodes/ route takes the
work as a ?work= query (uid or id).
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import consistency, manuscripts, publishing, state_machine
from .. import meta_client as metac
from ..db import find_work, touch_work
from ..errors import LLMStepError
from .deps import (get_conn, get_current_author, require_design,
                   require_episode_job, require_work)
from .works import get_meta

router = APIRouter(prefix="/api/v1", tags=["episodes"])


class StartIn(BaseModel):
    design_id: int


def _work_id(conn, work: str, author_id: int) -> int:
    return require_work(conn, work, author_id)["id"]


def _mark_writing(conn: sqlite3.Connection, episode_no: int,
                  design_id: int | None) -> None:
    """Flag the owning work as past design once writing starts."""
    if design_id is None:
        return
    row = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                       (design_id,)).fetchone()
    if row:
        touch_work(conn, row["work_id"], stage="writing")
        conn.commit()


def _owned_job(conn, work: str, episode_no, author_id):
    wid = _work_id(conn, work, author_id)
    job = require_episode_job(conn, wid, episode_no, author_id)
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    return job


@router.post("/episodes/{episode_no}/start")
def start_episode(episode_no: int, body: StartIn, work: str,
                  conn: sqlite3.Connection = Depends(get_conn),
                  meta=Depends(get_meta),
                  author: dict = Depends(get_current_author)):
    wid = _work_id(conn, work, author["id"])
    design = require_design(conn, body.design_id, author["id"])
    if design["work_id"] != wid:
        raise HTTPException(400, f"design {body.design_id} is not in this work")
    job = state_machine.get_job(conn, wid, episode_no)
    if job is not None:
        raise HTTPException(409, f"episode {episode_no} is already being"
                                 f" written in design {job['design_id']}")
    try:
        out = state_machine.start_writing(conn, meta, wid, episode_no,
                                          design_id=body.design_id)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    _mark_writing(conn, episode_no, body.design_id)
    return out


@router.post("/episodes/{episode_no}/redraft")
def redraft_episode(episode_no: int, work: str,
                    conn: sqlite3.Connection = Depends(get_conn),
                    meta=Depends(get_meta),
                    author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    try:
        return state_machine.redraft_episode(conn, meta, job["work_id"],
                                             episode_no)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


class ChatReviseIn(BaseModel):
    message: str = ""
    include_draft: bool = True


@router.post("/episodes/{episode_no}/manuscript-chat")
def manuscript_chat(episode_no: int, body: ChatReviseIn, work: str,
                    conn: sqlite3.Connection = Depends(get_conn),
                    meta=Depends(get_meta),
                    author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    try:
        return state_machine.chat_revise(
            conn, meta, job["work_id"], episode_no,
            message=body.message, include_draft=body.include_draft)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")


@router.post("/episodes/{episode_no}/submit-review")
def submit_review(episode_no: int, work: str,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    try:
        return state_machine.submit_for_review(conn, job["work_id"],
                                               episode_no)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


class BackIn(BaseModel):
    reason: str = ""


@router.post("/episodes/{episode_no}/back-to-writing")
def back_to_writing(episode_no: int, body: BackIn, work: str,
                    conn: sqlite3.Connection = Depends(get_conn),
                    author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    try:
        return state_machine.back_to_writing(conn, job["work_id"], episode_no,
                                             body.reason)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.post("/episodes/{episode_no}/request-publish")
def request_publish(episode_no: int, work: str,
                    conn: sqlite3.Connection = Depends(get_conn),
                    author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    try:
        return state_machine.request_publish(conn, job["work_id"], episode_no)
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))


@router.get("/episodes/{episode_no}")
def get_episode(episode_no: int, work: str,
                conn: sqlite3.Connection = Depends(get_conn),
                author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    wid = job["work_id"]
    revs = conn.execute(
        "SELECT revision_id, version, approval_status, LENGTH(text) AS length"
        " FROM manuscript_revisions WHERE work_id=? AND episode_no=?"
        " ORDER BY version",
        (wid, episode_no)).fetchall()
    issues = consistency.list_issues(conn, episode_no, None, wid)
    open_issues = sum(1 for i in issues if i["status"] == "open")
    return {"episode_no": episode_no, **state_machine._public(job),
            "revisions": [dict(r) for r in revs],
            "issues": {"open": open_issues, "total": len(issues)}}


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
                 ("state", "manuscript_revision_id",
                  "last_error", "needs_attention", "return_reason")}
                 if o["episode_no"] in jobs else None)}
        for o in outlines]}


@router.get("/designs/{design_id}/publishing")
def publishing_episodes(design_id: int,
                        conn: sqlite3.Connection = Depends(get_conn),
                        author: dict = Depends(get_current_author)):
    """Episodes in 발간중 (for the publishing tab)."""
    design = require_design(conn, design_id, author["id"])
    jobs = state_machine.jobs_by_state(conn, design_id, "PUBLISHING")
    out = []
    for j in jobs:
        issues = consistency.list_issues(conn, j["episode_no"], None,
                                         design["work_id"])
        out.append({"episode_no": j["episode_no"],
                    "title": j.get("title") or "",
                    "state": j["state"],
                    "manuscript_revision_id": j.get("manuscript_revision_id"),
                    "updated_at": j.get("updated_at", ""),
                    "published_date": j.get("published_date") or "",
                    "published_note": j.get("published_note") or "",
                    "published_platform": j.get("published_platform") or "",
                    "issues": {"open": sum(1 for i in issues
                                           if i["status"] == "open"),
                               "total": len(issues)}})
    return {"design_id": design_id, "episodes": out}


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
def get_manuscript_doc(episode_no: int, work: str,
                       conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    return {"document": manuscripts.get_doc(
        conn, job["work_id"], episode_no, job, _episode_title(conn, job))}


@router.patch("/episodes/{episode_no}/manuscript-draft")
def patch_manuscript_draft(episode_no: int, body: ManuscriptDraftIn,
                           work: str,
                           conn: sqlite3.Connection = Depends(get_conn),
                           author: dict = Depends(get_current_author)):
    job = dict(_owned_job(conn, work, episode_no, author["id"]))
    doc = _manuscript_error(lambda: manuscripts.save_draft(
        conn, job["work_id"], episode_no, job, _episode_title(conn, job),
        body.expectedRevision, body.content))
    return {"document": doc}


@router.post("/episodes/{episode_no}/manuscript-versions")
def post_manuscript_version(episode_no: int, body: ManuscriptVersionIn,
                            work: str,
                            conn: sqlite3.Connection = Depends(get_conn),
                            author: dict = Depends(get_current_author)):
    job = dict(_owned_job(conn, work, episode_no, author["id"]))
    doc = _manuscript_error(lambda: manuscripts.create_version(
        conn, job["work_id"], episode_no, job, _episode_title(conn, job),
        body.expectedRevision, body.requestId, body.content, body.label))
    return {"document": doc}


@router.post("/episodes/{episode_no}/manuscript-restore")
def post_manuscript_restore(episode_no: int, body: ManuscriptRestoreIn,
                            work: str,
                            conn: sqlite3.Connection = Depends(get_conn),
                            author: dict = Depends(get_current_author)):
    job = dict(_owned_job(conn, work, episode_no, author["id"]))
    doc = _manuscript_error(lambda: manuscripts.restore_version(
        conn, job["work_id"], episode_no, job, _episode_title(conn, job),
        body.expectedRevision, body.requestId, body.sourceVersionId))
    return {"document": doc}


@router.get("/episodes/{episode_no}/manuscript")
def get_manuscript(episode_no: int, work: str, version: int | None = None,
                   conn: sqlite3.Connection = Depends(get_conn),
                   author: dict = Depends(get_current_author)):
    job = _owned_job(conn, work, episode_no, author["id"])
    wid = job["work_id"]
    if version is None:
        row = conn.execute(
            "SELECT * FROM manuscript_revisions WHERE work_id=? AND episode_no=?"
            " ORDER BY version DESC LIMIT 1", (wid, episode_no)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM manuscript_revisions"
            " WHERE work_id=? AND episode_no=? AND version=?",
            (wid, episode_no, version)).fetchone()
    if not row:
        raise HTTPException(404, f"manuscript EP{episode_no} v{version} not found")
    return dict(row)


@router.get("/episodes/{episode_no}/approved-text")
def get_approved_text(episode_no: int, work: str,
                      conn: sqlite3.Connection = Depends(get_conn),
                      author: dict = Depends(get_current_author)):
    """발간중 회차의 승인 원고 원문 (읽기 전용)."""
    job = _owned_job(conn, work, episode_no, author["id"])
    text, rev_id = publishing.approved_text(conn, job["work_id"], episode_no)
    if not text.strip():
        loc = metac.work_locale(conn, job["work_id"])[1]
        if loc == "en-US":
            raise HTTPException(409, "no approved manuscript")
        raise HTTPException(409, "발간 승인된 원고가 없습니다")
    return {"text": text, "revision_id": rev_id}


class PublishRecordIn(BaseModel):
    published_date: str = ""
    note: str = ""


@router.put("/episodes/{episode_no}/publish-record")
def put_publish_record(episode_no: int, body: PublishRecordIn, work: str,
                       conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    """플랫폼 등록 기록 저장/삭제. 빈 날짜면 기록 삭제. 상태 불변."""
    job = _owned_job(conn, work, episode_no, author["id"])
    loc = metac.work_locale(conn, job["work_id"])[1]
    try:
        return publishing.set_publish_record(
            conn, job["work_id"], episode_no, body.published_date, body.note,
            loc)
    except ValueError as e:
        raise HTTPException(400, str(e))
