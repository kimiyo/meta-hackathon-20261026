"""Deterministic consistency checks (§6): machine-checkable constraints.

Each check compares the CURRENT pending proposal against committed memory
and returns candidates:

    {check, severity, title, detail, verdict, entity_id, fact_refs,
     manuscript_quote, evidence_episode, evidence_quote}

verdict is 'conflict' only for definite violations; anything needing
judgment is 'uncertain'. Checks never emit issues anchored outside the
current proposal: history-vs-history is out of analyze scope.
"""
from __future__ import annotations

import json
import sqlite3

from . import memory

# A rule predicate counts as a per-day cap when it names one explicitly.
_CAP_MARKERS = ("max_uses_per_day", "per_day", "제한", "횟수", ".max",
                "daily_limit", "1일", "하루")


def _jval(text):
    if text in ("", None):
        return ""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _num(value) -> float | None:
    v = _jval(value)
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _related(rule_pred: str, use_pred: str) -> bool:
    """Same constraint namespace? Dotted (ability.magic.*) or Korean stem."""
    if not rule_pred or not use_pred or rule_pred == use_pred:
        return True
    rp, up = rule_pred.split("."), use_pred.split(".")
    if len(rp) > 1 and len(up) > 1 and rp[:-1] == up[:-1]:
        return True
    if up[0] and up[0] in rule_pred:
        return True
    # Korean stem: 마력제한 vs 마력사용 share 마력.
    if len(rule_pred) >= 3 and len(use_pred) >= 3 \
            and rule_pred[:2] == use_pred[:2]:
        return True
    return False


def _pending_rows(conn, proposal_set_id: int | None) -> tuple[list, list]:
    if not proposal_set_id:
        return [], []
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM change_proposal_items WHERE proposal_set_id=?"
        " AND validation NOT IN ('deferred','error','stale')"
        " ORDER BY item_id", (proposal_set_id,))]
    events = memory.get_proposal_events(conn, proposal_set_id)
    events = [e for e in events if e.get("validation") != "deferred"]
    return items, events


def check_count_limits(conn, work_id: int, episode_no: int,
                       proposal_set_id: int | None) -> list[dict]:
    """횟수 제한: 규칙 상한 vs 같은 이야깃날의 사용 기록."""
    items, _events = _pending_rows(conn, proposal_set_id)
    if not items:
        return []
    out = []
    for rule in memory.committed_rules(conn, work_id, episode_no):
        cap = _num(rule["standard_value"])
        if cap is None:
            continue
        pred = rule["attribute_path"] or ""
        if not any(m in pred for m in _CAP_MARKERS):
            continue
        for it in items:
            if it["kind"] != "event" or it["entity_id"] != rule["entity_id"]:
                continue
            if not _related(pred, it["attribute_path"] or ""):
                continue
            ordinal = _num(it.get("standard_value", ""))
            if ordinal is None:
                continue
            # Caps apply per story-day; the usage day anchors the match.
            day_use = (it.get("story_time") or "").strip()
            if not day_use:
                out.append(_candidate(
                    "count_limit", rule, it, "uncertain",
                    f"횟수 제한 초과 가능: {pred} 상한 {cap:g}인데 "
                    f"{ordinal:g}번째 사용 기록이 있으나 이야기 시점이 없어"
                    " 같은 날인지 단정할 수 없음"))
                continue
            if ordinal > cap:
                out.append(_candidate(
                    "count_limit", rule, it, "conflict",
                    f"횟수 제한 위반: {pred} 상한 {cap:g}, {day_use}에 "
                    f"{ordinal:g}번째 사용"))
    return out


def check_death(conn, work_id: int, episode_no: int,
                proposal_set_id: int | None) -> list[dict]:
    """사망 후 행동: 사망 상태인 인물의 현재 서술 행동."""
    items, events = _pending_rows(conn, proposal_set_id)
    if not items and not events:
        return []
    dead: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT c.*, e.canonical_name FROM entity_changes c"
            " JOIN change_proposals s ON s.proposal_set_id=c.proposal_set_id"
            " JOIN entities e ON e.entity_id=c.entity_id"
            " WHERE c.work_id=? AND c.episode_no<? AND c.kind='state'"
            " AND s.status='committed'"
            " AND c.narrative_mode NOT IN ('dream','conjecture','hypothesis',"
            " 'lie','recollection')",
            (work_id, episode_no)).fetchall():
        if _is_dead(r["attribute_path"] or "", _jval(r["standard_value"]),
                   _jval(r["new_value"])):
            dead[r["entity_id"]] = dict(r)
    if not dead:
        return []
    out = []
    for it in items:
        ent = it["entity_id"] or ""
        if ent not in dead or it["kind"] != "event":
            continue
        if (it.get("narrative_mode") or "narration") != "narration":
            continue  # 회상·꿈·거짓말 속 과거 행동은 정상
        d = dead[ent]
        out.append(_candidate(
            "death", d, it, "conflict",
            f"사망 후 행동: {d['canonical_name']}은(는) EP{d['episode_no']}에"
            " 사망했는데 현재 원고에서 행동함"))
    for ev in events:
        for p in ev.get("participants", []):
            ent = p.get("entity_id") or ""
            if ent not in dead:
                continue
            if (ev.get("narrative_mode") or "narration") != "narration":
                continue
            d = dead[ent]
            out.append({
                "check": "death", "severity": "critical",
                "title": f"사망 후 행동: {d['canonical_name']}",
                "detail": f"EP{d['episode_no']} 사망 기록 이후 현재 원고의"
                          f" 사건에 참여함: {ev.get('summary','')[:120]}",
                "verdict": "conflict", "entity_id": ent,
                "fact_refs": [d["change_id"]],
                "manuscript_quote": ev.get("evidence_quote", ""),
                "evidence_episode": d["episode_no"],
                "evidence_quote": d["evidence_quote"] or "",
            })
    return out


def _is_dead(predicate: str, standard, display) -> bool:
    if standard == "사망":
        return True
    text = str(display or "")
    if predicate in ("생사", "사망", "생존"):
        return "사망" in text or "죽음" in text or text.strip() in ("죽었음", "사망함")
    return False


def check_exclusive_location(conn, work_id: int, episode_no: int,
                             proposal_set_id: int | None) -> list[dict]:
    """같은 시점의 배타적 위치. 시점이 다르면 정상 이동으로 본다."""
    items, _events = _pending_rows(conn, proposal_set_id)
    locs = [i for i in items
            if i["kind"] == "state" and (i["attribute_path"] or "") == "위치"
            and (i.get("narrative_mode") or "narration") == "narration"
            and i["operation"] in ("set", "create", "add")]
    if len(locs) < 2:
        return []
    out = []
    for a_i in range(len(locs)):
        for b_i in range(a_i + 1, len(locs)):
            a, b = locs[a_i], locs[b_i]
            if (a["entity_id"] or a["candidate_name"]) != \
               (b["entity_id"] or b["candidate_name"]):
                continue
            va, vb = _place(a), _place(b)
            if not va or not vb or va == vb:
                continue
            ta, tb = (a.get("story_time") or "").strip(), \
                (b.get("story_time") or "").strip()
            if ta and tb and ta == tb:
                out.append({
                    "check": "exclusive_location", "severity": "major",
                    "title": "같은 시점의 양립 불가능한 위치",
                    "detail": f"{ta}에 '{va}'와 '{vb}' 두 곳에 있음",
                    "verdict": "conflict",
                    "entity_id": a["entity_id"] or "",
                    "fact_refs": [],
                    "manuscript_quote": a.get("evidence_quote", "") or "",
                    "evidence_episode": episode_no,
                    "evidence_quote": b.get("evidence_quote", "") or "",
                })
    return out


def _place(item) -> str:
    std = _jval(item.get("standard_value", ""))
    if isinstance(std, str) and std.strip():
        return std.strip()
    disp = _jval(item.get("new_value", ""))
    return disp.strip() if isinstance(disp, str) else ""


def _std(item) -> str:
    v = _jval(item.get("standard_value", ""))
    if isinstance(v, str) and v.strip():
        return v.strip()
    d = _jval(item.get("new_value", ""))
    if isinstance(d, str):
        return d.strip()
    return json.dumps(d, ensure_ascii=False) if d != "" else ""


def check_state_clash(conn, work_id: int, episode_no: int,
                      proposal_set_id: int | None) -> list[dict]:
    """같은 이야깃날의 상태 충돌: 확정 상태 vs 현재 임시안.

    시점이 다르면 시간 흐름으로 설명되는 정상 변화로 보고 건너뛴다.
    """
    items, _events = _pending_rows(conn, proposal_set_id)
    states = [i for i in items
              if i["kind"] == "state" and i["entity_id"]
              and i["operation"] in ("set", "create", "add")
              and (i.get("narrative_mode") or "narration") == "narration"]
    if not states:
        return []
    approved = conn.execute(
        "SELECT c.* FROM entity_changes c"
        " JOIN change_proposals s ON s.proposal_set_id=c.proposal_set_id"
        " WHERE c.work_id=? AND c.episode_no<? AND c.kind='state'"
        " AND s.status='committed'"
        " AND c.narrative_mode NOT IN ('dream','conjecture','hypothesis',"
        " 'lie','recollection')",
        (work_id, episode_no)).fetchall()
    by_key: dict[tuple, list] = {}
    for r in approved:
        by_key.setdefault((r["entity_id"], r["attribute_path"] or ""),
                          []).append(dict(r))
    out = []
    for it in states:
        day = (it.get("story_time") or "").strip()
        if not day:
            continue  # 시점 모름: AI 의미 패스에 맡긴다
        val = _std(it)
        if not val:
            continue
        for old in by_key.get((it["entity_id"],
                               it["attribute_path"] or ""), []):
            if (old["story_time"] or "").strip() != day:
                continue
            if _std(old) == val:
                continue
            out.append({
                "check": "state_clash", "severity": "major",
                "title": f"같은 시점의 상태 충돌: {it['attribute_path']}",
                "detail": f"{day}에 '{_std(old)}'(EP{old['episode_no']} 확정)와"
                          f" '{val}'(현재 원고)이 다름",
                "verdict": "conflict",
                "entity_id": it["entity_id"],
                "fact_refs": [old["change_id"]],
                "manuscript_quote": it.get("evidence_quote", "") or "",
                "evidence_episode": old["episode_no"],
                "evidence_quote": old["evidence_quote"] or "",
            })
    return out


def _candidate(check: str, rule: dict, item: dict, verdict: str,
               detail: str) -> dict:
    return {
        "check": check,
        "severity": "critical" if verdict == "conflict" else "minor",
        "title": detail.split(":")[0][:80],
        "detail": detail,
        "verdict": verdict,
        "entity_id": rule["entity_id"],
        "fact_refs": [rule["change_id"]] if rule.get("change_id") else [],
        "manuscript_quote": item.get("evidence_quote", "") or "",
        "evidence_episode": rule.get("episode_no"),
        "evidence_quote": rule.get("evidence_quote", "") or "",
    }


CHECKS = (check_count_limits, check_death, check_exclusive_location,
          check_state_clash)


def run_deterministic_checks(conn: sqlite3.Connection, work_id: int,
                             episode_no: int,
                             proposal_set_id: int | None) -> dict:
    """Run all deterministic checks. Pure reads, no LLM."""
    candidates: list[dict] = []
    ran = []
    for fn in CHECKS:
        try:
            found = fn(conn, work_id, episode_no, proposal_set_id)
        except Exception as e:  # one broken check must not kill analysis
            ran.append({"check": fn.__name__, "status": "failed",
                        "error": str(e)[:200]})
            continue
        ran.append({"check": fn.__name__, "status": "completed",
                    "candidates": len(found)})
        candidates.extend(found)
    return {"candidates": candidates, "ran": ran}
