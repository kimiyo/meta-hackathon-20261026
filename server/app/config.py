"""Server configuration: loads .env from project root, validates required values.

The process refuses to boot when MODEL_API_KEY is missing or empty.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip("'\"")
    return values


@dataclass(frozen=True)
class Settings:
    model_api_key: str
    model_base_url: str
    model_text_id: str
    model_mode: str
    request_timeout_s: int
    llm_max_retry: int
    max_tokens_per_call: int
    daily_token_budget: int
    episode_retry_limit: int

    def public_summary(self) -> dict[str, str]:
        """Key-free summary safe for /health responses and logs."""
        return {
            "model": self.model_text_id,
            "mode": self.model_mode,
            "base_url": self.model_base_url,
        }


def load_settings(env: dict[str, str] | None = None) -> Settings:
    merged = _parse_dotenv(PROJECT_ROOT / ".env")
    merged.update({k: v for k, v in (os.environ if env is None else env).items()
                   if k in _KNOWN_KEYS})
    missing = [k for k in ("MODEL_API_KEY",) if not merged.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required config: {', '.join(missing)}. "
            "Set it in .env (see .env.example). Refusing to boot."
        )
    return Settings(
        model_api_key=merged["MODEL_API_KEY"],
        model_base_url=merged.get("MODEL_BASE_URL", "https://api.meta.ai/v1"),
        model_text_id=merged.get("MODEL_TEXT_ID", "muse-spark-1.3"),
        model_mode=merged.get("MODEL_MODE", "standard"),
        request_timeout_s=int(merged.get("REQUEST_TIMEOUT_S", "120")),
        llm_max_retry=int(merged.get("LLM_MAX_RETRY", "3")),
        max_tokens_per_call=int(merged.get("MAX_TOKENS_PER_CALL", "8000")),
        daily_token_budget=int(merged.get("DAILY_TOKEN_BUDGET", "1000000")),
        episode_retry_limit=int(merged.get("EPISODE_RETRY_LIMIT", "3")),
    )


_KNOWN_KEYS = {
    "MODEL_API_KEY", "MODEL_BASE_URL", "MODEL_TEXT_ID", "MODEL_MODE",
    "REQUEST_TIMEOUT_S", "LLM_MAX_RETRY", "MAX_TOKENS_PER_CALL",
    "DAILY_TOKEN_BUDGET", "EPISODE_RETRY_LIMIT",
}
