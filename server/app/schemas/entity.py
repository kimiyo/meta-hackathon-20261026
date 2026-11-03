"""Pydantic output schemas for the entity bundle (§6.3 / §2 Fact)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EntityItem(BaseModel):
    entity_ref: str
    candidate_name: str = ""
    kind: str = "attribute"
    operation: str = ""
    attribute_path: str = ""
    old_value: Any = ""
    new_value: Any = ""
    standard_value: Any = ""
    story_time: str = ""
    narrative_mode: str = "narration"
    evidence_quote: str = ""


class Mention(BaseModel):
    entity_ref: str = ""
    quote: str = ""


class EventParticipant(BaseModel):
    entity_ref: str = ""
    role: str = ""


class EventItem(BaseModel):
    summary: str = ""
    story_time: str = ""
    narrative_mode: str = "narration"
    cause: str = ""
    consequence: str = ""
    evidence_quote: str = ""
    participants: list[EventParticipant] = Field(default_factory=list)


class EntityOutput(BaseModel):
    items: list[EntityItem] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)
    events: list[EventItem] = Field(default_factory=list)
