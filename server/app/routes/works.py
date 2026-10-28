"""Works & designs routes (§5.1)."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import planning
from ..db import now_iso
from ..errors import LLMStepError
from .deps import get_conn

router = APIRouter(prefix="/api/v1", tags=["works"])


def get_meta():
    from ..main import get_meta_client
    return get_meta_client()


class WorkIn(BaseModel):
    title: str
    logline: str = ""


@router.post("/works")
def create_work(body: WorkIn, conn: sqlite3.Connection = Depends(get_conn)):
    cur = conn.execute("INSERT INTO works(title, logline, created_at) VALUES(?,?,?)",
                       (body.title, body.logline, now_iso()))
    conn.commit()
    return {"id": cur.lastrowid, "title": body.title, "logline": body.logline}


@router.get("/works")
def list_works(conn: sqlite3.Connection = Depends(get_conn)):
    return {"works": [dict(r) for r in
                      conn.execute("SELECT * FROM works ORDER BY id")]}


@router.get("/works/{work_id}")
def get_work(work_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"work {work_id} not found")
    work = dict(row)
    latest = conn.execute(
        "SELECT id, version, scope_total_episodes, status, approved_at"
        " FROM story_designs WHERE work_id=? ORDER BY version DESC LIMIT 1",
        (work_id,)).fetchone()
    work["latest_design"] = dict(latest) if latest else None
    return work


class DesignIn(BaseModel):
    keywords: str
    total_episodes: int = 20
    constraints: list[str] = []
    prev_feedback: str = ""


@router.post("/works/{work_id}/designs")
def create_design(work_id: int, body: DesignIn,
                  conn: sqlite3.Connection = Depends(get_conn),
                  meta=Depends(get_meta)):
    if not conn.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
        raise HTTPException(404, f"work {work_id} not found")
    if body.total_episodes < 1 or body.total_episodes > 200:
        raise HTTPException(400, "total_episodes must be 1..200")
    try:
        return planning.generate_design(
            meta, conn, work_id=work_id, keywords=body.keywords,
            total_episodes=body.total_episodes, constraints=body.constraints,
            prev_feedback=body.prev_feedback)
    except LLMStepError as e:
        raise HTTPException(502, f"planning failed: {e.reason} {e.detail}")


@router.get("/designs/{design_id}")
def get_design(design_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    d = planning.get_design(conn, design_id)
    if not d:
        raise HTTPException(404, f"design {design_id} not found")
    return d


@router.post("/designs/{design_id}/approve")
def approve_design(design_id: int, conn: sqlite3.Connection = Depends(get_conn)):
    if not conn.execute("SELECT 1 FROM story_designs WHERE id=?",
                        (design_id,)).fetchone():
        raise HTTPException(404, f"design {design_id} not found")
    return planning.approve_design(conn, design_id)


class OutlinePatch(BaseModel):
    title: str | None = None
    goal: str | None = None
    events: list[str] | None = None
    char_changes: list[str] | None = None
    foreshadowing: list[str] | None = None
    links: dict | None = None
    constraints: list[str] | None = None


@router.patch("/designs/{design_id}/outlines/{episode_no}")
def patch_outline(design_id: int, episode_no: int, body: OutlinePatch,
                  conn: sqlite3.Connection = Depends(get_conn)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return planning.update_outline(conn, design_id, episode_no, fields)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
