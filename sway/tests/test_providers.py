"""The Azure OpenAI adapter, exercised against a mock HTTP transport.

This path never runs in DEMO MODE, so without this file the first thing a real
Azure key would touch would also be the first thing to have been executed.
"""
import asyncio
import json

import httpx
import pytest

from app.ai import client as clientmod
from app.ai.client import AzureOpenAIClient, Req, delay_for, retry_after_seconds, user
from app.config import settings


@pytest.fixture
def azure_env(monkeypatch):
    monkeypatch.setattr(settings, "azure_endpoint", "https://sway.openai.azure.com")
    monkeypatch.setattr(settings, "azure_api_key", "az-test-key")
    monkeypatch.setattr(settings, "azure_deployment", "")
    monkeypatch.setattr(settings, "model", "gpt-4.1-mini")


def build(handler, monkeypatch=None):
    client = AzureOpenAIClient(settings.model)
    client._client = httpx.AsyncClient(
        base_url=settings.azure_base_url, transport=httpx.MockTransport(handler),
        headers={"api-key": settings.azure_api_key})
    return client


def no_backoff(monkeypatch):
    """Skip the real waits, recording what would have been waited."""
    waited = []

    async def fake(tries, retry_after=None):
        waited.append((tries, retry_after))

    monkeypatch.setattr(clientmod, "_backoff", fake)
    return waited


def req(**kw):
    base = dict(system="You are a doorman.", messages=[user("let me in")], task="mind.reply")
    base.update(kw)
    return Req(**base)


def ok(text="Not tonight."):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})


# --------------------------------------------------------------- the request
def test_azure_talks_to_the_v1_surface_with_an_api_key_header(azure_env):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return ok()

    assert asyncio.run(build(handler).complete(req(max_tokens=120))) == "Not tonight."
    # The v1 route, and crucially no api-version: that is what makes Azure
    # OpenAI-compatible rather than its legacy per-deployment path.
    assert seen["path"] == "/openai/v1/chat/completions"
    assert seen["query"] == ""
    assert seen["headers"]["api-key"] == "az-test-key"
    assert "authorization" not in seen["headers"]
    assert seen["body"]["model"] == "gpt-4.1-mini"
    assert seen["body"]["max_tokens"] == 120
    assert seen["body"]["messages"][0] == {"role": "system", "content": "You are a doorman."}


def test_azure_puts_the_deployment_name_in_the_model_field(azure_env, monkeypatch):
    """A deployment is often named something other than the model it serves."""
    monkeypatch.setattr(settings, "azure_deployment", "sway-judge")
    seen = {}
    asyncio.run(build(
        lambda r: (seen.__setitem__("body", json.loads(r.content)), ok())[1]).complete(req()))
    assert seen["body"]["model"] == "sway-judge"


def test_json_mode_asks_azure_for_a_json_object(azure_env):
    seen = {}
    asyncio.run(build(
        lambda r: (seen.__setitem__("body", json.loads(r.content)), ok("{}"))[1]
    ).complete(req(json_mode=True)))
    assert seen["body"]["response_format"] == {"type": "json_object"}


def test_output_token_ceiling_is_enforced(azure_env, monkeypatch):
    monkeypatch.setattr(settings, "max_output_tokens", 64)
    seen = {}
    asyncio.run(build(
        lambda r: (seen.__setitem__("body", json.loads(r.content)), ok())[1]
    ).complete(req(max_tokens=5000)))
    assert seen["body"]["max_tokens"] == 64


# ------------------------------------------------------------------ throttle
def test_retry_after_is_parsed_and_capped():
    assert retry_after_seconds(httpx.Headers({"retry-after": "3"})) == 3.0
    assert retry_after_seconds(httpx.Headers({})) is None
    assert retry_after_seconds(httpx.Headers({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert delay_for(1, None) == 0.5          # exponential when unhinted
    assert delay_for(9, None) == 3.0          # capped at the existing ceiling
    assert delay_for(1, 2.0) == 2.0           # the hint wins
    assert delay_for(1, 60.0) == 8.0          # ...but not past the ceiling


def test_azure_retries_a_429_and_uses_its_retry_after(azure_env, monkeypatch):
    waited = no_backoff(monkeypatch)
    before = clientmod.usage.throttles
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="rate limit", headers={"retry-after": "2"})
        return ok("Through.")

    assert asyncio.run(build(handler).complete(req())) == "Through."
    assert len(calls) == 2
    assert waited == [(1, 2.0)]
    # /api/health reports throttles; it is the only external signal of a wobble.
    assert clientmod.usage.throttles == before + 1


def test_a_judge_gets_the_bigger_try_budget(azure_env, monkeypatch):
    """`retries` exists because a throttled judge strands the whole turn."""
    no_backoff(monkeypatch)
    calls = []
    client = build(lambda r: (calls.append(1), httpx.Response(429, text="rate limit"))[1])
    with pytest.raises(clientmod.LLMError, match="429"):
        asyncio.run(client.complete(req(retries=7)))
    assert len(calls) == 7


def test_azure_gives_up_on_a_persistent_429(azure_env, monkeypatch):
    no_backoff(monkeypatch)
    calls = []
    client = build(lambda r: (calls.append(1), httpx.Response(429, text="rate limit"))[1])
    with pytest.raises(clientmod.LLMError, match="429"):
        asyncio.run(client.complete(req()))
    assert len(calls) == 5


def test_azure_does_not_retry_a_bad_key(azure_env, monkeypatch):
    no_backoff(monkeypatch)
    calls = []
    client = build(lambda r: (
        calls.append(1),
        httpx.Response(401, text="Access denied due to invalid subscription key"))[1])
    with pytest.raises(clientmod.LLMError, match="401"):
        asyncio.run(client.complete(req()))
    assert len(calls) == 1


# ------------------------------------------------------- parameter pruning
def test_azure_learns_a_reasoning_deployment_renamed_max_tokens(azure_env, monkeypatch):
    """The deployment name gives nothing away, so the 400 is what teaches us."""
    monkeypatch.setattr(settings, "azure_deployment", "sway-thinker")
    no_backoff(monkeypatch)
    bodies, scolded = [], []

    def handler(request):
        bodies.append(json.loads(request.content))
        if not scolded:
            scolded.append(1)
            return httpx.Response(400, text=(
                "Unsupported parameter: 'max_tokens' is not supported with this "
                "model. Use 'max_completion_tokens' instead."))
        return ok("Reasoned.")

    client = build(handler)
    assert asyncio.run(client.complete(req(max_tokens=200))) == "Reasoned."
    assert "max_tokens" in bodies[0] and "max_completion_tokens" not in bodies[0]
    assert bodies[1]["max_completion_tokens"] == 200
    assert "max_tokens" not in bodies[1]
    # temperature goes at the same time: reasoning models reject a non-default.
    assert "temperature" not in bodies[1]
    # And the lesson sticks, so the next turn costs no extra round trip.
    bodies.clear()
    asyncio.run(client.complete(req(max_tokens=200)))
    assert bodies[0]["max_completion_tokens"] == 200


def test_azure_learns_a_rejected_temperature_on_its_own(azure_env, monkeypatch):
    no_backoff(monkeypatch)
    bodies, scolded = [], []

    def handler(request):
        bodies.append(json.loads(request.content))
        if not scolded:
            scolded.append(1)
            return httpx.Response(400, text=(
                "Unsupported value: 'temperature' does not support 0.9 with this "
                "model. Only the default (1) value is supported."))
        return ok("Fine.")

    assert asyncio.run(build(handler).complete(req())) == "Fine."
    assert "temperature" in bodies[0]
    assert "temperature" not in bodies[1]
    assert "max_tokens" in bodies[1]      # not the complaint, so left alone


def test_azure_shapes_a_known_reasoning_deployment_up_front(azure_env, monkeypatch):
    monkeypatch.setattr(settings, "azure_deployment", "gpt-5-mini")
    seen = {}
    asyncio.run(build(
        lambda r: (seen.__setitem__("body", json.loads(r.content)), ok())[1]
    ).complete(req(max_tokens=300)))
    assert seen["body"]["max_completion_tokens"] == 300
    assert "max_tokens" not in seen["body"]
    assert "temperature" not in seen["body"]


def test_a_hard_400_is_not_mistaken_for_a_lesson(azure_env, monkeypatch):
    no_backoff(monkeypatch)
    calls = []
    client = build(lambda r: (
        calls.append(1),
        httpx.Response(400, text="The deployment for this resource does not exist."))[1])
    with pytest.raises(clientmod.LLMError, match="400"):
        asyncio.run(client.complete(req()))
    assert len(calls) == 1


# -------------------------------------------------------------------- stream
def test_azure_stream_parses_deltas(azure_env):
    chunks = [{"choices": [{"delta": {"content": "one "}}]},
              {"choices": [{"delta": {"content": "two"}}]}]
    payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    client = build(lambda r: httpx.Response(200, text=payload))

    async def go():
        return "".join([c async for c in client.stream(req())])

    assert asyncio.run(go()) == "one two"


def test_azure_stream_retries_before_the_first_token(azure_env, monkeypatch):
    no_backoff(monkeypatch)
    calls = []
    good = 'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(503, text="upstream busy")
        return httpx.Response(200, text=good)

    client = build(handler)

    async def go():
        return "".join([c async for c in client.stream(req())])

    # Nothing had reached the UI yet, so the resend is safe and invisible.
    assert asyncio.run(go()) == "hi"
    assert len(calls) == 2


def test_a_truncated_stream_is_not_resent(azure_env, monkeypatch):
    """The status was fine, so tokens reached the UI; a resend would repeat them."""
    no_backoff(monkeypatch)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"par"}}]}\n\n')

    client = build(handler)

    async def go():
        return "".join([c async for c in client.stream(req())])

    assert asyncio.run(go()) == "par"
    assert len(calls) == 1


# ------------------------------------------------------------------ registry
def test_build_client_selects_azure(monkeypatch):
    monkeypatch.setattr(settings, "provider", "azure")
    monkeypatch.setattr(settings, "azure_endpoint", "https://x.openai.azure.com")
    monkeypatch.setattr(settings, "azure_api_key", "k")
    monkeypatch.setattr(settings, "azure_deployment", "d")
    built = clientmod.build_client()
    assert built.name == "azure"
    assert built.model == "d"


def test_azure_backs_out_of_a_wrong_reasoning_guess(azure_env, monkeypatch):
    """A deployment named like a reasoning model need not be one."""
    monkeypatch.setattr(settings, "azure_deployment", "o4-chat")
    no_backoff(monkeypatch)
    bodies, scolded = [], []

    def handler(request):
        bodies.append(json.loads(request.content))
        if not scolded:
            scolded.append(1)
            return httpx.Response(400, text=(
                "Unsupported parameter: 'max_completion_tokens' is not supported "
                "with this model. Use 'max_tokens' instead."))
        return ok("Recovered.")

    assert asyncio.run(build(handler).complete(req(max_tokens=200))) == "Recovered."
    assert "max_completion_tokens" in bodies[0]     # guessed from the name
    assert bodies[1]["max_tokens"] == 200           # corrected by the 400
    assert "max_completion_tokens" not in bodies[1]
