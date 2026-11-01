"""Design-tree routes: nodes, partner chat, expand."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import design_nodes, ideas
from ..db import find_work
from ..errors import LLMStepError
from ..schemas import ideas as sch
from .deps import get_conn

router = APIRouter(prefix="/api/v1", tags=["nodes"])


def get_meta():
    from ..main import get_meta_client
    return get_meta_client()


CHAT_PROMPT = (Path(__file__).parent.parent / "prompts"
               / "design_chat.txt").read_text(encoding="utf-8")
EXPAND_PROMPT = (Path(__file__).parent.parent / "prompts"
                 / "design_expand.txt").read_text(encoding="utf-8")


def _node(conn: sqlite3.Connection, node_id: str) -> dict:
    n = design_nodes.get_node(conn, node_id)
    if not n:
        raise HTTPException(404, f"node {node_id} not found")
    return n


@router.get("/designs/{design_id}/nodes")
def list_nodes(design_id: int, q: str | None = None,
               archived: bool = False,
               conn: sqlite3.Connection = Depends(get_conn)):
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
                conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return design_nodes.create_node(
            conn, design_id, kind=body.kind, parent_id=body.parent_id,
            title=body.title, logline=body.logline, body=body.body)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/nodes/{node_id}")
def get_node(node_id: str, conn: sqlite3.Connection = Depends(get_conn)):
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
               conn: sqlite3.Connection = Depends(get_conn)):
    _node(conn, node_id)
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
                 conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return design_nodes.set_archived(conn, node_id, body.archived)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.delete("/nodes/{node_id}")
def delete_node(node_id: str,
                conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return design_nodes.delete_node_subtree(conn, node_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


def _ref_lines(conn: sqlite3.Connection, design_id: int,
               refs: list[str]) -> list[str]:
    """Resolve @ codes to node one-liners; unknowns stay listed."""
    lines = []
    for ref in refs:
        nid = design_nodes.resolve_node_id(conn, design_id, ref)
        if nid is None:
            lines.append(f"- {ref} = (알 수 없음)")
            continue
        n = design_nodes.get_node(conn, nid)
        lines.append(f"- {ref} = {n['full_code']}({n['kind']})"
                     f" {n['title']}: {n['logline']}")
    return lines


def _node_context(conn: sqlite3.Connection, node: dict,
                  extra: str = "") -> str:
    design = conn.execute("SELECT * FROM story_designs WHERE id=?",
                          (node["design_id"],)).fetchone()
    work = find_work(conn, design["work_id"]) if design else None
    root_code = design_nodes.design_root_code(conn, node["design_id"])
    chain, seen = [], node.get("parent_id")
    while seen is not None and len(chain) < 8:
        prow = conn.execute("SELECT id, kind, seq, title, logline, parent_id"
                            " FROM design_nodes WHERE id=?", (seen,)).fetchone()
        if not prow:
            break
        pcode = design_nodes.node_code(prow["kind"], prow["seq"])
        chain.append(f"{pcode}({prow['kind']}) {prow['title']}:"
                     f" {prow['logline']}")
        seen = prow["parent_id"]
    kids = conn.execute(
        "SELECT kind, seq, title, logline FROM design_nodes"
        " WHERE parent_id=? AND archived=0 ORDER BY sort_order, id LIMIT 30",
        (node["id"],)).fetchall()
    sibs = conn.execute(
        "SELECT kind, seq, title FROM design_nodes WHERE design_id=?"
        " AND COALESCE(parent_id,'')=COALESCE(?,'') AND id!=?"
        " ORDER BY sort_order, id LIMIT 20",
        (node["design_id"], node.get("parent_id"), node["id"])).fetchall()
    settings = ideas.list_settings(conn, work["id"])[:20] if work else []
    code_of = lambda r: design_nodes.node_code(r["kind"], r["seq"])
    parts = [
        f"작품: {work['title'] if work else '?'}"
        f" / 구조: {work['structure_json'] if work else '{}'}",
        f"설계 트리: {root_code or '?'}"
        " (이 트리 안에서는 E001 같은 짧은 코드로 지칭한다)",
        f"선택 노드: {node.get('code') or node['id']}({node['kind']})"
        f" {node['title']}",
        f"줄거리: {node['logline']}",
        f"상세: {json.dumps(node['body'], ensure_ascii=False)[:1000]}",
        "상위: " + (" > ".join(reversed(chain)) if chain else "(없음)"),
        "하위:",
    ]
    parts += [f"- {code_of(k)}({k['kind']}) {k['title']}: {k['logline']}"
              for k in kids] or ["(없음)"]
    parts += ["형제:"] + [
        f"- {code_of(s)}({s['kind']}) {s['title']}" for s in sibs] or ["(없음)"]
    parts += ["설정정보:"] + [
        f"- [{s['category']}] {s['title']}: {s['summary']}"
        for s in settings] or ["(없음)"]
    if extra:
        parts += ["", extra]
    return "\n".join(parts)[:8000]


class NodeChatIn(BaseModel):
    content: str
    refs: list[str] = []


@router.post("/nodes/{node_id}/chat")
def node_chat(node_id: str, body: NodeChatIn,
              conn: sqlite3.Connection = Depends(get_conn),
              meta=Depends(get_meta)):
    node = _node(conn, node_id)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content must not be blank")
    extra = f"작가 요청: {content}\n참조: {', '.join(body.refs) or '(없음)'}"
    if body.refs:
        extra += "\n참조 해석:\n" + "\n".join(
            _ref_lines(conn, node["design_id"], body.refs))
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (node["design_id"],)).fetchone()
    try:
        return meta.chat_json(bundle="design_chat", episode=None,
                              system=CHAT_PROMPT,
                              user=_node_context(conn, node, extra),
                              schema=sch.DesignChatOut, max_tokens=8000,
                              work_id=design["work_id"] if design else None)
    except LLMStepError as e:
        raise HTTPException(502, f"design_chat failed: {e.reason} {e.detail}")


class ExpandIn(BaseModel):
    kind: str | None = None


@router.post("/nodes/{node_id}/expand")
def node_expand(node_id: str, body: ExpandIn,
                conn: sqlite3.Connection = Depends(get_conn),
                meta=Depends(get_meta)):
    node = _node(conn, node_id)
    if body.kind is not None and body.kind not in design_nodes.KINDS:
        raise HTTPException(
            400, f"kind must be one of {sorted(design_nodes.KINDS)}")
    extra = (f"자식 수준: {body.kind}" if body.kind
             else "자식 수준: 선택 노드의 바로 아래 수준")
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (node["design_id"],)).fetchone()
    try:
        return meta.chat_json(bundle="design_expand", episode=None,
                              system=EXPAND_PROMPT,
                              user=_node_context(conn, node, extra),
                              schema=sch.ExpandOut, max_tokens=8000,
                              work_id=design["work_id"] if design else None)
    except LLMStepError as e:
        raise HTTPException(502, f"design_expand failed: {e.reason} {e.detail}")


@router.get("/designs/{design_id}/node-settings")
def design_node_settings(design_id: int,
                         conn: sqlite3.Connection = Depends(get_conn)):
    try:
        settings = design_nodes.list_design_node_settings(conn, design_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    return {"design_id": design_id, "settings": settings}


@router.get("/nodes/{node_id}/settings")
def node_settings(node_id: str,
                  conn: sqlite3.Connection = Depends(get_conn)):
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
                        conn: sqlite3.Connection = Depends(get_conn)):
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
                       conn: sqlite3.Connection = Depends(get_conn)):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return design_nodes.update_node_setting(conn, setting_id, fields)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/node-settings/{setting_id}")
def delete_node_setting(setting_id: int,
                        conn: sqlite3.Connection = Depends(get_conn)):
    try:
        return design_nodes.delete_node_setting(conn, setting_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
