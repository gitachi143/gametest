"""Builds the configured client once and tracks lightweight usage stats."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from ..config import settings
from .base import LLMClient, LLMError, LLMRequest
from .mock import MockClient

log = logging.getLogger("arcade.llm")


@dataclass
class Usage:
    calls: int = 0
    errors: int = 0
    prompt_chars: int = 0
    output_chars: int = 0
    latency_ms_total: float = 0.0
    by_task: dict[str, int] = field(default_factory=dict)

    def record(self, task: str, prompt_chars: int, output_chars: int, ms: float) -> None:
        self.calls += 1
        self.prompt_chars += prompt_chars
        self.output_chars += output_chars
        self.latency_ms_total += ms
        self.by_task[task] = self.by_task.get(task, 0) + 1

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "avg_latency_ms": round(self.latency_ms_total / self.calls, 1) if self.calls else 0,
            "est_prompt_tokens": self.prompt_chars // 4,
            "est_output_tokens": self.output_chars // 4,
            "by_task": dict(sorted(self.by_task.items(), key=lambda kv: -kv[1])[:12]),
        }


usage = Usage()
_client: LLMClient | None = None


def build_client() -> LLMClient:
    provider = settings.provider
    if provider in ("gemini", "vertex"):
        from .gemini import GeminiClient

        return GeminiClient(settings.model, mode=provider)
    if provider == "anthropic":
        from .anthropic_client import AnthropicClient

        return AnthropicClient(settings.model)
    if provider == "openai":
        from .openai_compat import OpenAICompatClient

        return OpenAICompatClient(settings.model)
    return MockClient(settings.model)


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = build_client()
        log.info("LLM provider=%s model=%s", _client.name, _client.model)
    return _client


def set_llm(client: LLMClient | None) -> None:
    """Test seam: swap in a fake client."""
    global _client
    _client = client


async def close_llm() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class LLM:
    """Instrumented facade the games use."""

    @staticmethod
    async def text(req: LLMRequest) -> str:
        started = time.perf_counter()
        try:
            out = await get_llm().complete(req)
        except LLMError:
            usage.errors += 1
            raise
        except Exception as exc:  # noqa: BLE001 - normalise provider surprises
            usage.errors += 1
            raise LLMError(str(exc)) from exc
        ms = (time.perf_counter() - started) * 1000
        usage.record(req.task, len(req.system) + sum(len(m.content) for m in req.messages), len(out), ms)
        return out

    @staticmethod
    async def json(req: LLMRequest, default: dict | list) -> dict | list:
        from .base import extract_json

        req.json_mode = True
        try:
            raw = await LLM.text(req)
        except LLMError as exc:
            log.warning("json call failed task=%s: %s", req.task, exc)
            return default
        parsed = extract_json(raw)
        if parsed is None:
            log.warning("unparseable json task=%s raw=%r", req.task, raw[:200])
            return default
        return parsed

    @staticmethod
    def stream(req: LLMRequest):
        return _StreamProxy(req)


class _StreamProxy:
    """Streams tokens while accumulating the full text for the caller."""

    def __init__(self, req: LLMRequest) -> None:
        self.req = req
        self.text = ""
        self._started = time.perf_counter()

    async def __aiter__(self):
        try:
            async for piece in get_llm().stream(self.req):
                self.text += piece
                yield piece
        except LLMError:
            usage.errors += 1
            raise
        except Exception as exc:  # noqa: BLE001
            usage.errors += 1
            raise LLMError(str(exc)) from exc
        finally:
            ms = (time.perf_counter() - self._started) * 1000
            usage.record(
                self.req.task,
                len(self.req.system) + sum(len(m.content) for m in self.req.messages),
                len(self.text),
                ms,
            )
