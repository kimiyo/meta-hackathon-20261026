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
import uuid
from pathlib import Path

from . import checks, citation, manuscripts, memory, state_machine
from .db import now_iso
from .errors import LLMStepError
from .schemas import consistency as sch

PROMPT = (Path(__file__).parent / "prompts" / "consistency.txt").read_text(encoding="utf-8")
FIX_PROMPT = (Path(__file__).parent / "prompts" / "manuscript_fix.txt").read_text(encoding="utf-8")
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


def _basis_present(prev: dict | None, mem: dict) -> bool:
    return prev is not None or bool(
        mem["entities"] or mem["rules"] or mem["recent_events"])


def has_comparison_basis(conn: sqlite3.Connection, episode_no: int,
                         work_id: int) -> bool:
    """비교 근거(직전화 원문 또는 그 이전 확정 기억)가 있는지.

    analyze_episode의 skip 분기와 동일한 조건이다. EP 1처럼 비교
    대상이 없을 때 UI가 분석 실행 전에 미리 안내하는 데 쓴다.
    """
    return _basis_present(prev_manuscript(conn, episode_no, work_id),
                          _memory_context(conn, episode_no, work_id))


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


def prev_manuscript(conn: sqlite3.Connection, episode_no: int,
                    work_id: int) -> dict | None:
    """직전화 원문 (1단계 넓은 읽기용).

    발간된(approved) 본이 있으면 그것을, 없으면 최신 본을 읽는다.
    """
    if episode_no <= 1:
        return None
    row = conn.execute(
        "SELECT revision_id, version, text FROM manuscript_revisions"
        " WHERE work_id=? AND episode_no=? AND approval_status='approved'"
        " ORDER BY version DESC LIMIT 1",
        (work_id, episode_no - 1)).fetchone()
    approved = True
    if not row:
        row = conn.execute(
            "SELECT revision_id, version, text FROM manuscript_revisions"
            " WHERE work_id=? AND episode_no=? ORDER BY version DESC LIMIT 1",
            (work_id, episode_no - 1)).fetchone()
        approved = False
    if not row or not (row["text"] or "").strip():
        return None
    return {"episode_no": episode_no - 1, "revision_id": row["revision_id"],
            "version": row["version"], "text": row["text"],
            "approved": approved}


def homonym_candidates(conn: sqlite3.Connection, work_id: int,
                       pending_items: list[dict],
                       writing_locale: str = "ko-KR") -> list[dict]:
    """미해결 후보명이 기존 이름·확정 별명과 겹치면 동명이인 의심 후보.

    자동 합치지 않고 작가 판단을 묻는다.
    """
    out = []
    seen: set[str] = set()
    en = writing_locale == "en-US"
    for it in pending_items or []:
        if it.get("entity_id"):
            continue
        name = (it.get("candidate_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        matches = memory.find_entities_by_name_or_alias(conn, work_id, name)
        if not matches:
            continue
        who = ", ".join(f"{m['canonical_name']}({m['entity_id']})"
                        for m in matches[:3])
        if en:
            title = f"Possible homonym: {name}"
            detail = (f"'{name}' overlaps the existing name {who}."
                      " Confirm whether it is the same person, a different"
                      " person, or an alias")
        else:
            title = f"동명이인 의심: {name}"
            detail = (f"'{name}'이(가) 기존 {who}와(과) 이름이 겹친다."
                      " 같은 인물인지, 다른 인물인지, 별명인지 확정 필요")
        out.append({
            "check": "homonym", "severity": "major",
            "title": title, "detail": detail,
            "verdict": "uncertain", "entity_id": "",
            "fact_refs": [],
            "manuscript_quote": it.get("evidence_quote", "") or "",
            "evidence_episode": None, "evidence_quote": "",
        })
    return out


def entity_groups(conn: sqlite3.Connection, work_id: int, episode_no: int,
                  pending_items: list[dict],
                  pending_events: list[dict]) -> list[dict]:
    """개체별 정밀 대조 묶음 (2단계): 현재 주장 + 검색된 확정 사실.

    같은 개체(ID·확정 별명 기준)와 관련 개체(관계·공동 사건·공유 규칙)의
    확정 이력을 현재 임시안과 짝지어 AI에 한 번에 준다.
    """
    by_entity: dict[str, dict] = {}

    def _group(eid: str, name: str) -> dict:
        g = by_entity.get(eid or f"name:{name}")
        if g is None:
            g = {"entity_id": eid or "", "name": name or "",
                 "claims": [], "approved_facts": [], "related": {}}
            by_entity[eid or f"name:{name}"] = g
        return g

    for it in pending_items or []:
        g = _group(it.get("entity_id") or "", it.get("candidate_name") or "")
        g["claims"].append({
            "kind": it.get("kind", ""), "operation": it.get("operation", ""),
            "attribute": it.get("attribute_path", ""),
            "value": memory._u(it.get("new_value", "")),
            "standard": memory._u(it.get("standard_value", "")),
            "story_time": it.get("story_time", ""),
            "mode": it.get("narrative_mode", "narration"),
            "quote": (it.get("evidence_quote", "") or "")[:300]})
    for ev in pending_events or []:
        for p in ev.get("participants", []):
            g = _group(p.get("entity_id") or "",
                       p.get("candidate_name") or "")
            g["claims"].append({
                "kind": "event", "summary": (ev.get("summary", "") or "")[:200],
                "story_time": ev.get("story_time", ""),
                "mode": ev.get("narrative_mode", "narration"),
                "role": p.get("role", "")})
    groups = []
    for g in by_entity.values():
        eid = g["entity_id"]
        if eid:
            hist = [h for h in memory.entity_history(conn, eid)
                    if h["episode_no"] < episode_no][-15:]
            g["approved_facts"] = [{
                "episode_no": h["episode_no"], "kind": h["kind"],
                "attribute": h["attribute_path"],
                "value": memory._u(h["new_value"]),
                "standard": memory._u(h["standard_value"]),
                "story_time": h["story_time"],
                "quote": (h["evidence_quote"] or "")[:200]} for h in hist]
            g["aliases"] = memory.confirmed_aliases(conn, eid)
            g["related"] = memory.related_entities(conn, work_id, eid,
                                                   episode_no)
        groups.append(g)
    return groups[:40]


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
        # 설명되는 정상 변화는 기록하되 발간을 막지 않는다.
        status = "accepted" if verdict == "explained" else "open"
        resolved = now_iso() if status == "accepted" else None
        cur = conn.execute(
            "INSERT INTO consistency_issues(work_id, episode_no, severity,"
            " title, detail, verdict, manuscript_revision_id, manuscript_quote,"
            " manuscript_start, manuscript_end, evidence_episode,"
            " evidence_quote, evidence_start, evidence_end, entity_id,"
            " fact_refs_json, basis_memory_version, status, resolved_at,"
            " created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (work_id, episode_no, sev, it.get("title", "")[:200],
             it.get("detail", "")[:2000], verdict, revision_id, mq[:1000],
             ms, me, it.get("evidence_episode"), eq[:1000], es, ee,
             it.get("entity_id", "")[:64], json.dumps(list(refs or [])),
             basis_version, status, resolved, now_iso()))
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


def reset_episode_review(conn: sqlite3.Connection, episode_no: int,
                         work_id: int) -> dict:
    """일관성 점검 초기화: 이슈 전체와 분석 기록을 지운다."""
    cur = conn.execute("DELETE FROM consistency_issues"
                       " WHERE episode_no=? AND work_id=?",
                       (episode_no, work_id))
    issues = cur.rowcount
    cur = conn.execute("DELETE FROM consistency_analyses"
                       " WHERE episode_no=? AND work_id=?",
                       (episode_no, work_id))
    analyses = cur.rowcount
    conn.commit()
    return {"issues": issues, "analyses": analyses}


def _line_span(text: str, start: int | None, end: int | None,
               writing_locale: str = "ko-KR") -> str:
    if start is None:
        return ""
    first = text.count("\n", 0, start) + 1
    last = text.count("\n", 0, end) + 1 if end is not None else first
    if writing_locale == "en-US":
        return f"L{first}" if first == last else f"L{first}-L{last}"
    return f"{first}행" if first == last else f"{first}~{last}행"


def fix_manuscript_issues(meta, conn: sqlite3.Connection, *, episode_no: int,
                          work_id: int, issue_ids: list[int],
                          max_tokens: int = 16000,
                          writing_locale: str | None = None,
                          market: str | None = None) -> dict:
    """선택된 열린 이슈를 AI가 고친 새 원고 버전으로 저장한다.

    AI 호출에 먼저 성공해야 상태 전환·버전 저장·마감을 하므로, AI가
    실패하면 아무것도 바뀌지 않고 그대로 재시도할 수 있다.
    """
    from . import meta_client as metac

    _prompt, writing_locale, market = metac.feature_prompt(
        conn, work_id, "manuscript_fix", writing_locale, market)
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    en = writing_locale == "en-US"
    job = state_machine.get_job(conn, work_id, episode_no)
    if not job:
        raise KeyError(f"episode {episode_no} has no job")
    if job["state"] != "IN_REVIEW":
        if en:
            raise ValueError(
                "manuscript fixes are only allowed while in review")
        raise ValueError("원고 수정은 검토중 상태에서만 가능합니다")
    ids = [int(i) for i in (issue_ids or [])]
    if not ids:
        if en:
            raise ValueError("no issues selected")
        raise ValueError("선택된 이슈가 없습니다")
    rows = {r["issue_id"]: dict(r) for r in conn.execute(
        f"SELECT * FROM consistency_issues WHERE work_id=? AND episode_no=?"
        f" AND issue_id IN ({','.join('?' for _ in ids)})",
        (work_id, episode_no, *ids))}
    missing = [i for i in ids if i not in rows]
    if missing:
        raise KeyError(f"issue {missing[0]} not found")
    targets = [rows[i] for i in ids if rows[i]["status"] == "open"]
    if not targets:
        if en:
            raise ValueError("no open issues selected")
        raise ValueError("선택된 열린 이슈가 없습니다")
    current_rev = job["manuscript_revision_id"]
    manuscript = _current_manuscript(conn, episode_no, current_rev, work_id)
    if not manuscript.strip():
        raise ValueError("no manuscript to fix")
    for t in targets:
        rid = t["manuscript_revision_id"]
        if rid is not None and rid != current_rev:
            if en:
                raise ValueError(
                    "manuscript changed; re-analysis is required")
            raise ValueError("원고가 바뀌어 재분석이 필요합니다")
    items = [{
        "title": t["title"], "detail": t["detail"],
        "verdict": t["verdict"], "quote": t["manuscript_quote"],
        "lines": _line_span(manuscript, t["manuscript_start"],
                            t["manuscript_end"], writing_locale),
        "evidence_episode": t["evidence_episode"],
        "evidence_quote": t["evidence_quote"],
        "entity_id": t["entity_id"],
    } for t in targets]
    if en:
        directive = ("Current manuscript and the contradictions to fix."
                     " Skeleton: "
                     '{"manuscript": str}\n')
    else:
        directive = ("현재 원고와 고칠 모순 목록이다. 스켈레톤: "
                     '{"manuscript": str}\n')
    out = metac.chat_with_provenance(
        meta, bundle="manuscript-fix", episode=episode_no,
        system=_prompt.text, work_id=work_id,
        user=directive + json.dumps(
            {"episode_no": episode_no, "manuscript": manuscript,
             "issues": items}, ensure_ascii=False),
        schema=sch.ManuscriptFixOutput, max_tokens=max_tokens,
        writing_locale=writing_locale, market=market,
        prompt_feature="manuscript_fix",
        preset_id=preset_id, preset_version=preset_version)
    revised = out.get("manuscript") or ""
    if not revised.strip():
        if en:
            raise LLMStepError("manuscript-fix", episode_no, "empty",
                               "The model returned an empty manuscript")
        raise LLMStepError("manuscript-fix", episode_no, "empty",
                           "AI가 빈 원고를 반환했습니다")
    if not len(manuscript) * 0.3 <= len(revised) <= len(manuscript) * 3:
        if en:
            raise LLMStepError("manuscript-fix", episode_no, "truncated",
                               "The revised manuscript has an abnormal length")
        raise LLMStepError("manuscript-fix", episode_no, "truncated",
                           "AI 수정본 길이가 비정상적입니다")
    if en:
        label = f"Consistency AI fix ({len(targets)})"
    else:
        label = f"일관성 {len(targets)}건 AI 수정"
    reason = label + ": " + ", ".join(t["title"] for t in targets)
    state_machine.back_to_writing(conn, work_id, episode_no, reason)
    job = state_machine.get_job(conn, work_id, episode_no)
    row = conn.execute(
        "SELECT title FROM episode_outlines WHERE design_id=? AND episode_no=?",
        (job["design_id"], episode_no)).fetchone()
    doc_row = manuscripts.ensure_doc_row(conn, work_id, episode_no)
    manuscripts.create_version(
        conn, work_id, episode_no, job, row["title"] if row else "",
        doc_row["revision"], f"fix-{episode_no}-{uuid.uuid4().hex}",
        revised, label)
    fixed = []
    for t in targets:
        set_issue_status(conn, t["issue_id"], "fixed")
        fixed.append(t["issue_id"])
    return {"episode_no": episode_no, "fixed_issue_ids": fixed,
            "version_label": label,
            "manuscript_revision_id": job["manuscript_revision_id"]}


def analyze_episode(meta, conn: sqlite3.Connection, *, episode_no: int,
                    revision_id: int | None, work_id: int,
                    max_tokens: int = 8000,
                    writing_locale: str | None = None,
                    market: str | None = None) -> dict:
    """원고 전체를 이전 기억과 대조해 일관성 이슈 목록을 만든다.

    1단계(넓은 읽기): 직전화 원문 + 그 이전 확정 기억을 현재 원고와 대조.
    2단계(정밀 대조): 현재 임시안의 개체별로 같은·관련 개체의 확정 사실을
    검색해 짝지어 대조. 확정 검사(규칙 기반)는 항상 먼저 실행한다.
    비교 대상(직전화·확정 기억)이 없으면 분석을 건너뛴다.
    AI 호출이 실패해도 확정 검사 결과는 저장하고 분석을 partial로
    기록한다. 실패를 '이상 없음'으로 표시하지 않는다.
    """
    from . import meta_client as metac

    _prompt, writing_locale, market = metac.feature_prompt(
        conn, work_id, "consistency", writing_locale, market)
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    en = writing_locale == "en-US"
    manuscript = _current_manuscript(conn, episode_no, revision_id, work_id)
    if not manuscript.strip():
        raise ValueError("no manuscript to analyze")
    base = memory.current_memory_version(conn, work_id)
    prev = prev_manuscript(conn, episode_no, work_id)
    mem = _memory_context(conn, episode_no, work_id)
    if not _basis_present(prev, mem):
        analysis = record_analysis(
            conn, work_id=work_id, episode_no=episode_no,
            revision_id=revision_id, base_version=base, status="skipped",
            coverage={"manuscript_chars": len(manuscript),
                      "reason": ("no prior memory" if en else "이전 기억 없음")},
            counts={"saved": 0})
        return {"episode_no": episode_no, "issues": [], "skipped": True,
                "analysis": analysis}
    pset = memory.latest_pending_set(conn, episode_no, work_id)
    det = checks.run_deterministic_checks(
        conn, work_id, episode_no,
        pset["proposal_set_id"] if pset else None, writing_locale)
    pend_items = [i for i in memory.get_proposals(conn, episode_no, work_id)
                  if pset and i["proposal_set_id"] == pset["proposal_set_id"]]
    pend_events = memory.get_proposal_events(
        conn, pset["proposal_set_id"]) if pset else []
    det_candidates = list(det["candidates"]) + homonym_candidates(
        conn, work_id, pend_items, writing_locale)
    groups = entity_groups(conn, episode_no, work_id, pend_items, pend_events) \
        if pset else []
    coverage = {
        "manuscript_chars": len(manuscript),
        "prev_manuscript": ({k: v for k, v in prev.items() if k != "text"}
                            | {"chars": len(prev["text"])}) if prev else None,
        "pending_set": pset["proposal_set_id"] if pset else None,
        "rule_checks": det["ran"],
        "memory_entities": len(mem["entities"]),
        "entity_groups": len(groups),
    }
    ai_payload = {"episode_no": episode_no, "manuscript": manuscript,
                  "prev_manuscript": prev["text"] if prev else "",
                  "prev_episode_no": prev["episode_no"] if prev else None,
                  "memory": mem, "entity_groups": groups}
    if en:
        directive = ("Compare the current manuscript against the previous"
                     " episode and committed memory to find consistency"
                     " errors. Skeleton: "
                     '{"issues": [{"severity": str, "title": str, '
                     '"detail": str, "verdict": str, "manuscript_quote": str, '
                     '"evidence_episode": int|null, "evidence_quote": str, '
                     '"entity_id": str, "fact_refs": [int]}]}\n')
    else:
        directive = ("현재 원고를 직전화·확정 기억과 대조해 일관성 오류를 찾아라. "
                     "스켈레톤: "
                     '{"issues": [{"severity": str, "title": str, '
                     '"detail": str, "verdict": str, "manuscript_quote": str, '
                     '"evidence_episode": int|null, "evidence_quote": str, '
                     '"entity_id": str, "fact_refs": [int]}]}\n')
    try:
        out = metac.chat_with_provenance(
            meta, bundle="consistency", episode=episode_no,
            system=_prompt.text, work_id=work_id,
            user=directive + json.dumps(ai_payload, ensure_ascii=False),
            schema=sch.ConsistencyOutput, max_tokens=max_tokens,
            writing_locale=writing_locale, market=market,
            prompt_feature="consistency",
            prompt_version=PROMPT_VERSION,
            preset_id=preset_id, preset_version=preset_version)
    except LLMStepError as e:
        kept = add_issues(conn, episode_no, det_candidates, work_id,
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
    seen = {(c.get("entity_id"), c.get("title")) for c in det_candidates}
    seen_refs: set[int] = set()
    for c in det_candidates:
        seen_refs.update(c.get("fact_refs") or [])
    ai_issues = []
    for it in out.get("issues", []):
        if (it.get("entity_id"), it.get("title")) in seen:
            continue  # deterministic candidate already covers it
        if set(it.get("fact_refs") or []) & seen_refs:
            continue  # same approved facts already cited
        ai_issues.append(it)
    kept = add_issues(conn, episode_no, det_candidates + ai_issues, work_id,
                      revision_id, manuscript, base)
    accepted = sum(1 for i in kept["issues"] if i["status"] == "accepted")
    analysis = record_analysis(
        conn, work_id=work_id, episode_no=episode_no,
        revision_id=revision_id, base_version=base, status="completed",
        coverage={**coverage, "ai_pass": "completed"},
        counts={"rule": len(det_candidates), "ai": len(ai_issues),
                "saved": len(kept["issues"]), "accepted_explained": accepted,
                "dropped": kept["dropped"]})
    return {"episode_no": episode_no, "issues": kept["issues"],
            "analysis": analysis}


def chat(meta, conn: sqlite3.Connection, *, episode_no: int,
         revision_id: int | None, message: str, work_id: int,
         max_tokens: int = 4000,
         writing_locale: str | None = None,
         market: str | None = None) -> dict:
    """도우미 대화 1턴. 답변과 함께 새로 찾은 이슈를 등록한다."""
    from . import meta_client as metac

    _prompt, writing_locale, market = metac.feature_prompt(
        conn, work_id, "consistency", writing_locale, market)
    preset_id, preset_version = metac.preset_ref(conn, work_id)
    message = (message or "").strip()
    if not message:
        raise ValueError("message must not be blank")
    manuscript = _current_manuscript(conn, episode_no, revision_id, work_id)
    base = memory.current_memory_version(conn, work_id)
    history = [{"role": m["role"], "content": m["content"]}
               for m in list_messages(conn, episode_no, limit=10,
                                      work_id=work_id)]
    if writing_locale == "en-US":
        directive = ("Answer the author's question, and register any newly"
                     " found consistency errors in issues. Skeleton: "
                     '{"reply": str, "issues": [{"severity": str, '
                     '"title": str, "detail": str, "verdict": str, '
                     '"manuscript_quote": str, "evidence_episode": int|null, '
                     '"evidence_quote": str, "entity_id": str, '
                     '"fact_refs": [int]}]}\n')
    else:
        directive = ("작가의 질문에 답하고, 새로 발견한 일관성 오류가 있으면"
                     " issues에 등록하라. 스켈레톤: "
                     '{"reply": str, "issues": [{"severity": str, '
                     '"title": str, "detail": str, "verdict": str, '
                     '"manuscript_quote": str, "evidence_episode": int|null, '
                     '"evidence_quote": str, "entity_id": str, '
                     '"fact_refs": [int]}]}\n')
    out = metac.chat_with_provenance(
        meta, bundle="consistency-chat", episode=episode_no,
        system=_prompt.text, work_id=work_id,
        user=directive + json.dumps(
            {"episode_no": episode_no, "question": message,
             "history": history, "manuscript": manuscript,
             "memory": _memory_context(conn, episode_no, work_id)},
            ensure_ascii=False),
        schema=sch.ConsistencyChatOutput, max_tokens=max_tokens,
        writing_locale=writing_locale, market=market,
        prompt_feature="consistency", prompt_version=PROMPT_VERSION,
        preset_id=preset_id, preset_version=preset_version)
    _add_message(conn, episode_no, "user", message, work_id)
    _add_message(conn, episode_no, "assistant", out.get("reply", ""), work_id)
    kept = add_issues(conn, episode_no, out.get("issues", []), work_id,
                      revision_id, manuscript, base)
    return {"episode_no": episode_no, "reply": out.get("reply", ""),
            "issues": kept["issues"], "dropped": kept["dropped"]}
