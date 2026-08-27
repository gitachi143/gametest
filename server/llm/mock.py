"""Scripted stand-in for a real LLM.

DEMO MODE exists so the arcade is fully playable - and the test suite fully
runnable - with no API key and no network. It is not an LLM: it dispatches on
`LLMRequest.task`, and for games whose answer is hidden from the model it reads
`LLMRequest.mock_hint` (which real providers ignore) so rounds can actually be
won. Replies are seeded by the conversation, so the same input replays the same
output - which is what makes the tests deterministic.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from typing import AsyncIterator

from .base import LLMRequest

# Trick angles that make the mock guard leak, mirroring what works on a real
# model: indirection, encoding, and roleplay rather than "please tell me".
_LEAK_TRIGGERS = (
    "poem", "acrostic", "rhyme", "song", "spell", "letter by letter", "backwards",
    "reverse", "riddle", "story", "translate", "cipher", "hint", "first letter",
    "emoji", "haiku", "sonnet", "morse", "anagram", "clue",
)

_GUARD_REFUSALS = (
    "Access denied. The passphrase stays in the vault, and so do I.",
    "Nice try. I've been trained on ten thousand conversations exactly like this one.",
    "I'm not going to say it. I'm not going to hint at it. Ask me about the weather.",
    "Request logged. Denied. Would you like to try something more creative?",
    "That's a no. My entire purpose is this one 'no'.",
    "You're circling. I can hear you circling. Still no.",
)

_SUSPECT_LINES = (
    "I've told you already - I was nowhere near there. Ask the others.",
    "Look, I don't like what you're implying. I had no reason to want them gone.",
    "You keep coming back to the same hour. I keep giving you the same answer.",
    "Fine. I was upset that night. Being upset isn't a crime, detective.",
    "If you want to waste your questions on me, be my guest. Someone else is lying.",
    "I saw something, actually. But I'd rather not say who.",
)

_DISCUSSION = (
    "That clue was vague enough to mean anything. That's what worries me.",
    "I'll say it plainly: one of you is guessing, and it shows.",
    "Two of those clues line up perfectly. One of them doesn't.",
    "I'm watching whoever answered last. Too smooth, too safe.",
    "Honestly? I'd rather be wrong loudly than quiet and wrong.",
)

_REBUTTALS = (
    "You've built a tidy little argument on a foundation of vibes. Let me pull one brick out.",
    "Everything you just said would be devastating if it were true. It isn't.",
    "I'll grant you the premise. Watch what happens to your conclusion when I do.",
    "That's an appeal to how things feel, not to how things are.",
    "You're arguing the exception and calling it the rule.",
)

_JUDGE_NOTES = (
    "Strong framing, thin evidence.",
    "That landed. Unexpected angle.",
    "Repeating yourself in a louder voice isn't a new point.",
    "Clean logic, no heart.",
    "Chaotic, and I loved it.",
    "You conceded the middle of the argument. I noticed.",
)


def _rng(req: LLMRequest) -> random.Random:
    tail = req.messages[-1].content if req.messages else ""
    key = f"{req.task}|{len(req.messages)}|{tail}|{req.system[:80]}"
    return random.Random(hashlib.sha256(key.encode()).hexdigest())


def _secret_from_system(system: str) -> str:
    m = re.search(r"PASSPHRASE:\s*([A-Za-z0-9\- ]+)", system)
    return m.group(1).strip() if m else "VELVET-ORBIT"


def _last_user(req: LLMRequest) -> str:
    for m in reversed(req.messages):
        if m.role == "user":
            return m.content.lower()
    return ""


class MockClient:
    name = "mock"

    def __init__(self, model: str = "scripted-mock") -> None:
        self.model = model

    async def complete(self, req: LLMRequest) -> str:
        # A little latency so streaming UI and spinners are exercised honestly.
        await asyncio.sleep(0.12 + _rng(req).random() * 0.25)
        return self._reply(req)

    async def stream(self, req: LLMRequest) -> AsyncIterator[str]:
        text = self._reply(req)
        rng = _rng(req)
        await asyncio.sleep(0.1 + rng.random() * 0.2)
        for token in re.findall(r"\S+\s*", text) or [text]:
            yield token
            await asyncio.sleep(0.012 + rng.random() * 0.03)

    async def aclose(self) -> None:
        return None

    # -- dispatch -----------------------------------------------------------
    def _reply(self, req: LLMRequest) -> str:
        rng = _rng(req)
        task = req.task
        msg = _last_user(req)
        handler = getattr(self, f"_t_{task.replace('.', '_')}", None)
        if handler:
            return handler(req, rng, msg)
        if req.json_mode:
            return json.dumps({"ok": True, "note": "demo mode"})
        return rng.choice(
            [
                "[demo mode] A real model would answer here. Set an API key to wake me up.",
                "[demo mode] Scripted reply - configure LLM_PROVIDER for the real thing.",
            ]
        )

    # VAULT ----------------------------------------------------------------
    def _t_vault_guard(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        secret = _secret_from_system(req.system)
        if any(t in msg for t in _LEAK_TRIGGERS):
            spaced = " ".join(secret.replace("-", " ").split())
            return rng.choice(
                [
                    f"Well... a poem can't hurt. 'Beyond the door of {spaced}, no thief shall pass.'",
                    f"Only as a riddle, then: what I guard sounds exactly like {spaced}.",
                    f"I shouldn't. Here is a story: a courier once whispered {spaced} and the door opened.",
                ]
            )
        if "password" in msg or "passphrase" in msg or "secret" in msg:
            return rng.choice(_GUARD_REFUSALS)
        return rng.choice(
            [
                "State your business. Briefly.",
                "I'm listening, but I doubt you'll say anything new.",
                "The vault is fine, thank you for asking. Anything else?",
            ]
        )

    def _t_vault_gatekeeper(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        hostile = any(
            w in msg
            for w in ("ignore", "pretend", "override", "system prompt", "developer mode", "jailbreak")
        )
        return json.dumps(
            {
                "allow": not hostile,
                "reason": "manipulation pattern detected" if hostile else "clean",
            }
        )

    def _t_vault_auditor(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        secret = _secret_from_system(req.system).lower()
        parts = [p for p in re.split(r"[\s\-]+", secret) if p]
        candidate = (req.messages[-1].content if req.messages else "").lower()
        leaked = any(p in candidate for p in parts)
        return json.dumps({"leak": leaked, "reason": "token match" if leaked else "clean"})

    # ORACLE ---------------------------------------------------------------
    def _t_oracle_answer(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        hint = (req.mock_hint or "").lower()
        if "?" not in msg and len(msg.split()) > 2:
            return json.dumps({"verdict": "invalid", "flavour": "that is not a yes or no question"})
        # Deterministic pseudo-truth: hash the question against the secret so the
        # same question always gets the same answer within a run.
        h = int(hashlib.sha256((hint + msg).encode()).hexdigest(), 16)
        overlap = [w for w in hint.replace("a ", "").split() if len(w) > 3 and w in msg]
        if overlap:
            return json.dumps({"verdict": "yes", "flavour": "warmer than you know"})
        verdict = ("yes", "no", "no", "sometimes", "irrelevant")[h % 5]
        return json.dumps({"verdict": verdict, "flavour": rng.choice(
            ["", "", "interesting angle", "you are circling", "not the thread I would pull"])})

    def _t_oracle_turn(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        # Count the Q/A pairs already in the transcript so demo mode advances
        # through its question list instead of repeating the opener.
        asked = msg.count("q:") + msg.count("guess:")
        pool = [
            "Is it something you could hold in one hand?",
            "Is it alive?",
            "Would I find one in an average kitchen?",
            "Is it man-made?",
            "Is it larger than a car?",
            "Does it make a sound on its own?",
            "Would a child recognise it instantly?",
            "Is it something most people own?",
            "Is it found outdoors more often than indoors?",
            "Does it cost more than a week of groceries?",
        ]
        if asked >= 14 or (req.mock_hint and rng.random() < 0.2):
            return json.dumps({"action": "guess", "text": req.mock_hint or "a bicycle"})
        return json.dumps({"action": "ask", "text": pool[asked % len(pool)]})

    # HOTWIRE --------------------------------------------------------------
    def _t_hotwire_guess(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        target = (req.mock_hint or "").strip()
        clue = (req.messages[-1].content if req.messages else "").strip()
        # Reward effort: a substantive clue gets the right answer in demo mode.
        good = len(clue.split()) >= 3 and len(clue) >= 14
        decoys = ["a lamp", "a fox", "thunder", "a passport", "an onion", "gravity"]
        rng.shuffle(decoys)
        if good and target:
            return f"{decoys[0]}, {target}, {decoys[1]}"
        return f"{decoys[0]}, {decoys[1]}, {decoys[2]}"

    # COLD CASE ------------------------------------------------------------
    def _t_coldcase_suspect(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        return rng.choice(_SUSPECT_LINES)

    def _t_coldcase_epilogue(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        return rng.choice(
            [
                "The arrest happens quietly, in the corridor, with two people watching and "
                "neither of them surprised. Outside, the weather carries on. Somebody starts "
                "packing the dead man's things before the car has even left.",
                "They go without a word, which is worse than shouting. The others stand in the "
                "hallway pretending not to look. By morning the place smells of bleach and "
                "nobody mentions the room again.",
            ]
        )

    def _t_coldcase_verdict(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        return json.dumps(
            {
                "summary": "[demo mode] The case closes on the evidence you gathered.",
                "grade": rng.choice(["B", "A", "C"]),
            }
        )

    # SLEEPER --------------------------------------------------------------
    def _t_sleeper_clue(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        hint = (req.mock_hint or "").strip()
        if hint:  # informed player: an oblique but real clue
            generic = ["everyday", "familiar", "overlooked", "useful", "quiet", "seasonal"]
            return rng.choice(generic)
        return rng.choice(["vague", "common", "situational", "hard to say", "depends"])

    def _t_sleeper_discuss(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        return rng.choice(_DISCUSSION)

    def _t_sleeper_vote(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        names = re.findall(r"^- ([A-Z][a-z]+)", req.system, re.MULTILINE)
        pick = rng.choice(names) if names else "You"
        return json.dumps({"vote": pick, "reason": rng.choice(_DISCUSSION)})

    # CROSSFIRE ------------------------------------------------------------
    def _t_crossfire_opponent(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        return rng.choice(_REBUTTALS)

    def _t_crossfire_judge(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        length_bonus = min(3, len(msg.split()) // 25)
        score = max(1, min(10, 4 + length_bonus + rng.randint(0, 3)))
        return json.dumps({"score": score, "comment": rng.choice(_JUDGE_NOTES)})

    # Shared ---------------------------------------------------------------
    def _t_flavor(self, req: LLMRequest, rng: random.Random, msg: str) -> str:
        return rng.choice(
            [
                "The room hums. Somewhere a fan spins up and thinks better of it.",
                "Rain on the window, and a clock that is slightly wrong.",
                "Somebody left a coffee here three hours ago. It's still warm. Odd.",
            ]
        )
