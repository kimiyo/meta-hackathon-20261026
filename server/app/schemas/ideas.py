"""Pydantic output schemas for the ideas/design-assistant bundles."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _as_text_list(v):
    """Accept ["A) ..."] and also [{"label": ..., "text": ...}] shapes.

    The model sometimes returns choice objects; keep the readable text
    instead of failing the whole turn on schema validation.
    """
    if not isinstance(v, list):
        return v
    out = []
    for item in v:
        if isinstance(item, dict):
            parts = [str(item[k]).strip() for k in ("label", "text", "value")
                     if isinstance(item.get(k), (str, int)) and str(item[k]).strip()]
            # {"label": "A", "text": "..."} -> "A) ..."; drop a bare letter value
            if len(parts) > 1 and len(parts[0]) <= 2:
                item = f"{parts[0]}) {parts[1]}"
            else:
                item = max(parts, key=len) if parts else ""
        if isinstance(item, (str, int)) and str(item).strip():
            out.append(str(item).strip())
    return out


class SettingProposal(BaseModel):
    code: str = ""  # existing D-number when revising, "" for a new entry
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
    merge_from: list[str] = Field(default_factory=list)  # codes merged away
    synopsis_proposal: str = ""
    structure: StructureProposal | None = None

    _texts = field_validator("choices", "merge_from", mode="before")(
        lambda v: _as_text_list(v))


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
