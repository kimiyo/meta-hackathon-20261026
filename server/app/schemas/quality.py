"""Pydantic output schemas for the quality bundle (§6.5)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Scores(BaseModel):
    immersion: float = 0.0
    tension: float = 0.0
    coherence: float = 0.0


class ProofreadEdit(BaseModel):
    start_quote: str = ""
    replacement: str = ""
    reason: str = ""


class QualityOutput(BaseModel):
    scores: Scores = Field(default_factory=Scores)
    issues: list[str] = Field(default_factory=list)
    proofread_edits: list[ProofreadEdit] = Field(default_factory=list)
