"""Memory query & commit routes (§5.3). Reads use committed rows only."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

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


@router.post("/episodes/{episode_no}/entities/extract")
def extract_entities(episode_no: int,
                     conn: sqlite3.Connection = Depends(get_conn),
                     meta=Depends(get_meta),
                     author: dict = Depends(get_current_author)):
    """프리셋 작업: 현재 원고에서 Entity 변경안을 추출해 임시 저장한다."""
    job = _job_or_404(conn, episode_no, author["id"])
    if not job.get("manuscript_revision_id"):
        raise HTTPException(400, "no manuscript to extract from")
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (job["design_id"],)).fetchone()
    memory.discard_pending_for_revision(conn, episode_no,
                                        job["manuscript_revision_id"])
    try:
        out = extraction.extract_changes(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            manuscript=_revision_text(conn, job["manuscript_revision_id"]),
            work_id=design["work_id"] if design else None)
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    items = [i for i in memory.get_proposals(conn, episode_no)
             if i["proposal_set_id"] == out["proposal_set_id"]]
    return {"episode_no": episode_no, **out, "items": items}


@router.get("/episodes/{episode_no}/entity-proposals")
def entity_proposals(episode_no: int,
                     conn: sqlite3.Connection = Depends(get_conn),
                     author: dict = Depends(get_current_author)):
    """현재 임시 변경안(있으면) + 발간 대상 원고와의 신선도."""
    job = _job_or_404(conn, episode_no, author["id"])
    pset = memory.latest_pending_set(conn, episode_no)
    if not pset:
        return {"episode_no": episode_no, "proposal_set": None,
                "items": [], "fresh": False}
    items = [i for i in memory.get_proposals(conn, episode_no)
             if i["proposal_set_id"] == pset["proposal_set_id"]]
    rev = conn.execute("SELECT version FROM manuscript_revisions"
                       " WHERE revision_id=?",
                       (pset["manuscript_revision_id"],)).fetchone()
    return {"episode_no": episode_no,
            "proposal_set": {**pset,
                             "version": rev["version"] if rev else None},
            "items": items,
            "fresh": pset["manuscript_revision_id"]
            == job.get("manuscript_revision_id")}



