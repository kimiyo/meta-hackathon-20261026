"""Memory query & commit routes (§5.3). Reads use committed rows only.

All memory access is scoped to the owning work (§1).
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import extraction, memory, verification
from ..errors import LLMStepError
from .deps import get_conn, get_current_author, require_episode_job, require_work
from .works import get_meta

router = APIRouter(prefix="/api/v1", tags=["memory"])


def _work_of_job(conn, job) -> int | None:
    if job.get("work_id") is not None:
        return job["work_id"]
    design = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                          (job["design_id"],)).fetchone()
    return design["work_id"] if design else None


def _author_work_ids(conn, author_id: int) -> list[int]:
    rows = conn.execute("SELECT id FROM works WHERE author_id=?",
                        (author_id,)).fetchall()
    return [r["id"] for r in rows]


def _model_version() -> str:
    try:
        from ..main import settings
        return settings.model_text_id
    except Exception:
        return ""


@router.get("/entities")
def list_entities(type: str | None = None, q: str | None = None,
                  work_id: int | None = None,
                  as_of_episode: int | None = None,
                  content: str | None = None,
                  any: str | None = None,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    if work_id is not None:
        require_work(conn, work_id, author["id"])
        ents = memory.search_entities(conn, type, q, work_id,
                                      as_of_episode, content, any)
    else:
        out = []
        for wid in _author_work_ids(conn, author["id"]):
            out.extend(memory.search_entities(conn, type, q, wid,
                                              as_of_episode, content, any))
        out.sort(key=lambda e: e["entity_id"])
        ents = out
    if any:
        for e in ents:
            e["match_snippet"] = memory.entity_match_snippet(
                conn, e["entity_id"], any, as_of_episode)
    return {"entities": ents}


@router.get("/entities/{entity_id}/state")
def entity_state(entity_id: str, as_of_episode: int | None = None,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    ent = memory.get_entity(conn, entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    require_work(conn, ent["work_id"], author["id"])
    if as_of_episode is None:
        row = conn.execute("SELECT MAX(episode_no) m FROM episode_jobs"
                           " WHERE work_id=?", (ent["work_id"],)).fetchone()
        as_of_episode = row["m"] if row["m"] is not None else 0
    return {"entity_id": entity_id, "as_of_episode": as_of_episode,
            "attributes": memory.reconstruct_state(conn, entity_id,
                                                   as_of_episode,
                                                   ent["work_id"])}


@router.get("/entities/{entity_id}/history")
def entity_history(entity_id: str, from_episode: int | None = None,
                   to_episode: int | None = None,
                   conn: sqlite3.Connection = Depends(get_conn),
                   author: dict = Depends(get_current_author)):
    ent = memory.get_entity(conn, entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    require_work(conn, ent["work_id"], author["id"])
    hist = []
    for h in memory.entity_history(conn, entity_id, from_episode, to_episode):
        d = dict(h)
        for k in ("old_value", "new_value"):
            v = memory._u(d.get(k))
            d[k] = "" if v is None else v
        hist.append(d)
    return {"entity_id": entity_id, "history": hist}


@router.get("/entities/{entity_id}/relations")
def entity_relations(entity_id: str,
                     conn: sqlite3.Connection = Depends(get_conn),
                     author: dict = Depends(get_current_author)):
    ent = memory.get_entity(conn, entity_id)
    if not ent:
        raise HTTPException(404, f"entity {entity_id} not found")
    require_work(conn, ent["work_id"], author["id"])
    rows = conn.execute("SELECT * FROM entity_relations"
                        " WHERE subject_id=? OR object_id=?"
                        " ORDER BY valid_from_episode, relation_id",
                        (entity_id, entity_id)).fetchall()
    return {"entity_id": entity_id,
            "relations": [dict(r) for r in rows]}


def _job_or_404(conn, work: str, episode_no, author_id):
    wid = require_work(conn, work, author_id)["id"]
    job = require_episode_job(conn, wid, episode_no, author_id)
    if not job:
        raise HTTPException(404, f"episode {episode_no} has no job yet")
    return job


def _revision_text(conn, revision_id):
    row = conn.execute("SELECT text FROM manuscript_revisions"
                       " WHERE revision_id=?", (revision_id,)).fetchone()
    return row["text"] if row else ""


class ExtractIn(BaseModel):
    instruction: str = ""
    verify: bool = True


@router.post("/episodes/{episode_no}/entities/extract")
def extract_entities(episode_no: int, work: str,
                     body: ExtractIn | None = None,
                     conn: sqlite3.Connection = Depends(get_conn),
                     meta=Depends(get_meta),
                     author: dict = Depends(get_current_author)):
    """현재 원고에서 Entity 변경안을 추출해 임시 저장한다(§5 흐름).

    추출 시작 시 원고 revision과 기준 기억 버전을 고정한다. 새 실행이
    완성된 뒤에만 기존 pending을 대체하고(완성 후 교체), 실패하면
    failed 실행으로 기록하고 기존안을 유지한다. 검증(verify)은 의미
    검증 패스로, 실패해도 추출 자체는 유지된다.
    """
    job = _job_or_404(conn, work, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "설정 추출은 검토중 상태에서만 가능합니다")
    if not job.get("manuscript_revision_id"):
        raise HTTPException(400, "no manuscript to extract from")
    manuscript = _revision_text(conn, job["manuscript_revision_id"])
    if not manuscript.strip():
        raise HTTPException(400, "manuscript is empty")
    work_id = _work_of_job(conn, job)
    if work_id is None:
        raise HTTPException(400, "episode has no owning work")
    base = memory.current_memory_version(conn, work_id)
    do_verify = body.verify if body else True
    try:
        out = extraction.extract_changes(
            meta, conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"],
            manuscript=manuscript, work_id=work_id,
            base_memory_version=base, model_version=_model_version(),
            author_instruction=(body.instruction if body else ""))
    except LLMStepError as e:
        memory.record_failed_run(
            conn, episode_no=episode_no,
            revision_id=job["manuscript_revision_id"], work_id=work_id,
            base_memory_version=base, model_version=_model_version(),
            prompt_version=extraction.PROMPT_VERSION,
            error=f"{e.reason}: {e.detail}")
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    verify_out: dict = {"verified": False}
    if do_verify and out["items"]:
        try:
            verify_out = {"verified": True, **verification.verify_proposals(
                meta, conn, episode_no=episode_no,
                revision_id=job["manuscript_revision_id"],
                proposal_set_id=out["proposal_set_id"],
                manuscript=manuscript, work_id=work_id)}
        except LLMStepError as e:
            verify_out = {"verified": False, "error": f"{e.reason}: {e.detail}"}
    memory.discard_pending_except(conn, episode_no, out["proposal_set_id"],
                                  work_id)
    items = [i for i in memory.get_proposals(conn, episode_no, work_id)
             if i["proposal_set_id"] == out["proposal_set_id"]]
    return {"episode_no": episode_no, **out, "verify": verify_out,
            "items": items}


@router.post("/episodes/{episode_no}/entities/save")
def save_entities(episode_no: int, work: str,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    """검토 확정: 신선한 임시 변경안을 공식 기억으로 원자 저장한다.

    저장된 기억은 설정 화면에서 바로 조회된다. 같은 원고에 이미
    저장했으면 중복 저장 없이 성공한다. 기준 기억이 바뀌었으면
    재검토를 요구한다.
    """
    job = _job_or_404(conn, work, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "설정 저장은 검토중 상태에서만 가능합니다")
    work_id = _work_of_job(conn, job)
    rev_id = job.get("manuscript_revision_id")
    pset = memory.get_pending_set_for_revision(conn, episode_no, rev_id,
                                               work_id)
    if pset is None:
        saved = memory.get_committed_set_for_revision(conn, episode_no, rev_id,
                                                      work_id)
        if saved is not None:
            return {"episode_no": episode_no, "saved": True,
                    "already": True,
                    "proposal_set_id": saved["proposal_set_id"]}
        if memory.has_any_proposal(conn, episode_no, work_id):
            raise HTTPException(400, "원고가 바뀌어 설정 재추출이 필요합니다")
        raise HTTPException(400, "설정 추출을 먼저 실행하세요")
    try:
        result = memory.save_episode_memory(
            conn, episode_no=episode_no, revision_id=rev_id,
            proposal_set_id=pset["proposal_set_id"], work_id=work_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"episode_no": episode_no, "saved": True, "commit": result,
            "proposal_set_id": pset["proposal_set_id"]}


@router.delete("/entity-items/{item_id}")
def delete_entity_item(item_id: int,
                       conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    """임시 변경안 항목 삭제. 검토 단계의 pending 항목만 가능하다."""
    row = conn.execute(
        "SELECT s.episode_no, s.work_id FROM change_proposal_items i"
        " JOIN change_proposals s ON s.proposal_set_id=i.proposal_set_id"
        " WHERE i.item_id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"item {item_id} not found")
    job = require_episode_job(conn, row["work_id"], row["episode_no"],
                              author["id"])
    if not job or _work_of_job(conn, job) != row["work_id"]:
        raise HTTPException(404, f"item {item_id} not found")
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "설정 편집은 검토중 상태에서만 가능합니다")
    try:
        return memory.delete_pending_item(conn, item_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/episodes/{episode_no}/entity-events/{index}")
def delete_entity_event(episode_no: int, index: int, work: str,
                        conn: sqlite3.Connection = Depends(get_conn),
                        author: dict = Depends(get_current_author)):
    """임시 변경안의 사건 1건 삭제. 검토 단계의 pending 사건만 가능하다."""
    job = _job_or_404(conn, work, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "설정 편집은 검토중 상태에서만 가능합니다")
    work_id = _work_of_job(conn, job)
    pset = memory.latest_pending_set(conn, episode_no, work_id)
    if not pset:
        raise HTTPException(404, f"episode {episode_no} has no pending events")
    try:
        return memory.delete_pending_event(conn, pset["proposal_set_id"], index)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e))


@router.delete("/episodes/{episode_no}/entity-events/{index}/participants/{pindex}")
def delete_entity_event_participant(episode_no: int, index: int, pindex: int,
                                    work: str,
                                    conn: sqlite3.Connection = Depends(get_conn),
                                    author: dict = Depends(get_current_author)):
    """임시 변경안의 사건 참여자 1명 제외. 검토 단계의 pending만 가능하다."""
    job = _job_or_404(conn, work, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "설정 편집은 검토중 상태에서만 가능합니다")
    work_id = _work_of_job(conn, job)
    pset = memory.latest_pending_set(conn, episode_no, work_id)
    if not pset:
        raise HTTPException(404, f"episode {episode_no} has no pending events")
    try:
        return memory.delete_pending_event_participant(
            conn, pset["proposal_set_id"], index, pindex)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except (ValueError, IndexError) as e:
        raise HTTPException(400, str(e))


def _with_version(conn, pset):
    if not pset:
        return None
    rev = conn.execute("SELECT version FROM manuscript_revisions"
                       " WHERE revision_id=?",
                       (pset["manuscript_revision_id"],)).fetchone()
    return {**pset, "version": rev["version"] if rev else None}


@router.post("/episodes/{episode_no}/entities/reset")
def reset_entities(episode_no: int, work: str,
                   conn: sqlite3.Connection = Depends(get_conn),
                   author: dict = Depends(get_current_author)):
    """이번 화의 설정 기억을 모두 지운다(설정 초기화).

    확정 변경·사건·임시안·이 화 출처의 별명(확정·기각·후보)·이슈를
    지우고, 참조가 끊긴 설정은 함께 정리한다.
    """
    job = _job_or_404(conn, work, episode_no, author["id"])
    if job["state"] != "IN_REVIEW":
        raise HTTPException(400, "설정 초기화는 검토중 상태에서만 가능합니다")
    work_id = _work_of_job(conn, job)
    if work_id is None:
        raise HTTPException(400, "episode has no owning work")
    result = memory.reset_episode_memory(conn, episode_no, work_id)
    return {"episode_no": episode_no, **result}


@router.get("/episodes/{episode_no}/entity-proposals")
def entity_proposals(episode_no: int, work: str,
                     conn: sqlite3.Connection = Depends(get_conn),
                     author: dict = Depends(get_current_author)):
    """임시 변경안 + 저장된 기억(현재 원고 기준) + 신선도 + 추출 기준."""
    job = _job_or_404(conn, work, episode_no, author["id"])
    work_id = _work_of_job(conn, job)
    rev_id = job.get("manuscript_revision_id")
    pset = memory.latest_pending_set(conn, episode_no, work_id)
    items = ([i for i in memory.get_proposals(conn, episode_no, work_id)
              if i["proposal_set_id"] == pset["proposal_set_id"]]
             if pset else [])
    saved = memory.get_committed_set_for_revision(conn, episode_no, rev_id,
                                                  work_id)
    saved_items = ([i for i in memory.get_proposals(conn, episode_no, work_id)
                    if i["proposal_set_id"] == saved["proposal_set_id"]]
                   if saved else [])
    events = (memory.get_proposal_events(conn, pset["proposal_set_id"])
              if pset else [])
    saved_events = (memory.get_proposal_events(conn, saved["proposal_set_id"])
                    if saved else [])
    latest = memory.latest_set(conn, episode_no, work_id)
    return {"episode_no": episode_no,
            "proposal_set": _with_version(conn, pset),
            "items": items,
            "events": events,
            "fresh": bool(pset) and pset["manuscript_revision_id"] == rev_id,
            "saved_set": _with_version(conn, saved),
            "saved_items": saved_items,
            "saved_events": saved_events,
            "committed_summary": memory.committed_summary(
                conn, episode_no, work_id or 0),
            "memory_version": memory.current_memory_version(conn, work_id or 0),
            "latest_run": (_with_version(conn, latest)
                           if latest and (not pset or latest["proposal_set_id"]
                                          != pset["proposal_set_id"]) else None)}


class AliasResolveIn(BaseModel):
    entity_id: str | None = None
    basis: str = ""


class AliasRejectIn(BaseModel):
    basis: str = ""


@router.get("/works/{key}/aliases")
def alias_candidates(key: str,
                     conn: sqlite3.Connection = Depends(get_conn),
                     author: dict = Depends(get_current_author)):
    """작가 판정 대기 중인 별명·표기 후보(§7 설정 연결)."""
    w = require_work(conn, key, author["id"])
    return {"work_id": w["id"],
            "candidates": memory.list_alias_candidates(conn, w["id"])}


def _owned_alias(conn, alias_id, author_id):
    row = conn.execute("SELECT * FROM entity_aliases WHERE alias_id=?",
                       (alias_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"alias {alias_id} not found")
    require_work(conn, row["work_id"], author_id)
    return row


@router.post("/aliases/{alias_id}/resolve")
def resolve_alias(alias_id: int, body: AliasResolveIn,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    """별명 후보 확정: 기존 Entity에 연결하거나(entity_id) 새 인물로
    등록한다(entity_id null)."""
    _owned_alias(conn, alias_id, author["id"])
    if body.entity_id is not None and not memory.get_entity(conn, body.entity_id):
        raise HTTPException(404, f"entity {body.entity_id} not found")
    try:
        return memory.resolve_alias(conn, alias_id,
                                    entity_id=body.entity_id,
                                    basis=body.basis or "작가 확정")
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.post("/aliases/{alias_id}/reject")
def reject_alias(alias_id: int, body: AliasRejectIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    """별명 후보 기각(오탐)."""
    _owned_alias(conn, alias_id, author["id"])
    return memory.reject_alias(conn, alias_id, body.basis or "작가 기각")
