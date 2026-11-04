"""Works & designs routes (§5.1). Work keys accept integer id or uid."""
from __future__ import annotations

import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import custom_styles, drafting, locales, planning, publishing, style_presets
from ..db import find_work, new_uid, now_iso, touch_work
from ..errors import (LLMStepError, UNKNOWN_MARKET, UNKNOWN_PLATFORM,
                      UNKNOWN_WRITING_LOCALE, WORK_LOCALE_LOCKED, coded_error)
from ..ideas import check_total_episodes
from .deps import (get_conn, get_current_author, require_design,
                       require_work)

router = APIRouter(prefix="/api/v1", tags=["works"])

TITLE_MAX = 80


def get_meta():
    from ..main import get_meta_client
    return get_meta_client()


def _work_or_404(conn: sqlite3.Connection, key: str,
                 author_id: int) -> dict:
    return require_work(conn, key, author_id)


class WorkIn(BaseModel):
    title: str
    logline: str = ""
    market: str | None = None
    writing_locale: str | None = None
    platform: str | None = None


@router.post("/works")
def create_work(body: WorkIn, conn: sqlite3.Connection = Depends(get_conn),
                author: dict = Depends(get_current_author)):
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(400, "title must not be blank")
    if len(title) > TITLE_MAX:
        raise HTTPException(400, f"title must be ≤{TITLE_MAX} chars")
    try:
        market = locales.validate_market(
            body.market if body.market is not None
            else locales.DEFAULT_MARKET)
    except ValueError:
        raise coded_error(400, UNKNOWN_MARKET, {"value": body.market})
    try:
        wlocale = locales.validate_writing_locale(
            body.writing_locale if body.writing_locale is not None
            else locales.DEFAULT_WRITING_LOCALE)
    except ValueError:
        raise coded_error(400, UNKNOWN_WRITING_LOCALE,
                           {"value": body.writing_locale})
    if body.platform is None:
        # US works store "" until the US default is decided; KR works
        # keep the DB default. Never auto-apply the Korean default (§4.6).
        platform_value: str | None = "" if market == "US" else None
    elif body.platform == "":
        platform_value = ""
    else:
        try:
            platform_value = publishing.check_platform(body.platform)
        except ValueError:
            raise coded_error(400, UNKNOWN_PLATFORM,
                               {"value": body.platform})
    uid, ts = new_uid(), now_iso()
    cols = ["uid", "author_id", "title", "logline", "market",
            "writing_locale", "created_at", "updated_at"]
    vals: list = [uid, author["id"], title, body.logline, market,
                  wlocale, ts, ts]
    if platform_value is not None:
        cols.insert(4, "platform")
        vals.insert(4, platform_value)
    cur = conn.execute(
        f"INSERT INTO works({', '.join(cols)})"
        f" VALUES({', '.join('?' * len(cols))})", vals)
    conn.commit()
    return dict(conn.execute("SELECT * FROM works WHERE id=?",
                             (cur.lastrowid,)).fetchone())


@router.get("/works")
def list_works(conn: sqlite3.Connection = Depends(get_conn),
             author: dict = Depends(get_current_author)):
    rows = conn.execute(
        "SELECT w.*, (SELECT COUNT(*) FROM episode_jobs j"
        " JOIN story_designs d ON d.id=j.design_id"
        " WHERE d.work_id=w.id AND j.state='PUBLISHING')"
        " AS completed_episodes,"
        " (SELECT scope_total_episodes FROM story_designs"
        " WHERE work_id=w.id ORDER BY version DESC LIMIT 1)"
        " AS total_episodes"
        " FROM works w WHERE w.author_id=? ORDER BY w.updated_at DESC,"
        " w.id DESC", (author["id"],)).fetchall()
    return {"works": [dict(r) for r in rows]}


@router.get("/works/{key}")
def get_work(key: str, conn: sqlite3.Connection = Depends(get_conn),
             author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    latest = conn.execute(
        "SELECT id, version, scope_total_episodes, status, approved_at"
        " FROM story_designs WHERE work_id=? ORDER BY version DESC LIMIT 1",
        (work["id"],)).fetchone()
    work["latest_design"] = dict(latest) if latest else None
    work["cover_url"] = publishing.cover_url(work.get("cover_path") or "")
    work["author_pen_name"] = author.get("pen_name") or ""
    work["platforms"] = list(publishing.PLATFORMS)
    return work


class DesignIn(BaseModel):
    keywords: str
    total_episodes: int = 20
    constraints: list[str] = []
    prev_feedback: str = ""


@router.post("/works/{key}/designs")
def create_design(key: str, body: DesignIn,
                  conn: sqlite3.Connection = Depends(get_conn),
                  meta=Depends(get_meta),
                  author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    try:
        check_total_episodes(body.total_episodes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        out = planning.generate_design(
            meta, conn, work_id=work["id"], keywords=body.keywords,
            total_episodes=body.total_episodes, constraints=body.constraints,
            prev_feedback=body.prev_feedback)
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    except LLMStepError as e:
        raise HTTPException(502, f"planning failed: {e.reason} {e.detail}")
    touch_work(conn, work["id"])
    conn.commit()
    return out


@router.get("/designs/{design_id}")
def get_design(design_id: int, conn: sqlite3.Connection = Depends(get_conn),
             author: dict = Depends(get_current_author)):
    require_design(conn, design_id, author["id"])
    d = planning.get_design(conn, design_id)
    if not d:
        raise HTTPException(404, f"design {design_id} not found")
    return d


class OutlinePatch(BaseModel):
    title: str | None = None
    goal: str | None = None
    events: list[str] | None = None
    char_changes: list[str] | None = None
    foreshadowing: list[str] | None = None
    links: dict | None = None
    constraints: list[str] | None = None


@router.patch("/designs/{design_id}/outlines/{episode_no}")
def patch_outline(design_id: int, episode_no: int, body: OutlinePatch,
                  conn: sqlite3.Connection = Depends(get_conn),
                  author: dict = Depends(get_current_author)):
    require_design(conn, design_id, author["id"])
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        return planning.update_outline(conn, design_id, episode_no, fields)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


BLURB_MAX = 2000


class WorkPatch(BaseModel):
    title: str | None = None
    logline: str | None = None
    publish_title: str | None = None
    publish_pen_name: str | None = None
    publish_blurb: str | None = None
    platform: str | None = None
    market: str | None = None
    writing_locale: str | None = None


@router.patch("/works/{key}")
def patch_work(key: str, body: WorkPatch,
               conn: sqlite3.Connection = Depends(get_conn),
               author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    sets, args = [], []
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(400, "title must not be blank")
        if len(body.title.strip()) > TITLE_MAX:
            raise HTTPException(400, f"title must be ≤{TITLE_MAX} chars")
        sets.append("title=?")
        args.append(body.title.strip())
    if body.logline is not None:
        sets.append("logline=?")
        args.append(body.logline)
    if body.publish_title is not None:
        if len(body.publish_title.strip()) > TITLE_MAX:
            raise HTTPException(400,
                                f"publish_title must be ≤{TITLE_MAX} chars")
        sets.append("publish_title=?")
        args.append(body.publish_title.strip())
    if body.publish_pen_name is not None:
        if len(body.publish_pen_name.strip()) > TITLE_MAX:
            raise HTTPException(400,
                                f"publish_pen_name must be ≤{TITLE_MAX} chars")
        sets.append("publish_pen_name=?")
        args.append(body.publish_pen_name.strip())
    if body.publish_blurb is not None:
        if len(body.publish_blurb) > BLURB_MAX:
            raise HTTPException(400,
                                f"publish_blurb must be ≤{BLURB_MAX} chars")
        sets.append("publish_blurb=?")
        args.append(body.publish_blurb)
    if body.platform is not None:
        if body.platform == "":
            # Explicit empty: US works awaiting the US default (§4.6).
            sets.append("platform=?")
            args.append("")
        else:
            try:
                sets.append("platform=?")
                args.append(publishing.check_platform(body.platform))
            except ValueError:
                raise coded_error(400, UNKNOWN_PLATFORM,
                                   {"value": body.platform})
    if body.market is not None or body.writing_locale is not None:
        try:
            new_market = (locales.validate_market(body.market)
                          if body.market is not None
                          else (work.get("market") or locales.DEFAULT_MARKET))
            new_wlocale = (locales.validate_writing_locale(body.writing_locale)
                           if body.writing_locale is not None
                           else (work.get("writing_locale")
                                 or locales.DEFAULT_WRITING_LOCALE))
        except ValueError as e:
            raise HTTPException(400, str(e))
        if (new_market != work.get("market")
                or new_wlocale != work.get("writing_locale")):
            allowed, reason = locales.can_change_locale(conn, work["id"])
            if not allowed:
                raise coded_error(409, WORK_LOCALE_LOCKED,
                                   {"reason": reason,
                                    "market": work.get("market"),
                                    "writing_locale":
                                        work.get("writing_locale")})
            sets.append("market=?")
            args.append(new_market)
            sets.append("writing_locale=?")
            args.append(new_wlocale)
    if not sets:
        raise HTTPException(400, "no valid work fields")
    args.append(work["id"])
    conn.execute(f"UPDATE works SET {', '.join(sets)} WHERE id=?", args)
    touch_work(conn, work["id"])
    conn.commit()
    out = dict(conn.execute("SELECT * FROM works WHERE id=?",
                            (work["id"],)).fetchone())
    out["cover_url"] = publishing.cover_url(out.get("cover_path") or "")
    out["author_pen_name"] = author.get("pen_name") or ""
    return out


class CoverIn(BaseModel):
    filename: str = ""
    content_base64: str = ""


@router.post("/works/{key}/cover")
def upload_cover(key: str, body: CoverIn,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    try:
        name = publishing.save_cover(work["id"], body.filename,
                                     body.content_base64,
                                     work.get("writing_locale") or "ko-KR")
    except ValueError as e:
        raise HTTPException(400, str(e))
    old = work.get("cover_path") or ""
    conn.execute("UPDATE works SET cover_path=? WHERE id=?",
                 (name, work["id"]))
    touch_work(conn, work["id"])
    conn.commit()
    if old and old != name:
        publishing.delete_cover_file(old)
    return {"cover_url": publishing.cover_url(name)}


@router.delete("/works/{key}/cover")
def delete_cover(key: str,
                 conn: sqlite3.Connection = Depends(get_conn),
                 author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    old = work.get("cover_path") or ""
    if not old:
        raise HTTPException(404, "no cover")
    conn.execute("UPDATE works SET cover_path='' WHERE id=?", (work["id"],))
    touch_work(conn, work["id"])
    conn.commit()
    publishing.delete_cover_file(old)
    return {"cover_url": ""}


@router.post("/works/{key}/blurb")
def generate_blurb(key: str,
                   conn: sqlite3.Connection = Depends(get_conn),
                   meta=Depends(get_meta),
                   author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    ctx = publishing.collect_blurb_context(conn, work)
    if not publishing.has_blurb_material(ctx):
        if (work.get("writing_locale") or "ko-KR") == "en-US":
            raise HTTPException(409, "no blurb material"
                                     " (need a logline, synopsis, or outlines)")
        raise HTTPException(409, "소개글 재료가 없습니다"
                                 "(로그라인·시놉시스·회차 개요 중 하나 필요)")
    try:
        blurb = publishing.make_blurb(
            meta, work["id"], ctx,
            writing_locale=work.get("writing_locale") or "ko-KR",
            market=work.get("market") or "KR")
    except FileNotFoundError as e:
        raise HTTPException(501, f"AI prompt unavailable: {e}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LLMStepError as e:
        raise HTTPException(502, f"{e.reason}: {e.detail}")
    return {"blurb": blurb}


class WorkDeleteIn(BaseModel):
    title: str


class StylePatch(BaseModel):
    length_min: int | None = None
    length_max: int | None = None
    preset: str | None = None
    custom_style_id: int | None = None


class CustomStyleIn(BaseModel):
    name: str
    pov: str = ""
    diction: str = ""
    feedback: str = ""


class CustomStylePatch(BaseModel):
    name: str | None = None
    pov: str | None = None
    diction: str | None = None
    feedback: str | None = None


@router.get("/works/{key}/style")
def get_work_style(key: str, conn: sqlite3.Connection = Depends(get_conn),
                     author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    market = work.get("market") or locales.DEFAULT_MARKET
    return {"work_id": work["id"],
            **drafting.get_length_range(conn, work["id"]),
            "presets": style_presets.preset_list(market),
            "custom_styles": custom_styles.list_custom(conn, author["id"])}


@router.patch("/works/{key}/style")
def patch_work_style(key: str, body: StylePatch,
                     conn: sqlite3.Connection = Depends(get_conn),
                     author: dict = Depends(get_current_author)):
    work = _work_or_404(conn, key, author["id"])
    if (body.length_min is None) != (body.length_max is None):
        raise HTTPException(400, "length_min and length_max go together")
    if body.preset is not None and body.custom_style_id is not None:
        raise HTTPException(400, "preset and custom_style_id are exclusive")
    if (body.length_min is None and body.preset is None
            and body.custom_style_id is None):
        raise HTTPException(400, "no valid style fields")
    try:
        if body.length_min is not None:
            drafting.set_length_target(conn, work["id"],
                                       body.length_min, body.length_max)
        if body.preset is not None:
            drafting.set_style_preset(conn, work["id"], body.preset)
        if body.custom_style_id is not None:
            custom_styles.apply_custom(conn, work["id"], author["id"],
                                       body.custom_style_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    touch_work(conn, work["id"])
    conn.commit()
    out = drafting.get_length_range(conn, work["id"])
    market = work.get("market") or locales.DEFAULT_MARKET
    return {"work_id": work["id"], **out,
            "presets": style_presets.preset_list(market),
            "custom_styles": custom_styles.list_custom(conn, author["id"])}


@router.get("/style-customs")
def list_style_customs(conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    return {"custom_styles": custom_styles.list_custom(conn, author["id"])}


@router.post("/style-customs")
def create_style_custom(body: CustomStyleIn,
                        conn: sqlite3.Connection = Depends(get_conn),
                        author: dict = Depends(get_current_author)):
    try:
        row = custom_styles.create_custom(conn, author["id"], body.name,
                                          body.pov, body.diction, body.feedback)
    except custom_styles.DuplicateName as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return row


@router.patch("/style-customs/{style_id}")
def patch_style_custom(style_id: int, body: CustomStylePatch,
                       conn: sqlite3.Connection = Depends(get_conn),
                       author: dict = Depends(get_current_author)):
    try:
        return custom_styles.update_custom(conn, author["id"], style_id,
                                           name=body.name, pov=body.pov,
                                           diction=body.diction,
                                           feedback=body.feedback)
    except KeyError as e:
        raise HTTPException(404, str(e))
    except custom_styles.DuplicateName as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/style-customs/{style_id}")
def delete_style_custom(style_id: int,
                        conn: sqlite3.Connection = Depends(get_conn),
                        author: dict = Depends(get_current_author)):
    try:
        return custom_styles.delete_custom(conn, author["id"], style_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@router.delete("/works/{key}")
def delete_work(key: str, body: WorkDeleteIn,
                conn: sqlite3.Connection = Depends(get_conn),
                author: dict = Depends(get_current_author)):
    """Permanent delete after title confirmation. AI usage logs stay.

    Work-scoped rows go; global episode_jobs/manuscript_revisions rows stay
    (episode numbers are global) and become orphans of the deleted design.
    """
    work = _work_or_404(conn, key, author["id"])
    if (body.title or "") != work["title"]:
        raise HTTPException(400, "title does not match")
    wid = work["id"]
    conn.execute("DELETE FROM idea_messages WHERE thread_id IN"
                 " (SELECT id FROM idea_threads WHERE work_id=?)", (wid,))
    conn.execute("DELETE FROM idea_threads WHERE work_id=?", (wid,))
    conn.execute("DELETE FROM story_synopsis WHERE work_id=?", (wid,))
    conn.execute("DELETE FROM synopsis_revisions WHERE work_id=?", (wid,))
    conn.execute("DELETE FROM setting_entries WHERE work_id=?", (wid,))
    conn.execute("UPDATE design_nodes SET parent_id=NULL WHERE design_id IN"
                 " (SELECT id FROM story_designs WHERE work_id=?)", (wid,))
    conn.execute("DELETE FROM design_nodes WHERE design_id IN"
                 " (SELECT id FROM story_designs WHERE work_id=?)", (wid,))
    conn.execute("DELETE FROM episode_outlines WHERE design_id IN"
                 " (SELECT id FROM story_designs WHERE work_id=?)", (wid,))
    conn.execute("DELETE FROM story_designs WHERE work_id=?", (wid,))
    conn.execute("DELETE FROM style_policies WHERE work_id=?", (wid,))
    conn.execute("DELETE FROM works WHERE id=?", (wid,))
    conn.commit()
    return {"deleted": wid}
