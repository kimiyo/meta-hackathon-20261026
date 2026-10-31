"""StorySpine API server."""
from __future__ import annotations

import threading
from dataclasses import replace as _dc_replace

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import budget as budgetmod
from . import db as dbmod
from .config import PROJECT_ROOT, load_settings
from .meta_client import build_client
from .routes import entities, episodes, ideas, nodes, ops, works

settings = load_settings()
_settings_lock = threading.Lock()

app = FastAPI(title="StorySpine", version="0.2.0")


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


def current_settings():
    """Current process settings (model may have been switched at runtime)."""
    return settings


def build_client_with_model(model_id: str):
    """Temp client for verifying a candidate model (does not switch)."""
    return build_client(_dc_replace(settings, model_text_id=model_id),
                        recorder=_recorder, budget_checker=_budget_checker)


def switch_model(model_id: str):
    """Replace the process model id (thread-safe). Next AI call wins.

    Runtime-only: a restart restores .env. All settings readers import
    the module global at call time, so rebinding is safe.
    """
    global settings
    with _settings_lock:
        settings = _dc_replace(settings, model_text_id=model_id)
        return settings


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


class NoStoreStaticFiles(StaticFiles):
    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


# Serve the static frontend (plain HTML/CSS/JS, no build step) at /.
_frontend = PROJECT_ROOT / "frontend"
if _frontend.is_dir():
    app.mount("/", NoStoreStaticFiles(directory=_frontend, html=True), name="spa")
