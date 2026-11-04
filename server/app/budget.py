"""Token budget aggregation + llm_calls recording over SQLite (§4.3, §3.2)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import now_iso
from .errors import BudgetExceeded

# English output-budget basis (plan §8.1). INTERIM, pending real model
# measurement: ceil(words_max * 9/5) = 1.3 validation overshoot x ~1.35
# output tokens per English word, plus headroom because max_tokens also
# covers reasoning. Do NOT tune this ratio from Korean char data — the
# per-word rate must be re-measured on real English drafts (target
# lengths, output tokens, retry rate) before launch, and the EN default
# episode length itself is still undecided (plan §8.1: no mechanical
# char→word conversion). Kept separate from the KR char basis in
# drafting.BUDGET_NUM/BUDGET_DEN so each market's math stays explicit.
EN_BUDGET_NUM, EN_BUDGET_DEN = 9, 5


def en_draft_output_budget(words_max: int, floor: int = 8000) -> int:
    """Ceiling-only output budget for an English (words-unit) draft.

    Mirrors drafting.draft_output_budget on the interim EN basis above.
    Actual spend is unchanged when text is short; only the ceiling moves.
    """
    return max(floor,
               (words_max * EN_BUDGET_NUM + EN_BUDGET_DEN - 1)
               // EN_BUDGET_DEN)


def record_llm_call(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        "INSERT INTO llm_calls(ts, bundle, episode_no, work_id, model,"
        " input_tokens, output_tokens, latency_ms, ok, error,"
        " prompt_system, prompt_user, output_text, finish_reason,"
        " reasoning_tokens, market, writing_locale, prompt_feature,"
        " prompt_version, preset_id, preset_version)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (record.get("ts", now_iso()), record["bundle"], record.get("episode_no"),
         record.get("work_id"), record["model"], record.get("input_tokens", 0),
         record.get("output_tokens", 0), record.get("latency_ms", 0),
         record.get("ok", 0), (record.get("error", "") or "")[:300],
         record.get("prompt_system", "") or "",
         record.get("prompt_user", "") or "",
         record.get("output_text", "") or "",
         record.get("finish_reason", "") or "",
         record.get("reasoning_tokens", 0) or 0,
         record.get("market", "") or "",
         record.get("writing_locale", "") or "",
         record.get("prompt_feature", "") or "",
         record.get("prompt_version", "") or "",
         record.get("preset_id", "") or "",
         record.get("preset_version", "") or ""),
    )
    conn.commit()


def daily_usage(conn: sqlite3.Connection, day: str | None = None) -> dict:
    day = day or datetime.now(timezone.utc).date().isoformat()
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens),0) AS tin,"
        " COALESCE(SUM(output_tokens),0) AS tout, COUNT(*) AS n"
        " FROM llm_calls WHERE substr(ts,1,10)=?",
        (day,),
    ).fetchone()
    return {"day": day, "input_tokens": row["tin"],
            "output_tokens": row["tout"], "calls": row["n"],
            "total_tokens": row["tin"] + row["tout"]}


def check_budget(conn: sqlite3.Connection, daily_budget: int) -> dict:
    usage = daily_usage(conn)
    if usage["total_tokens"] >= daily_budget:
        raise BudgetExceeded(
            f"daily token budget exhausted: {usage['total_tokens']}/{daily_budget}")
    return {**usage, "budget": daily_budget,
            "remaining": daily_budget - usage["total_tokens"]}


def budget_status(conn: sqlite3.Connection, daily_budget: int) -> dict:
    usage = daily_usage(conn)
    return {**usage, "budget": daily_budget,
            "remaining": max(0, daily_budget - usage["total_tokens"])}
