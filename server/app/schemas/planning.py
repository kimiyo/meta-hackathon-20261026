"""Pydantic output schemas for the planning bundle (§6.1)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Concept(BaseModel):
    title_candidates: list[str] = Field(default_factory=list)
    genre: str = ""
    tone: str = ""
    theme: str = ""
    ending: str = ""


class Section(BaseModel):
    range: str = ""  # e.g. "1-10"
    summary: str = ""


class ConceptOutput(BaseModel):
    concept: Concept
    sections: list[Section] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EpisodeOutline(BaseModel):
    episode_no: int
    title: str = ""
    goal: str = ""
    events: list[str] = Field(default_factory=list)
    char_changes: list[str] = Field(default_factory=list)
    foreshadowing: list[str] = Field(default_factory=list)
    links: dict = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)


class OutlineBatch(BaseModel):
    episode_outlines: list[EpisodeOutline]
    warnings: list[str] = Field(default_factory=list)
