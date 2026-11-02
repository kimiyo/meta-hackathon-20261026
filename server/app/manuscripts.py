"""Manuscript documents: working draft + immutable version snapshots.

Backs the shared manuscript panel (center of writing, later review):
draft autosave, manual version snapshots, and restore-as-copy. AI
drafting syncs into the document via sync_doc_after_revision().

Wire protocol (spec: 글쓰기 화면 중앙 원고 영역 §9, paths renamed to the
app's /episodes scope): every response wraps {document}; 409 carries
{code, message, latestRevision}; locked (publishing) manuscripts answer 423.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .db import now_iso, touch_work


class RevisionConflict(Exception):
    def __init__(self, message: str, latest: int):
        super().__init__(message)
        self.latest = latest


class ManuscriptLocked(Exception):
    pass


def norm_text(s: str) -> str:
    return (s or "").replace("\r\n", "\n").replace("\r", "\n")


def is_locked_job(job: dict | None) -> bool:
    return bool(job) and job.get("state") == "PUBLISHING"


def _doc_row(conn: sqlite3.Connection, episode_no: int):
    return conn.execute("SELECT * FROM manuscript_docs WHERE episode_no=?",
                        (episode_no,)).fetchone()


def ensure_doc_row(conn: sqlite3.Connection, episode_no: int) -> dict:
    row = _doc_row(conn, episode_no)
    if row is None:
        conn.execute("INSERT INTO manuscript_docs(episode_no, draft_content,"
                     " revision, approved_revision_id, updated_at)"
                     " VALUES(?,?,?,?,?)",
                     (episode_no, "", 0, None, now_iso()))
        conn.commit()
        row = _doc_row(conn, episode_no)
    return dict(row)


def _versions(conn: sqlite3.Connection, episode_no: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT revision_id, version, label, text, created_at"
        " FROM manuscript_revisions WHERE episode_no=? ORDER BY version",
        (episode_no,))]


def get_doc(conn: sqlite3.Connection, episode_no: int, job: dict | None,
            title: str = "") -> dict:
    """Assemble the manuscript document (spec §8.1 shape). Read-only."""
    versions = _versions(conn, episode_no)
    latest_text = versions[-1]["text"] if versions else ""
    row = _doc_row(conn, episode_no)
    draft = row["draft_content"] if row else latest_text
    revision = row["revision"] if row else 0
    approved = row["approved_revision_id"] if row else None
    return {
        "episodeId": str(episode_no),
        "title": title,
        "revision": revision,
        "draft": draft,
        "currentVersionId": str(versions[-1]["revision_id"]) if versions else None,
        "approvedVersionId": str(approved) if approved else None,
        "state": "approved" if is_locked_job(job) else "writing",
        "versions": [{"id": str(v["revision_id"]), "number": v["version"],
                      "label": v["label"] or "", "content": v["text"],
                      "createdAt": v["created_at"]} for v in versions],
    }


def _check_writable(job: dict | None) -> None:
    if is_locked_job(job):
        raise ManuscriptLocked("publishing manuscript is locked")


def _check_int(name: str, value) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def save_draft(conn: sqlite3.Connection, episode_no: int, job: dict | None,
               title: str, expected_revision, content: str) -> dict:
    _check_writable(job)
    _check_int("expectedRevision", expected_revision)
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    row = ensure_doc_row(conn, episode_no)
    if expected_revision != row["revision"]:
        raise RevisionConflict("revision conflict", row["revision"])
    content = norm_text(content)
    if content != row["draft_content"]:
        conn.execute("UPDATE manuscript_docs SET draft_content=?,"
                     " revision=revision+1, updated_at=? WHERE episode_no=?",
                     (content, now_iso(), episode_no))
        conn.commit()
    return get_doc(conn, episode_no, job, title)


def _idem_lookup(conn: sqlite3.Connection, request_id: str, op: str,
                 body: dict, latest: int):
    fingerprint = hashlib.sha256(json.dumps(
        body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    row = conn.execute("SELECT * FROM idempotency_keys WHERE request_id=?",
                       (request_id,)).fetchone()
    if row is None:
        return None, fingerprint
    if row["op"] != op or row["body_hash"] != fingerprint:
        raise RevisionConflict("duplicate requestId with different body",
                               latest)
    return json.loads(row["response_json"]), fingerprint


def _idem_store(conn: sqlite3.Connection, request_id: str, op: str,
                fingerprint: str, doc: dict) -> None:
    conn.execute("INSERT INTO idempotency_keys(request_id, op, body_hash,"
                 " response_json, created_at) VALUES(?,?,?,?,?)",
                 (request_id, op, fingerprint,
                  json.dumps(doc, ensure_ascii=False), now_iso()))
    conn.commit()


def _adopt_revision(conn: sqlite3.Connection, job: dict,
                    revision_id: int) -> None:
    """A hand-made version becomes the episode's current text."""
    conn.execute("UPDATE episode_jobs SET manuscript_revision_id=?"
                 " WHERE episode_no=?", (revision_id, job["episode_no"]))
    conn.commit()
    job.update(manuscript_revision_id=revision_id)


def _insert_version(conn: sqlite3.Connection, episode_no: int, content: str,
                    label: str) -> dict:
    from . import drafting

    return drafting.save_revision(conn, episode_no, content, label=label)


def create_version(conn: sqlite3.Connection, episode_no: int, job: dict,
                   title: str, expected_revision, request_id: str,
                   content: str, label: str | None = None) -> dict:
    _check_writable(job)
    _check_int("expectedRevision", expected_revision)
    if not isinstance(content, str):
        raise ValueError("content must be a string")
    if not request_id or not isinstance(request_id, str):
        raise ValueError("requestId must be a non-empty string")
    if label is not None and not isinstance(label, str):
        raise ValueError("label must be a string")
    row = ensure_doc_row(conn, episode_no)
    body = {"expectedRevision": expected_revision,
            "content": norm_text(content), "label": label or ""}
    hit, fingerprint = _idem_lookup(conn, request_id, "version", body,
                                    row["revision"])
    if hit is not None:
        return hit
    if expected_revision != row["revision"]:
        raise RevisionConflict("revision conflict", row["revision"])
    rev = _insert_version(conn, episode_no, body["content"],
                          body["label"] or "수동 저장")
    # save_revision already synced draft=latest and bumped revision.
    _adopt_revision(conn, job, rev["revision_id"])
    touch_work(conn, _work_of_job(conn, job))
    conn.commit()
    doc = get_doc(conn, episode_no, job, title)
    _idem_store(conn, request_id, "version", fingerprint, doc)
    return doc


def restore_version(conn: sqlite3.Connection, episode_no: int, job: dict,
                    title: str, expected_revision, request_id: str,
                    source_id: str) -> dict:
    _check_writable(job)
    _check_int("expectedRevision", expected_revision)
    if not source_id or not isinstance(source_id, str):
        raise ValueError("sourceVersionId must be a non-empty string")
    try:
        source_rid = int(source_id)
    except ValueError:
        raise ValueError(f"bad sourceVersionId: {source_id}")
    source = conn.execute(
        "SELECT * FROM manuscript_revisions"
        " WHERE episode_no=? AND revision_id=?",
        (episode_no, source_rid)).fetchone()
    if source is None:
        raise KeyError(f"revision {source_id} not found")
    return create_version(
        conn, episode_no, job, title, expected_revision,
        f"restore:{request_id}", source["text"],
        label=f"v{source['version']} 복원")


def sync_doc_after_revision(conn: sqlite3.Connection, episode_no: int,
                            revision_id: int) -> None:
    """AI paths call this after saving a revision (draft follows latest)."""
    rev = conn.execute("SELECT text FROM manuscript_revisions"
                       " WHERE revision_id=?", (revision_id,)).fetchone()
    if rev is None:
        return
    row = _doc_row(conn, episode_no)
    if row is None:
        conn.execute("INSERT INTO manuscript_docs(episode_no, draft_content,"
                     " revision, approved_revision_id, updated_at)"
                     " VALUES(?,?,?,?,?)",
                     (episode_no, rev["text"], 1, None, now_iso()))
    else:
        conn.execute("UPDATE manuscript_docs SET draft_content=?,"
                     " revision=revision+1, updated_at=? WHERE episode_no=?",
                     (rev["text"], now_iso(), episode_no))
    conn.commit()


def snapshot_approval(conn: sqlite3.Connection, episode_no: int,
                      revision_id: int) -> None:
    """Record which revision the approve flow committed (no revision bump)."""
    ensure_doc_row(conn, episode_no)
    conn.execute("UPDATE manuscript_docs SET approved_revision_id=?,"
                 " updated_at=? WHERE episode_no=?",
                 (revision_id, now_iso(), episode_no))
    conn.commit()


def draft_base(conn: sqlite3.Connection, episode_no: int,
               revision_text: str) -> str:
    """Redraft base: the working draft when one exists, else the revision."""
    row = _doc_row(conn, episode_no)
    return row["draft_content"] if row else revision_text


def _work_of_job(conn: sqlite3.Connection, job: dict) -> int | None:
    row = conn.execute("SELECT work_id FROM story_designs WHERE id=?",
                       (job["design_id"],)).fetchone()
    return row["work_id"] if row else None
