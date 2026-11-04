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
from .routes import auth, consistency, design_threads, entities, episodes, ideas, locales, nodes, ops, works

settings = load_settings()
_settings_lock = threading.Lock()

app = FastAPI(title="StorySpine", version="0.2.0")


def _recorder_for(author_id: int | None):
    def _recorder(record: dict) -> None:
        conn = dbmod.connect()
        try:
            budgetmod.record_llm_call(conn, {**record, "author_id": author_id})
        finally:
            conn.close()
    return _recorder


def _checker_for(author_id: int | None):
    def _budget_checker() -> None:
        conn = dbmod.connect()
        try:
            budgetmod.check_budget(conn, settings.daily_token_budget,
                                   author_id=author_id)
        finally:
            conn.close()
    return _budget_checker


def get_meta_client(author_id: int | None = None):
    """One MetaClient per call site (stateless, sqlite-backed recording).

    author_id scopes recording and the daily budget to one user; None
    keeps the legacy global behavior (admin flows without a user).
    """
    return build_client(settings, recorder=_recorder_for(author_id),
                        budget_checker=_checker_for(author_id))


def current_settings():
    """Current process settings (model may have been switched at runtime)."""
    return settings


def build_client_with_model(model_id: str, author_id: int | None = None):
    """Temp client for verifying a candidate model (does not switch)."""
    return build_client(_dc_replace(settings, model_text_id=model_id),
                        recorder=_recorder_for(author_id),
                        budget_checker=_checker_for(author_id))


def switch_model(model_id: str):
    """Replace the process model id (thread-safe). Next AI call wins.

    Runtime-only: a restart restores .env. All settings readers import
    the module global at call time, so rebinding is safe.
    """
    global settings
    with _settings_lock:
        settings = _dc_replace(settings, model_text_id=model_id)
        return settings


app.include_router(auth.router)
app.include_router(design_threads.router)
app.include_router(entities.router)
app.include_router(works.router)
app.include_router(episodes.router)
app.include_router(consistency.router)
app.include_router(ideas.router)
app.include_router(locales.router)
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


@app.get("/covers/{name}")
def serve_cover(name: str):
    """Cover image bytes. Per-request lookup so test DBs resolve."""
    from fastapi.responses import FileResponse

    if "/" in name or "\\" in name or not name:
        return JSONResponse({"detail": "not found"}, status_code=404)
    path = dbmod.covers_dir() / name
    if not path.is_file():
        return JSONResponse({"detail": "not found"}, status_code=404)
    return FileResponse(path)

# Serve the static frontend (plain HTML/CSS/JS, no build step) at /.
_frontend = PROJECT_ROOT / "frontend"
if _frontend.is_dir():
    app.mount("/", NoStoreStaticFiles(directory=_frontend, html=True), name="spa")
