"""Consistency analysis: current manuscript vs committed memory (§6).

검토 화면의 일관성 분석 도우미. 이전 회차에서 확정된 기억(Entity)과
현재 원고를 대조해 일관성 이슈를 consistency_issues에 구조화 저장한다.

분석은 확정 검사(규칙 기반 후보)와 AI 의미 비교를 병행한다. 분석 판정
verdict(conflict/explained/uncertain)와 작가 처리 status(open/fixed/
accepted/dismissed)는 분리된다. 도구가 실패하면 '이상 없음'으로
표시하지 않고 분석 실패로 기록한다.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from . import checks, citation, memory
from .db import now_iso
from .errors import LLMStepError
from .schemas import consistency as sch

PROMPT = (Path(__file__).parent / "prompts" / "consistency.txt").read_text(encoding="utf-8")
PROMPT_VERSION = "consistency-v2"

SEVERITIES = ("critical", "major", "minor")
VERDICTS = ("conflict", "explained", "uncertain")
STATUSES = ("open", "fixed", "accepted", "dismissed")
LEGACY_STATUS = {"resolved": "fixed", "wontfix": "dismissed"}


def _memory_context(conn: sqlite3.Connection, episode_no: int, work_id: int,
                    limit_entities: int = 60) -> dict:
    entities = []
    for ent in memory.search_entities(conn, work_id=work_id)[:limit_entities]:
        entities.append({
            "entity_id": ent["entity_id"], "name": ent["canonical_name"],
            "type": ent["type"],
            "attributes": memory.reconstruct_state(conn, ent["entity_id"],
                                                   episode_no - 1, work_id),
        })
    rules = [{
        "entity_id": r["entity_id"], "name": r["canonical_name"],
        "predicate": r["attribute_path"],
        "standard_value": memory._u(r["standard_value"]),
        "display": memory._u(r["new_value"]),
        "story_time": r["story_time"], "episode_no": r["episode_no"],
    } for r in memory.committed_rules(conn, work_id, episode_no - 1)]
    ev_rows = conn.execute(
        "SELECT episode_no, summary, story_time FROM events"
        " WHERE work_id=? AND episode_no<? ORDER BY episode_no DESC LIMIT 30",
        (work_id, episode_no)).fetchall()
    relations = [{
        "subject": r["subject_id"], "predicate": r["predicate"],
        "object": r["object_id"], "from_episode": r["valid_from_episode"],
    } for r in memory.active_relations(conn, work_id, episode_no - 1)]
    return {"entities": entities, "rules": rules,
            "recent_events": [dict(r) for r in ev_rows],
            "relations": relations}


def _current_manuscript(conn: sqlite3.Connection, episode_no: int,
                        revision_id: int | None, work_id: int) -> str:
    if revision_id:
        row = conn.execute(
            "SELECT text FROM manuscript_revisions WHERE revision_id=?"
            " AND work_id=?", (revision_id, work_id)).fetchone()
        if row:
            return row["text"]
    row = conn.execute(
        "SELECT text FROM manuscript_revisions WHERE work_id=? AND episode_no=?"
        " ORDER BY version DESC LIMIT 1", (work_id, episode_no)).fetchone()
    return row["text"] if row else ""


def _approved_corpus(conn: sqlite3.Connection, episode_no: int,
                   work_id: int) -> str:
    rows = conn.execute(
        "SELECT text FROM manuscript_revisions WHERE work_id=? AND episode_no<=?"
        " AND approval_status='approved' ORDER BY episode_no, version",
        (work_id, episode_no)).fetchall()
    return "\n".join(r["text"] for r in rows)


def add_issues(conn: sqlite3.Connection, episode_no: int, issues: list[dict],
               work_id: int, revision_id: int | None = None,
               manuscript: str = "", basis_version: int = 0) -> dict:
    """Register issues. Quotes are citation-verified; quoteless dropped."""
    saved = []
    dropped = 0
    corpus = _approved_corpus(conn, episode_no, work_id) if manuscript else ""
    for it in issues or []:
        sev = it.get("severity", "major")
        if sev not in SEVERITIES:
            sev = "major"
        verdict = it.get("verdict", "uncertain")
        if verdict not in VERDICTS:
            verdict = "uncertain"
        mq = it.get("manuscript_quote", "") or ""
        if manuscript:
            chk = citation.check_quote(manuscript, mq)
            if not mq.strip() or not chk["ok"]:
                dropped += 1  # no unverified current-manuscript anchor
                continue
            ms, me = chk["start"], chk["end"]
        else:
            ms, me = None, None
        eq = it.get("evidence_quote", "") or ""
        es, ee = None, None
        if eq.strip() and corpus:
            echk = citation.check_quote(corpus, eq)
            es, ee = echk["start"], echk["end"]
        refs = it.get("fact_refs") or it.get("fact_refs_json") or []
        if isinstance(refs, str):
            try:
                refs = json.loads(refs)
            except ValueError:
                refs = []
        cur = conn.execute(
            "INSERT INTO consistency_issues(work_id, episode_no, severity,"
            " title, detail, verdict, manuscript_revision_id, manuscript_quote,"
            " manuscript_start, manuscript_end, evidence_episode,"
            " evidence_quote, evidence_start, evidence_end, entity_id,"
            " fact_refs_json, basis_memory_version, status, created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (work_id, episode_no, sev, it.get("title", "")[:200],
             it.get("detail", "")[:2000], verdict, revision_id, mq[:1000],
             ms, me, it.get("evidence_episode"), eq[:1000], es, ee,
             it.get("entity_id", "")[:64], json.dumps(list(refs or [])),
             basis_version, "open", now_iso()))
        row = conn.execute("SELECT * FROM consistency_issues WHERE issue_id=?",
                           (cur.lastrowid,)).fetchone()
        saved.append(dict(row))
    conn.commit()
    return {"issues": saved, "dropped": dropped}


def list_issues(conn: sqlite3.Connection, episode_no: int,
                status: str | None = None,
                work_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM consistency_issues WHERE episode_no=?"
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    sql += " ORDER BY issue_id"
    return [dict(r) for r in conn.execute(sql, args)]


def open_count(conn: sqlite3.Connection, episode_no: int,
               work_id: int | None = None) -> int:
    sql = ("SELECT COUNT(*) c FROM consistency_issues"
           " WHERE episode_no=? AND status='open'")
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    return conn.execute(sql, args).fetchone()["c"]


def set_issue_status(conn: sqlite3.Connection, issue_id: int,
                     status: str) -> dict:
    status = LEGACY_STATUS.get(status, status)
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
                  limit: int = 50, work_id: int | None = None) -> list[dict]:
    sql = "SELECT * FROM review_messages WHERE episode_no=?"
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    rows = conn.execute(sql + " ORDER BY id DESC LIMIT ?",
                        (*args, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def _add_message(conn: sqlite3.Connection, episode_no: int, role: str,
                 content: str, work_id: int) -> None:
    conn.execute("INSERT INTO review_messages(work_id, episode_no, role, content,"
                 " created_at) VALUES(?,?,?,?,?)",
                 (work_id, episode_no, role, content, now_iso()))
    conn.commit()


def record_analysis(conn, *, work_id, episode_no, revision_id,
                    base_version, status, coverage=None, counts=None,
                    error="") -> dict:
    cur = conn.execute(
        "INSERT INTO consistency_analyses(work_id, episode_no,"
        " manuscript_revision_id, base_memory_version, status, coverage_json,"
        " counts_json, error, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (work_id, episode_no, revision_id, base_version, status,
         json.dumps(coverage or {}), json.dumps(counts or {}),
         error[:1000], now_iso()))
    conn.commit()
    return dict(conn.execute("SELECT * FROM consistency_analyses"
                             " WHERE analysis_id=?",
                             (cur.lastrowid,)).fetchone())


def latest_analysis(conn, episode_no: int,
                    work_id: int | None = None) -> dict | None:
    sql = "SELECT * FROM consistency_analyses WHERE episode_no=?"
    args: list = [episode_no]
    if work_id is not None:
        sql += " AND work_id=?"
        args.append(work_id)
    sql += " ORDER BY analysis_id DESC LIMIT 1"
    row = conn.execute(sql, args).fetchone()
    return dict(row) if row else None


def analyze_episode(meta, conn: sqlite3.Connection, *, episode_no: int,
                    revision_id: int | None, work_id: int,
                    max_tokens: int = 8000) -> dict:
    """원고 전체를 확정 기억과 대조해 일관성 이슈 목록을 만든다.

    확정 검사(규칙 기반)를 먼저 실행하고, AI 의미 비교를 병행한다.
    AI 호출이 실패해도 확정 검사 결과는 저장하고 분석을 partial로
    기록한다. 실패를 '이상 없음'으로 표시하지 않는다.
    """
    manuscript = _current_manuscript(conn, episode_no, revision_id, work_id)
    if not manuscript.strip():
        raise ValueError("no manuscript to analyze")
    base = memory.current_memory_version(conn, work_id)
    pset = memory.latest_pending_set(conn, episode_no, work_id)
    det = checks.run_deterministic_checks(
        conn, work_id, episode_no,
        pset["proposal_set_id"] if pset else None)
    coverage = {
        "manuscript_chars": len(manuscript),
        "pending_set": pset["proposal_set_id"] if pset else None,
        "rule_checks": det["ran"],
        "memory_entities": len(memory.search_entities(conn, work_id=work_id)),
    }
    try:
        out = meta.chat_json(
            bundle="consistency", episode=episode_no, system=PROMPT,
            work_id=work_id,
            user="현재 원고를 확정 기억과 대조해 일관성 오류를 찾아라. 스켈레톤: "
                 '{"issues": [{"severity": str, "title": str, "detail": str, '
                 '"verdict": str, "manuscript_quote": str, '
                 '"evidence_episode": int|null, "evidence_quote": str, '
                 '"entity_id": str, "fact_refs": [int]}]}\n'
                 + json.dumps({"episode_no": episode_no,
                               "manuscript": manuscript,
                               "memory": _memory_context(conn, episode_no,
                                                         work_id)},
                              ensure_ascii=False),
            schema=sch.ConsistencyOutput, max_tokens=max_tokens)
    except LLMStepError as e:
        kept = add_issues(conn, episode_no, det["candidates"], work_id,
                          revision_id, manuscript, base)
        analysis = record_analysis(
            conn, work_id=work_id, episode_no=episode_no,
            revision_id=revision_id, base_version=base, status="partial",
            coverage={**coverage, "ai_pass": "failed"},
            counts={"rule": len(kept["issues"]),
                    "dropped": kept["dropped"]},
            error=f"{e.reason}: {e.detail}")
        return {"episode_no": episode_no, "issues": kept["issues"],
                "partial": True, "error": f"{e.reason}: {e.detail}",
                "analysis": analysis}
    merged = list(det["candidates"])
    seen = {(c.get("entity_id"), c.get("title")) for c in merged}
    ai_issues = []
    for it in out.get("issues", []):
        if (it.get("entity_id"), it.get("title")) in seen:
            continue  # deterministic candidate already covers it
        ai_issues.append(it)
    kept = add_issues(conn, episode_no, merged + ai_issues, work_id,
                      revision_id, manuscript, base)
    analysis = record_analysis(
        conn, work_id=work_id, episode_no=episode_no,
        revision_id=revision_id, base_version=base, status="completed",
        coverage={**coverage, "ai_pass": "completed"},
        counts={"rule": len(det["candidates"]), "ai": len(ai_issues),
                "saved": len(kept["issues"]), "dropped": kept["dropped"]})
    return {"episode_no": episode_no, "issues": kept["issues"],
            "analysis": analysis}


def chat(meta, conn: sqlite3.Connection, *, episode_no: int,
         revision_id: int | None, message: str, work_id: int,
         max_tokens: int = 4000) -> dict:
    """도우미 대화 1턴. 답변과 함께 새로 찾은 이슈를 등록한다."""
    message = (message or "").strip()
    if not message:
        raise ValueError("message must not be blank")
    manuscript = _current_manuscript(conn, episode_no, revision_id, work_id)
    base = memory.current_memory_version(conn, work_id)
    history = [{"role": m["role"], "content": m["content"]}
               for m in list_messages(conn, episode_no, limit=10,
                                      work_id=work_id)]
    out = meta.chat_json(
        bundle="consistency-chat", episode=episode_no, system=PROMPT,
        work_id=work_id,
        user="작가의 질문에 답하고, 새로 발견한 일관성 오류가 있으면 issues에"
             " 등록하라. 스켈레톤: "
             '{"reply": str, "issues": [{"severity": str, "title": str, '
             '"detail": str, "verdict": str, "manuscript_quote": str, '
             '"evidence_episode": int|null, "evidence_quote": str, '
             '"entity_id": str, "fact_refs": [int]}]}\n'
             + json.dumps({"episode_no": episode_no, "question": message,
                           "history": history, "manuscript": manuscript,
                           "memory": _memory_context(conn, episode_no,
                                                     work_id)},
                          ensure_ascii=False),
        schema=sch.ConsistencyChatOutput, max_tokens=max_tokens)
    _add_message(conn, episode_no, "user", message, work_id)
    _add_message(conn, episode_no, "assistant", out.get("reply", ""), work_id)
    kept = add_issues(conn, episode_no, out.get("issues", []), work_id,
                      revision_id, manuscript, base)
    return {"episode_no": episode_no, "reply": out.get("reply", ""),
            "issues": kept["issues"], "dropped": kept["dropped"]}
