"""Consistency-chat schemas: structured issue items + memory draft."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class IssueItem(BaseModel):
    severity: Literal["critical", "major", "minor"] = "major"
    title: str = ""
    detail: str = ""
    verdict: Literal["conflict", "explained", "uncertain"] = "uncertain"
    manuscript_quote: str = ""
    evidence_episode: int | None = None
    evidence_quote: str = ""
    entity_id: str = ""
    fact_refs: list[int] = Field(default_factory=list)


class ConsistencyOutput(BaseModel):
    issues: list[IssueItem] = Field(default_factory=list)


class ConsistencyChatOutput(BaseModel):
    reply: str = ""
    issues: list[IssueItem] = Field(default_factory=list)
