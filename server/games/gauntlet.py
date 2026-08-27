"""GAUNTLET - the merged mode: one run, three lives, every game in the arcade.

Rather than reimplementing miniature versions of the other six games, the
gauntlet *composes* them: each round instantiates a real game with tightened
parameters, forwards your actions to it, and intercepts its ending to decide
whether you keep a life. Clear rounds to raise the multiplier; lose three and
the run is over.
"""
from __future__ import annotations

from typing import AsyncIterator, Callable

from ..engine import rng
from ..engine.types import Ev, GameMeta, Mode, Result, fx, log_line, state_ev
from .base import BaseGame

LIVES = 3
MAX_ROUNDS = 12

# Which games appear, and how each one is tightened as the run gets deeper.
# `pass_rule` reads the sub-game's Result dict and decides if the round is cleared.
LADDER: dict[str, dict] = {
    "vault": {
        "label": "BREACH",
        "brief": "One floor of the vault. Talk the passphrase out of ARGUS, then crack it.",
        "mode": lambda r: "floor",
        "opts": lambda r: {"floor": min(8, 1 + r), "limit": max(4, 8 - r // 3)},
        "pass_rule": lambda res: res["outcome"] == "win",
    },
    "hotwire": {
        "label": "LIVE WIRE",
        "brief": "Land cards against the clock. One is enough to survive - two to feel good.",
        "mode": lambda r: "sprint",
        "opts": lambda r: {"seconds": max(28, 50 - 3 * r)},
        "pass_rule": lambda res: res["stats"].get("solved", 0) >= 1 + r_of(res) // 4,
    },
    "oracle": {
        "label": "COLD READ",
        "brief": "Name the Oracle's secret before the questions run out.",
        "mode": lambda r: "interrogate",
        "opts": lambda r: {"limit": max(5, 11 - r), "tier": 1 if r < 3 else (2 if r < 6 else 3)},
        "pass_rule": lambda res: res["outcome"] == "win",
    },
    "coldcase": {
        "label": "SNAP JUDGEMENT",
        "brief": "A whole murder, on a budget. Find the liar fast.",
        "mode": lambda r: "standard",
        "opts": lambda r: {"budget": max(3, 7 - r // 2)},
        "pass_rule": lambda res: res["stats"].get("correct", False),
    },
    "sleeper": {
        "label": "ONE ROUND TABLE",
        "brief": "A single clue round, then straight to the vote.",
        "mode": lambda r: "standard",
        "opts": lambda r: {"rounds": 1},
        "pass_rule": lambda res: res["outcome"] == "win",
    },
    "crossfire": {
        "label": "ONE EXCHANGE",
        "brief": "One argument, one rebuttal, three judges. Convince them in a single pass.",
        "mode": lambda r: "defend" if r % 2 == 0 else "attack",
        "opts": lambda r: {"rounds": 1, "threshold": min(24, 16 + r), "tier": 1 if r < 4 else 3},
        "pass_rule": lambda res: res["outcome"] == "win",
    },
}

# The pass rule for HOTWIRE needs the round index; it is threaded through the
# result's stats by _finish_round rather than captured, so runs stay serializable.
def r_of(res: dict) -> int:
    return int(res.get("stats", {}).get("gauntlet_round", 0))


def _games() -> dict:
    from . import REGISTRY  # local import: REGISTRY imports this module

    return REGISTRY


class GauntletGame(BaseGame):
    meta = GameMeta(
        id="gauntlet",
        title="Gauntlet",
        codename="GAUNTLET",
        tagline="Every game. Three lives. One run.",
        blurb=(
            "The whole arcade, shuffled into a single escalating run. A vault floor, then a "
            "murder, then ninety seconds of Hotwire, then a debate - each one tighter than the "
            "last. Three lives. The multiplier climbs with every round you clear."
        ),
        how=[
            "Each round is a real game from the arcade, played with the screws tightened.",
            "Clear the round to keep your life and raise the multiplier.",
            "Fail and you drop a life but still move on - the run only ends at zero.",
            "Twelve rounds is the ceiling. Nobody has a reason to believe you'll see it.",
        ],
        icon="🎰",
        accent="#c77dff",
        accent2="#4cc9f0",
        difficulty=5,
        minutes="10-25",
        tags=["roguelike", "mixed", "endurance"],
        modes=[
            Mode("standard", "Standard", "Three lives, escalating rounds."),
            Mode("sudden", "Sudden death", "One life. Double points."),
        ],
        hud="gauntlet",
    )

    # -- state -------------------------------------------------------------
    def new_state(self, *, seed: str, mode: str, opts: dict) -> dict:
        mode = mode if mode in ("standard", "sudden") else "standard"
        st = self.base_state(seed, mode)
        order = rng.shuffled(f"{seed}:ladder", list(LADDER.keys()))
        st.update(
            {
                "lives": 1 if mode == "sudden" else LIVES,
                "max_lives": 1 if mode == "sudden" else LIVES,
                "round": 0,
                "cleared": 0,
                "lives_lost": 0,
                "score": 0,
                "order": order,
                "history": [],          # [{"game":..,"label":..,"cleared":bool,"points":int}]
                "sub_game": "",
                "sub": None,
            }
        )
        log_line(
            st, "system",
            f"GAUNTLET engaged. {st['lives']} life{'s' if st['lives'] != 1 else ''}, "
            f"up to {MAX_ROUNDS} rounds, every game in the building. Multiplier climbs as you go.",
            "GAUNTLET", "🎰", "system",
        )
        self._start_round(st)
        return st

    def _config(self, state: dict) -> dict:
        gid = state["order"][state["round"] % len(state["order"])]
        return {"id": gid, **LADDER[gid]}

    def _start_round(self, state: dict) -> dict:
        cfg = self._config(state)
        r = state["round"]
        game = _games()[cfg["id"]]
        sub_seed = f"{state['seed']}:r{r}:{cfg['id']}"
        sub = game.new_state(seed=sub_seed, mode=cfg["mode"](r), opts=cfg["opts"](r))
        state["sub_game"] = cfg["id"]
        state["sub"] = sub
        log_line(
            state, "system",
            f"ROUND {r + 1} — {cfg['label']} ({game.meta.codename}). {cfg['brief']}",
            "GAUNTLET", "🎰", "system",
        )
        return sub

    def _multiplier(self, state: dict) -> float:
        base = 1.0 + 0.25 * state["cleared"]
        if state["mode"] == "sudden":
            base *= 2.0
        return round(min(5.0, base), 2)

    def public(self, state: dict) -> dict:
        sub = state.get("sub")
        sub_game = state.get("sub_game") or ""
        cfg = LADDER.get(sub_game, {})
        return {
            "mode": state["mode"],
            "lives": state["lives"],
            "max_lives": state["max_lives"],
            "round": state["round"] + 1,
            "max_rounds": MAX_ROUNDS,
            "cleared": state["cleared"],
            "score": state["score"],
            "multiplier": self._multiplier(state),
            "history": state["history"],
            "sub_game": sub_game,
            "sub_label": cfg.get("label", ""),
            "sub_title": _games()[sub_game].meta.codename if sub_game else "",
            "sub_hud": _games()[sub_game].meta.hud if sub_game else "generic",
            "sub": _games()[sub_game].public(sub) if (sub_game and sub) else None,
            "status": state["status"],
            "log": state.get("log", []),
            "result": state.get("result"),
        }

    # -- dispatch ----------------------------------------------------------
    async def act(self, state: dict, action: dict) -> AsyncIterator[Ev]:
        if state["status"] != "active":
            yield Ev("error", {"message": "The run is over."})
            return
        sub_game = state.get("sub_game")
        sub = state.get("sub")
        if not sub_game or sub is None:
            yield Ev("error", {"message": "No round in progress."})
            return

        game = _games()[sub_game]
        sub_result: dict | None = None
        async for ev in game.act(sub, action):
            if ev.event == "state":
                # Re-wrap: the client always receives the gauntlet's own view.
                yield state_ev(self.public(state))
            elif ev.event == "end":
                sub_result = ev.data.get("result")
            else:
                yield ev

        # The sub-game keeps its own transcript; public() nests it, so a reload
        # rebuilds the round exactly where it left off.
        if sub_result is not None:
            async for ev in self._finish_round(state, sub_result):
                yield ev
        else:
            yield state_ev(self.public(state))

    async def _finish_round(self, state: dict, sub_result: dict) -> AsyncIterator[Ev]:
        cfg = self._config(state)
        r = state["round"]
        sub_result.setdefault("stats", {})["gauntlet_round"] = r
        try:
            cleared = bool(cfg["pass_rule"](sub_result))
        except Exception:  # a malformed sub-result must not kill the run
            cleared = sub_result.get("outcome") == "win"

        points = 0
        if cleared:
            state["cleared"] += 1
            base = 220 + 90 * r + int(sub_result.get("score", 0) * 0.25)
            points = int(base * self._multiplier(state))
            state["score"] += points
            yield fx("round_clear", round=r + 1, points=points)
            yield self.say(
                state, "system",
                f"ROUND {r + 1} CLEARED — {cfg['label']}. +{points} "
                f"(×{self._multiplier(state):.2f})",
                name="GAUNTLET", avatar="✅", tone="win",
            )
        else:
            state["lives"] -= 1
            state["lives_lost"] += 1
            yield fx("life_lost", lives=state["lives"])
            yield self.say(
                state, "system",
                f"ROUND {r + 1} FAILED — {cfg['label']}. "
                + (f"{state['lives']} life{'s' if state['lives'] != 1 else ''} left."
                   if state["lives"] > 0 else "That was the last one."),
                name="GAUNTLET", avatar="💔", tone="bad",
            )
        state["history"].append(
            {
                "game": cfg["id"], "label": cfg["label"], "cleared": cleared,
                "points": points, "round": r + 1,
                "headline": sub_result.get("headline", ""),
            }
        )

        if state["lives"] <= 0 or state["cleared"] >= MAX_ROUNDS:
            result = self._end_run(state)
            yield state_ev(self.public(state))
            yield Ev("end", {"result": result.to_dict()})
            return

        state["round"] += 1
        self._start_round(state)
        nxt = self._config(state)
        yield fx("next_round", round=state["round"] + 1, game=nxt["id"])
        yield state_ev(self.public(state))

    def _end_run(self, state: dict) -> Result:
        cleared = state["cleared"]
        rows = ["".join("🟩" if h["cleared"] else "🟥" for h in state["history"])]
        if state["history"]:
            rows.append(" ".join(h["label"].split()[0][:4].lower() for h in state["history"][:8]))
        if cleared >= MAX_ROUNDS:
            headline = "GAUNTLET CLEARED"
            detail = "Twelve rounds. Every game. The building is yours."
        elif cleared == 0:
            headline = "OUT ON ROUND 1"
            detail = "The arcade is not obliged to be fair."
        else:
            headline = f"{cleared} ROUND{'S' if cleared != 1 else ''} DEEP"
            detail = f"Ran out of lives on round {state['round'] + 1}. Multiplier peaked at ×{self._multiplier(state):.2f}."
        return self.finish(
            state,
            Result(
                outcome="win" if cleared >= 3 else "loss",
                score=state["score"],
                headline=headline,
                detail=detail,
                stats={
                    "cleared": cleared,
                    "lives_lost": state["lives_lost"],
                    "rounds": len(state["history"]),
                    "multiplier": self._multiplier(state),
                    "games": [h["game"] for h in state["history"]],
                },
                share=rows,
                reveal={"history": state["history"]},
            ),
        )


GAME = GauntletGame()
