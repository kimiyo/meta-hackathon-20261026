"""Design partner threads: per-node conversations with history inclusion."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import design_nodes, design_threads
from ..errors import LLMStepError
from ..schemas import ideas as sch
from .deps import (get_conn, get_current_author, require_design,
                   require_design_message, require_design_thread,
                   require_node)
from .nodes import get_meta

router = APIRouter(prefix="/api/v1", tags=["design-threads"])

CHAT_PROMPT = (Path(__file__).parent.parent / "prompts"
               / "design_chat.txt").read_text(encoding="utf-8")


@router.get("/designs/{design_id}/threads")
def list_threads(design_id: int, node_id: str | None = None,
                 q: str | None = None,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    require_design(conn, design_id, author["id"])
    if node_id is not None:
        node = design_nodes.get_node(conn, node_id)
        if not node or node["design_id"] != design_id:
            raise HTTPException(404, f"node {node_id} not found")
        require_node(conn, node_id, author["id"])
    return {"design_id": design_id,
            "threads": design_threads.list_threads(
                conn, design_id, node_id=node_id, q=q or None)}


class ThreadIn(BaseModel):
    node_id: str
    title: str = ""


@router.post("/designs/{design_id}/threads")
def create_thread(design_id: int, body: ThreadIn,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    require_design(conn, design_id, author["id"])
    try:
        return design_threads.create_thread(conn, design_id, body.node_id,
                                            body.title)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/design-threads/{thread_id}/messages")
def list_messages(thread_id: int, limit: int = 200,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    require_design_thread(conn, thread_id, author["id"])
    return {"thread_id": thread_id,
            "messages": design_threads.list_messages(
                conn, thread_id, limit)}


class ThreadMessageIn(BaseModel):
    content: str
    refs: list[str] = []
    include_messages: list[int] | None = None


@router.post("/design-threads/{thread_id}/messages")
def post_message(thread_id: int, body: ThreadMessageIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 meta=Depends(get_meta),
                 author: dict = Depends(get_current_author)):
    t = require_design_thread(conn, thread_id, author["id"])
    node = design_nodes.get_node(conn, t["node_id"])
    if not node:
        raise HTTPException(404, f"node {t['node_id']} not found")
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content must not be blank")
    user_msg = design_threads.add_message(conn, thread_id, "user", content)
    history = None
    if body.include_messages is not None:
        want = set(body.include_messages)
        msgs = [m for m in design_threads.list_messages(conn, thread_id, 10000)
                if m["id"] in want and m["id"] != user_msg["id"]]
        history = design_threads.history_lines(msgs)
    extra = f"작가 요청: {content}\n참조: {', '.join(body.refs) or '(없음)'}"
    if body.refs:
        extra += "\n참조 해석:\n" + "\n".join(
            design_threads.ref_lines(conn, node["design_id"], body.refs))
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (node["design_id"],)).fetchone()
    try:
        out = meta.chat_json(
            bundle="design_chat", episode=None, system=CHAT_PROMPT,
            user=design_threads.node_context(conn, node, extra,
                                             history=history),
            schema=sch.DesignChatOut, max_tokens=8000,
            work_id=design["work_id"] if design else None)
    except LLMStepError as e:
        raise HTTPException(502, f"design_chat failed: {e.reason} {e.detail}")
    assistant = design_threads.add_message(
        conn, thread_id, "assistant", out.get("reply") or "(빈 응답)",
        proposals={"children": out.get("proposed_children") or [],
                   "patch": out.get("proposed_patch") or {}})
    thread = design_threads.get_thread(conn, thread_id)
    pub = dict(thread)
    pub["node_code"] = node.get("code")
    pub["snippet"] = None
    return {"thread": pub, "user": user_msg, "assistant": assistant}


class AppliedIn(BaseModel):
    patch: bool | None = None
    child: int | None = None


@router.patch("/design-messages/{message_id}/applied")
def mark_applied(message_id: int, body: AppliedIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    require_design_message(conn, message_id, author["id"])
    try:
        return design_threads.mark_applied(conn, message_id,
                                           patch=body.patch,
                                           child=body.child)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
