"""Consistency analysis: current manuscript vs committed memory.

검토 화면의 일관성 분석 도우미. 이전 회차에서 확정된 기억(Entity)과
현재 원고를 대조해 일관성 오류를 consistency_issues에 구조화 저장한다.
별명·동명이인 의심도 이슈로 등록한다(별도 판정 화면 없음).

채팅은 기억을 직접 쓰지 않는다. 발간요청 시점에 open 이슈가 남아
있으면 차단되고, 작가가 집필 화면에서 수정한 뒤 다시 검토한다.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import memory
from .db import now_iso
from .schemas import consistency as sch

PROMPT = (Path(__file__).parent / "prompts" / "consistency.txt").read_text(encoding="utf-8")
MEMORY_PROMPT = (Path(__file__).parent / "prompts" / "memory_draft.txt").read_text(encoding="utf-8")

SEVERITIES = ("critical", "major", "minor")
STATUSES = ("open", "resolved", "wontfix")


def _memory_context(conn: sqlite3.Connection, episode_no: int,
                   limit_entities: int = 60) -> list[dict]:
    out = []
    for ent in memory.search_entities(conn)[:limit_entities]:
        out.append({
            "entity_id": ent["entity_id"], "name": ent["canonical_name"],
            "type": ent["type"],
            "attributes": memory.reconstruct_state(conn, ent["entity_id"],
                                                   episode_no - 1),
        })
    return out


def _current_manuscript(conn: sqlite3.Connection, episode_no: int,
                       revision_id: int | None) -> str:
    if revision_id:
        row = conn.execute(
            "SELECT text FROM manuscript_revisions WHERE revision_id=?",
            (revision_id,)).fetchone()
        if row:
            return row["text"]
    row = conn.execute(
        "SELECT text FROM manuscript_revisions WHERE episode_no=?"
        " ORDER BY version DESC LIMIT 1", (episode_no,)).fetchone()
    return row["text"] if row else ""


def add_issues(conn: sqlite3.Connection, episode_no: int,
              issues: list[dict]) -> list[dict]:
    saved = []
    for it in issues or []:
        sev = it.get("severity", "major")
        if sev not in SEVERITIES:
            sev = "major"
        cur = conn.execute(
            "INSERT INTO consistency_issues(episode_no, severity, title, detail,"
            " manuscript_quote, evidence_episode, evidence_quote, entity_id,"
            " status, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (episode_no, sev, it.get("title", "")[:200],
             it.get("detail", "")[:2000],
             it.get("manuscript_quote", "")[:1000],
             it.get("evidence_episode"),
             it.get("evidence_quote", "")[:1000],
             it.get("entity_id", "")[:64], "open", now_iso()))
        saved.append({"issue_id": cur.lastrowid, **it, "status": "open"})
    conn.commit()
    return saved


def list_issues(conn: sqlite3.Connection, episode_no: int,
               status: str | None = None) -> list[dict]:
    sql = "SELECT * FROM consistency_issues WHERE episode_no=?"
    args: list = [episode_no]
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY issue_id"
    return [dict(r) for r in conn.execute(sql, args)]


def open_count(conn: sqlite3.Connection, episode_no: int) -> int:
    row = conn.execute("SELECT COUNT(*) c FROM consistency_issues"
                       " WHERE episode_no=? AND status='open'",
                       (episode_no,)).fetchone()
    return row["c"]


def set_issue_status(conn: sqlite3.Connection, issue_id: int,
                    status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status}")
    row = conn.execute("SELECT * FROM consistency_issues WHERE issue_id=?",
                       (issue_id,)).fetchone()
    if not row:
        raise KeyError(f"issue {issue_id} not found")
    conn.execute("UPDATE consistency_issues SET status=?,"
                 " resolved_at=CASE WHEN ?='open' THEN NULL ELSE ? END"
                 " WHERE issue_id=?",
                 (status, status, now_iso(), issue_id))
    conn.commit()
    out = dict(conn.execute("SELECT * FROM consistency_issues WHERE issue_id=?",
                            (issue_id,)).fetchone())
    return out


def list_messages(conn: sqlite3.Connection, episode_no: int,
                 limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT * FROM review_messages WHERE episode_no=?"
                        " ORDER BY id DESC LIMIT ?", (episode_no, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _add_message(conn: sqlite3.Connection, episode_no: int, role: str,
                content: str) -> None:
    conn.execute("INSERT INTO review_messages(episode_no, role, content,"
                 " created_at) VALUES(?,?,?,?)",
                 (episode_no, role, content, now_iso()))
    conn.commit()


def get_memory_draft(conn: sqlite3.Connection, episode_no: int) -> dict:
    row = conn.execute("SELECT * FROM episode_memory_drafts WHERE episode_no=?",
                       (episode_no,)).fetchone()
    return dict(row) if row else {"episode_no": episode_no, "content": ""}


def save_memory_draft(conn: sqlite3.Connection, episode_no: int,
                     content: str) -> dict:
    conn.execute("INSERT INTO episode_memory_drafts(episode_no, content,"
                 " updated_at) VALUES(?,?,?)"
                 " ON CONFLICT(episode_no) DO UPDATE SET content=excluded.content,"
                 " updated_at=excluded.updated_at",
                 (episode_no, content, now_iso()))
    conn.commit()
    return get_memory_draft(conn, episode_no)


def generate_memory_draft(meta, conn: sqlite3.Connection, *,
                         episode_no: int, revision_id: int | None,
                         work_id: int | None = None,
                         max_tokens: int = 4000) -> dict:
    """프리셋 작업: 다음 화 인계용 기억 초안을 프롬프트 없이 생성한다."""
    manuscript = _current_manuscript(conn, episode_no, revision_id)
    if not manuscript.strip():
        raise ValueError("no manuscript to summarize")
    out = meta.chat_json(
        bundle="memory-draft", episode=episode_no, system=MEMORY_PROMPT,
        work_id=work_id,
        user="다음 화 집필에 꼭 필요한 정보를 정리하라. 스켈레톤: "
             '{"memory_draft": str}\n'
             + json.dumps({"episode_no": episode_no,
                           "manuscript": manuscript,
                           "memory": _memory_context(conn, episode_no)},
                          ensure_ascii=False),
        schema=sch.MemoryDraftOutput, max_tokens=max_tokens)
    content = (out.get("memory_draft") or "").strip()
    if not content:
        raise ValueError("empty memory draft")
    return save_memory_draft(conn, episode_no, content)


def analyze_episode(meta, conn: sqlite3.Connection, *, episode_no: int,
                   revision_id: int | None, work_id: int | None = None,
                   max_tokens: int = 8000) -> dict:
    """원고 전체를 기억과 대조해 이슈 목록 + 기억 초안을 만든다."""
    manuscript = _current_manuscript(conn, episode_no, revision_id)
    if not manuscript.strip():
        raise ValueError("no manuscript to analyze")
    out = meta.chat_json(
        bundle="consistency", episode=episode_no, system=PROMPT,
        work_id=work_id,
        user="현재 원고를 확정 기억과 대조해 일관성 오류를 찾아라. 스켈레톤: "
             '{"issues": [{"severity": str, "title": str, "detail": str, '
             '"manuscript_quote": str, "evidence_episode": int|null, '
             '"evidence_quote": str, "entity_id": str}], "memory_draft": str}\n'
             + json.dumps({"episode_no": episode_no,
                           "manuscript": manuscript,
                           "memory": _memory_context(conn, episode_no)},
                          ensure_ascii=False),
        schema=sch.ConsistencyOutput, max_tokens=max_tokens)
    saved = add_issues(conn, episode_no, out.get("issues", []))
    draft = None
    if (out.get("memory_draft") or "").strip():
        draft = save_memory_draft(conn, episode_no, out["memory_draft"])
    return {"episode_no": episode_no, "issues": saved,
            "memory_draft": draft or get_memory_draft(conn, episode_no)}


def chat(meta, conn: sqlite3.Connection, *, episode_no: int,
        revision_id: int | None, message: str,
        work_id: int | None = None, max_tokens: int = 4000) -> dict:
    """도우미 대화 1턴. 답변과 함께 새로 찾은 이슈를 등록한다."""
    message = (message or "").strip()
    if not message:
        raise ValueError("message must not be blank")
    manuscript = _current_manuscript(conn, episode_no, revision_id)
    history = [{"role": m["role"], "content": m["content"]}
               for m in list_messages(conn, episode_no, limit=10)]
    out = meta.chat_json(
        bundle="consistency-chat", episode=episode_no, system=PROMPT,
        work_id=work_id,
        user="작가의 질문에 답하고, 새로 발견한 일관성 오류가 있으면 issues에"
             " 등록하라. 스켈레톤: "
             '{"reply": str, "issues": [{"severity": str, "title": str, '
             '"detail": str, "manuscript_quote": str, '
             '"evidence_episode": int|null, "evidence_quote": str, '
             '"entity_id": str}]}\n'
             + json.dumps({"episode_no": episode_no, "question": message,
                           "history": history, "manuscript": manuscript,
                           "memory": _memory_context(conn, episode_no)},
                          ensure_ascii=False),
        schema=sch.ConsistencyChatOutput, max_tokens=max_tokens)
    _add_message(conn, episode_no, "user", message)
    _add_message(conn, episode_no, "assistant", out.get("reply", ""))
    saved = add_issues(conn, episode_no, out.get("issues", []))
    return {"episode_no": episode_no, "reply": out.get("reply", ""),
            "issues": saved}
