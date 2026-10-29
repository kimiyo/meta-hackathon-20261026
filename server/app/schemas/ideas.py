"""Pydantic output schemas for the ideas/design-assistant bundles."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SettingProposal(BaseModel):
    category: str = "misc"
    title: str = ""
    summary: str = ""
    detail: str = ""


class PartProposal(BaseModel):
    title: str = ""
    summary: str = ""


class StructureProposal(BaseModel):
    use_part: bool | None = None
    use_chapter: bool | None = None
    total_episodes: int | None = None
    parts: list[PartProposal] | None = None


class IdeaChatOut(BaseModel):
    reply: str = ""
    choices: list[str] = Field(default_factory=list)
    settings: list[SettingProposal] = Field(default_factory=list)
    synopsis_proposal: str = ""
    structure: StructureProposal | None = None


class StructureHint(BaseModel):
    use_part: bool = False
    use_chapter: bool = False
    total_episodes: int | None = None
    reason: str = ""


class ExtractOut(BaseModel):
    synopsis_proposal: str = ""
    settings: list[SettingProposal] = Field(default_factory=list)
    structure: StructureHint = Field(default_factory=StructureHint)


class ChildProposal(BaseModel):
    kind: str = ""
    title: str = ""
    logline: str = ""
    body: dict = Field(default_factory=dict)


class DesignChatOut(BaseModel):
    reply: str = ""
    proposed_children: list[ChildProposal] = Field(default_factory=list)
    proposed_patch: dict = Field(default_factory=dict)


class ExpandOut(BaseModel):
    children: list[ChildProposal] = Field(default_factory=list)


class HandoffNode(BaseModel):
    kind: str = ""
    title: str = ""
    summary: str = ""
    episode_no: int = 0
    children: list["HandoffNode"] = Field(default_factory=list)


class HandoffOut(BaseModel):
    tree: list[HandoffNode] = Field(default_factory=list)


HandoffNode.model_rebuild()
