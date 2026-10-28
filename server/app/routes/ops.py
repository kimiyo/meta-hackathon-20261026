"""Observation & admin routes (§5.4). No keys in any response."""
from __future__ import annotations

import os
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException

from .. import budget as budgetmod
from .. import db as dbmod
from .deps import get_conn

router = APIRouter(prefix="/api/v1", tags=["ops"])


@router.get("/ops/llm-calls")
def llm_calls(bundle: str | None = None, episode: int | None = None,
              limit: int = 50, conn: sqlite3.Connection = Depends(get_conn)):
    sql = "SELECT * FROM llm_calls WHERE 1=1"
    args: list = []
    if bundle:
        sql += " AND bundle=?"
        args.append(bundle)
    if episode is not None:
        sql += " AND episode_no=?"
        args.append(episode)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(min(limit, 200))
    return {"calls": [dict(r) for r in conn.execute(sql, args)]}


@router.get("/ops/budget")
def budget(conn: sqlite3.Connection = Depends(get_conn)):
    from ..main import settings
    return budgetmod.budget_status(conn, settings.daily_token_budget)


_RESET_TABLES = (
    "event_participants", "entity_mentions", "entity_changes", "events",
    "entity_relations", "entity_snapshots", "entity_aliases",
    "change_proposal_items", "change_proposals", "manuscript_revisions",
    "episode_jobs", "episode_outlines", "story_designs", "works",
    "llm_calls", "style_policies",
)


@router.post("/admin/reset-demo")
def reset_demo(x_admin_token: str | None = Header(default=None),
               conn: sqlite3.Connection = Depends(get_conn)):
    expected = os.environ.get("ADMIN_TOKEN", "")
    if not expected or x_admin_token != expected:
        raise HTTPException(403, "invalid or missing admin token")
    with conn:
        for t in _RESET_TABLES:
            conn.execute(f"DELETE FROM {t}")
    dbmod._migrate(conn)
    return {"reset": True, "tables": list(_RESET_TABLES)}
