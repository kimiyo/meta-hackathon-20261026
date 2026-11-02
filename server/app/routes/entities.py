"""Memory query & commit routes (§5.3). Reads use committed rows only."""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from .. import memory
from .deps import get_conn, get_current_author

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



