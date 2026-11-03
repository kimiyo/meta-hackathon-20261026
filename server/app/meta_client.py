"""The only AI call exit. No other module may import httpx/openai.

i18n (plan §6): chat_json accepts the work's market/writing_locale plus
prompt/preset provenance and records them for the llm_calls tracking
columns (docs/i18n-contracts.md §4). The JSON-recovery retry is routed
to the call's writing locale. Callers that must also run against legacy
test doubles use chat_with_provenance(), which passes the extra keywords
only when the callee accepts them.
"""
from __future__ import annotations

import hashlib
import inspect
import json as _json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Callable, Literal, get_args, get_origin

import httpx
from pydantic import BaseModel, ValidationError

from .errors import BudgetExceeded, LLMStepError

log = logging.getLogger(__name__)

RECOVERY_PROMPT = (
    "이전 출력이 JSON 파싱 또는 스키마 검증에 실패했다. "
    "설명 없이 올바른 JSON만 다시 출력하라."
)

RECOVERY_PROMPT_EN = (
    "The previous output failed JSON parsing or schema validation. "
    "Output only correct JSON without explanation."
)

DEFAULT_MARKET = "KR"
DEFAULT_WRITING_LOCALE = "ko-KR"

# chat_json keywords beyond the legacy fake signature
# (bundle, episode, system, user, schema, max_tokens, work_id).
_I18N_KEYS = ("writing_locale", "market", "prompt_feature",
              "prompt_version", "preset_id", "preset_version")


def _skeleton(tp: Any, seen: tuple = ()) -> Any:
    """Example JSON value for a type: shows nesting and value shapes."""
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return {k: _skeleton(f.annotation, seen + (tp,))
                for k, f in tp.model_fields.items()}
    if get_origin(tp) is Literal:
        return "|".join(str(a) for a in get_args(tp))
    args = [a for a in get_args(tp) if a is not type(None)]
    if get_origin(tp) is list:
        if not args or args[0] in seen:  # recursive list: same shape again
            return []
        return [_skeleton(args[0], seen)]
    if get_origin(tp) is dict or tp is dict:
        return {}
    if args:  # Optional / Union: show the non-null shape
        return _skeleton(args[0], seen)
    return {str: "", int: 0, bool: False, float: 0.0}.get(tp, None)


def _recovery_prompt(schema: type[BaseModel],
                     writing_locale: str = DEFAULT_WRITING_LOCALE) -> str:
    if writing_locale == "en-US":
        return (f"{RECOVERY_PROMPT_EN} It must have exactly the following"
                f" shape (including value shapes): {_dump(_skeleton(schema))}")
    return (f"{RECOVERY_PROMPT} 정확히 다음 형태여야 한다(값의 형태 포함): "
            f"{_dump(_skeleton(schema))}")


def work_locale(conn: sqlite3.Connection | None,
                work_id: int | None) -> tuple[str, str]:
    """(market, writing_locale) for a work; KR/ko-KR when unknown.

    Missing rows, NULL values, and pre-i18n databases without the
    columns all fall back to the legacy defaults.
    """
    if conn is None or work_id is None:
        return DEFAULT_MARKET, DEFAULT_WRITING_LOCALE
    try:
        row = conn.execute("SELECT market, writing_locale FROM works"
                           " WHERE id=?", (work_id,)).fetchone()
    except sqlite3.OperationalError:
        return DEFAULT_MARKET, DEFAULT_WRITING_LOCALE
    if not row:
        return DEFAULT_MARKET, DEFAULT_WRITING_LOCALE
    return (row["market"] or DEFAULT_MARKET,
            row["writing_locale"] or DEFAULT_WRITING_LOCALE)


def preset_ref(conn: sqlite3.Connection | None,
               work_id: int | None) -> tuple[str, str]:
    """(preset_id, preset_version) best-effort for llm_calls provenance.

    The style catalog carries no versions yet, so the version is always
    "" (recorded as unknown until catalog versioning lands).
    """
    if conn is None or work_id is None:
        return "", ""
    try:
        row = conn.execute("SELECT style_preset FROM style_policies"
                           " WHERE work_id=?", (work_id,)).fetchone()
    except sqlite3.OperationalError:
        return "", ""
    if not row or not row["style_preset"]:
        return "", ""
    return row["style_preset"], ""


def _accepts_i18n_kwargs(fn: Callable) -> bool:
    """True when fn takes **kw or every _I18N_KEYS keyword."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if any(p.kind == inspect.Parameter.VAR_KEYWORD
           for p in params.values()):
        return True
    return all(k in params for k in _I18N_KEYS)


def chat_with_provenance(meta, *, bundle: str, episode: int | None,
                         system: str, user: str, schema: type[BaseModel],
                         max_tokens: int, work_id: int | None = None,
                         writing_locale: str = DEFAULT_WRITING_LOCALE,
                         market: str = DEFAULT_MARKET,
                         prompt_feature: str = "",
                         prompt_version: str = "",
                         preset_id: str = "",
                         preset_version: str = "") -> dict:
    """Call meta.chat_json with i18n provenance when supported.

    The real MetaClient receives writing_locale/market plus prompt and
    preset provenance for llm_calls logging and locale-appropriate
    recovery. Legacy test doubles with the original seven-parameter
    signature get the exact legacy call instead.
    """
    base = {"bundle": bundle, "episode": episode, "system": system,
            "user": user, "schema": schema, "max_tokens": max_tokens,
            "work_id": work_id}
    if not _accepts_i18n_kwargs(meta.chat_json):
        return meta.chat_json(**base)
    return meta.chat_json(
        **base, writing_locale=writing_locale, market=market,
        prompt_feature=prompt_feature, prompt_version=prompt_version,
        preset_id=preset_id, preset_version=preset_version)


def feature_prompt(conn: sqlite3.Connection | None, work_id: int | None,
                   feature: str, writing_locale: str | None = None,
                   market: str | None = None):
    """Resolve (bundle, writing_locale, market) for one AI feature.

    Explicit locale arguments win; otherwise the work's stored settings
    are used (KR/ko-KR fallback). The prompt text comes from
    prompt_loader.load, so a missing required locale file raises
    FileNotFoundError instead of silently substituting another language.
    """
    from . import prompt_loader

    if writing_locale is None or market is None:
        _market, _locale = work_locale(conn, work_id)
        if market is None:
            market = _market
        if writing_locale is None:
            writing_locale = _locale
    bundle = prompt_loader.load(feature, writing_locale, market)
    return bundle, writing_locale, market


def _dump(obj: Any) -> str:
    try:
        return _json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)[:4000]


def _sent_prompts(messages: list[dict]) -> tuple[str, str]:
    sys_txt = "\n".join(m.get("content", "") for m in messages
                        if m.get("role") == "system")
    usr_txt = "\n\n".join(m.get("content", "") for m in messages
                          if m.get("role") == "user")
    return sys_txt, usr_txt

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
        work_id: int | None = None,
        writing_locale: str = DEFAULT_WRITING_LOCALE,
        market: str = DEFAULT_MARKET,
        prompt_feature: str = "",
        prompt_version: str = "",
        preset_id: str = "",
        preset_version: str = "",
    ) -> dict:
        """Call the model, force JSON, validate against schema. Structured errors."""
        locale = writing_locale or DEFAULT_WRITING_LOCALE
        i18n = {"market": market or "", "writing_locale": locale,
                "prompt_feature": prompt_feature or "",
                "prompt_version": prompt_version or "",
                "preset_id": preset_id or "",
                "preset_version": preset_version or ""}
        self._check_budget(bundle, episode, work_id, system, user, i18n)
        prompt_ref = _prompt_ref(system, user)

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        raw, usage, latency_ms = self._post_with_retry(
            bundle, episode, work_id, messages, max_tokens, prompt_ref, i18n
        )
        parsed = self._parse_and_validate(raw, schema)
        if parsed is not None:
            self._record(bundle, episode, work_id, usage, latency_ms, True, "",
                         system, user, _dump(parsed), i18n)
            return parsed

        # Single recovery attempt: ask for JSON-only output once.
        log.warning("[%s] schema/parse failure, 1 recovery attempt (prompt=%s)",
                    bundle, prompt_ref)
        self._check_budget(bundle, episode, work_id, system, user, i18n)
        recovery = _recovery_prompt(schema, locale)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "assistant", "content": (raw or "")[:2000]},
            {"role": "user", "content": recovery},
        ]
        raw2, usage2, latency2 = self._post_with_retry(
            bundle, episode, work_id, messages, max_tokens,
            prompt_ref + ":recovery", i18n,
        )
        parsed2 = self._parse_and_validate(raw2, schema)
        # One row covers both calls; finish shows "first>recovery".
        finish = f"{usage['finish']}>{usage2['finish']}"
        total = {"in": usage["in"] + usage2["in"],
                 "out": usage["out"] + usage2["out"],
                 "reasoning": usage["reasoning"] + usage2["reasoning"],
                 "finish": finish}
        marker = ("[recovery retry]" if locale == "en-US" else "[복구 재시도]")
        user_stored = user + "\n\n" + marker + "\n" + recovery
        if parsed2 is not None:
            self._record(bundle, episode, work_id, total,
                         latency_ms + latency2, True, "", system, user_stored,
                         _dump(parsed2), i18n)
            return parsed2
        if not (raw2 or "").strip() and "length" in finish:
            # Reasoning-heavy models can burn the whole max_tokens budget
            # before emitting content; report the cutoff, not a schema fault.
            self._record(bundle, episode, work_id, total,
                         latency_ms + latency2, False, "max_tokens_exhausted",
                         system, user_stored, raw2 or "", i18n)
            if locale == "en-US":
                detail = ("Model response was cut off at the token limit."
                          " Raise max_tokens.")
            else:
                detail = ("모델 응답이 토큰 한도에 잘렸습니다."
                          " max_tokens를 올리세요.")
            raise LLMStepError(bundle, episode, "max_tokens_exhausted",
                               detail)
        self._record(bundle, episode, work_id, total,
                     latency_ms + latency2, False, "json_schema_failed",
                     system, user_stored, raw2 or "", i18n)
        raise LLMStepError(bundle, episode, "json_schema_failed",
                           (raw2 or "")[:200])

    # -- internals -------------------------------------------------------
    def _check_budget(self, bundle: str, episode: int | None,
                      work_id: int | None = None, system: str = "",
                      user: str = "", i18n: dict | None = None) -> None:
        try:
            self._budget_checker()
        except BudgetExceeded:
            self._record(bundle, episode, work_id, {"in": 0, "out": 0}, 0,
                         False, "budget_exceeded", system, user, "",
                         i18n or {})
            raise

    def _client(self) -> httpx.Client:
        if self._http is not None:
            return self._http
        return httpx.Client(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=self._timeout_s,
        )

    def _post_with_retry(self, bundle, episode, work_id, messages, max_tokens,
                         prompt_ref, i18n: dict | None = None):
        i18n = i18n or {}
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
                    sys_txt, usr_txt = _sent_prompts(messages)
                    self._record(bundle, episode, work_id, {"in": 0, "out": 0},
                                 latency_ms, False, f"http_{resp.status_code}",
                                 sys_txt, usr_txt, "", i18n)
                    raise LLMStepError(bundle, episode, f"http_{resp.status_code}",
                                       resp.text[:200])
                try:
                    body = resp.json()
                except ValueError:
                    last_error = f"bad_envelope: {resp.text[:200]}"
                    self._backoff(bundle, attempt, last_error, prompt_ref)
                    continue
                try:
                    choice = body["choices"][0]
                    content = choice["message"].get("content")
                    finish = choice.get("finish_reason") or ""
                except (KeyError, IndexError, TypeError, AttributeError):
                    content, finish = None, ""
                usage = body.get("usage", {}) or {}
                details = usage.get("completion_tokens_details") or {}
                return (content, {
                    "in": usage.get("prompt_tokens", 0),
                    "out": usage.get("completion_tokens", 0),
                    "reasoning": details.get("reasoning_tokens", 0)
                    if isinstance(details, dict) else 0,
                    "finish": str(finish),
                }, latency_ms)
        finally:
            if own_client:
                client.close()
        sys_txt, usr_txt = _sent_prompts(messages)
        self._record(bundle, episode, work_id, {"in": 0, "out": 0}, 0, False,
                     f"retry_exhausted:{last_error}"[:200], sys_txt, usr_txt, "",
                     i18n)
        raise LLMStepError(bundle, episode, "retry_exhausted", last_error)

    def _backoff(self, bundle: str, attempt: int, error: str, prompt_ref: str) -> None:
        if attempt >= self._max_retry:
            return
        delay = min(2 ** attempt, 30)
        log.warning("[%s] attempt %d failed (%s), retry in %ss (prompt=%s)",
                    bundle, attempt + 1, error[:100], delay, prompt_ref)
        time.sleep(delay)

    def _parse_and_validate(self, raw: str | None, schema: type[BaseModel]):
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

    def _record(self, bundle, episode, work_id, usage, latency_ms, ok, error,
                system: str = "", user: str = "", output: str = "",
                i18n: dict | None = None) -> None:
        i18n = i18n or {}
        self._recorder({
            "ts": datetime.now(timezone.utc).isoformat(),
            "bundle": bundle,
            "episode_no": episode,
            "work_id": work_id,
            "model": self._model_id,
            "input_tokens": usage["in"],
            "output_tokens": usage["out"],
            "reasoning_tokens": usage.get("reasoning", 0) or 0,
            "finish_reason": usage.get("finish", ""),
            "latency_ms": latency_ms,
            "ok": 1 if ok else 0,
            "error": (error or "")[:300],
            "prompt_system": system,
            "prompt_user": user,
            "output_text": output,
            "market": i18n.get("market", ""),
            "writing_locale": i18n.get("writing_locale", ""),
            "prompt_feature": i18n.get("prompt_feature", ""),
            "prompt_version": i18n.get("prompt_version", ""),
            "preset_id": i18n.get("preset_id", ""),
            "preset_version": i18n.get("preset_version", ""),
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
