"""Budget errors shared by meta_client and budget store."""
from __future__ import annotations


class BudgetExceeded(RuntimeError):
    """Raised when the daily token budget is exhausted."""


class LLMStepError(RuntimeError):
    """Structured failure of one LLM step (retryable kinds already exhausted)."""

    def __init__(self, bundle: str, episode: int | None, reason: str, detail: str = ""):
        self.bundle = bundle
        self.episode = episode
        self.reason = reason
        self.detail = detail[:500]
        super().__init__(f"[{bundle} ep={episode}] {reason}: {self.detail}")
