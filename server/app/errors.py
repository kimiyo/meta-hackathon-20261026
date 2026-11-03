"""Budget errors shared by meta_client and budget store.

i18n error details (plan §5.4, §9): new code-carrying failures use
{"code": ..., "params": {...}} as the HTTPException detail so the
frontend can map code -> error.<CODE> in the active UI locale.
Legacy string details keep working; senders and receivers must
handle both shapes during the transition.
"""
from __future__ import annotations

from fastapi import HTTPException


class BudgetExceeded(RuntimeError):
    """Raised when the daily token budget is exhausted."""


class LLMStepError(RuntimeError):
    """Structured failure of one LLM step (retryable kinds already exhausted)."""

    def __init__(self, bundle: str, episode: int | None, reason: str, detail: str = ""):
        self.bundle = bundle
        self.episode = episode
        self.reason = reason
        self.detail = detail[:500]
        super().__init__(f"[{bundle} ep={episode}] {reason}: {self.detail}")


# Catalog of structured error codes. Frontend renders error.<CODE> with
# params substitution; see docs/i18n-contracts.md.
WORK_LOCALE_LOCKED = "WORK_LOCALE_LOCKED"  # market/writing_locale change refused (§4.4)
UNKNOWN_MARKET = "UNKNOWN_MARKET"
UNKNOWN_WRITING_LOCALE = "UNKNOWN_WRITING_LOCALE"
UNKNOWN_UI_LOCALE = "UNKNOWN_UI_LOCALE"
UNKNOWN_PLATFORM = "UNKNOWN_PLATFORM"
WORK_TITLE_REQUIRED = "WORK_TITLE_REQUIRED"


def error_detail(code: str, params: dict | None = None,
                 extra: dict | None = None) -> dict:
    """Build a {"code", "params"} detail dict (plan §5.4).

    Extra keys (e.g. latestRevision for manuscript conflicts) ride along
    untouched so existing consumers keep working.
    """
    detail: dict = {"code": code, "params": dict(params or {})}
    if extra:
        detail.update(extra)
    return detail


def coded_error(status: int, code: str, params: dict | None = None,
                extra: dict | None = None) -> HTTPException:
    """HTTPException whose detail is error_detail(code, params, extra)."""
    return HTTPException(status, error_detail(code, params, extra))


def is_coded_detail(detail) -> bool:
    """True when a response detail carries the {code, params} shape."""
    return isinstance(detail, dict) and isinstance(detail.get("code"), str)
