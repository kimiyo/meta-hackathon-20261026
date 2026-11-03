"""Publishing support: work platform, AI blurb, covers, approved text."""
from __future__ import annotations

import base64
import binascii
import secrets
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from .db import covers_dir

PLATFORMS = ("문피아", "카카오페이지", "네이버시리즈", "리디", "기타")
DEFAULT_PLATFORM = "문피아"

COVER_MAX_BYTES = 5 * 1024 * 1024
COVER_EXTS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

BLURB_PROMPT = (Path(__file__).parent / "prompts" / "blurb.txt").read_text(
    encoding="utf-8")
BLURB_AI_MAX_CHARS = 500
BLURB_CTX_CHARS = 3000


class BlurbOut(BaseModel):
    blurb: str


def check_platform(platform: str) -> str:
    p = (platform or "").strip()
    if p not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform!r}")
    return p


PUBLISH_NOTE_MAX = 200


def check_publish_record(published_date: str, note: str) -> tuple[str, str]:
    """Validate a platform-registration record. Empty date clears it."""
    import datetime as dt

    d = (published_date or "").strip()
    n = (note or "").strip()
    if not d:
        return "", ""
    try:
        dt.date.fromisoformat(d)
    except ValueError:
        raise ValueError("등록일은 YYYY-MM-DD 형식이어야 합니다")
    if len(n) > PUBLISH_NOTE_MAX:
        raise ValueError(f"메모는 {PUBLISH_NOTE_MAX}자 이하여야 합니다")
    return d, n


def set_publish_record(conn: sqlite3.Connection, work_id: int, episode_no: int,
                       published_date: str, note: str) -> dict:
    """Record (or clear) external platform registration. No state change."""
    d, n = check_publish_record(published_date, note)
    platform = ""
    if d:
        work = conn.execute("SELECT platform FROM works WHERE id=?",
                            (work_id,)).fetchone()
        platform = (work["platform"] if work and work["platform"]
                    else DEFAULT_PLATFORM)
    conn.execute(
        "UPDATE episode_jobs SET published_date=?, published_note=?,"
        " published_platform=? WHERE work_id=? AND episode_no=?",
        (d, n, platform, work_id, episode_no))
    conn.commit()
    return {"published_date": d, "published_note": n,
            "published_platform": platform}


def approved_text(conn: sqlite3.Connection, work_id: int,
                  episode_no: int) -> tuple[str, int | None]:
    """Approved (발간) revision text of an episode, if any."""
    from . import state_machine

    job = state_machine.get_job(conn, work_id, episode_no)
    if not job or not job.get("manuscript_revision_id"):
        return "", None
    rev = conn.execute(
        "SELECT revision_id, text FROM manuscript_revisions"
        " WHERE revision_id=?",
        (job["manuscript_revision_id"],)).fetchone()
    if not rev:
        return "", None
    return rev["text"] or "", rev["revision_id"]


def collect_blurb_context(conn: sqlite3.Connection,
                          work: dict) -> dict:
    """Title + logline + synopsis + latest-design outlines for blurb."""
    from . import ideas

    wid = work["id"]
    syn = ideas.get_synopsis(conn, wid).get("content") or ""
    design = conn.execute(
        "SELECT id FROM story_designs WHERE work_id=? ORDER BY version DESC"
        " LIMIT 1", (wid,)).fetchone()
    outlines = []
    if design:
        for r in conn.execute(
                "SELECT episode_no, title, goal FROM episode_outlines"
                " WHERE design_id=? ORDER BY episode_no", (design["id"],)):
            outlines.append({"episode_no": r["episode_no"],
                             "title": r["title"] or "",
                             "goal": r["goal"] or ""})
    return {"title": work.get("title") or "",
            "logline": work.get("logline") or "",
            "synopsis": syn[:BLURB_CTX_CHARS],
            "outlines": outlines[:30]}


def has_blurb_material(ctx: dict) -> bool:
    if (ctx.get("logline") or "").strip():
        return True
    if (ctx.get("synopsis") or "").strip():
        return True
    return any((o.get("title") or o.get("goal") or "").strip()
               for o in ctx.get("outlines") or [])


def make_blurb(meta, work_id: int, ctx: dict) -> str:
    """Ask the model for a ≤500-char platform blurb. Not saved."""
    import json

    out = meta.chat_json(
        bundle="publish-blurb", episode=None, system=BLURB_PROMPT,
        work_id=work_id,
        user="작품 정보다. 스켈레톤: {\"blurb\": str}\n" + json.dumps(
            ctx, ensure_ascii=False),
        schema=BlurbOut, max_tokens=8000)
    blurb = (out.get("blurb") or "").strip()
    if not blurb:
        raise ValueError("AI가 빈 소개글을 반환했습니다")
    if len(blurb) > BLURB_AI_MAX_CHARS:
        blurb = blurb[:BLURB_AI_MAX_CHARS].rstrip()
    return blurb


def cover_url(cover_path: str) -> str:
    return f"/covers/{cover_path}" if cover_path else ""


def save_cover(work_id: int, filename: str, content_b64: str) -> str:
    """Validate and store a cover image. Returns the stored file name."""
    ext = Path(filename or "").suffix.lower()
    if ext not in COVER_EXTS:
        raise ValueError("커버는 jpg/png만 가능합니다")
    try:
        raw = base64.b64decode(content_b64 or "", validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("이미지 데이터가 올바르지 않습니다")
    if not raw:
        raise ValueError("이미지 데이터가 비어 있습니다")
    if len(raw) > COVER_MAX_BYTES:
        raise ValueError("커버는 5MB 이하여야 합니다")
    name = f"{work_id}_{secrets.token_hex(8)}{ext}"
    (covers_dir() / name).write_bytes(raw)
    return name


def delete_cover_file(cover_path: str) -> None:
    if not cover_path or "/" in cover_path or "\\" in cover_path:
        return
    try:
        (covers_dir() / cover_path).unlink(missing_ok=True)
    except OSError:
        pass
