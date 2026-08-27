"""Any OpenAI-compatible /chat/completions endpoint (OpenAI, OpenRouter, vLLM,
Ollama, LM Studio, ...). Set LLM_PROVIDER=openai plus OPENAI_BASE_URL."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from ..config import settings
from .base import LLMError, LLMRequest


class OpenAICompatClient:
    name = "openai"

    def __init__(self, model: str) -> None:
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=settings.openai_base_url,
            timeout=httpx.Timeout(settings.llm_timeout),
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )

    def _payload(self, req: LLMRequest, stream: bool) -> dict:
        messages = [{"role": "system", "content": req.system}] if req.system else []
        messages += [m.as_dict() for m in req.messages]
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": min(req.max_tokens, settings.max_output_tokens),
            "stream": stream,
        }
        if req.json_mode:
            payload["response_format"] = {"type": "json_object"}
        return payload

    async def complete(self, req: LLMRequest) -> str:
        try:
            r = await self._client.post("/chat/completions", json=self._payload(req, False))
            if r.status_code >= 400:
                raise LLMError(f"OpenAI-compatible error {r.status_code}: {r.text[:300]}")
            data = r.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenAI-compatible request failed: {exc}") from exc
        choices = data.get("choices") or [{}]
        return ((choices[0].get("message") or {}).get("content") or "").strip()

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST", "/chat/completions", json=self._payload(req, True)
            ) as r:
                if r.status_code >= 400:
                    body = (await r.aread()).decode("utf-8", "replace")
                    raise LLMError(f"OpenAI-compatible error {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        delta = json.loads(raw)["choices"][0].get("delta") or {}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta.get("content"):
                        yield delta["content"]
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenAI-compatible stream failed: {exc}") from exc

    async def aclose(self) -> None:
        await self._client.aclose()
