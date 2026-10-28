"""Pydantic output schemas for the entity bundle (§6.3)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EntityItem(BaseModel):
    entity_ref: str
    candidate_name: str = ""
    operation: str = ""
    attribute_path: str = ""
    old_value: Any = ""
    new_value: Any = ""
    story_time: str = ""
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
    cause: str = ""
    consequence: str = ""
    participants: list[EventParticipant] = Field(default_factory=list)


class EntityOutput(BaseModel):
    items: list[EntityItem] = Field(default_factory=list)
    mentions: list[Mention] = Field(default_factory=list)
    events: list[EventItem] = Field(default_factory=list)
