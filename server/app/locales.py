"""Market/locale registry for Korea/US support (plan §3).

Single source of truth for which markets and UI locales are enabled.
Adding a future market means adding one MARKETS entry plus its locale
data — never `if market == ...` branches in feature code.
"""
from __future__ import annotations

import sqlite3

MARKETS: dict[str, dict[str, str]] = {
    "KR": {
        "default_ui_locale": "ko-KR",
        "default_writing_locale": "ko-KR",
        "preset_catalog": "kr",
        "default_length_unit": "characters",
    },
    "US": {
        "default_ui_locale": "en-US",
        "default_writing_locale": "en-US",
        "preset_catalog": "us",
        "default_length_unit": "words",
    },
}

UI_LOCALES: list[str] = ["ko-KR", "en-US"]

DEFAULT_MARKET = "KR"
DEFAULT_WRITING_LOCALE = "ko-KR"
DEFAULT_UI_LOCALE = "ko-KR"


def validate_market(market: str) -> str:
    """Return the normalized market code, or raise ValueError."""
    code = (market or "").strip().upper()
    if code not in MARKETS:
        supported = ", ".join(sorted(MARKETS))
        raise ValueError(f"unknown market: {market!r} (supported: {supported})")
    return code


def validate_writing_locale(locale: str) -> str:
    """Return the normalized writing locale, or raise ValueError."""
    loc = (locale or "").strip()
    known = {cfg["default_writing_locale"] for cfg in MARKETS.values()}
    if loc not in known:
        supported = ", ".join(sorted(known))
        raise ValueError(
            f"unknown writing_locale: {locale!r} (supported: {supported})")
    return loc


def validate_ui_locale(locale: str) -> str:
    """Return the normalized UI locale, or raise ValueError."""
    loc = (locale or "").strip()
    if loc not in UI_LOCALES:
        supported = ", ".join(UI_LOCALES)
        raise ValueError(
            f"unknown ui_locale: {locale!r} (supported: {supported})")
    return loc


def market_config(market: str) -> dict[str, str]:
    """Read-only copy of one market's registry entry."""
    return dict(MARKETS[validate_market(market)])


def locales_payload() -> dict:
    """Exact body for GET /api/v1/locales (plan §3.1)."""
    return {
        "markets": [
            {
                "code": code,
                "name_key": f"market.{code}",
                "default_ui_locale": cfg["default_ui_locale"],
                "default_writing_locale": cfg["default_writing_locale"],
            }
            for code, cfg in MARKETS.items()
        ],
        "ui_locales": list(UI_LOCALES),
    }


# Tables whose rows mean "AI-generated content or manuscript exists"
# for plan §4.4. manuscript_docs rows are created lazily on view, so an
# empty row (no draft, revision 0, nothing approved) does not lock.
_CONTENT_CHECKS: tuple[tuple[str, str], ...] = (
    ("idea_threads", "SELECT COUNT(*) FROM idea_threads WHERE work_id=?"),
    ("story_designs", "SELECT COUNT(*) FROM story_designs WHERE work_id=?"),
    ("manuscript_revisions",
     "SELECT COUNT(*) FROM manuscript_revisions WHERE work_id=?"),
    ("manuscript_docs",
     "SELECT COUNT(*) FROM manuscript_docs WHERE work_id=?"
     " AND (draft_content <> '' OR revision > 0"
     " OR approved_revision_id IS NOT NULL)"),
    ("episode_jobs", "SELECT COUNT(*) FROM episode_jobs WHERE work_id=?"),
    ("review_messages",
     "SELECT COUNT(*) FROM review_messages WHERE work_id=?"),
)


def can_change_locale(conn: sqlite3.Connection,
                      work_id: int) -> tuple[bool, str]:
    """Whether a work's market/writing_locale may change (plan §4.4).

    Returns (True, "") when no AI-generated content or manuscript exists
    yet, else (False, reason) naming the blocking content kind.
    """
    for kind, sql in _CONTENT_CHECKS:
        try:
            n = conn.execute(sql, (work_id,)).fetchone()[0]
        except sqlite3.OperationalError:
            continue  # table predates this checkout; treat as empty
        if n:
            return False, f"{kind}:{n}"
    return True, ""
