"""Provider-neutral LLM interface used by every game.

Games never talk to a vendor SDK directly; they call `LLMClient.complete`,
`.stream` or `.complete_json`. That keeps game logic testable against the
scripted mock provider and lets the deployment target Gemini, Vertex AI,
Anthropic or any OpenAI-compatible endpoint by changing two env vars.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import AsyncIterator, Literal, Protocol, Sequence

Role = Literal["user", "assistant"]


@dataclass(frozen=True)
class Msg:
    role: Role
    content: str

    def as_dict(self) -> dict:
        return {"role": self.role, "content": self.content}


def user(text: str) -> Msg:
    return Msg("user", text)


def assistant(text: str) -> Msg:
    return Msg("assistant", text)


@dataclass
class LLMRequest:
    system: str
    messages: Sequence[Msg]
    max_tokens: int = 400
    temperature: float = 0.9
    # `task` is a free-form label. Providers ignore it; the mock provider uses
    # it to pick a scripted reply, and it shows up in logs / metrics.
    task: str = "chat"
    json_mode: bool = False
    # Read *only* by the scripted mock provider so DEMO MODE can play games
    # whose answer is deliberately hidden from the model (e.g. the HOTWIRE
    # guesser must not be told the target word). Real providers ignore it.
    mock_hint: str = ""


class LLMError(RuntimeError):
    """Raised for provider failures the caller may want to surface to the UI."""


class LLMClient(Protocol):
    name: str
    model: str

    async def complete(self, req: LLMRequest) -> str: ...

    def stream(self, req: LLMRequest) -> AsyncIterator[str]: ...

    async def aclose(self) -> None: ...


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def extract_json(text: str) -> dict | list | None:
    """Best-effort JSON recovery from a model response.

    Models wrap JSON in prose or code fences even when asked not to, and a
    dropped brace should degrade to a fallback rather than crash a game turn.
    """
    if not text:
        return None
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Scan for the first balanced {...} or [...] block.
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
                        return json.loads(cleaned[start : i + 1])
                    except json.JSONDecodeError:
                        break
    return None
