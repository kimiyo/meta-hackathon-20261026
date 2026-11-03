"""Supported markets/locales API (plan §3.1). No auth: needed pre-login."""
from __future__ import annotations

from fastapi import APIRouter

from ..locales import locales_payload

router = APIRouter(prefix="/api/v1", tags=["locales"])


@router.get("/locales")
def get_locales() -> dict:
    return locales_payload()
