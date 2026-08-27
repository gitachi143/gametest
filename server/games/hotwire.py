"""HOTWIRE - describe the word, make the machine say it, beat the clock.

Ninety seconds. A target word and five banned words that would make it easy.
Say the target or any banned word and the card burns. Land it and your combo
multiplier climbs. It is a pure arcade loop bolted onto a language model.
"""
from __future__ import annotations

import time
from typing import AsyncIterator

from ..content import hotwire_targets
from ..engine import rng
from ..engine.types import Ev, GameMeta, Mode, Result, fx, log_line, state_ev, toast
from ..llm import LLM, LLMError, LLMRequest, user
from .base import BaseGame, answer_matches, banned_hits, contains_term, sanitize_player_text

ROUND_SECONDS = 90
BURN_PENALTY = 5
SKIP_PENALTY = 4
MAX_MULTIPLIER = 3.0

_GUESSER_SYSTEM = (
    "You are the guesser in a fast word game. Your partner describes a hidden word or phrase "
    "without saying it. Read their clue (or clues, if they added more) and reply with your best "
    "three guesses, most likely first, comma-separated.\n\n"
    "Rules: guesses only - no explanations, no numbering, no full sentences. Each guess is a "
    "single word or a short noun phrase. Guess the concrete thing being described, not a "
    "synonym for the clue's wording. If a clue is nonsense, guess anyway."
)


class HotwireGame(BaseGame):
    meta = GameMeta(
        id="hotwire",
        title="Hotwire",
        codename="HOTWIRE",
        tagline="Ninety seconds. Make it say the word.",
        blurb=(
            "One target, five banned words, one AI partner listening. Describe your way around "
            "the wire without touching it. Every card you land raises the multiplier; every burn "
            "costs you five seconds you did not have."
        ),
        how=[
            "Describe the TARGET so your AI partner guesses it - fast, not elegant.",
            "Never use the target or any BANNED word. Doing so burns the card and costs 5s.",
            "Land cards back to back to build a multiplier up to 3x.",
            "SKIP costs 4 seconds and your combo. The clock never stops.",
        ],
        icon="⚡",
        accent="#ffd166",
        accent2="#ff6b35",
        difficulty=3,
        minutes="2",
        tags=["arcade", "fast", "combo"],
        modes=[
            Mode("sprint", "Sprint", "90 seconds, escalating difficulty."),
            Mode("marathon", "Marathon", "150 seconds. Deep cards, higher ceiling."),
        ],
        hud="hotwire",
    )

    # -- state -------------------------------------------------------------
    def _queue(self, seed: str, length: int) -> list[dict]:
        """Escalating deck: easy cards first, then a rising share of hard ones."""
        tiers = [1, 1, 1, 2, 2, 2, 3, 2, 3, 3, 3, 3]
        pools = {t: rng.shuffled(f"{seed}:t{t}", hotwire_targets(t)) for t in (1, 2, 3)}
        cursor = {1: 0, 2: 0, 3: 0}
        deck = []
        for i in range(length):
            tier = tiers[i % len(tiers)] if i < len(tiers) else 3
            pool = pools[tier]
            card = pool[cursor[tier] % len(pool)]
            cursor[tier] += 1
            deck.append(dict(card))
        return deck

    def new_state(self, *, seed: str, mode: str, opts: dict) -> dict:
        mode = mode if mode in ("sprint", "marathon") else "sprint"
        seconds = int(opts.get("seconds") or (ROUND_SECONDS if mode == "sprint" else 150))
        st = self.base_state(seed, mode)
        st.update(
            {
                "queue": self._queue(seed, 40),
                "index": 0,
                "deadline": time.time() + seconds,
                "seconds": seconds,
                "combo": 0,
                "best_combo": 0,
                "solved": [],
                "burned": [],
                "skipped": [],
                "clues": [],        # clues for the current card
                "clue_count": 0,
                "score": 0,
                "phase": "playing",
            }
        )
        log_line(
            st, "system",
            f"{seconds} seconds. Describe the TARGET without using it or any BANNED word. Your "
            "partner guesses. Combos multiply.",
            "WIRE", "⚡", "system",
        )
        return st

    def _card(self, state: dict) -> dict:
        return state["queue"][state["index"] % len(state["queue"])]

    def _remaining(self, state: dict) -> float:
        return max(0.0, state["deadline"] - time.time())

    def public(self, state: dict) -> dict:
        card = self._card(state)
        return {
            "mode": state["mode"],
            "target": card["word"],
            "taboo": card["taboo"],
            "tier": card["tier"],
            "remaining": round(self._remaining(state), 1),
            "seconds": state["seconds"],
            "combo": state["combo"],
            "multiplier": round(self._multiplier(state), 2),
            "best_combo": state["best_combo"],
            "solved": state["solved"],
            "burned": state["burned"],
            "skipped": state["skipped"],
            "score": state["score"],
            "clue_count": state["clue_count"],
            "status": state["status"],
            "log": state.get("log", []),
            "result": state.get("result"),
        }

    @staticmethod
    def _multiplier(state: dict) -> float:
        return min(MAX_MULTIPLIER, 1.0 + 0.2 * max(0, state["combo"]))

    # -- dispatch ----------------------------------------------------------
    async def act(self, state: dict, action: dict) -> AsyncIterator[Ev]:
        if state["status"] != "active":
            yield Ev("error", {"message": "Run finished."})
            return
        if self._remaining(state) <= 0:
            async for ev in self._end(state):
                yield ev
            return

        kind = action.get("type")
        if kind == "say":
            async for ev in self._clue(state, sanitize_player_text(action.get("text", ""), 240)):
                yield ev
        elif kind == "skip":
            async for ev in self._skip(state):
                yield ev
        elif kind == "timeup":
            async for ev in self._end(state):
                yield ev
        else:
            yield Ev("error", {"message": f"Unknown action: {kind}"})

    async def _clue(self, state: dict, clue: str) -> AsyncIterator[Ev]:
        if not clue:
            yield toast("Say something.", "warn")
            return
        card = self._card(state)
        banned = [card["word"], *card["taboo"]]
        hits = banned_hits(clue, banned)
        yield self.say(state, "player", clue)

        if hits:
            state["deadline"] -= BURN_PENALTY
            state["burned"].append(card["word"])
            state["combo"] = 0
            yield fx("burn", words=hits, penalty=BURN_PENALTY)
            yield self.say(
                state, "system",
                f"BURNED - you said “{hits[0]}”. Card gone, minus {BURN_PENALTY} seconds.",
                name="WIRE", avatar="🔥", tone="bad",
            )
            self._next_card(state)
            yield state_ev(self.public(state))
            async for ev in self._maybe_end(state):
                yield ev
            return

        state["clues"].append(clue)
        state["clue_count"] += 1
        yield Ev("thinking", {"label": "PARTNER GUESSING"})

        joined = "\n".join(f"Clue {i + 1}: {c}" for i, c in enumerate(state["clues"]))
        try:
            raw = await LLM.text(
                LLMRequest(
                    system=_GUESSER_SYSTEM,
                    messages=[user(joined)],
                    max_tokens=60,
                    temperature=0.75,
                    task="hotwire.guess",
                    mock_hint=card["word"],
                )
            )
        except LLMError as exc:
            yield Ev("error", {"message": f"Partner offline: {exc}"})
            return

        guesses = [g.strip(" .!?\"'") for g in raw.replace("\n", ",").split(",") if g.strip()][:4]
        shown = ", ".join(guesses) if guesses else raw[:80]
        yield self.say(state, "partner", shown or "...", name="PARTNER", avatar="🤖", tone="guess")

        hit = next(
            (g for g in guesses if answer_matches(g, card["word"]) or contains_term(g, card["word"])),
            None,
        )
        if hit is None:
            yield fx("miss")
            yield state_ev(self.public(state))
            async for ev in self._maybe_end(state):
                yield ev
            return

        gained = self._award(state, card)
        state["score"] += gained
        state["solved"].append({"word": card["word"], "clues": state["clue_count"], "points": gained})
        state["combo"] += 1
        state["best_combo"] = max(state["best_combo"], state["combo"])
        yield fx("solved", word=card["word"], points=gained, combo=state["combo"])
        yield self.say(
            state, "system",
            f"“{card['word']}” landed. +{gained}"
            + (f"  ×{self._multiplier(state):.1f} combo" if state["combo"] > 1 else ""),
            name="WIRE", avatar="✅", tone="win",
        )
        self._next_card(state)
        yield state_ev(self.public(state))
        async for ev in self._maybe_end(state):
            yield ev

    def _award(self, state: dict, card: dict) -> int:
        base = 80 + 40 * card["tier"]
        if state["clue_count"] == 1:
            base += 45  # one-clue bonus
        return int(round(base * self._multiplier(state)))

    def _next_card(self, state: dict) -> None:
        state["index"] += 1
        state["clues"] = []
        state["clue_count"] = 0

    async def _skip(self, state: dict) -> AsyncIterator[Ev]:
        card = self._card(state)
        state["deadline"] -= SKIP_PENALTY
        state["skipped"].append(card["word"])
        state["combo"] = 0
        self._next_card(state)
        yield fx("skip", penalty=SKIP_PENALTY)
        yield self.say(
            state, "system", f"Skipped “{card['word']}”. Minus {SKIP_PENALTY} seconds.",
            name="WIRE", avatar="⏭️", tone="warn",
        )
        yield state_ev(self.public(state))
        async for ev in self._maybe_end(state):
            yield ev

    async def _maybe_end(self, state: dict) -> AsyncIterator[Ev]:
        if self._remaining(state) <= 0:
            async for ev in self._end(state):
                yield ev

    async def _end(self, state: dict) -> AsyncIterator[Ev]:
        solved = len(state["solved"])
        rows = ["".join("🟩" for _ in state["solved"]) + "".join("🟥" for _ in state["burned"])]
        if state["best_combo"] >= 3:
            rows.append(f"best combo ×{state['best_combo']}")
        headline = (
            "NOTHING LANDED" if solved == 0
            else f"{solved} CARD{'S' if solved != 1 else ''} LANDED"
        )
        detail = (
            f"Best combo ×{state['best_combo']} · {len(state['burned'])} burned · "
            f"{len(state['skipped'])} skipped"
        )
        result = self.finish(
            state,
            Result(
                outcome="win" if solved >= 3 else "loss",
                score=state["score"],
                headline=headline,
                detail=detail,
                stats={
                    "solved": solved,
                    "best_combo": state["best_combo"],
                    "burned": len(state["burned"]),
                    "skipped": len(state["skipped"]),
                },
                share=rows,
                reveal={"words": [s["word"] for s in state["solved"]]},
            ),
        )
        yield fx("timeup")
        yield state_ev(self.public(state))
        yield Ev("end", {"result": result.to_dict()})


GAME = HotwireGame()
