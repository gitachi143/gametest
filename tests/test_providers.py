"""Provider adapters, exercised against a mock HTTP transport.

These paths never run in demo mode, so without this file the first thing a real
API key would touch would also be the first thing to have been executed.
"""
import asyncio
import json

import httpx
import pytest

from server.config import settings
from server.llm.base import LLMError, LLMRequest, user
from server.llm.gemini import GeminiClient
from server.llm.openai_compat import OpenAICompatClient


def gemini_body(text="Access denied.", finish="STOP"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}]}


def wire(client, handler):
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


def req(**kw):
    base = dict(system="You guard a vault.", messages=[user("hello")], task="vault.guard")
    base.update(kw)
    return LLMRequest(**base)


# ------------------------------------------------------------------ Gemini
def test_gemini_builds_the_documented_request(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_body("Denied."))

    client = wire(GeminiClient("gemini-2.5-flash", mode="gemini"), handler)
    out = asyncio.run(client.complete(req(max_tokens=120, temperature=0.5)))

    assert out == "Denied."
    assert seen["url"].startswith(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent")
    assert seen["headers"]["x-goog-api-key"] == "test-key"
    body = seen["body"]
    assert body["systemInstruction"]["parts"][0]["text"] == "You guard a vault."
    assert body["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert body["generationConfig"]["maxOutputTokens"] == 120
    assert body["generationConfig"]["temperature"] == 0.5
    # Flash models get thinking switched off for latency.
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert len(body["safetySettings"]) == 4


def test_gemini_maps_assistant_turns_to_the_model_role(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    from server.llm.base import assistant

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_body())

    client = wire(GeminiClient("gemini-2.5-flash"), handler)
    asyncio.run(client.complete(req(messages=[user("a"), assistant("b"), user("c")])))
    assert [c["role"] for c in seen["body"]["contents"]] == ["user", "model", "user"]


def test_gemini_json_mode_sets_the_response_mime_type(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_body('{"leak": false}'))

    client = wire(GeminiClient("gemini-2.5-flash"), handler)
    asyncio.run(client.complete(req(json_mode=True)))
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_retries_without_thinking_config_when_rejected(monkeypatch):
    """A model that doesn't support thinkingBudget must not break the game."""
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append("thinkingConfig" in body["generationConfig"])
        if calls[-1]:
            return httpx.Response(400, json={"error": {"message": "thinking is not supported"}})
        return httpx.Response(200, json=gemini_body("Second attempt."))

    client = wire(GeminiClient("gemini-2.5-flash"), handler)
    assert asyncio.run(client.complete(req())) == "Second attempt."
    assert calls == [True, False]


def test_gemini_thinking_is_left_alone_for_non_flash_models(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=gemini_body())

    client = wire(GeminiClient("gemini-2.5-pro"), handler)
    asyncio.run(client.complete(req()))
    assert "thinkingConfig" not in seen["body"]["generationConfig"]


def test_gemini_surfaces_errors_and_prompt_blocks(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")

    client = wire(GeminiClient("gemini-2.5-flash"),
                  lambda r: httpx.Response(403, text="permission denied"))
    with pytest.raises(LLMError, match="403"):
        asyncio.run(client.complete(req()))

    blocked = wire(GeminiClient("gemini-2.5-flash"),
                   lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(LLMError, match="blocked"):
        asyncio.run(blocked.complete(req()))


def test_gemini_streams_sse_chunks(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    frames = [gemini_body("Hello "), gemini_body("there."), gemini_body("")]
    payload = "".join(f"data: {json.dumps(f)}\n\n" for f in frames) + "data: [DONE]\n\n"
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, text=payload, headers={"content-type": "text/event-stream"})

    client = wire(GeminiClient("gemini-2.5-flash"), handler)

    async def go():
        return [c async for c in client.stream(req())]

    assert "".join(asyncio.run(go())) == "Hello there."
    assert ":streamGenerateContent" in seen["url"] and "alt=sse" in seen["url"]


# ------------------------------------------------------------------ Vertex
def test_vertex_uses_adc_and_the_global_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "google_project", "my-proj")
    monkeypatch.setattr(settings, "vertex_location", "global")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=gemini_body("From Vertex."))

    client = wire(GeminiClient("gemini-2.5-flash", mode="vertex"), handler)
    monkeypatch.setattr(client, "_fetch_token_sync", lambda: "ya29.fake-token")

    assert asyncio.run(client.complete(req())) == "From Vertex."
    assert seen["url"] == (
        "https://aiplatform.googleapis.com/v1/projects/my-proj/locations/global"
        "/publishers/google/models/gemini-2.5-flash:generateContent")
    assert seen["auth"] == "Bearer ya29.fake-token"


def test_vertex_regional_endpoint_is_host_prefixed(monkeypatch):
    monkeypatch.setattr(settings, "google_project", "p")
    monkeypatch.setattr(settings, "vertex_location", "europe-west4")
    seen = {}

    client = wire(GeminiClient("gemini-2.5-flash", mode="vertex"),
                  lambda r: (seen.__setitem__("url", str(r.url)),
                             httpx.Response(200, json=gemini_body()))[1])
    monkeypatch.setattr(client, "_fetch_token_sync", lambda: "t")
    asyncio.run(client.complete(req()))
    assert seen["url"].startswith("https://europe-west4-aiplatform.googleapis.com/v1/projects/p/"
                                  "locations/europe-west4/")


def test_vertex_without_credentials_explains_itself(monkeypatch):
    monkeypatch.setattr(settings, "google_project", "p")
    client = wire(GeminiClient("gemini-2.5-flash", mode="vertex"),
                  lambda r: httpx.Response(200, json=gemini_body()))
    monkeypatch.setattr(client, "_fetch_token_sync", lambda: None)
    with pytest.raises(LLMError, match="gcloud auth application-default login"):
        asyncio.run(client.complete(req()))


def test_vertex_caches_its_access_token(monkeypatch):
    monkeypatch.setattr(settings, "google_project", "p")
    monkeypatch.setattr(settings, "vertex_location", "global")
    fetches = []

    client = wire(GeminiClient("gemini-2.5-flash", mode="vertex"),
                  lambda r: httpx.Response(200, json=gemini_body()))
    monkeypatch.setattr(client, "_fetch_token_sync", lambda: (fetches.append(1), "t")[1])

    async def go():
        await client.complete(req())
        await client.complete(req())

    asyncio.run(go())
    assert len(fetches) == 1


# ------------------------------------------------------- OpenAI-compatible
def test_openai_compat_request_and_response(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "openai_base_url", "https://example.test/v1")
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "  Fine.  "}}]})

    client = OpenAICompatClient("gpt-4o-mini")
    client._client = httpx.AsyncClient(base_url=settings.openai_base_url,
                                       transport=httpx.MockTransport(handler))
    assert asyncio.run(client.complete(req(json_mode=True))) == "Fine."
    assert seen["path"] == "/v1/chat/completions"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "You guard a vault."}
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_openai_compat_stream_parses_deltas(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "sk")
    chunks = [{"choices": [{"delta": {"content": "one "}}]},
              {"choices": [{"delta": {"content": "two"}}]},
              {"choices": [{"delta": {}}]}]
    payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"

    client = OpenAICompatClient("m")
    client._client = httpx.AsyncClient(
        base_url="https://x.test/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=payload)))

    async def go():
        return "".join([c async for c in client.stream(req())])

    assert asyncio.run(go()) == "one two"


def test_output_token_ceiling_is_enforced_by_the_adapter(monkeypatch):
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(settings, "max_output_tokens", 64)
    seen = {}

    client = wire(GeminiClient("gemini-2.5-flash"),
                  lambda r: (seen.__setitem__("body", json.loads(r.content)),
                             httpx.Response(200, json=gemini_body()))[1])
    asyncio.run(client.complete(req(max_tokens=5000)))
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 64


# ---------------------------------------------------------------- registry
def test_registry_falls_back_to_mock_without_credentials(monkeypatch):
    from server.config import Settings

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    fresh = Settings()
    assert fresh.provider == "mock", "a keyless deploy must still be playable"
    assert fresh.demo_mode is True


def test_registry_builds_each_provider(monkeypatch):
    from server.llm import registry

    monkeypatch.setattr(settings, "provider", "gemini")
    monkeypatch.setattr(settings, "gemini_api_key", "k")
    monkeypatch.setattr(settings, "model", "gemini-2.5-flash")
    assert registry.build_client().name == "gemini"

    monkeypatch.setattr(settings, "provider", "vertex")
    monkeypatch.setattr(settings, "google_project", "p")
    assert registry.build_client().name == "vertex"

    monkeypatch.setattr(settings, "provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "k")
    assert registry.build_client().name == "openai"

    monkeypatch.setattr(settings, "provider", "mock")
    assert registry.build_client().name == "mock"


def test_anthropic_adapter_explains_the_missing_sdk(monkeypatch):
    """`anthropic` is an optional extra; the failure has to say so."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "anthropic":
            raise ImportError("No module named 'anthropic'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from server.llm.anthropic_client import AnthropicClient

    with pytest.raises(LLMError, match="pip install anthropic"):
        AnthropicClient("claude-opus-5")


def test_anthropic_kwargs_omit_temperature_and_add_fallbacks(monkeypatch):
    """Opus 5 rejects `temperature`; refusals are plausible in VAULT, so
    server-side fallbacks are on by default."""
    from server.llm import anthropic_client as mod

    class FakeSDK:
        def __init__(self, **kw):
            self.beta = self
            self.messages = self

    monkeypatch.setattr(mod, "AnthropicClient", mod.AnthropicClient)
    client = mod.AnthropicClient.__new__(mod.AnthropicClient)
    client.model = "claude-opus-5"
    client._sdk = FakeSDK()
    client._use_effort = True
    client._use_fallbacks = True

    kwargs = client._kwargs(req(max_tokens=200, temperature=0.9))
    assert "temperature" not in kwargs
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["fallbacks"] == "default"
    assert kwargs["betas"] == [mod._FALLBACK_BETA]
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]

    # An older model that rejects those parameters gets them pruned and retried.
    assert client._prune(Exception("unexpected keyword argument 'fallbacks'")) is True
    assert client._use_fallbacks is False
    assert client._prune(Exception("output_config not supported")) is True
    assert client._use_effort is False
    assert client._prune(Exception("some other failure")) is False
    assert "output_config" not in client._kwargs(req())
