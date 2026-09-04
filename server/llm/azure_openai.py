"""Azure OpenAI, spoken over its v1 API.

Azure's older surface (`/openai/deployments/<name>/chat/completions?api-version=`)
is not OpenAI-compatible. The v1 API is: it drops `api-version`, takes the
*deployment* name in the `model` field, and otherwise speaks plain
`/chat/completions`. So this subclasses the OpenAI-compatible client and only
overrides what Azure does differently:

* **Auth.** An `api-key` header rather than `Authorization: Bearer`.
* **Retries.** Unlike Vertex's shared-capacity throttle, an Azure 429 is a real
  per-minute token quota and arrives with a `Retry-After`. Honour it, but only
  up to `_RETRY_AFTER_CAP` - a TPM exhaustion can ask for 60s, and stranding a
  game turn that long is worse than degrading to a fallback line.
* **Parameter pruning.** Reasoning deployments (o-series, gpt-5) renamed
  `max_tokens` to `max_completion_tokens` and reject a non-default
  `temperature`. Deployment names are arbitrary, so the model id is only a
  hint: the 400 itself is what we learn from, and the lesson sticks for the
  process lifetime.
"""
from __future__ import annotations

import asyncio
import random
from typing import AsyncIterator

import httpx

from ..config import settings
from .base import LLMError, LLMRequest
from .openai_compat import OpenAICompatClient

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_TRIES = 5
_BACKOFF_CAP = 4.0
_RETRY_AFTER_CAP = 8.0

# Deployments whose *names* start like this are assumed to be reasoning models,
# so the first call is already shaped correctly instead of costing a 400.
_REASONING_HINTS = ("o1", "o3", "o4", "gpt-5")


def delay_for(tries: int, retry_after: float | None) -> float:
    """Seconds to wait before a resend, before jitter.

    An explicit `Retry-After` wins over the exponential schedule, but is capped:
    honouring a 60s TPM hint would strand the turn long past the point where a
    fallback line is the better answer.
    """
    if retry_after is not None:
        return min(retry_after, _RETRY_AFTER_CAP)
    return min(0.5 * (2 ** (tries - 1)), _BACKOFF_CAP)


class AzureOpenAIClient(OpenAICompatClient):
    name = "azure"

    def __init__(self, model: str) -> None:
        # Deliberately not calling super().__init__: it would build a client
        # against OPENAI_BASE_URL with bearer auth.
        # On Azure the wire name is the *deployment*, which need not match the
        # model id the rest of the app reports.
        self.model = settings.azure_deployment or model
        self._client = httpx.AsyncClient(
            base_url=settings.azure_base_url,
            timeout=httpx.Timeout(settings.llm_timeout),
            headers={"api-key": settings.azure_api_key},
        )
        low = self.model.lower()
        self._reasoning = low.startswith(_REASONING_HINTS)
        self._send_temperature = not self._reasoning

    # -- Azure's payload dialect --------------------------------------------
    def _payload(self, req: LLMRequest, stream: bool) -> dict:
        payload = super()._payload(req, stream)
        if self._reasoning:
            payload["max_completion_tokens"] = payload.pop("max_tokens")
        if not self._send_temperature:
            payload.pop("temperature", None)
        return payload

    def _learn_from(self, exc: LLMError) -> bool:
        """Adapt to a 400 about an unsupported parameter. True if worth a resend."""
        if exc.status != 400:
            return False
        body = str(exc).lower()
        adapted = False
        # Checked most-specific first: "max_completion_tokens" does not contain
        # "max_tokens" as a substring, but reading it that way is one typo away.
        if "max_completion_tokens" in body and self._reasoning:
            self._reasoning = False         # the name misled us; it wants the classic field
            adapted = True
        elif "max_tokens" in body and not self._reasoning:
            self._reasoning = True          # also switches off temperature below
            self._send_temperature = False
            adapted = True
        if "temperature" in body and self._send_temperature:
            self._send_temperature = False
            adapted = True
        return adapted

    async def _backoff(self, tries: int, retry_after: float | None) -> None:
        # Jittered so concurrent turns don't all resend on the same tick.
        await asyncio.sleep(delay_for(tries, retry_after) + random.uniform(0, 0.4))

    @staticmethod
    def _retry_after(exc: LLMError) -> float | None:
        return exc.retry_after if exc.status == 429 else None

    # -- public API ---------------------------------------------------------
    async def complete(self, req: LLMRequest) -> str:
        for tries in range(1, _MAX_TRIES + 1):
            try:
                return await super().complete(req)
            except LLMError as exc:
                if self._learn_from(exc):
                    continue            # reshaped payload, same try budget
                if exc.status not in _RETRYABLE_STATUS or tries >= _MAX_TRIES:
                    raise
                await self._backoff(tries, self._retry_after(exc))
        return ""

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        """Retries only while nothing has been yielded.

        Once the UI has shown a token, resending would duplicate the reply, so
        a mid-stream failure propagates instead.
        """
        for tries in range(1, _MAX_TRIES + 1):
            started = False
            try:
                async for piece in super().stream(req):
                    started = True
                    yield piece
                return
            except LLMError as exc:
                if started:
                    raise
                if self._learn_from(exc):
                    continue
                if exc.status not in _RETRYABLE_STATUS or tries >= _MAX_TRIES:
                    raise
                await self._backoff(tries, self._retry_after(exc))
