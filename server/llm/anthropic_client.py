"""Anthropic provider, via the official `anthropic` SDK.

Install with `pip install anthropic` and set LLM_PROVIDER=anthropic.

Notes specific to current Claude models, which this adapter handles for you:
  * `temperature` is rejected by Opus 5 / Sonnet 5 / the 4.6+ family, so it is
    never sent.
  * Game turns are latency-critical, so requests run at `effort: "low"` with
    adaptive thinking left on (disabling thinking on Opus 5 has its own
    failure modes).
  * Opus 5 / Fable 5 get server-side refusal fallbacks enabled - useful here
    because several games (VAULT especially) are adversarial roleplay that a
    safety classifier may decline.
Any parameter an older model rejects is pruned after the first 400 and the
request is retried, so pointing this at an older Claude model still works.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from ..config import settings
from .base import LLMError, LLMRequest

_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5", "claude-mythos-5")


class AnthropicClient:
    name = "anthropic"

    def __init__(self, model: str) -> None:
        self.model = model
        try:
            from anthropic import AsyncAnthropic  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on install
            raise LLMError(
                "LLM_PROVIDER=anthropic needs the official SDK: pip install anthropic"
            ) from exc
        self._sdk = AsyncAnthropic(
            api_key=settings.anthropic_api_key or None,
            timeout=float(settings.llm_timeout),
        )
        self._use_effort = True
        self._use_fallbacks = model.startswith(_FALLBACK_MODELS)

    def _kwargs(self, req: LLMRequest) -> dict[str, Any]:
        system = req.system
        if req.json_mode:
            system += "\n\nRespond with raw JSON only. No prose, no code fences."
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": min(req.max_tokens, settings.max_output_tokens),
            "system": system,
            "messages": [m.as_dict() for m in req.messages],
        }
        if self._use_effort:
            kwargs["output_config"] = {"effort": "low"}
        if self._use_fallbacks:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def _messages_api(self):
        # `betas`/`fallbacks` live on the beta namespace.
        return self._sdk.beta.messages if self._use_fallbacks else self._sdk.messages

    def _prune(self, exc: Exception) -> bool:
        """Drop an unsupported parameter so an older model can be retried."""
        text = str(exc).lower()
        if self._use_fallbacks and ("fallback" in text or "beta" in text):
            self._use_fallbacks = False
            return True
        if self._use_effort and ("effort" in text or "output_config" in text):
            self._use_effort = False
            return True
        return False

    async def complete(self, req: LLMRequest) -> str:
        for _ in range(3):
            try:
                msg = await self._messages_api().create(**self._kwargs(req))
            except Exception as exc:  # noqa: BLE001 - SDK raises typed subclasses
                if self._prune(exc):
                    continue
                raise LLMError(f"Anthropic request failed: {exc}") from exc
            if getattr(msg, "stop_reason", None) == "refusal":
                raise LLMError("Anthropic declined this request (stop_reason=refusal).")
            return "".join(
                b.text for b in msg.content if getattr(b, "type", "") == "text"
            ).strip()
        return ""

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        for _ in range(3):
            started = False
            try:
                async with self._messages_api().stream(**self._kwargs(req)) as s:
                    async for piece in s.text_stream:
                        started = True
                        if piece:
                            yield piece
                return
            except Exception as exc:  # noqa: BLE001
                if not started and self._prune(exc):
                    continue
                raise LLMError(f"Anthropic stream failed: {exc}") from exc

    async def aclose(self) -> None:
        await self._sdk.close()
