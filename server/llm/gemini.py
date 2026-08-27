"""Google Gemini provider - Google AI Studio and GCP Vertex AI.

Both endpoints speak the same `generateContent` request shape, so one client
covers them; only the URL and the auth header differ.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import AsyncIterator

import httpx

from ..config import settings
from .base import LLMError, LLMRequest

_AI_STUDIO = "https://generativelanguage.googleapis.com/v1beta"

# Models where turning thinking off is both supported and a large latency win.
_FLASH_HINTS = ("2.5-flash", "2.0-flash", "-flash-lite", "flash-latest")


class GeminiClient:
    """Talks to Gemini. `mode` is "gemini" (AI Studio key) or "vertex" (ADC)."""

    def __init__(self, model: str, mode: str = "gemini") -> None:
        self.model = model
        self.name = mode
        self._mode = mode
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout))
        self._token: tuple[str, float] | None = None
        self._token_lock = asyncio.Lock()
        # Optimistically disable Gemini 2.5 "thinking" on flash models for
        # snappy game turns; flipped off permanently if the API rejects it.
        env = os.environ.get("GEMINI_THINKING_BUDGET", "").strip()
        if env == "off":
            self._thinking_budget: int | None = None
        elif env:
            try:
                self._thinking_budget = int(env)
            except ValueError:
                self._thinking_budget = None
        else:
            low = model.lower()
            self._thinking_budget = 0 if any(h in low for h in _FLASH_HINTS) else None
        self._thinking_supported = self._thinking_budget is not None

    # -- auth ---------------------------------------------------------------
    async def _access_token(self) -> str:
        """Vertex AI bearer token via ADC, metadata server, or gcloud CLI."""
        async with self._token_lock:
            if self._token and self._token[1] - time.time() > 60:
                return self._token[0]

            token = await asyncio.to_thread(self._fetch_token_sync)
            if not token:
                raise LLMError(
                    "Could not obtain Google credentials for Vertex AI. Run "
                    "`gcloud auth application-default login` locally, or deploy "
                    "with a service account."
                )
            self._token = (token, time.time() + 3000)
            return token

    def _fetch_token_sync(self) -> str | None:
        try:  # 1. google-auth (local ADC, workload identity, service account)
            import google.auth  # type: ignore
            import google.auth.transport.requests  # type: ignore

            creds, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(google.auth.transport.requests.Request())
            return creds.token
        except Exception:
            pass
        try:  # 2. GCE / Cloud Run metadata server - no dependency needed
            r = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/"
                "service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"},
                timeout=3.0,
            )
            if r.status_code == 200:
                return r.json().get("access_token")
        except Exception:
            pass
        try:  # 3. gcloud CLI
            import subprocess

            out = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True, text=True, timeout=20,
            )
            if out.returncode == 0:
                return out.stdout.strip()
        except Exception:
            pass
        return None

    # -- request shaping ----------------------------------------------------
    def _payload(self, req: LLMRequest) -> dict:
        contents = [
            {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
            for m in req.messages
        ]
        gen: dict = {
            "temperature": req.temperature,
            "maxOutputTokens": min(req.max_tokens, settings.max_output_tokens),
        }
        if req.json_mode:
            gen["responseMimeType"] = "application/json"
        if self._thinking_supported and self._thinking_budget is not None:
            gen["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}
        body: dict = {"contents": contents, "generationConfig": gen}
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        # Games like VAULT and SLEEPER are adversarial roleplay; the default
        # filters occasionally fire on ordinary play, so relax them to the
        # lowest non-blocking setting and let the game's own rules govern.
        body["safetySettings"] = [
            {"category": c, "threshold": "BLOCK_ONLY_HIGH"}
            for c in (
                "HARM_CATEGORY_HARASSMENT",
                "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                "HARM_CATEGORY_DANGEROUS_CONTENT",
            )
        ]
        return body

    async def _url(self, method: str) -> tuple[str, dict]:
        if self._mode == "vertex":
            proj, loc = settings.google_project, settings.vertex_location or "global"
            host = (
                "https://aiplatform.googleapis.com"
                if loc == "global"
                else f"https://{loc}-aiplatform.googleapis.com"
            )
            url = (
                f"{host}/v1/projects/{proj}/locations/{loc}"
                f"/publishers/google/models/{self.model}:{method}"
            )
            headers = {"Authorization": f"Bearer {await self._access_token()}"}
            return url, headers
        url = f"{_AI_STUDIO}/models/{self.model}:{method}"
        return url, {"x-goog-api-key": settings.gemini_api_key}

    @staticmethod
    def _text_from(chunk: dict) -> str:
        out = []
        for cand in chunk.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                if isinstance(part.get("text"), str):
                    out.append(part["text"])
        return "".join(out)

    def _handle_error(self, resp_text: str, status: int) -> None:
        if status == 400 and "thinking" in resp_text.lower() and self._thinking_supported:
            self._thinking_supported = False  # retry path handles the resend
            raise _RetryWithoutThinking()
        detail = resp_text[:400]
        raise LLMError(f"Gemini {self._mode} error {status}: {detail}")

    # -- public API ---------------------------------------------------------
    async def complete(self, req: LLMRequest) -> str:
        for attempt in range(2):
            url, headers = await self._url("generateContent")
            try:
                r = await self._client.post(url, headers=headers, json=self._payload(req))
                if r.status_code >= 400:
                    self._handle_error(r.text, r.status_code)
                data = r.json()
            except _RetryWithoutThinking:
                continue
            except httpx.HTTPError as exc:
                if attempt == 0:
                    await asyncio.sleep(0.6)
                    continue
                raise LLMError(f"Gemini request failed: {exc}") from exc
            text = self._text_from(data)
            if not text:
                blocked = (data.get("promptFeedback") or {}).get("blockReason")
                if blocked:
                    raise LLMError(f"Gemini blocked the prompt ({blocked}).")
            return text.strip()
        return ""

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        for attempt in range(2):
            url, headers = await self._url("streamGenerateContent")
            got_any = False
            try:
                async with self._client.stream(
                    "POST", url, headers=headers, params={"alt": "sse"},
                    json=self._payload(req),
                ) as r:
                    if r.status_code >= 400:
                        body = (await r.aread()).decode("utf-8", "replace")
                        self._handle_error(body, r.status_code)
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            piece = self._text_from(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
                        if piece:
                            got_any = True
                            yield piece
                return
            except _RetryWithoutThinking:
                continue
            except httpx.HTTPError as exc:
                if got_any or attempt == 1:
                    raise LLMError(f"Gemini stream failed: {exc}") from exc
                await asyncio.sleep(0.6)

    async def aclose(self) -> None:
        await self._client.aclose()


class _RetryWithoutThinking(Exception):
    pass
