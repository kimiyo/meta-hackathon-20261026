"""Planning pipeline (§6.1): concept+sections, then 10-episode outline
batches merged into one story design. The program validates counts,
continuity and required fields; the AI only generates."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import now_iso
from .errors import LLMStepError
from .schemas import planning as sch

PROMPT = (Path(__file__).parent / "prompts" / "planning.txt").read_text(encoding="utf-8")
BATCH = 10


def _skeleton_outline(start: int) -> str:
    return (
        '{"episode_outlines": [{"episode_no": %d, "title": str, "goal": str, '
        '"events": [str, ...], "char_changes": [str, ...], '
        '"foreshadowing": [str, ...], "links": {"prev": str, "next": str}, '
        '"constraints": [str, ...]}], "warnings": []}' % start
    )


def generate_concept(meta, *, keywords: str, total_episodes: int,
                     constraints: list[str], prev_feedback: str = "",
                     work_id: int | None = None,
                     max_tokens: int) -> dict:
    user = (
        f"키워드: {keywords}\n총 회차: {total_episodes}\n"
        f"제약: {json.dumps(constraints, ensure_ascii=False)}\n"
        + (f"이전 피드백(반영): {prev_feedback}\n" if prev_feedback else "")
        + "다음 스켈레톤의 JSON으로 concept와 sections를 생성하라. 키 이름을 바꾸지 마라:\n"
        + '{"concept": {"title_candidates": [str, ...], "genre": str, "tone": str, '
        + '"theme": str, "ending": str}, "sections": [{"range": "1-10", '
        + '"summary": str}], "warnings": []}'
    )
    return meta.chat_json(bundle="planning", episode=None, system=PROMPT,
                          user=user, schema=sch.ConceptOutput, work_id=work_id,
                          max_tokens=max_tokens)


def generate_outline_batch(meta, *, keywords: str, concept: dict, start: int,
                           end: int, constraints: list[str], prev_summary: str,
                           work_id: int | None = None,
                           max_tokens: int) -> dict:
    user = (
        f"키워드: {keywords}\n컨셉: {json.dumps(concept, ensure_ascii=False)}\n"
        f"제약: {json.dumps(constraints, ensure_ascii=False)}\n"
        f"회차 개요 episode_no {start}부터 {end}까지(총 {end - start + 1}개)를 "
        f"다음 스켈레톤의 JSON으로 생성하라. 키 이름을 바꾸지 마라:\n"
        + _skeleton_outline(start)
    )
    if prev_summary:
        user += f"\n앞 구간(연속성 유지): {prev_summary}"
    return meta.chat_json(bundle="planning", episode=None, system=PROMPT,
                          user=user, schema=sch.OutlineBatch, work_id=work_id,
                          max_tokens=max_tokens)


def generate_design(meta, conn: sqlite3.Connection, *, work_id: int,
                    keywords: str, total_episodes: int,
                    constraints: list[str] | None = None,
                    prev_feedback: str = "", max_tokens: int = 8000) -> dict:
    """Full design generation + persistence. Returns design summary."""
    constraints = constraints or []
    concept_out = generate_concept(
        meta, keywords=keywords, total_episodes=total_episodes,
        constraints=constraints, prev_feedback=prev_feedback, work_id=work_id,
        max_tokens=max_tokens)
    merged: list[dict] = []
    prev_summary = ""
    for start in range(1, total_episodes + 1, BATCH):
        end = min(start + BATCH - 1, total_episodes)
        batch = generate_outline_batch(
            meta, keywords=keywords, concept=concept_out["concept"],
            start=start, end=end, constraints=constraints,
            prev_summary=prev_summary, max_tokens=max_tokens, work_id=work_id)
        merged.extend(batch["episode_outlines"])
        prev_summary = "; ".join(
            f"EP{o['episode_no']}:{o['title']}" for o in batch["episode_outlines"])
    _validate_outlines(merged, total_episodes)
    row = conn.execute("SELECT COALESCE(MAX(version),0) v FROM story_designs"
                       " WHERE work_id=?", (work_id,)).fetchone()
    version = row["v"] + 1
    cur = conn.execute(
        "INSERT INTO story_designs(work_id, version, scope_total_episodes,"
        " concept_json, sections_json) VALUES(?,?,?,?,?)",
        (work_id, version, total_episodes,
         json.dumps(concept_out["concept"], ensure_ascii=False),
         json.dumps(concept_out["sections"], ensure_ascii=False)),
    )
    design_id = cur.lastrowid
    for o in merged:
        conn.execute(
            "INSERT INTO episode_outlines(design_id, episode_no, title, goal,"
            " events_json, char_changes_json, foreshadowing_json, links_json,"
            " constraints_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (design_id, o["episode_no"], o["title"], o["goal"],
             json.dumps(o["events"], ensure_ascii=False),
             json.dumps(o["char_changes"], ensure_ascii=False),
             json.dumps(o["foreshadowing"], ensure_ascii=False),
             json.dumps(o["links"], ensure_ascii=False),
             json.dumps(o["constraints"], ensure_ascii=False)),
        )
    conn.commit()
    return {"design_id": design_id, "version": version,
            "total_episodes": total_episodes,
            "outline_count": len(merged)}


def _validate_outlines(merged: list[dict], total: int) -> None:
    nos = [o["episode_no"] for o in merged]
    if len(merged) != total or nos != list(range(1, total + 1)):
        raise LLMStepError("planning", None, "outline_count_mismatch",
                           f"expected 1..{total}, got {nos[:5]}...({len(nos)})")
    for o in merged:
        if not o["title"] or not o["goal"]:
            raise LLMStepError("planning", o["episode_no"],
                               "outline_missing_field", "title/goal required")


def get_design(conn, design_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM story_designs WHERE id=?",
                       (design_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["concept"] = json.loads(d.pop("concept_json"))
    d["sections"] = json.loads(d.pop("sections_json"))
    outlines = conn.execute(
        "SELECT * FROM episode_outlines WHERE design_id=? ORDER BY episode_no",
        (design_id,)).fetchall()
    d["outlines"] = []
    for o in outlines:
        od = dict(o)
        for k in ("events_json", "char_changes_json", "foreshadowing_json",
                  "links_json", "constraints_json"):
            od[k[:-5]] = json.loads(od.pop(k))
        d["outlines"].append(od)
    return d


def approve_design(conn, design_id: int) -> dict:
    conn.execute("UPDATE story_designs SET status='approved', approved_at=?"
                 " WHERE id=?", (now_iso(), design_id))
    conn.commit()
    return {"design_id": design_id, "status": "approved"}


def update_outline(conn, design_id: int, episode_no: int, fields: dict) -> dict:
    allowed = {"title", "goal", "events", "char_changes", "foreshadowing",
               "links", "constraints"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        col = k if k in ("title", "goal") else {
            "events": "events_json", "char_changes": "char_changes_json",
            "foreshadowing": "foreshadowing_json", "links": "links_json",
            "constraints": "constraints_json"}[k]
        sets.append(f"{col}=?")
        args.append(v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    if not sets:
        raise ValueError("no valid outline fields")
    args += [design_id, episode_no]
    cur = conn.execute(
        f"UPDATE episode_outlines SET {', '.join(sets)}"
        " WHERE design_id=? AND episode_no=?", args)
    conn.commit()
    if cur.rowcount == 0:
        raise KeyError(f"outline {design_id}/{episode_no} not found")
    return {"design_id": design_id, "episode_no": episode_no, "updated": True}
