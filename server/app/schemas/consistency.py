"""Consistency-chat schemas: structured issue items + memory draft."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IssueItem(BaseModel):
    severity: Literal["critical", "major", "minor"] = "major"
    title: str = ""
    detail: str = ""
    manuscript_quote: str = ""
    evidence_episode: int | None = None
    evidence_quote: str = ""
    entity_id: str = ""


class ConsistencyOutput(BaseModel):
    issues: list[IssueItem] = Field(default_factory=list)
    memory_draft: str = ""


class ConsistencyChatOutput(BaseModel):
    reply: str = ""
    issues: list[IssueItem] = Field(default_factory=list)


class MemoryDraftOutput(BaseModel):
    memory_draft: str = ""
