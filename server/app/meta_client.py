"""The only AI call exit. No other module may import httpx/openai."""
from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from pydantic import BaseModel, ValidationError

from .errors import BudgetExceeded, LLMStepError

log = logging.getLogger(__name__)

RECOVERY_PROMPT = (
    "이전 출력이 JSON 파싱 또는 스키마 검증에 실패했다. "
    "설명 없이 올바른 JSON만 다시 출력하라."
)


def _recovery_prompt(schema: type[BaseModel]) -> str:
    fields = ", ".join(schema.model_fields.keys())
    return f"{RECOVERY_PROMPT} 최상위 키는 정확히 [{fields}] 이어야 한다."

# Exceptions worth one structured retry via recovery prompt: none — recovery is
# a single extra model call, not an exception retry.


class MetaClient:
    """Unique exit to the Meta Model API (OpenAI-compatible chat completions)."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model_id: str,
        timeout_s: int = 120,
        max_retry: int = 3,
        daily_budget: int = 1_000_000,
        recorder: Callable[[dict], None] | None = None,
        budget_checker: Callable[[], None] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._timeout_s = timeout_s
        self._max_retry = max_retry
        self._daily_budget = daily_budget
        self._recorder = recorder or (lambda record: None)
        self._budget_checker = budget_checker or (lambda: None)
        self._http = http_client  # test seam; otherwise built per call

    @property
    def model_id(self) -> str:
        return self._model_id

    def budget_status(self) -> dict:
        return {"model": self._model_id, "daily_budget": self._daily_budget}

    # -- public API ------------------------------------------------------
    def chat_json(
        self,
        *,
        bundle: str,
        episode: int | None,
        system: str,
        user: str,
        schema: type[BaseModel],
        max_tokens: int,
    ) -> dict:
        """Call the model, force JSON, validate against schema. Structured errors."""
        self._check_budget(bundle, episode)
        prompt_ref = _prompt_ref(system, user)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        raw, usage, latency_ms = self._post_with_retry(
            bundle, episode, messages, max_tokens, prompt_ref
        )
        parsed = self._parse_and_validate(raw, schema)
        if parsed is not None:
            self._record(bundle, episode, usage, latency_ms, True, "")
            return parsed

        # Single recovery attempt: ask for JSON-only output once.
        log.warning("[%s] schema/parse failure, 1 recovery attempt (prompt=%s)",
                    bundle, prompt_ref)
        self._check_budget(bundle, episode)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": (raw or "")[:2000]},
            {"role": "user", "content": _recovery_prompt(schema)},
        ]
        raw2, usage2, latency2 = self._post_with_retry(
            bundle, episode, messages, max_tokens, prompt_ref + ":recovery"
        )
        parsed2 = self._parse_and_validate(raw2, schema)
        total_in = usage["in"] + usage2["in"]
        total_out = usage["out"] + usage2["out"]
        if parsed2 is not None:
            self._record(bundle, episode, {"in": total_in, "out": total_out},
                         latency_ms + latency2, True, "")
            return parsed2
        self._record(bundle, episode, {"in": total_in, "out": total_out},
                     latency_ms + latency2, False, "json_schema_failed")
        raise LLMStepError(bundle, episode, "json_schema_failed",
                           (raw2 or "")[:200])

    # -- internals -------------------------------------------------------
    def _check_budget(self, bundle: str, episode: int | None) -> None:
        try:
            self._budget_checker()
        except BudgetExceeded:
            self._record(bundle, episode, {"in": 0, "out": 0}, 0, False,
                         "budget_exceeded")
            raise

    def _client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        return httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout_s,
        )

    def _post_with_retry(self, bundle, episode, messages, max_tokens, prompt_ref):
        import json as _json

        payload = {
            "model": self._model_id,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
        }
        last_error = "unknown"
        own_client = self._http is None
        client = self._client()
        try:
            for attempt in range(self._max_retry + 1):
                t0 = time.time()
                try:
                    resp = client.post(
                        "/chat/completions", json=payload,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
                except (httpx.ConnectError, httpx.TimeoutException,
                        httpx.NetworkError) as e:
                    last_error = f"{type(e).__name__}: {e}"[:200]
                    self._backoff(bundle, attempt, last_error, prompt_ref)
                    continue
                latency_ms = int((time.time() - t0) * 1000)
                if resp.status_code in (429,) or resp.status_code >= 500:
                    last_error = f"http_{resp.status_code}: {resp.text[:200]}"
                    self._backoff(bundle, attempt, last_error, prompt_ref)
                    continue
                if resp.status_code != 200:
                    self._record(bundle, episode, {"in": 0, "out": 0}, latency_ms,
                                 False, f"http_{resp.status_code}")
                    raise LLMStepError(bundle, episode, f"http_{resp.status_code}",
                                       resp.text[:200])
                try:
                    body = resp.json()
                except ValueError:
                    last_error = f"bad_envelope: {resp.text[:200]}"
                    self._backoff(bundle, attempt, last_error, prompt_ref)
                    continue
                try:
                    content = body["choices"][0]["message"].get("content")
                except (KeyError, IndexError, TypeError):
                    content = None
                usage = body.get("usage", {}) or {}
                return (content, {
                    "in": usage.get("prompt_tokens", 0),
                    "out": usage.get("completion_tokens", 0),
                }, latency_ms)
        finally:
            if own_client:
                client.close()
        self._record(bundle, episode, {"in": 0, "out": 0}, 0, False,
                     f"retry_exhausted:{last_error}"[:200])
        raise LLMStepError(bundle, episode, "retry_exhausted", last_error)

    def _backoff(self, bundle: str, attempt: int, error: str, prompt_ref: str) -> None:
        if attempt >= self._max_retry:
            return
        delay = min(2 ** attempt, 30)
        log.warning("[%s] attempt %d failed (%s), retry in %ss (prompt=%s)",
                    bundle, attempt + 1, error[:100], delay, prompt_ref)
        time.sleep(delay)

    def _parse_and_validate(self, raw: str | None, schema: type[BaseModel]):
        import json as _json

        if not raw:
            return None
        try:
            data = _json.loads(raw)
        except ValueError:
            return None
        try:
            validated = schema.model_validate(data)
        except ValidationError:
            return None
        return validated.model_dump()

    def _record(self, bundle, episode, usage, latency_ms, ok, error) -> None:
        self._recorder({
            "ts": datetime.now(timezone.utc).isoformat(),
            "bundle": bundle,
            "episode_no": episode,
            "model": self._model_id,
            "input_tokens": usage["in"],
            "output_tokens": usage["out"],
            "latency_ms": latency_ms,
            "ok": 1 if ok else 0,
            "error": (error or "")[:300],
        })


def _prompt_ref(system: str, user: str) -> str:
    digest = hashlib.sha256((system + "\n" + user).encode()).hexdigest()[:12]
    head = (system + " " + user)[:200].replace("\n", " ")
    return f"{digest}:{head}"


def build_client(settings, recorder=None, budget_checker=None) -> MetaClient:
    """Construct a MetaClient from Settings (single construction site)."""
    return MetaClient(
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        model_id=settings.model_text_id,
        timeout_s=settings.request_timeout_s,
        max_retry=settings.llm_max_retry,
        daily_budget=settings.daily_token_budget,
        recorder=recorder,
        budget_checker=budget_checker,
    )
