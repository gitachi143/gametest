"""Provider-neutral model access.

Games never touch a vendor SDK. Everything goes through `LLM.text`, `.stream`
or `.json`, so the same code plays against Vertex AI, Google AI Studio,
Anthropic, any OpenAI-compatible endpoint, or the scripted opponent.

Two behaviours here are load-bearing rather than decorative:

* **A global concurrency gate.** Vertex serves `gemini-*-flash` from dynamic
  shared capacity, not a per-minute quota, so a burst of simultaneous calls
  earns a 429 that no quota increase can fix. Queueing four at a time turns a
  thundering herd into a short wait.
* **Retry on 429/5xx.** The throttle clears in about a second, so five tries
  across ~8s absorbs it. Giving up would strand a turn with no opponent.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Literal, Sequence

import httpx

from ..config import settings

log = logging.getLogger("sway.ai")

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Msg:
    role: Role
    content: str


def user(text: str) -> Msg:
    return Msg("user", text)


def assistant(text: str) -> Msg:
    return Msg("assistant", text)


@dataclass
class Req:
    system: str
    messages: Sequence[Msg]
    max_tokens: int = 320
    temperature: float = 0.9
    task: str = "chat"
    json_mode: bool = False
    schema: dict | None = None
    # Some calls matter more than others: a throttled judge would strand the
    # turn, a throttled flavour line degrades to a fallback string.
    retries: int = 0
    # Read only by the scripted provider so DEMO MODE can behave plausibly.
    mock: dict = field(default_factory=dict)


class LLMError(RuntimeError):
    """A provider failure the caller may want to surface or fall back from."""


# --------------------------------------------------------------------- usage
@dataclass
class Usage:
    calls: int = 0
    errors: int = 0
    throttles: int = 0
    prompt_chars: int = 0
    output_chars: int = 0
    latency_total: float = 0.0
    by_task: dict = field(default_factory=dict)

    def record(self, task: str, prompt: int, out: int, ms: float) -> None:
        self.calls += 1
        self.prompt_chars += prompt
        self.output_chars += out
        self.latency_total += ms
        self.by_task[task] = self.by_task.get(task, 0) + 1

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "errors": self.errors,
            "throttles": self.throttles,
            "avg_latency_ms": round(self.latency_total / self.calls, 1) if self.calls else 0,
            "est_prompt_tokens": self.prompt_chars // 4,
            "est_output_tokens": self.output_chars // 4,
            "by_task": dict(sorted(self.by_task.items(), key=lambda kv: -kv[1])[:12]),
        }


usage = Usage()


# ------------------------------------------------------------------- helpers
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(text: str) -> dict | list | None:
    """Best-effort JSON recovery. Models fence and preface even under
    instruction, and a dropped brace should cost a fallback, not a turn."""
    if not text:
        return None
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cleaned.find(opener)
        if start == -1:
            continue
        depth, in_str, esc = 0, False, False
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(cleaned[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


class _Throttled(Exception):
    """A retryable upstream status."""


class _DropThinking(Exception):
    """The endpoint rejected thinkingConfig; resend without it."""


_RETRYABLE = frozenset({429, 500, 502, 503, 504})
_MAX_TRIES = 5
_BACKOFF_CAP = 3.0


def tries_for(req: "Req") -> int:
    return max(1, req.retries or _MAX_TRIES)


async def _backoff(tries: int) -> None:
    delay = min(0.5 * (2 ** (tries - 1)), _BACKOFF_CAP)
    await asyncio.sleep(delay + random.uniform(0, 0.4))


# -------------------------------------------------------------------- Gemini
_AI_STUDIO = "https://generativelanguage.googleapis.com/v1beta"
_FLASH = ("2.5-flash", "2.0-flash", "-flash-lite", "flash-latest")


class GeminiClient:
    """Google AI Studio (`mode="gemini"`) and Vertex AI (`mode="vertex"`)."""

    def __init__(self, model: str, mode: str = "gemini") -> None:
        self.model, self.name, self._mode = model, mode, mode
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(settings.llm_timeout))
        self._token: tuple[str, float] | None = None
        self._token_lock = asyncio.Lock()
        env = (os.environ.get("GEMINI_THINKING_BUDGET") or "").strip()
        if env == "off":
            self._budget: int | None = None
        elif env.isdigit():
            self._budget = int(env)
        else:
            self._budget = 0 if any(h in model.lower() for h in _FLASH) else None
        self._thinking_ok = self._budget is not None
        self._schema_ok = True

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token and self._token[1] - time.time() > 60:
                return self._token[0]
            token = await asyncio.to_thread(self._fetch_token)
            if not token:
                raise LLMError(
                    "No Google credentials for Vertex AI. Run `gcloud auth "
                    "application-default login`, or deploy with a service account."
                )
            self._token = (token, time.time() + 3000)
            return token

    def _fetch_token(self) -> str | None:
        try:                                    # ADC / workload identity
            import google.auth
            import google.auth.transport.requests

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(google.auth.transport.requests.Request())
            return creds.token
        except Exception:
            pass
        try:                                    # Cloud Run / GCE metadata server
            r = httpx.get(
                "http://metadata.google.internal/computeMetadata/v1/instance/"
                "service-accounts/default/token",
                headers={"Metadata-Flavor": "Google"}, timeout=3.0,
            )
            if r.status_code == 200:
                return r.json().get("access_token")
        except Exception:
            pass
        try:                                    # gcloud CLI
            import subprocess

            out = subprocess.run(["gcloud", "auth", "print-access-token"],
                                 capture_output=True, text=True, timeout=20)
            if out.returncode == 0:
                return out.stdout.strip()
        except Exception:
            pass
        return None

    def _payload(self, req: Req) -> dict:
        gen: dict = {
            "temperature": req.temperature,
            "maxOutputTokens": min(req.max_tokens, settings.max_output_tokens),
        }
        if req.json_mode:
            gen["responseMimeType"] = "application/json"
            if req.schema and self._schema_ok:
                gen["responseSchema"] = req.schema
        if self._thinking_ok and self._budget is not None:
            gen["thinkingConfig"] = {"thinkingBudget": self._budget}
        body: dict = {
            "contents": [
                {"role": "user" if m.role == "user" else "model", "parts": [{"text": m.content}]}
                for m in req.messages
            ],
            "generationConfig": gen,
            # The minds argue, threaten and manipulate by design; the default
            # filters occasionally fire on ordinary play.
            "safetySettings": [
                {"category": c, "threshold": "BLOCK_ONLY_HIGH"}
                for c in ("HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                          "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")
            ],
        }
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}
        return body

    async def _url(self, method: str) -> tuple[str, dict]:
        if self._mode == "vertex":
            proj, loc = settings.google_project, settings.vertex_location or "global"
            host = ("https://aiplatform.googleapis.com" if loc == "global"
                    else f"https://{loc}-aiplatform.googleapis.com")
            return (
                f"{host}/v1/projects/{proj}/locations/{loc}"
                f"/publishers/google/models/{self.model}:{method}",
                {"Authorization": f"Bearer {await self._access_token()}"},
            )
        return f"{_AI_STUDIO}/models/{self.model}:{method}", {"x-goog-api-key": settings.gemini_api_key}

    @staticmethod
    def _text_from(payload: dict) -> str:
        out = []
        for cand in payload.get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                if isinstance(part.get("text"), str):
                    out.append(part["text"])
        return "".join(out)

    def _raise(self, body: str, status: int) -> None:
        low = body.lower()
        if status == 400 and "thinking" in low and self._thinking_ok:
            self._thinking_ok = False
            raise _DropThinking()
        if status == 400 and "responseschema" in low.replace("_", "") and self._schema_ok:
            self._schema_ok = False
            raise _DropThinking()
        msg = f"{self._mode} error {status}: {body[:300]}"
        if status in _RETRYABLE:
            usage.throttles += 1
            raise _Throttled(msg)
        raise LLMError(msg)

    async def complete(self, req: Req) -> str:
        budget = tries_for(req)
        for tries in range(1, budget + 1):
            url, headers = await self._url("generateContent")
            try:
                r = await self._client.post(url, headers=headers, json=self._payload(req))
                if r.status_code >= 400:
                    self._raise(r.text, r.status_code)
                data = r.json()
            except _DropThinking:
                continue
            except _Throttled as exc:
                if tries >= budget:
                    raise LLMError(str(exc)) from exc
                await _backoff(tries)
                continue
            except httpx.HTTPError as exc:
                if tries >= budget:
                    raise LLMError(f"request failed: {exc}") from exc
                await _backoff(tries)
                continue
            text = self._text_from(data)
            if not text:
                blocked = (data.get("promptFeedback") or {}).get("blockReason")
                if blocked:
                    raise LLMError(f"prompt blocked ({blocked})")
            return text.strip()
        return ""

    async def stream(self, req: Req) -> AsyncIterator[str]:
        budget = tries_for(req)
        for tries in range(1, budget + 1):
            url, headers = await self._url("streamGenerateContent")
            sent = False
            try:
                async with self._client.stream(
                    "POST", url, headers=headers, params={"alt": "sse"}, json=self._payload(req)
                ) as r:
                    if r.status_code >= 400:
                        self._raise((await r.aread()).decode("utf-8", "replace"), r.status_code)
                    async for raw_line in r.aiter_lines():
                        if not raw_line.startswith("data:"):
                            continue
                        raw = raw_line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            piece = self._text_from(json.loads(raw))
                        except json.JSONDecodeError:
                            continue
                        if piece:
                            sent = True
                            yield piece
                return
            except _DropThinking:
                continue
            except _Throttled as exc:
                # Status is read before the first yield, so a resend cannot
                # duplicate text the player already saw.
                if tries >= budget:
                    raise LLMError(str(exc)) from exc
                await _backoff(tries)
                continue
            except httpx.HTTPError as exc:
                if sent or tries >= budget:
                    raise LLMError(f"stream failed: {exc}") from exc
                await _backoff(tries)
                continue

    async def aclose(self) -> None:
        await self._client.aclose()


# ----------------------------------------------------------------- Anthropic
class AnthropicClient:
    def __init__(self, model: str) -> None:
        self.model, self.name = model, "anthropic"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout),
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": settings.anthropic_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    def _body(self, req: Req, stream: bool = False) -> dict:
        return {
            "model": self.model,
            "system": req.system,
            "messages": [{"role": m.role, "content": m.content} for m in req.messages],
            "max_tokens": min(req.max_tokens, settings.max_output_tokens),
            "temperature": req.temperature,
            "stream": stream,
        }

    async def complete(self, req: Req) -> str:
        for tries in range(1, _MAX_TRIES + 1):
            try:
                r = await self._client.post("/v1/messages", json=self._body(req))
                if r.status_code in _RETRYABLE:
                    usage.throttles += 1
                    raise _Throttled(f"anthropic {r.status_code}")
                if r.status_code >= 400:
                    raise LLMError(f"anthropic error {r.status_code}: {r.text[:300]}")
            except (_Throttled, httpx.HTTPError) as exc:
                if tries >= _MAX_TRIES:
                    raise LLMError(str(exc)) from exc
                await _backoff(tries)
                continue
            blocks = r.json().get("content") or []
            return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()
        return ""

    async def stream(self, req: Req) -> AsyncIterator[str]:
        async with self._client.stream("POST", "/v1/messages", json=self._body(req, True)) as r:
            if r.status_code >= 400:
                raise LLMError(f"anthropic error {r.status_code}")
            async for raw_line in r.aiter_lines():
                if not raw_line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(raw_line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "content_block_delta":
                    piece = (ev.get("delta") or {}).get("text")
                    if piece:
                        yield piece

    async def aclose(self) -> None:
        await self._client.aclose()


# ------------------------------------------------------- OpenAI-compatible
class OpenAICompatClient:
    def __init__(self, model: str) -> None:
        self.model, self.name = model, "openai"
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.llm_timeout),
            base_url=settings.openai_base_url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        )

    def _body(self, req: Req, stream: bool = False) -> dict:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": req.system}]
            + [{"role": m.role, "content": m.content} for m in req.messages],
            "max_tokens": min(req.max_tokens, settings.max_output_tokens),
            "temperature": req.temperature,
            "stream": stream,
        }
        if req.json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    async def complete(self, req: Req) -> str:
        for tries in range(1, _MAX_TRIES + 1):
            try:
                r = await self._client.post("/chat/completions", json=self._body(req))
                if r.status_code in _RETRYABLE:
                    usage.throttles += 1
                    raise _Throttled(f"openai {r.status_code}")
                if r.status_code >= 400:
                    raise LLMError(f"openai error {r.status_code}: {r.text[:300]}")
            except (_Throttled, httpx.HTTPError) as exc:
                if tries >= _MAX_TRIES:
                    raise LLMError(str(exc)) from exc
                await _backoff(tries)
                continue
            choices = r.json().get("choices") or [{}]
            return ((choices[0].get("message") or {}).get("content") or "").strip()
        return ""

    async def stream(self, req: Req) -> AsyncIterator[str]:
        async with self._client.stream("POST", "/chat/completions", json=self._body(req, True)) as r:
            if r.status_code >= 400:
                raise LLMError(f"openai error {r.status_code}")
            async for raw_line in r.aiter_lines():
                if not raw_line.startswith("data:"):
                    continue
                raw = raw_line[5:].strip()
                if not raw or raw == "[DONE]":
                    continue
                try:
                    delta = (json.loads(raw)["choices"][0].get("delta") or {}).get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    async def aclose(self) -> None:
        await self._client.aclose()


# ------------------------------------------------------------------ registry
_client = None
_gate: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(max(1, settings.llm_concurrency))
    return _gate


def build_client():
    p = settings.provider
    if p in ("gemini", "vertex"):
        return GeminiClient(settings.model, mode=p)
    if p == "anthropic":
        return AnthropicClient(settings.model)
    if p == "openai":
        return OpenAICompatClient(settings.model)
    from .mock import MockClient

    return MockClient(settings.model)


def get_client():
    global _client
    if _client is None:
        _client = build_client()
        log.info("model backend provider=%s model=%s", _client.name, _client.model)
    return _client


def set_client(client) -> None:
    """Test seam."""
    global _client
    _client = client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class LLM:
    """The instrumented, rate-gated facade the game calls."""

    @staticmethod
    async def text(req: Req) -> str:
        started = time.perf_counter()
        async with _semaphore():
            try:
                out = await get_client().complete(req)
            except LLMError:
                usage.errors += 1
                raise
            except Exception as exc:                      # normalise surprises
                usage.errors += 1
                raise LLMError(str(exc)) from exc
        usage.record(req.task, len(req.system) + sum(len(m.content) for m in req.messages),
                     len(out), (time.perf_counter() - started) * 1000)
        return out

    @staticmethod
    async def json(req: Req, default: dict) -> dict:
        req.json_mode = True
        try:
            raw = await LLM.text(req)
        except LLMError as exc:
            log.warning("json call failed task=%s: %s", req.task, exc)
            return {**default, "_fallback": True}
        parsed = extract_json(raw)
        if not isinstance(parsed, dict):
            log.warning("unparseable json task=%s raw=%r", req.task, raw[:200])
            return {**default, "_fallback": True}
        return parsed

    @staticmethod
    def stream(req: Req) -> "_Stream":
        return _Stream(req)


class _Stream:
    """Streams tokens while accumulating the full text for the caller."""

    def __init__(self, req: Req) -> None:
        self.req = req
        self.text = ""

    async def __aiter__(self):
        started = time.perf_counter()
        async with _semaphore():
            try:
                async for piece in get_client().stream(self.req):
                    self.text += piece
                    yield piece
            except LLMError:
                usage.errors += 1
                raise
            except Exception as exc:
                usage.errors += 1
                raise LLMError(str(exc)) from exc
            finally:
                usage.record(
                    self.req.task,
                    len(self.req.system) + sum(len(m.content) for m in self.req.messages),
                    len(self.text), (time.perf_counter() - started) * 1000,
                )
