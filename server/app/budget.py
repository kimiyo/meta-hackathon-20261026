"""Token budget aggregation + llm_calls recording over SQLite (§4.3, §3.2)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from .db import now_iso
from .errors import BudgetExceeded


def record_llm_call(conn: sqlite3.Connection, record: dict) -> None:
    conn.execute(
        "INSERT INTO llm_calls(ts, bundle, episode_no, work_id, model,"
        " input_tokens, output_tokens, latency_ms, ok, error,"
        " prompt_system, prompt_user, output_text)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (record.get("ts", now_iso()), record["bundle"], record.get("episode_no"),
         record.get("work_id"), record["model"], record.get("input_tokens", 0),
         record.get("output_tokens", 0), record.get("latency_ms", 0),
         record.get("ok", 0), (record.get("error", "") or "")[:300],
         record.get("prompt_system", "") or "",
         record.get("prompt_user", "") or "",
         record.get("output_text", "") or ""),
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
