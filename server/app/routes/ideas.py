"""Ideas/structure routes: threads, chat, synopsis, settings, handoff."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import ideas
from ..db import find_work, touch_work
from ..errors import LLMStepError
from ..schemas import ideas as sch
from .deps import get_conn

router = APIRouter(prefix="/api/v1", tags=["ideas"])


def get_meta():
    from ..main import get_meta_client
    return get_meta_client()


CHAT_PROMPT = (Path(__file__).parent.parent / "prompts"
               / "ideas_chat.txt").read_text(encoding="utf-8")
EXTRACT_PROMPT = (Path(__file__).parent.parent / "prompts"
                  / "ideas_extract.txt").read_text(encoding="utf-8")
HANDOFF_PROMPT = (Path(__file__).parent.parent / "prompts"
                  / "handoff_migrate.txt").read_text(encoding="utf-8")


def _work(conn: sqlite3.Connection, key: str) -> dict:
    w = find_work(conn, key)
    if not w:
        raise HTTPException(404, f"work {key} not found")
    return w


def _unlocked(work: dict) -> None:
    if work["ideas_locked"]:
        raise HTTPException(409, "ideas handed off (read-only)")


def _thread(conn: sqlite3.Connection, thread_id: int) -> dict:
    t = ideas.get_thread(conn, thread_id)
    if not t:
        raise HTTPException(404, f"thread {thread_id} not found")
    return t


# ---------------------------------------------------------------- threads

@router.get("/works/{key}/threads")
def list_threads(key: str, conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    return {"work_id": w["id"], "threads": ideas.list_threads(conn, w["id"])}


class ThreadIn(BaseModel):
    title: str = ""


@router.post("/works/{key}/threads")
def create_thread(key: str, body: ThreadIn,
                  conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    _unlocked(w)
    return ideas.create_thread(conn, w["id"], body.title)


@router.get("/threads/{thread_id}/messages")
def list_messages(thread_id: int, limit: int = 100,
                  conn: sqlite3.Connection = Depends(get_conn)):
    _thread(conn, thread_id)
    return {"thread_id": thread_id,
            "messages": ideas.list_messages(conn, thread_id, limit)}


class MessageIn(BaseModel):
    content: str
    refs: list[str] = []
    history: int | None = None
    include_synopsis: bool | None = None
    include_settings: list[int] | None = None
    include_messages: list[int] | None = None
    include_structure: bool | None = None


def _resolve_refs(conn: sqlite3.Connection, work_id: int, refs: list[str],
                  skip_setting_ids: set[int]) -> tuple[list[str], list[str],
                                                       list[str]]:
    """Resolve @ tokens to full settings / design nodes. Unknown stay listed."""
    setting_lines, node_lines, unknown = [], [], []
    for ref in refs:
        s = conn.execute(
            "SELECT * FROM setting_entries WHERE work_id=? AND title=?",
            (work_id, ref)).fetchone()
        if s is not None:
            if s["id"] not in skip_setting_ids:
                setting_lines.append(
                    f"- [{s['category']}] {s['title']}: {s['summary']}\n"
                    f"  {s['detail']}")
                skip_setting_ids.add(s["id"])
            continue
        n = conn.execute(
            "SELECT n.id, n.kind, n.title, n.logline FROM design_nodes n"
            " JOIN story_designs d ON d.id=n.design_id"
            " WHERE d.work_id=? AND n.id=?", (work_id, ref)).fetchone()
        if n is not None:
            node_lines.append(
                f"- {n['id']}({n['kind']}) {n['title']}: {n['logline']}")
        else:
            unknown.append(ref)
    return setting_lines, node_lines, unknown


def _chat_context(conn: sqlite3.Connection, work: dict, thread_id: int, *,
                  user_msg: dict, history: int = 12,
                  include_synopsis: bool | None = None,
                  include_settings: list[int] | None = None,
                  include_messages: list[int] | None = None,
                  include_structure: bool | None = None,
                  refs: list[str] = []) -> str:
    """Build the AI context. None fields keep the legacy behavior."""
    if include_messages is None:
        h = max(1, min(history or 12, 50))
        msgs = ideas.list_messages(conn, thread_id, 200)[-h:]
    else:
        want = set(include_messages)
        msgs = [m for m in ideas.list_messages(conn, thread_id, 10000)
                if m["id"] in want and m["id"] != user_msg["id"]]
        msgs = msgs + [user_msg]  # the question itself is always included
    convo = [f"{'작가' if m['role'] == 'user' else 'AI'}: {m['content']}"
             for m in msgs]
    syn = ideas.get_synopsis(conn, work["id"])
    parts = [f"작품: {work['title']}"]
    struct = ideas.parse_structure(work["structure_json"])
    if include_structure is None:
        parts.append(f"현재 구조 결정: {work['structure_json']}")
    elif include_structure:
        plines = [f"Part {'사용' if struct['use_part'] else '미사용'}, "
                  f"Chapter {'사용' if struct['use_chapter'] else '미사용'}, "
                  f"총 {struct['total_episodes']}화, "
                  f"Part {len(struct['parts'])}개"]
        plines += [f"- {p['title']}: {p['summary']}" for p in struct["parts"]]
        parts.append("[포함된 작품 구조]:\n" + "\n".join(plines))
    else:
        parts.append("[작품 구조 미포함]")
    if include_synopsis is None:
        parts.append(f"현재 전체 줄거리(v{syn['version']}):"
                     f" {(syn['content'] or '')[:1500]}")
    elif include_synopsis:
        parts.append("[포함된 전체 줄거리]:\n"
                     f"{(syn['content'] or '')[:2000]}")
    else:
        parts.append("[전체 줄거리 미포함]")
    all_settings = ideas.list_settings(conn, work["id"])
    skip: set[int] = set()
    if include_settings is None:
        parts.append("설정정보:")
        parts += [f"- [{s['category']}] {s['title']}: {s['summary']}"
                  for s in all_settings[:30]] or ["(없음)"]
        skip = {s["id"] for s in all_settings[:30]}
    elif include_settings:
        want_ids = set(include_settings)
        picked = [s for s in all_settings if s["id"] in want_ids]
        parts.append("[포함된 설정정보]:")
        parts += [f"- [{s['category']}] {s['title']}: {s['summary']}\n"
                  f"  {s['detail']}" for s in picked] or ["(선택 없음)"]
        skip = {s["id"] for s in picked}
    else:
        parts.append("[포함된 설정정보]: (선택 없음)")
    if refs:
        sl, nl, unk = _resolve_refs(conn, work["id"], list(refs), skip)
        if sl or nl or unk:
            parts.append("[@언급]:")
            parts += sl + nl
            if unk:
                parts.append(f"(확인 불가: {', '.join(unk)})")
    convo_label = "최근 대화:" if include_messages is None else "[포함된 대화]:"
    parts += ["", convo_label] + (convo or ["(없음)"])
    return "\n".join(parts)[:7000]


@router.post("/threads/{thread_id}/messages")
def post_message(thread_id: int, body: MessageIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 meta=Depends(get_meta)):
    t = _thread(conn, thread_id)
    w = _work(conn, t["work_id"])
    _unlocked(w)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content must not be blank")
    user_msg = ideas.add_message(conn, thread_id, "user", content, body.refs)
    try:
        out = meta.chat_json(
            bundle="idea_chat", episode=None, system=CHAT_PROMPT, work_id=w["id"],
            user=_chat_context(
                conn, w, thread_id, user_msg=user_msg,
                history=body.history or 12,
                include_synopsis=body.include_synopsis,
                include_settings=body.include_settings,
                include_messages=body.include_messages,
                include_structure=body.include_structure, refs=body.refs),
            schema=sch.IdeaChatOut, max_tokens=8000)
    except LLMStepError as e:
        raise HTTPException(502, f"idea_chat failed: {e.reason} {e.detail}")
    assistant = ideas.add_message(
        conn, thread_id, "assistant", out.get("reply", ""),
        out.get("refs", []), out.get("choices", []),
        {"settings": out.get("settings", []),
         "synopsis_proposal": out.get("synopsis_proposal", ""),
         "structure": out.get("structure")})
    touch_work(conn, w["id"])
    conn.commit()
    return {"user": user_msg, "assistant": assistant}


# ---------------------------------------------------------------- synopsis

@router.get("/works/{key}/synopsis")
def get_synopsis(key: str, conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    return ideas.get_synopsis(conn, w["id"])


class SynopsisIn(BaseModel):
    content: str


@router.put("/works/{key}/synopsis")
def put_synopsis(key: str, body: SynopsisIn,
                 conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    _unlocked(w)
    return ideas.update_synopsis(conn, w["id"], body.content)


@router.get("/works/{key}/synopsis/revisions")
def synopsis_revisions(key: str,
                       conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    return {"work_id": w["id"],
            "revisions": ideas.list_synopsis_revisions(conn, w["id"])}


# ---------------------------------------------------------------- structure

class PartIn(BaseModel):
    title: str = ""
    summary: str = ""


class StructureIn(BaseModel):
    use_part: bool = False
    use_chapter: bool = False
    total_episodes: int = 25
    parts: list[PartIn] = []


@router.get("/works/{key}/structure")
def get_structure(key: str, conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    return ideas.parse_structure(w["structure_json"])


@router.put("/works/{key}/structure")
def put_structure(key: str, body: StructureIn,
                  conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    _unlocked(w)
    try:
        return ideas.update_structure(conn, w["id"], body.model_dump())
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------- settings

@router.get("/works/{key}/settings")
def list_settings(key: str, category: str | None = None,
                  conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    return {"work_id": w["id"],
            "settings": ideas.list_settings(conn, w["id"], category)}


class SettingIn(BaseModel):
    category: str = "misc"
    title: str = ""
    summary: str = ""
    detail: str = ""
    status: str = "draft"


@router.post("/works/{key}/settings")
def create_setting(key: str, body: SettingIn,
                   conn: sqlite3.Connection = Depends(get_conn)):
    w = _work(conn, key)
    _unlocked(w)
    try:
        return ideas.create_setting(
            conn, w["id"], category=body.category, title=body.title,
            summary=body.summary, detail=body.detail, status=body.status)
    except ValueError as e:
        raise HTTPException(400, str(e))


class SettingPatch(BaseModel):
    category: str | None = None
    title: str | None = None
    summary: str | None = None
    detail: str | None = None
    status: str | None = None


@router.patch("/settings/{setting_id}")
def patch_setting(setting_id: int, body: SettingPatch,
                  conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM setting_entries WHERE id=?",
                       (setting_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"setting {setting_id} not found")
    w = _work(conn, row["work_id"])
    _unlocked(w)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return ideas.update_setting(conn, setting_id, fields)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/settings/{setting_id}")
def delete_setting(setting_id: int,
                   conn: sqlite3.Connection = Depends(get_conn)):
    row = conn.execute("SELECT * FROM setting_entries WHERE id=?",
                       (setting_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"setting {setting_id} not found")
    w = _work(conn, row["work_id"])
    _unlocked(w)
    try:
        return ideas.delete_setting(conn, setting_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


# ---------------------------------------------------------------- extract

class ExtractIn(BaseModel):
    thread_id: int | None = None
    limit: int = 20


@router.post("/works/{key}/extract")
def extract(key: str, body: ExtractIn,
            conn: sqlite3.Connection = Depends(get_conn),
            meta=Depends(get_meta)):
    w = _work(conn, key)
    _unlocked(w)
    thread_id = body.thread_id
    if thread_id is None:
        threads = ideas.list_threads(conn, w["id"])
        if not threads:
            raise HTTPException(400, "no conversation yet")
        thread_id = threads[-1]["id"]
    else:
        _thread(conn, thread_id)
    msgs = ideas.list_messages(conn, thread_id, 200)[-body.limit:]
    syn = ideas.get_synopsis(conn, w["id"])
    settings = ideas.list_settings(conn, w["id"])[:30]
    convo = "\n".join(
        f"{'작가' if m['role'] == 'user' else 'AI'}: {m['content']}"
        for m in msgs) or "(대화 없음)"
    user = (f"작품: {w['title']}\n현재 구조 결정: {w['structure_json']}\n"
            f"현재 전체 줄거리(v{syn['version']}):\n{syn['content'][:2000]}\n"
            f"현재 설정정보:\n" + ("\n".join(
                f"- [{s['category']}] {s['title']}: {s['summary']}"
                for s in settings) or "(없음)") +
            f"\n\n분석할 대화:\n{convo}")[:8000]
    try:
        return meta.chat_json(bundle="idea_extract", episode=None,
                              system=EXTRACT_PROMPT, user=user,
                              schema=sch.ExtractOut, max_tokens=8000,
                              work_id=w["id"])
    except LLMStepError as e:
        raise HTTPException(502, f"idea_extract failed: {e.reason} {e.detail}")


# ---------------------------------------------------------------- handoff

def _handoff_user_text(w: dict, structure: dict, syn: dict,
                       settings: list) -> str:
    levels = " / ".join(
        ((["Part(부)"] if structure["use_part"] else []) +
         (["Chapter(장)"] if structure["use_chapter"] else []) +
         ["Episode(화)"]))
    parts_txt = "\n".join(f"- {p['title']}: {p['summary']}"
                          for p in structure["parts"]) or "(Part 미정)"
    settings_txt = "\n".join(
        f"- [{s['code'] or '번호없음'}|{s['category']}] {s['title']}:"
        f" {s['summary']}" + (f" / {s['detail']}" if s["detail"] else "")
        for s in settings) or "(설정 없음)"
    return (f"작품: {w['title']}\n작품 구조: {levels},"
            f" 총 {structure['total_episodes']}화\n"
            f"Part 목록:\n{parts_txt}\n\n전체 줄거리:\n"
            f"{syn['content'] or '(없음)'}\n\n설정정보:\n{settings_txt}")[:12000]


@router.get("/works/{key}/handoff/preview")
def handoff_preview(key: str,
                    conn: sqlite3.Connection = Depends(get_conn),
                    meta=Depends(get_meta)):
    """AI migration preview: the exact tree confirm will create. No writes."""
    w = _work(conn, key)
    if w["ideas_locked"]:
        raise HTTPException(409, "ideas already handed off (read-only)")
    structure = ideas.parse_structure(w["structure_json"])
    syn = ideas.get_synopsis(conn, w["id"])
    settings = ideas.list_settings(conn, w["id"])
    user = _handoff_user_text(w, structure, syn, settings)
    try:
        plan = meta.chat_json(bundle="handoff_migrate", episode=None,
                              system=HANDOFF_PROMPT, user=user,
                              schema=sch.HandoffOut, max_tokens=16000,
                              work_id=w["id"])
    except LLMStepError as e:
        raise HTTPException(
            502, f"handoff_migrate failed: {e.reason} {e.detail}")
    tree, counts = ideas.build_migration_tree(structure, plan)
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM story_designs"
                       " WHERE work_id=?", (w["id"],)).fetchone()
    levels = ((["Part(부)"] if structure["use_part"] else []) +
              (["Chapter(장)"] if structure["use_chapter"] else []) +
              ["Episode(화)"])
    return {"title": w["title"], "design_version": row["v"] + 1,
            "levels": levels, "tree": tree, "counts": counts,
            "plan": plan, "locked_after": True,
            "synopsis_chars": len(syn["content"] or ""),
            "settings": [{"code": s["code"], "category": s["category"],
                          "title": s["title"]} for s in settings]}


@router.post("/works/{key}/handoff")
def handoff(key: str, body: sch.HandoffOut,
            conn: sqlite3.Connection = Depends(get_conn)):
    """Apply a previewed migration plan (no AI call).

    The stored ideas-phase structure drives the migration; afterwards the
    ideas screen turns read-only.
    """
    w = _work(conn, key)
    if w["ideas_locked"]:
        raise HTTPException(409, "ideas already handed off (read-only)")
    try:
        return ideas.handoff_migrate(conn, w["id"], body.model_dump())
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
