"""Publishing support: final per-episode editions, teasers, work covers.

An edition is display-only data for external platforms. It starts as a
copy of the approved (발간중) manuscript text and is never fed back into
extraction or consistency analysis.
"""
from __future__ import annotations

import base64
import binascii
import secrets
import sqlite3
from pathlib import Path

from pydantic import BaseModel

from .db import covers_dir, now_iso

PLATFORMS = ("문피아", "카카오페이지", "네이버시리즈", "리디", "기타")
DEFAULT_PLATFORM = "문피아"

TEASER_MARK = "〈다음 화 예고〉"
TEASER_MAX_CHARS = 100
TEASER_CONTEXT_CHARS = 1500

COVER_MAX_BYTES = 5 * 1024 * 1024
COVER_EXTS = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}

PROMPT = (Path(__file__).parent / "prompts" / "teaser.txt").read_text(
    encoding="utf-8")
BLURB_PROMPT = (Path(__file__).parent / "prompts" / "blurb.txt").read_text(
    encoding="utf-8")
BLURB_AI_MAX_CHARS = 500
BLURB_CTX_CHARS = 3000


class TeaserOut(BaseModel):
    teaser: str


class BlurbOut(BaseModel):
    blurb: str


def check_platform(platform: str) -> str:
    p = (platform or "").strip()
    if p not in PLATFORMS:
        raise ValueError(f"unknown platform: {platform!r}")
    return p


def get_edition(conn: sqlite3.Connection, work_id: int,
                episode_no: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM publish_editions WHERE work_id=? AND episode_no=?",
        (work_id, episode_no)).fetchone()
    return dict(row) if row else None


def upsert_edition(conn: sqlite3.Connection, work_id: int, episode_no: int,
                   *, platform: str | None = None, text: str | None = None,
                   teaser_episode: int | None = None,
                   teaser_text: str | None = None,
                   source_revision_id: int | None = None) -> dict:
    edition = get_edition(conn, work_id, episode_no)
    ts = now_iso()
    if edition is None:
        if platform is None:
            platform = DEFAULT_PLATFORM
        else:
            platform = check_platform(platform)
        conn.execute(
            "INSERT INTO publish_editions(work_id, episode_no, platform, text,"
            " teaser_episode, teaser_text, source_revision_id, created_at,"
            " updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (work_id, episode_no, platform, text or "",
             teaser_episode, teaser_text or "", source_revision_id, ts, ts))
    else:
        sets, args = [], []
        if platform is not None:
            sets.append("platform=?")
            args.append(check_platform(platform))
        if text is not None:
            sets.append("text=?")
            args.append(text)
        if teaser_episode is not None or teaser_text is not None:
            sets.append("teaser_episode=?")
            args.append(teaser_episode)
            sets.append("teaser_text=?")
            args.append(teaser_text or "")
        if source_revision_id is not None:
            sets.append("source_revision_id=?")
            args.append(source_revision_id)
        sets.append("updated_at=?")
        args.append(ts)
        args.extend((work_id, episode_no))
        conn.execute(
            f"UPDATE publish_editions SET {', '.join(sets)}"
            " WHERE work_id=? AND episode_no=?", args)
    conn.commit()
    return get_edition(conn, work_id, episode_no)


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


def episode_head_text(conn: sqlite3.Connection, work_id: int,
                      episode_no: int) -> str:
    """Latest readable text of an episode (draft wins, else revision)."""
    from . import manuscripts

    try:
        doc = manuscripts.ensure_doc_row(conn, work_id, episode_no)
    except Exception:
        doc = {}
    draft = (doc.get("draft_content") if doc else "") or ""
    if draft.strip():
        return draft
    row = conn.execute(
        "SELECT text FROM manuscript_revisions WHERE work_id=? AND episode_no=?"
        " ORDER BY version DESC LIMIT 1", (work_id, episode_no)).fetchone()
    return (row["text"] if row else "") or ""


def strip_teaser_block(text: str) -> str:
    """Drop a previously appended teaser block so re-runs replace it."""
    idx = (text or "").rfind("\n\n" + TEASER_MARK)
    if idx < 0:
        return text or ""
    return (text[:idx]).rstrip() + "\n"


def compose_with_teaser(body: str, teaser: str) -> str:
    return strip_teaser_block(body).rstrip() + "\n\n" + TEASER_MARK + "\n" + teaser.strip() + "\n"


def make_teaser(meta, work_id: int, episode_no: int,
                next_text: str) -> str:
    """Ask the model for a ≤100-char teaser from the next episode head."""
    import json

    head = (next_text or "").strip()[:TEASER_CONTEXT_CHARS]
    if not head:
        raise ValueError("다음 화 원고가 없습니다")
    out = meta.chat_json(
        bundle="publish-teaser", episode=episode_no, system=PROMPT,
        work_id=work_id,
        user="다음 화 앞부분이다. 스켈레톤: {\"teaser\": str}\n" + json.dumps(
            {"episode_no": episode_no + 1, "head": head}, ensure_ascii=False),
        schema=TeaserOut, max_tokens=8000)
    teaser = (out.get("teaser") or "").strip()
    if not teaser:
        raise ValueError("AI가 빈 예고 문구를 반환했습니다")
    if len(teaser) > TEASER_MAX_CHARS:
        teaser = teaser[:TEASER_MAX_CHARS].rstrip()
    return teaser


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
