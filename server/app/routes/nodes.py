"""Design-tree routes: nodes, partner chat, expand."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import design_nodes, design_threads, ideas
from .. import meta_client as metac
from .. import prompt_loader
from ..db import find_work
from ..errors import LLMStepError
from ..schemas import ideas as sch
from .deps import (get_conn, get_current_author, require_design,
                   require_node)

router = APIRouter(prefix="/api/v1", tags=["nodes"])


def get_meta(author: dict = Depends(get_current_author)):
    from ..main import get_meta_client
    return get_meta_client(author["id"])


CHAT_PROMPT = (Path(__file__).parent.parent / "prompts"
               / "design_chat.txt").read_text(encoding="utf-8")
EXPAND_PROMPT = (Path(__file__).parent.parent / "prompts"
                 / "design_expand.txt").read_text(encoding="utf-8")


def _node(conn: sqlite3.Connection, node_id: str,
            author_id: int) -> dict:
    return require_node(conn, node_id, author_id)


def _node_setting_node(conn: sqlite3.Connection, setting_id: int,
                       author_id: int) -> dict:
    row = conn.execute("SELECT node_id FROM node_settings WHERE id=?",
                       (setting_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"node setting {setting_id} not found")
    return _node(conn, row["node_id"], author_id)


@router.get("/designs/{design_id}/nodes")
def list_nodes(design_id: int, q: str | None = None,
               archived: bool = False,
               conn: sqlite3.Connection = Depends(get_conn),
               author: dict = Depends(get_current_author)):
    require_design(conn, design_id, author["id"])
    try:
        nodes = design_nodes.list_nodes(conn, design_id,
                                        include_archived=archived, q=q)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"design_id": design_id, "nodes": nodes}


class NodeIn(BaseModel):
    kind: str
    parent_id: str | None = None
    title: str = ""
    logline: str = ""
    body: dict = {}


@router.post("/designs/{design_id}/nodes")
def create_node(design_id: int, body: NodeIn,
                conn: sqlite3.Connection = Depends(get_conn),
                author: dict = Depends(get_current_author)):
    require_design(conn, design_id, author["id"])
    try:
        return design_nodes.create_node(
            conn, design_id, kind=body.kind, parent_id=body.parent_id,
            title=body.title, logline=body.logline, body=body.body)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/nodes/{node_id}")
def get_node(node_id: str, conn: sqlite3.Connection = Depends(get_conn),
             author: dict = Depends(get_current_author)):
    _node(conn, node_id, author["id"])
    full = design_nodes.get_node_with_children(conn, node_id)
    if not full:
        raise HTTPException(404, f"node {node_id} not found")
    return full


class NodePatch(BaseModel):
    title: str | None = None
    logline: str | None = None
    body: dict | None = None
    parent_id: str | None = None
    sort_order: int | None = None


@router.patch("/nodes/{node_id}")
def patch_node(node_id: str, body: NodePatch,
               conn: sqlite3.Connection = Depends(get_conn),
               author: dict = Depends(get_current_author)):
    _node(conn, node_id, author["id"])
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return design_nodes.update_node(conn, node_id, fields)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class ArchiveIn(BaseModel):
    archived: bool = True


@router.post("/nodes/{node_id}/archive")
def archive_node(node_id: str, body: ArchiveIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    _node(conn, node_id, author["id"])
    try:
        return design_nodes.set_archived(conn, node_id, body.archived)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.delete("/nodes/{node_id}")
def delete_node(node_id: str,
                conn: sqlite3.Connection = Depends(get_conn),
                author: dict = Depends(get_current_author)):
    _node(conn, node_id, author["id"])
    try:
        return design_nodes.delete_node_subtree(conn, node_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# Prompt builders live in design_threads (shared with thread messages).


class NodeChatIn(BaseModel):
    content: str
    refs: list[str] = []


@router.post("/nodes/{node_id}/chat")
def node_chat(node_id: str, body: NodeChatIn,
              conn: sqlite3.Connection = Depends(get_conn),
              meta=Depends(get_meta),
              author: dict = Depends(get_current_author)):
    node = _node(conn, node_id, author["id"])
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content must not be blank")
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (node["design_id"],)).fetchone()
    work_id = design["work_id"] if design else None
    market, writing_locale = metac.work_locale(conn, work_id)
    en = writing_locale == "en-US"
    if en:
        refs_txt = ", ".join(body.refs) or "(none)"
        extra = f"Author request: {content}\nRefs: {refs_txt}"
    else:
        extra = f"작가 요청: {content}\n참조: {', '.join(body.refs) or '(없음)'}"
    if body.refs:
        extra += ("\nRef notes:\n" if en else "\n참조 해석:\n") + "\n".join(
            design_threads.ref_lines(conn, node["design_id"], body.refs,
                                     writing_locale))
    try:
        system = prompt_loader.load(
            "design_chat", writing_locale, market).text
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    try:
        return metac.chat_with_provenance(
            meta, bundle="design_chat", episode=None, system=system,
            user=design_threads.node_context(conn, node, extra,
                                             writing_locale=writing_locale),
            schema=sch.DesignChatOut,
            work_id=work_id, writing_locale=writing_locale, market=market,
            prompt_feature="design_chat",
            preset_id=preset_id, preset_version=preset_version)
    except LLMStepError as e:
        raise HTTPException(502, f"design_chat failed: {e.reason} {e.detail}")


class ExpandIn(BaseModel):
    kind: str | None = None


@router.post("/nodes/{node_id}/expand")
def node_expand(node_id: str, body: ExpandIn,
                conn: sqlite3.Connection = Depends(get_conn),
                meta=Depends(get_meta),
                author: dict = Depends(get_current_author)):
    node = _node(conn, node_id, author["id"])
    if body.kind is not None and body.kind not in design_nodes.KINDS:
        raise HTTPException(
            400, f"kind must be one of {sorted(design_nodes.KINDS)}")
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (node["design_id"],)).fetchone()
    work_id = design["work_id"] if design else None
    market, writing_locale = metac.work_locale(conn, work_id)
    if writing_locale == "en-US":
        extra = (f"Child level: {body.kind}" if body.kind
                 else "Child level: directly below the selected node")
    else:
        extra = (f"자식 수준: {body.kind}" if body.kind
                 else "자식 수준: 선택 노드의 바로 아래 수준")
    try:
        system = prompt_loader.load(
            "design_expand", writing_locale, market).text
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    try:
        return metac.chat_with_provenance(
            meta, bundle="design_expand", episode=None, system=system,
            user=design_threads.node_context(conn, node, extra,
                                             writing_locale=writing_locale),
            schema=sch.ExpandOut,
            work_id=work_id, writing_locale=writing_locale, market=market,
            prompt_feature="design_expand",
            preset_id=preset_id, preset_version=preset_version)
    except LLMStepError as e:
        raise HTTPException(502, f"design_expand failed: {e.reason} {e.detail}")


@router.get("/designs/{design_id}/node-settings")
def design_node_settings(design_id: int,
                         conn: sqlite3.Connection = Depends(get_conn),
                         author: dict = Depends(get_current_author)):
    require_design(conn, design_id, author["id"])
    try:
        settings = design_nodes.list_design_node_settings(conn, design_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"design_id": design_id, "settings": settings}


@router.get("/nodes/{node_id}/settings")
def node_settings(node_id: str,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    _node(conn, node_id, author["id"])
    try:
        return design_nodes.effective_node_settings(conn, node_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


class NodeSettingIn(BaseModel):
    category: str = "misc"
    title: str = ""
    summary: str = ""
    detail: str = ""
    code: str | None = None


@router.post("/nodes/{node_id}/settings")
def create_node_setting(node_id: str, body: NodeSettingIn,
                        conn: sqlite3.Connection = Depends(get_conn),
                        author: dict = Depends(get_current_author)):
    _node(conn, node_id, author["id"])
    try:
        return design_nodes.create_node_setting(
            conn, node_id, category=body.category, title=body.title,
            summary=body.summary, detail=body.detail, code=body.code)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


class NodeSettingPatch(BaseModel):
    category: str | None = None
    title: str | None = None
    summary: str | None = None
    detail: str | None = None


@router.patch("/node-settings/{setting_id}")
def patch_node_setting(setting_id: int, body: NodeSettingPatch,
                       conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    _node_setting_node(conn, setting_id, author["id"])
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return design_nodes.update_node_setting(conn, setting_id, fields)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/node-settings/{setting_id}")
def delete_node_setting(setting_id: int,
                        conn: sqlite3.Connection = Depends(get_conn),
                        author: dict = Depends(get_current_author)):
    _node_setting_node(conn, setting_id, author["id"])
    try:
        return design_nodes.delete_node_setting(conn, setting_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
