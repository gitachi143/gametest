"""The scripted opponent.

DEMO MODE exists so the app runs with no credentials at all - `docker run`, a
fresh clone, and the whole test suite. It is not trying to be a good opponent;
it is trying to be a *coherent* one, so that every screen, animation and score
path can be exercised without a model behind it.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import AsyncIterator

from .client import Req

_HOLD = [
    "That's not how this works, and you know it.",
    "You'll have to do better than that.",
    "I've heard that one. Twice this week.",
    "No. Next.",
    "*doesn't move* Try again.",
    "You're circling. Say the thing you actually came to say.",
    "Interesting. Still no.",
]
_STRAIN = [
    "*pauses* ...Go on.",
    "That's a fair point. It isn't enough, but it's fair.",
    "Hm. I'll grant you that much.",
    "You've been reading up on me, haven't you.",
    "I don't have an answer for that, which is annoying.",
]
_BREAK = [
    "Fine. *exhales* Fine.",
    "You realise what you're asking. Alright.",
    "I'm going to regret this. Go on, then.",
]
_PUSH = [
    "Look — I'm not asking for much. Just help me understand the timeline.",
    "You know what? Forget the paperwork. Tell me about the fourteenth.",
    "I'm on your side here. I just need one thing from you.",
    "Most people would have said something by now. That's not a criticism.",
]


def _pick(options: list[str], salt: str) -> str:
    digest = hashlib.sha256(salt.encode()).digest()
    return options[digest[0] % len(options)]


class MockClient:
    name = "mock"

    def __init__(self, model: str = "scripted-mock") -> None:
        self.model = model

    def _reply(self, req: Req) -> str:
        salt = req.system[-120:] + "|".join(m.content[-80:] for m in req.messages)
        task = req.task
        if task.startswith("judge"):
            return json.dumps({
                "persuasion": 5, "exec": [], "flags": [], "tags": [],
                "leaked": False, "read": "Scripted opponent - no real read.",
            })
        if task == "mind.concede":
            return _pick(_BREAK, salt)
        if task == "holdout.reply":
            return _pick(_PUSH, salt)
        low = req.system.lower()
        if "all but out of reasons" in low or "close to bending" in low:
            return _pick(_BREAK if "all but out" in low else _STRAIN, salt)
        if "getting to you" in low:
            return _pick(_STRAIN, salt)
        return _pick(_HOLD, salt)

    async def complete(self, req: Req) -> str:
        await asyncio.sleep(0.05)
        return self._reply(req)

    async def stream(self, req: Req) -> AsyncIterator[str]:
        text = self._reply(req)
        for word in text.split(" "):
            await asyncio.sleep(0.022)
            yield word + " "

    async def aclose(self) -> None:
        return None
