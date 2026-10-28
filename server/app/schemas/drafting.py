"""Pydantic output schemas for the drafting bundle (§6.2)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LookupRequest(BaseModel):
    kind: str = ""  # entity_state|event_search|mention_search
    entity_id: str | None = None
    episode: int | None = None
    query: str = ""


class DraftOutput(BaseModel):
    manuscript: str
    lookup_requests: list[LookupRequest] = Field(default_factory=list)
