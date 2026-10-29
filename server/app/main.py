"""Muse Novel Studio API server."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import budget as budgetmod
from . import db as dbmod
from .config import PROJECT_ROOT, load_settings
from .meta_client import build_client
from .routes import entities, episodes, ideas, nodes, ops, works

settings = load_settings()

app = FastAPI(title="Muse Novel Studio", version="0.2.0")


def _recorder(record: dict) -> None:
    conn = dbmod.connect()
    try:
        budgetmod.record_llm_call(conn, record)
    finally:
        conn.close()


def _budget_checker() -> None:
    conn = dbmod.connect()
    try:
        budgetmod.check_budget(conn, settings.daily_token_budget)
    finally:
        conn.close()


def get_meta_client():
    """One MetaClient per call site (stateless, sqlite-backed recording)."""
    return build_client(settings, recorder=_recorder,
                        budget_checker=_budget_checker)


app.include_router(entities.router)
app.include_router(works.router)
app.include_router(episodes.router)
app.include_router(ideas.router)
app.include_router(nodes.router)
app.include_router(ops.router)


@app.get("/api/v1/health")
def health() -> JSONResponse:
    # Never include the API key or key fragments here.
    try:
        conn = dbmod.connect()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        db_status = "ok"
    except Exception:
        db_status = "error"
    return JSONResponse({
        "ok": db_status == "ok",
        "db": db_status,
        **settings.public_summary(),
    })


# Serve the static frontend (plain HTML/CSS/JS, no build step) at /.
_dist = PROJECT_ROOT / "frontend"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="spa")
