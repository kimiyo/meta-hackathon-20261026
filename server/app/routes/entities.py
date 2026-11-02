"""Memory query & commit routes (§5.3). Reads use committed rows only."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import extraction, memory
from ..errors import LLMStepError
from .deps import get_conn, get_current_author, require_episode_job
from .works import get_meta

router = APIRouter(prefix="/api/v1", tags=["memory"])


@router.get("/entities")
def list_entities(type: str | None = None, q: str | None = None,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    return {"entities": memory.search_entities(conn, type, q)}


@router.get("/entities/{entity_id}/state")
def entity_state(entity_id: str, as_of_episode: int,
                 conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    ent = memory.get_entity(conn, entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    return {"entity_id": entity_id, "as_of_episode": as_of_episode,
            "attributes": memory.reconstruct_state(conn, entity_id, as_of_episode)}


@router.get("/entities/{entity_id}/history")
def entity_history(entity_id: str, from_episode: int | None = None,
                   to_episode: int | None = None,
                   conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    if not memory.get_entity(conn, entity_id):
        raise HTTPException(404, f"entity {entity_id} not found")
    return {"entity_id": entity_id,
            "history": memory.entity_history(conn, entity_id, from_episode, to_episode)}


def _job_or_404(conn, episode_no, author_id):
    job = require_episode_job(conn, episode_no, author_id)
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    return job


def _revision_text(conn, revision_id):
    row = conn.execute("SELECT text FROM manuscript_revisions"
                       " WHERE revision_id=?", (revision_id,)).fetchone()
    return row["text"] if row else ""


class ExtractIn(BaseModel):
    instruction: str = ""


@router.post("/episodes/{episode_no}/entities/extract")
def extract_entities(episode_no: int, body: ExtractIn | None = None,
                     conn: sqlite3.Connection = Depends(get_conn),
                     meta=Depends(get_meta),
                     author: dict = Depends(get_current_author)):
    """프리셋 작업: 현재 원고에서 Entity 변경안을 추출해 임시 저장한다.

    instruction이 있으면 수정 요청을 반영해 다시 추출한다.
    """
    job = _job_or_404(conn, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "entity extraction needs IN_REVIEW")
    if not job.get("manuscript_revision_id"):
        raise HTTPException(400, "no manuscript to extract from")
    manuscript = _revision_text(conn, job["manuscript_revision_id"])
    if not manuscript.strip():
        raise HTTPException(400, "manuscript is empty")
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (job["design_id"],)).fetchone()
    memory.discard_pending_for_revision(conn, episode_no,
                                        job["manuscript_revision_id"])
    try:
        out = extraction.extract_changes(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            manuscript=manuscript,
            work_id=design["work_id"] if design else None,
            author_instruction=(body.instruction if body else ""))
    except ValueError as e:
        raise HTTPException(502, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    items = [i for i in memory.get_proposals(conn, episode_no)
             if i["proposal_set_id"] == out["proposal_set_id"]]
    return {"episode_no": episode_no, **out, "items": items}


@router.post("/episodes/{episode_no}/entities/save")
def save_entities(episode_no: int,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    """검토 확정: 신선한 임시 변경안을 공식 기억으로 저장한다.

    저장된 기억은 설정 화면에서 바로 조회된다. 같은 원고에 이미
    저장했으면 중복 저장 없이 성공한다.
    """
    job = _job_or_404(conn, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "entity save needs IN_REVIEW")
    rev_id = job.get("manuscript_revision_id")
    pset = memory.get_pending_set_for_revision(conn, episode_no, rev_id)
    if pset is None:
        saved = memory.get_committed_set_for_revision(conn, episode_no, rev_id)
        if saved is not None:
            return {"episode_no": episode_no, "saved": True,
                    "already": True,
                    "proposal_set_id": saved["proposal_set_id"]}
        if memory.has_any_proposal(conn, episode_no):
            raise HTTPException(400, "원고가 바뀌어 Entity 재추출이 필요합니다")
        raise HTTPException(400, "Entity 추출을 먼저 실행하세요")
    result = memory.save_episode_memory(
        conn, episode_no=episode_no, revision_id=rev_id,
        proposal_set_id=pset["proposal_set_id"])
    return {"episode_no": episode_no, "saved": True, "commit": result,
            "proposal_set_id": pset["proposal_set_id"]}


@router.delete("/entity-items/{item_id}")
def delete_entity_item(item_id: int,
                       conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    """임시 변경안 항목 삭제. 검토 단계의 pending 항목만 가능하다."""
    row = conn.execute(
        "SELECT s.episode_no FROM change_proposal_items i"
        " JOIN change_proposals s ON s.proposal_set_id=i.proposal_set_id"
        " WHERE i.item_id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"item {item_id} not found")
    job = require_episode_job(conn, row["episode_no"], author["id"])
    if not job:
        raise HTTPException(404, f"item {item_id} not found")
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "entity edit needs IN_REVIEW")
    try:
        return memory.delete_pending_item(conn, item_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


def _with_version(conn, pset):
    if not pset:
        return None
    rev = conn.execute("SELECT version FROM manuscript_revisions"
                       " WHERE revision_id=?",
                       (pset["manuscript_revision_id"],)).fetchone()
    return {**pset, "version": rev["version"] if rev else None}


@router.get("/episodes/{episode_no}/entity-proposals")
def entity_proposals(episode_no: int,
                     conn: sqlite3.Connection = Depends(get_conn),
                     author: dict = Depends(get_current_author)):
    """임시 변경안 + 저장된 기억(현재 원고 기준) + 신선도."""
    job = _job_or_404(conn, episode_no, author["id"])
    rev_id = job.get("manuscript_revision_id")
    pset = memory.latest_pending_set(conn, episode_no)
    items = ([i for i in memory.get_proposals(conn, episode_no)
              if i["proposal_set_id"] == pset["proposal_set_id"]]
             if pset else [])
    saved = memory.get_committed_set_for_revision(conn, episode_no, rev_id)
    saved_items = ([i for i in memory.get_proposals(conn, episode_no)
                    if i["proposal_set_id"] == saved["proposal_set_id"]]
                   if saved else [])
    events = (memory.get_proposal_events(conn, pset["proposal_set_id"])
              if pset else [])
    return {"episode_no": episode_no,
            "proposal_set": _with_version(conn, pset),
            "items": items,
            "events": events,
            "fresh": bool(pset) and pset["manuscript_revision_id"] == rev_id,
            "saved_set": _with_version(conn, saved),
            "saved_items": saved_items}



