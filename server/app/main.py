"""Muse Novel Studio API server (Phase 0 scaffolding)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, load_settings

settings = load_settings()

app = FastAPI(title="Muse Novel Studio", version="0.1.0")


@app.get("/api/v1/health")
def health() -> JSONResponse:
    # Never include the API key or key fragments here.
    return JSONResponse({
        "ok": True,
        "db": "pending",  # Phase 2 wires SQLite
        **settings.public_summary(),
    })


# Serve the built SPA at / when a production build exists (Phase 0+: optional).
_dist = PROJECT_ROOT / "web" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="spa")
