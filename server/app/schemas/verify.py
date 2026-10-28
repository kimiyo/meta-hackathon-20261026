"""Pydantic output schemas for the verify bundle (§6.4)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Verdict(BaseModel):
    item_id: int
    verdict: str = ""
    reason: str = ""
    counter_evidence_quote: str = ""


class VerifyOutput(BaseModel):
    verdicts: list[Verdict] = Field(default_factory=list)
