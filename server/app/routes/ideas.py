"""Ideas/structure routes: threads, chat, synopsis, settings, handoff."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import design_nodes, ideas, refs as refsmod
from .. import meta_client as metac
from .. import prompt_loader
from ..db import find_work, touch_work
from ..errors import LLMStepError
from ..schemas import ideas as sch
from .deps import (get_conn, get_current_author, require_thread,
                       require_work)

router = APIRouter(prefix="/api/v1", tags=["ideas"])


def _work_locale(work: dict) -> tuple[str, str]:
    """(writing_locale, market) from a work row; ko-KR/KR fallback."""
    return (work.get("writing_locale") or "ko-KR",
            work.get("market") or "KR")


def get_meta():
    from ..main import get_meta_client
    return get_meta_client()


CHAT_PROMPT = (Path(__file__).parent.parent / "prompts"
               / "ideas_chat.txt").read_text(encoding="utf-8")
EXTRACT_PROMPT = (Path(__file__).parent.parent / "prompts"
                  / "ideas_extract.txt").read_text(encoding="utf-8")
HANDOFF_PROMPT = (Path(__file__).parent.parent / "prompts"
                  / "handoff_migrate.txt").read_text(encoding="utf-8")
# Covers reasoning + visible JSON (both count toward max_tokens). Single
# idea_chat calls measured up to 7,936 under the old 8,000 cap.
IDEA_MAX_TOKENS = 16000


def _work(conn: sqlite3.Connection, key: str, author_id: int) -> dict:
    return require_work(conn, key, author_id)


def _unlocked(work: dict) -> None:
    if work["ideas_locked"]:
        raise HTTPException(409, "ideas handed off (read-only)")


def _thread(conn: sqlite3.Connection, thread_id: int,
             author_id: int) -> dict:
    return require_thread(conn, thread_id, author_id)


# ---------------------------------------------------------------- threads

@router.get("/works/{key}/threads")
def list_threads(key: str, conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
    return {"work_id": w["id"], "threads": ideas.list_threads(conn, w["id"])}


class ThreadIn(BaseModel):
    title: str = ""


@router.post("/works/{key}/threads")
def create_thread(key: str, body: ThreadIn,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
    _unlocked(w)
    return ideas.create_thread(conn, w["id"], body.title)


@router.get("/threads/{thread_id}/messages")
def list_messages(thread_id: int, limit: int = 100,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    _thread(conn, thread_id, author["id"])
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
    """Shared @ resolver (single implementation in app/refs.py)."""
    return refsmod.resolve_refs(conn, work_id, refs, skip_setting_ids)


def _normalize_setting_proposals(items: list | None,
                                 settings: list[dict] = (),
                                 writing_locale: str = "ko-KR") -> list:
    """Fill blank titles and drop codes that do not belong to this work.

    The prompt requires titles, but a blank may still slip through;
    fall back to the summary head, then to '무제' ('Untitled' for
    en-US). A code the model invented would make the button patch
    nothing, so it is cleared and the proposal becomes a new entry.
    """
    known = {s["code"] for s in settings if s.get("code")}
    untitled = "Untitled" if writing_locale == "en-US" else "무제"
    out = []
    for s in items or []:
        s = dict(s)
        if not (s.get("title") or "").strip():
            head = (s.get("summary") or "").strip().split("\n")[0][:30]
            s["title"] = head.strip() or untitled
        code = (s.get("code") or "").strip().upper()
        s["code"] = code if code in known else ""
        out.append(s)
    return out


def _normalize_merge(codes: list | None, proposals: list,
                     settings: list[dict]) -> list[dict]:
    """Merge targets → existing entries, never the entry being written."""
    by_code = {s["code"]: s for s in settings if s.get("code")}
    keep = {p["code"] for p in proposals if p.get("code")}
    out, seen = [], set()
    for c in codes or []:
        c = str(c).strip().upper()
        if c in by_code and c not in keep and c not in seen:
            seen.add(c)
            s = by_code[c]
            out.append({"code": c, "category": s["category"],
                        "title": s["title"]})
    return out


# Per-section character budgets. The model has a 1M-token window; these
# bound latency/cost and, above all, keep the author's latest question whole.
CTX_SYNOPSIS = 6000
CTX_SETTINGS = 8000
CTX_INDEX = 3000
CTX_CONVO = 8000
CTX_LAST = 4000
INDEX_MAX = 100


def _clip(text: str, limit: int,
          writing_locale: str = "ko-KR") -> tuple[str, bool]:
    text = text or ""
    if len(text) <= limit:
        return text, False
    if writing_locale == "en-US":
        return (text[:limit]
                + f"\n…({len(text) - limit} chars omitted)", True)
    return text[:limit] + f"\n…(이하 {len(text) - limit}자 생략)", True


def _clip_lines(lines: list[str], limit: int,
                writing_locale: str = "ko-KR") -> list[str]:
    """Keep whole lines up to limit; say how many were left out."""
    out, used = [], 0
    for i, ln in enumerate(lines):
        if used + len(ln) > limit:
            if writing_locale == "en-US":
                out.append(f"…({len(lines) - i} omitted)")
            else:
                out.append(f"…({len(lines) - i}개 생략)")
            break
        out.append(ln)
        used += len(ln) + 1
    return out


def _setting_line(s: dict, full: bool,
                  writing_locale: str = "ko-KR") -> str:
    no_code = "unnumbered" if writing_locale == "en-US" else "번호없음"
    head = f"- {s['code'] or no_code} [{s['category']}] {s['title']}"
    if not full:
        return head
    return f"{head}: {s['summary']}\n  {s['detail']}"


def _same_setting(s: dict, p: dict) -> bool:
    return ((s.get("summary") or "") == (p.get("summary") or "")
            and (s.get("detail") or "") == (p.get("detail") or ""))


def _find_setting(settings: list[dict], p: dict) -> dict | None:
    """Same rule as the screen: code first, then category + title."""
    if p.get("code"):
        for s in settings:
            if s.get("code") == p["code"]:
                return s
    for s in settings:
        if s["category"] == p.get("category") and s["title"] == p.get("title"):
            return s
    return None


def _structure_applied(prop: dict, struct: dict) -> bool:
    for k in ("use_part", "use_chapter", "total_episodes"):
        if prop.get(k) is not None and prop[k] != struct.get(k):
            return False
    if prop.get("parts") is not None:
        mine = [(p.get("title", ""), p.get("summary", "")) for p in prop["parts"]]
        cur = [(p["title"], p["summary"]) for p in struct.get("parts", [])]
        if mine != cur:
            return False
    return True


def _proposal_digest(m: dict, settings: list[dict], syn: dict,
                     struct: dict, writing_locale: str = "ko-KR") -> str:
    """One line telling the model what it proposed and what was applied.

    The reply itself never carries proposal content, so without this the
    next turn cannot know what it suggested or whether the author took it.
    """
    props = m.get("proposals") or {}
    en = writing_locale == "en-US"
    applied = "applied" if en else "반영됨"
    pending = "pending" if en else "미반영"
    bits = []
    sp = []
    for p in props.get("settings") or []:
        s = _find_setting(settings, p)
        state = applied if s and _same_setting(s, p) else pending
        sp.append(f"[{p.get('category', 'misc')}]{p.get('title', '')}({state})")
    if sp:
        bits.append(("Settings " if en else "설정 ") + ", ".join(sp))
    merged = props.get("merge_from") or []
    if merged:
        alive = {s["code"] for s in settings}
        done = all(x["code"] not in alive for x in merged)
        bits.append(("Merged away " if en else "통합 삭제 ")
                    + "·".join(x["code"] for x in merged)
                    + f"({applied if done else pending})")
    sy = props.get("synopsis_proposal") or ""
    if sy:
        bits.append(("Synopsis revision" if en else "줄거리 개정")
                    + f"({applied if sy == (syn['content'] or '') else pending})")
    st = props.get("structure")
    if isinstance(st, dict) and any(st.get(k) is not None for k in
                                    ("use_part", "use_chapter",
                                     "total_episodes", "parts")):
        desc = []
        if st.get("total_episodes") is not None:
            if en:
                desc.append(f"Total {st['total_episodes']} episodes")
            else:
                desc.append(f"총 {st['total_episodes']}화")
        if st.get("parts") is not None:
            if en:
                desc.append(f"{len(st['parts'])} Parts")
            else:
                desc.append(f"Part {len(st['parts'])}개")
        changed = "changed" if en else "변경"
        bits.append((f"Structure {'·'.join(desc) or changed}"
                     if en else f"구조 {'·'.join(desc) or changed}")
                    + f"({applied if _structure_applied(st, struct) else pending})")
    if not bits:
        return ""
    if en:
        return f"\n  (This reply proposed: {' / '.join(bits)})"
    return f"\n  (이 답변의 제안: {' / '.join(bits)})"


def _chat_context(conn: sqlite3.Connection, work: dict, thread_id: int, *,
                  user_msg: dict, history: int = 12,
                  include_synopsis: bool | None = None,
                  include_settings: list[int] | None = None,
                  include_messages: list[int] | None = None,
                  include_structure: bool | None = None,
                  refs: list[str] = [],
                  writing_locale: str = "ko-KR") -> tuple[str, dict]:
    """Build the AI context. None fields keep the legacy behavior.

    Returns (text, info); info["synopsis_safe"] is False when the model
    saw no synopsis or a clipped one, so a full rewrite would lose text.
    """
    en = writing_locale == "en-US"
    no_code = "unnumbered" if en else "번호없음"
    none = "(none)" if en else "(없음)"
    if include_messages is None:
        h = max(1, min(history or 12, 50))
        msgs = ideas.list_messages(conn, thread_id, 200)[-h:]
    else:
        want = set(include_messages)
        msgs = [m for m in ideas.list_messages(conn, thread_id, 10000)
                if m["id"] in want and m["id"] != user_msg["id"]]
        msgs = msgs + [user_msg]  # the question itself is always included
    syn = ideas.get_synopsis(conn, work["id"])
    struct = ideas.parse_structure(work["structure_json"])
    all_settings = ideas.list_settings(conn, work["id"])
    parts = [(f"Work: {work['title']}" if en
              else f"작품: {work['title']}")]
    if include_structure is None:
        if en:
            parts.append(f"Current structure: {work['structure_json']}")
        else:
            parts.append(f"현재 구조 결정: {work['structure_json']}")
    elif include_structure:
        if en:
            plines = [f"Part {'enabled' if struct['use_part'] else 'disabled'}, "
                      f"Chapter {'enabled' if struct['use_chapter'] else 'disabled'}, "
                      f"Total {struct['total_episodes']} episodes, "
                      f"{len(struct['parts'])} Parts"]
        else:
            plines = [f"Part {'사용' if struct['use_part'] else '미사용'}, "
                      f"Chapter {'사용' if struct['use_chapter'] else '미사용'}, "
                      f"총 {struct['total_episodes']}화, "
                      f"Part {len(struct['parts'])}개"]
        plines += [f"- {p['title']}: {p['summary']}" for p in struct["parts"]]
        if en:
            parts.append("[Included work structure]:\n" + "\n".join(plines))
        else:
            parts.append("[포함된 작품 구조]:\n" + "\n".join(plines))
    else:
        parts.append("[Work structure excluded]" if en else "[작품 구조 미포함]")
    syn_text = syn["content"] or ""
    if include_synopsis is False:
        parts.append("[Full synopsis excluded]" if en else "[전체 줄거리 미포함]")
        syn_safe = not syn_text  # nothing to lose on an empty synopsis
    else:
        shown, cut = _clip(syn_text, CTX_SYNOPSIS, writing_locale)
        if en:
            label = (f"Current full synopsis (v{syn['version']}):"
                     if include_synopsis is None
                     else "[Included full synopsis]:")
        else:
            label = (f"현재 전체 줄거리(v{syn['version']}):" if include_synopsis
                     is None else "[포함된 전체 줄거리]:")
        if cut:
            label += (" (tail clipped: no full-synopsis rewrite)" if en
                      else " (뒷부분 생략됨: 줄거리 전문 개정 금지)")
        parts.append(f"{label}\n{shown}")
        syn_safe = not cut
    skip: set[int] = set()
    if include_settings is None:
        picked = all_settings[:30]
        parts.append("Settings:" if en else "설정정보:")
        parts += [f"- {s['code'] or no_code} [{s['category']}] {s['title']}:"
                  f" {s['summary']}" for s in picked] or [none]
    else:
        want_ids = set(include_settings)
        picked = [s for s in all_settings if s["id"] in want_ids]
        parts.append("[Included settings]:" if en else "[포함된 설정정보]:")
        parts += _clip_lines([_setting_line(s, True, writing_locale)
                              for s in picked],
                             CTX_SETTINGS, writing_locale) or \
            [("(nothing selected)" if en else "(선택 없음)")]
        rest = [s for s in all_settings if s["id"] not in want_ids]
        if rest:
            if en:
                parts.append("[Other existing settings (titles only)]:")
            else:
                parts.append("[다른 기존 설정 목록(제목만)]:")
            parts += _clip_lines([_setting_line(s, False, writing_locale)
                                  for s in rest[:INDEX_MAX]], CTX_INDEX,
                                 writing_locale)
    skip = {s["id"] for s in picked}
    if refs:
        sl, nl, unk = _resolve_refs(conn, work["id"], list(refs), skip)
        if sl or nl or unk:
            parts.append("[@Mentions]:" if en else "[@언급]:")
            parts += sl + nl
            if unk:
                if en:
                    parts.append(f"(unresolvable: {', '.join(unk)})")
                else:
                    parts.append(f"(확인 불가: {', '.join(unk)})")
    author = "Author" if en else "작가"
    convo = []
    for m in msgs[:-1]:
        if m["role"] == "user":
            convo.append(f"{author}: {m['content']}")
        else:
            convo.append(f"AI: {m['content']}"
                         + _proposal_digest(m, all_settings, syn, struct,
                                            writing_locale))
    # Older turns go first when over budget; the latest question stays whole.
    convo.reverse()
    convo = _clip_lines(convo, CTX_CONVO, writing_locale)
    convo.reverse()
    if msgs:
        last = msgs[-1]
        who = author if last["role"] == "user" else "AI"
        digest = "" if last["role"] == "user" else \
            _proposal_digest(last, all_settings, syn, struct, writing_locale)
        convo.append(
            f"{who}: {_clip(last['content'], CTX_LAST, writing_locale)[0]}"
            f"{digest}")
    if en:
        convo_label = ("Recent conversation:" if include_messages is None
                       else "[Included conversation]:")
    else:
        convo_label = ("최근 대화:" if include_messages is None else "[포함된 대화]:")
    parts += ["", convo_label] + (convo or [none])
    return "\n".join(parts), {"synopsis_safe": syn_safe}


@router.post("/threads/{thread_id}/messages")
def post_message(thread_id: int, body: MessageIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 meta=Depends(get_meta),
                  author: dict = Depends(get_current_author)):
    t = _thread(conn, thread_id, author["id"])
    w = _work(conn, t["work_id"], author["id"])
    _unlocked(w)
    content = (body.content or "").strip()
    if not content:
        raise HTTPException(400, "content must not be blank")
    writing_locale, market = _work_locale(w)
    user_msg = ideas.add_message(conn, thread_id, "user", content, body.refs)
    ctx, info = _chat_context(
        conn, w, thread_id, user_msg=user_msg,
        history=body.history or 12,
        include_synopsis=body.include_synopsis,
        include_settings=body.include_settings,
        include_messages=body.include_messages,
        include_structure=body.include_structure, refs=body.refs,
        writing_locale=writing_locale)
    try:
        system = prompt_loader.load(
            "ideas_chat", writing_locale, market).text
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    preset_id, preset_version = metac.preset_ref(conn, w["id"])
    try:
        out = metac.chat_with_provenance(
            meta, bundle="idea_chat", episode=None, system=system,
            work_id=w["id"], user=ctx, schema=sch.IdeaChatOut,
            max_tokens=IDEA_MAX_TOKENS,
            writing_locale=writing_locale, market=market,
            prompt_feature="ideas_chat",
            preset_id=preset_id, preset_version=preset_version)
    except LLMStepError as e:
        raise HTTPException(502, f"idea_chat failed: {e.reason} {e.detail}")
    settings = ideas.list_settings(conn, w["id"])
    sps = _normalize_setting_proposals(out.get("settings", []), settings,
                                       writing_locale)
    syn_prop = out.get("synopsis_proposal", "")
    # A full rewrite from a missing/clipped synopsis would delete text.
    syn_blocked = bool(syn_prop) and not info["synopsis_safe"]
    assistant = ideas.add_message(
        conn, thread_id, "assistant", out.get("reply", ""),
        [], out.get("choices", []),
        {"settings": sps,
         "merge_from": _normalize_merge(out.get("merge_from"), sps, settings),
         "synopsis_proposal": "" if syn_blocked else syn_prop,
         "synopsis_blocked": syn_blocked,
         "structure": out.get("structure")})
    touch_work(conn, w["id"])
    conn.commit()
    return {"user": user_msg, "assistant": assistant}


# ---------------------------------------------------------------- synopsis

@router.get("/works/{key}/synopsis")
def get_synopsis(key: str, conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
    return ideas.get_synopsis(conn, w["id"])


class SynopsisIn(BaseModel):
    content: str


@router.put("/works/{key}/synopsis")
def put_synopsis(key: str, body: SynopsisIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
    _unlocked(w)
    return ideas.update_synopsis(conn, w["id"], body.content)


@router.get("/works/{key}/synopsis/revisions")
def synopsis_revisions(key: str,
                       conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
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
def get_structure(key: str, conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
    return ideas.parse_structure(w["structure_json"])


@router.put("/works/{key}/structure")
def put_structure(key: str, body: StructureIn,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
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
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
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
                   conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
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
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    row = conn.execute("SELECT * FROM setting_entries WHERE id=?",
                       (setting_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"setting {setting_id} not found")
    w = _work(conn, row["work_id"], author["id"])
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
                   conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    row = conn.execute("SELECT * FROM setting_entries WHERE id=?",
                       (setting_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"setting {setting_id} not found")
    w = _work(conn, row["work_id"], author["id"])
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
            meta=Depends(get_meta),
                  author: dict = Depends(get_current_author)):
    w = _work(conn, key, author["id"])
    _unlocked(w)
    thread_id = body.thread_id
    if thread_id is None:
        threads = ideas.list_threads(conn, w["id"])
        if not threads:
            raise HTTPException(400, "no conversation yet")
        thread_id = threads[-1]["id"]
    else:
        _thread(conn, thread_id, author["id"])
    msgs = ideas.list_messages(conn, thread_id, 200)[-body.limit:]
    writing_locale, market = _work_locale(w)
    en = writing_locale == "en-US"
    author = "Author" if en else "작가"
    no_code = "unnumbered" if en else "번호없음"
    syn = ideas.get_synopsis(conn, w["id"])
    settings = ideas.list_settings(conn, w["id"])[:30]
    lines = [f"{author if m['role'] == 'user' else 'AI'}: {m['content']}"
             for m in msgs][::-1]
    convo = "\n".join(_clip_lines(lines, CTX_CONVO,
                                  writing_locale)[::-1]) or \
        ("(no conversation)" if en else "(대화 없음)")
    if en:
        user = (f"Work: {w['title']}\n"
                f"Current structure: {w['structure_json']}\n"
                f"Current full synopsis (v{syn['version']}):\n"
                f"{_clip(syn['content'], CTX_SYNOPSIS, writing_locale)[0]}\n"
                f"Current settings:\n" + ("\n".join(_clip_lines(
                    [f"- {s['code'] or no_code} [{s['category']}]"
                     f" {s['title']}: {s['summary']}" for s in settings],
                    CTX_SETTINGS, writing_locale))
                    or "(none)") +
                f"\n\nConversation to analyze:\n{convo}")
    else:
        user = (f"작품: {w['title']}\n현재 구조 결정: {w['structure_json']}\n"
                f"현재 전체 줄거리(v{syn['version']}):\n"
                f"{_clip(syn['content'], CTX_SYNOPSIS)[0]}\n"
                f"현재 설정정보:\n" + ("\n".join(_clip_lines(
                    [f"- {s['code'] or '번호없음'} [{s['category']}] {s['title']}:"
                     f" {s['summary']}" for s in settings], CTX_SETTINGS))
                    or "(없음)") +
                f"\n\n분석할 대화:\n{convo}")
    try:
        system = prompt_loader.load(
            "ideas_extract", writing_locale, market).text
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    preset_id, preset_version = metac.preset_ref(conn, w["id"])
    try:
        return metac.chat_with_provenance(
            meta, bundle="idea_extract", episode=None, system=system,
            user=user, schema=sch.ExtractOut, max_tokens=IDEA_MAX_TOKENS,
            work_id=w["id"], writing_locale=writing_locale, market=market,
            prompt_feature="ideas_extract",
            preset_id=preset_id, preset_version=preset_version)
    except LLMStepError as e:
        raise HTTPException(502, f"idea_extract failed: {e.reason} {e.detail}")


# ---------------------------------------------------------------- handoff

def _handoff_user_text(w: dict, structure: dict, syn: dict,
                       settings: list,
                       writing_locale: str = "ko-KR") -> str:
    en = writing_locale == "en-US"
    if en:
        level_names = ((["Part"] if structure["use_part"] else []) +
                       (["Chapter"] if structure["use_chapter"] else []) +
                       ["Episode"])
    else:
        level_names = ((["Part(부)"] if structure["use_part"] else []) +
                       (["Chapter(장)"] if structure["use_chapter"] else []) +
                       ["Episode(화)"])
    levels = " / ".join(level_names)
    no_code = "unnumbered" if en else "번호없음"
    parts_txt = "\n".join(f"- {p['title']}: {p['summary']}"
                          for p in structure["parts"]) or \
        ("(Parts undecided)" if en else "(Part 미정)")
    settings_txt = "\n".join(
        f"- [{s['code'] or no_code}|{s['category']}] {s['title']}:"
        f" {s['summary']}" + (f" / {s['detail']}" if s["detail"] else "")
        for s in settings) or ("(no settings)" if en else "(설정 없음)")
    if en:
        return (f"Work: {w['title']}\nWork structure: {levels},"
                f" {structure['total_episodes']} episodes total\n"
                f"Parts:\n{parts_txt}\n\nFull synopsis:\n"
                f"{syn['content'] or '(none)'}\n\nSettings:\n"
                f"{settings_txt}")[:12000]
    return (f"작품: {w['title']}\n작품 구조: {levels},"
            f" 총 {structure['total_episodes']}화\n"
            f"Part 목록:\n{parts_txt}\n\n전체 줄거리:\n"
            f"{syn['content'] or '(없음)'}\n\n설정정보:\n{settings_txt}")[:12000]


@router.get("/works/{key}/handoff/preview")
def handoff_preview(key: str,
                    conn: sqlite3.Connection = Depends(get_conn),
                    meta=Depends(get_meta),
                  author: dict = Depends(get_current_author)):
    """AI migration preview: the exact tree confirm will create. No writes."""
    w = _work(conn, key, author["id"])
    if w["ideas_locked"]:
        raise HTTPException(409, "ideas already handed off (read-only)")
    writing_locale, market = _work_locale(w)
    en = writing_locale == "en-US"
    structure = ideas.parse_structure(w["structure_json"])
    syn = ideas.get_synopsis(conn, w["id"])
    settings = ideas.list_settings(conn, w["id"])
    user = _handoff_user_text(w, structure, syn, settings, writing_locale)
    try:
        system = prompt_loader.load(
            "handoff_migrate", writing_locale, market).text
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    preset_id, preset_version = metac.preset_ref(conn, w["id"])
    try:
        plan = metac.chat_with_provenance(
            meta, bundle="handoff_migrate", episode=None, system=system,
            user=user, schema=sch.HandoffOut, max_tokens=16000,
            work_id=w["id"], writing_locale=writing_locale, market=market,
            prompt_feature="handoff_migrate",
            preset_id=preset_id, preset_version=preset_version)
    except LLMStepError as e:
        raise HTTPException(
            502, f"handoff_migrate failed: {e.reason} {e.detail}")
    tree, counts = ideas.build_migration_tree(structure, plan)
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM story_designs"
                       " WHERE work_id=?", (w["id"],)).fetchone()
    if en:
        levels = ((["Part"] if structure["use_part"] else []) +
                  (["Chapter"] if structure["use_chapter"] else []) +
                  ["Episode"])
    else:
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
            conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    """Apply a previewed migration plan (no AI call).

    The stored ideas-phase structure drives the migration; afterwards the
    ideas screen turns read-only.
    """
    w = _work(conn, key, author["id"])
    if w["ideas_locked"]:
        raise HTTPException(409, "ideas already handed off (read-only)")
    try:
        return ideas.handoff_migrate(conn, w["id"], body.model_dump())
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
