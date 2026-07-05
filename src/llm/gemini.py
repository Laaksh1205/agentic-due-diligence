import asyncio
import logging
import random
from typing import TypeVar

import google.genai as genai
from google.genai import types as genai_types
from pydantic import BaseModel

from src.config import settings
from src.llm.base import LLMCall, LLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Per-token pricing in USD (as of June 2025, standard tier)
_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-flash-lite": {"input": 0.10 / 1_000_000, "output": 0.40 / 1_000_000},
    "gemini-2.5-flash": {"input": 0.075 / 1_000_000, "output": 0.30 / 1_000_000},
    "gemini-2.5-pro": {"input": 1.25 / 1_000_000, "output": 10.00 / 1_000_000},
}
_FALLBACK_PRICING = {"input": 0.075 / 1_000_000, "output": 0.30 / 1_000_000}

# Transient server/rate-limit conditions worth retrying with backoff. Gemini's
# free tier frequently returns 503 UNAVAILABLE ("high demand") under load; a
# single transient failure should not drop a signal or report section.
_RETRYABLE_CODES = {429, 500, 502, 503, 504}
_RETRYABLE_TOKENS = ("UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL", "DEADLINE", "OVERLOADED")


def _is_retryable(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code in _RETRYABLE_CODES:
        return True
    msg = str(exc).upper()
    return any(tok in msg for tok in _RETRYABLE_TOKENS) or any(str(c) in msg for c in _RETRYABLE_CODES)


class GeminiProvider(LLMProvider):
    """Gemini LLM provider using the google-genai SDK.

    Uses `response_schema` for structured output so the model returns
    JSON that maps directly to the requested Pydantic model — no prompt
    parsing required. Temperature is fixed at 0 for deterministic extraction.

    Every call is traced as a Langfuse generation span (Task 3.3.2) using
    OTel context propagation — the span is automatically nested under the
    active agent span without any manual wiring.
    """

    def __init__(self) -> None:
        super().__init__()
        self._client = genai.Client(api_key=settings.google_api_key)
        self._fast_model = settings.gemini_fast_model
        self._smart_model = settings.gemini_smart_model

    async def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        system: str = "",
        use_fast: bool = True,
    ) -> T:
        from src.llm import tracing  # lazy import avoids circular at module load

        model_name = self._fast_model if use_fast else self._smart_model
        full_prompt = f"[SYSTEM]\n{system}\n\n[USER]\n{prompt}" if system else prompt

        config = genai_types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
            system_instruction=system if system else None,
        )

        async with tracing.llm_generation_span(model_name, full_prompt):
            response = await self._generate_with_retry(model_name, prompt, config)

            usage = response.usage_metadata
            prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
            completion_tokens = getattr(usage, "candidates_token_count", 0) or 0
            pricing = _PRICING.get(model_name, _FALLBACK_PRICING)
            cost = prompt_tokens * pricing["input"] + completion_tokens * pricing["output"]

            self._record(LLMCall(
                model=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
            ))

            tracing.record_generation_usage(
                model=model_name,
                output=response.text,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
            )

        return schema.model_validate_json(response.text)

    async def _generate_with_retry(self, model_name: str, prompt: str, config):
        """Call generate_content with exponential backoff on transient errors.

        Honours ``settings.max_agent_retries`` (initial attempt + N retries).
        Only transient server/rate-limit conditions are retried; deterministic
        failures (bad request, schema errors) propagate immediately.
        """
        attempts = max(1, settings.max_agent_retries + 1)
        delay = 1.0
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return await self._client.aio.models.generate_content(
                    model=model_name, contents=prompt, config=config,
                )
            except Exception as exc:
                last_exc = exc
                if attempt < attempts - 1 and _is_retryable(exc):
                    sleep_for = delay + random.uniform(0, 0.3 * delay)
                    logger.warning(
                        "[gemini] transient error (attempt %d/%d): %s — retrying in %.1fs",
                        attempt + 1, attempts, str(exc)[:120], sleep_for,
                    )
                    await asyncio.sleep(sleep_for)
                    delay *= 2
                    continue
                raise
        assert last_exc is not None  # unreachable; loop either returns or raises
        raise last_exc
