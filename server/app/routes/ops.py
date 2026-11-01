"""Observation & admin routes (§5.4). No keys in any response."""
from __future__ import annotations

import os
import re
import sqlite3

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from .. import budget as budgetmod
from .. import db as dbmod
from ..db import find_work
from .deps import (get_conn, get_current_author, require_work)

router = APIRouter(prefix="/api/v1", tags=["ops"])

MODEL_PRESETS = [
    {"id": "muse-wonder-1.0", "label": "Muse Wonder",
     "desc": "최상위 모델 · 복잡한 작업용"},
    {"id": "muse-spark-1.3", "label": "Muse Spark 1.3",
     "desc": "권장 범용 모델"},
]
_MODEL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class ModelSwitch(BaseModel):
    model: str


class _VerifySchema(BaseModel):
    ok: bool


def _require_admin_if_set(x_admin_token: str | None) -> None:
    expected = os.environ.get("ADMIN_TOKEN", "")
    if expected and x_admin_token != expected:
        raise HTTPException(403, "invalid or missing admin token")


def _verify_model(model_id: str) -> None:
    """One minimal live call with the candidate model (test seam)."""
    from ..main import build_client_with_model
    client = build_client_with_model(model_id)
    try:
        # 넉넉한 상한: 추론 토큰 상시 사용 모델의 reasoning 분을 고려.
        client.chat_json(bundle="model_verify", episode=None,
                         system="Reply with JSON only.",
                         user='Reply with exactly this JSON and nothing else: {"ok": true}',
                         schema=_VerifySchema, max_tokens=1024)
    except Exception as e:
        raise HTTPException(400, f"model verify failed: {e}") from e


@router.get("/ops/model")
def get_model():
    from ..main import current_settings
    s = current_settings()
    return {"model": s.model_text_id, "mode": s.model_mode,
            "base_url": s.model_base_url, "presets": MODEL_PRESETS,
            "persistent": False}


@router.post("/ops/model")
def set_model(body: ModelSwitch,
              x_admin_token: str | None = Header(default=None)):
    from ..main import switch_model
    _require_admin_if_set(x_admin_token)
    model_id = (body.model or "").strip()
    if not _MODEL_RE.match(model_id):
        raise HTTPException(400, f"invalid model id: {model_id!r}")
    _verify_model(model_id)
    s = switch_model(model_id)
    return {"model": s.model_text_id, "mode": s.model_mode}


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


@router.get("/works/{key}/ai-log")
def work_ai_log(key: str, limit: int = 50,
                conn: sqlite3.Connection = Depends(get_conn),
                author: dict = Depends(get_current_author)):
    """This work's AI calls, newest first, with prompts and outputs."""
    w = require_work(conn, key, author["id"])
    rows = conn.execute(
        "SELECT * FROM llm_calls WHERE work_id=? ORDER BY id DESC LIMIT ?",
        (w["id"], min(limit, 200))).fetchall()
    return {"work_id": w["id"], "calls": [dict(r) for r in rows]}


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
