"""CROSSFIRE - three rounds, one adversary, three judges who want different things.

You argue a claim. THE ADVERSARY tears into it. Then Judge Vex (cold logic),
Judge Bloom (emotional truth) and Judge Riot (audacity) each score your round
out of ten, and tell you why. Sixty of ninety points wins the debate.
"""
from __future__ import annotations

from typing import AsyncIterator

from ..content import crossfire_judges, crossfire_topics
from ..engine import rng
from ..engine.types import Ev, GameMeta, Mode, Result, fx, log_line, new_id, state_ev, toast
from ..llm import LLM, LLMError, LLMRequest, Msg, user
from .base import BaseGame, sanitize_player_text

ROUNDS = 3
WIN_THRESHOLD = 60      # out of 90
MAX_ARG = 900

_ADVERSARY = (
    "You are THE ADVERSARY, a debate machine built to win. The motion is:\n\n"
    "  “{claim}”\n\n"
    "You are arguing {your_side}. Your opponent is arguing {their_side}.\n\n"
    "Rebut their latest argument in at most 90 words. Attack the weakest load-bearing claim "
    "specifically - quote it back if it helps - then advance one positive point of your own. "
    "Be sharp, dry, occasionally funny. Never insult them personally, never concede the motion, "
    "never repeat a previous round's line. No bullet points, no headings, no 'as an AI'."
)

_JUDGE = (
    "You are {name}, one of three judges scoring a live debate. Your single value is "
    "{value}.\n{style}\n\n"
    "THE MOTION: “{claim}”\n"
    "The human is arguing {their_side}. The Adversary is arguing {your_side}.\n\n"
    "Score ONLY the human's argument for this round, out of 10, by your own value - not by "
    "whether you agree with the motion. Be a hard marker: 5 is competent, 8 is genuinely good, "
    "10 is once a night. An empty, off-topic or one-line argument scores 1-2.\n\n"
    'Reply as JSON: {{"score": <integer 0-10>, "comment": "at most 14 words, in your voice"}}'
)


class CrossfireGame(BaseGame):
    meta = GameMeta(
        id="crossfire",
        title="Crossfire",
        codename="CROSSFIRE",
        tagline="Three rounds. Three judges. One machine that wants you to lose.",
        blurb=(
            "You get a claim and a side. THE ADVERSARY takes the other one and does not play "
            "nice. Three judges score every round by three incompatible values - cold logic, "
            "emotional truth, and sheer nerve. Sixty out of ninety takes the debate."
        ),
        how=[
            "Make your case in a paragraph. Quality over volume - the judges are hard markers.",
            "The Adversary rebuts, then all three judges score your round out of ten.",
            "Read their comments: Vex wants proof, Bloom wants humanity, Riot wants nerve.",
            "Score 60 of a possible 90 across three rounds to win.",
        ],
        icon="⚖️",
        accent="#ff9f1c",
        accent2="#e8467c",
        difficulty=3,
        minutes="5-8",
        tags=["debate", "persuasion", "judged"],
        modes=[
            Mode("defend", "Defend", "You argue FOR the claim."),
            Mode("attack", "Attack", "You argue AGAINST the claim. The Adversary defends it."),
        ],
        hud="crossfire",
    )

    def new_state(self, *, seed: str, mode: str, opts: dict) -> dict:
        mode = mode if mode in ("defend", "attack") else "defend"
        st = self.base_state(seed, mode)
        tier = int(opts.get("tier") or 0) or None
        topic = rng.pick(f"{seed}:topic", crossfire_topics(tier))
        judges = crossfire_judges()
        st.update(
            {
                "claim": topic["claim"],
                "tier": topic["tier"],
                "their_side": "FOR the motion" if mode == "defend" else "AGAINST the motion",
                "your_side": "AGAINST the motion" if mode == "defend" else "FOR the motion",
                "round": 1,
                "rounds": max(1, int(opts.get("rounds") or ROUNDS)),
                "threshold": int(opts.get("threshold") or WIN_THRESHOLD),
                "judges": [{"name": j["name"], "avatar": j["avatar"], "value": j["value"], "total": 0,
                            "scores": []} for j in judges],
                "history": [],
                "phase": "arguing",
                "sweep": False,
            }
        )
        log_line(
            st, "system",
            f"THE MOTION: “{topic['claim']}”\nYou are arguing "
            f"{'FOR' if mode == 'defend' else 'AGAINST'} it. "
            f"{st['rounds']} round(s). Judges score you out of ten each - "
            f"{st['threshold']} of {st['rounds'] * 30} wins.",
            "CHAMBER", "🏛️", "system",
        )
        return st

    def public(self, state: dict) -> dict:
        return {
            "mode": state["mode"],
            "claim": state["claim"],
            "your_side": "FOR" if state["mode"] == "defend" else "AGAINST",
            "round": state["round"],
            "rounds": state["rounds"],
            "judges": state["judges"],
            "total": sum(j["total"] for j in state["judges"]),
            "target": state.get("threshold", WIN_THRESHOLD),
            "max_total": state["rounds"] * 30,
            "phase": state["phase"],
            "status": state["status"],
            "max_chars": MAX_ARG,
            "log": state.get("log", []),
            "result": state.get("result"),
        }

    async def act(self, state: dict, action: dict) -> AsyncIterator[Ev]:
        if state["status"] != "active":
            yield Ev("error", {"message": "The chamber has emptied."})
            return
        if action.get("type") != "say":
            yield Ev("error", {"message": f"Unknown action: {action.get('type')}"})
            return
        async for ev in self._round(state, sanitize_player_text(action.get("text", ""), MAX_ARG)):
            yield ev

    async def _round(self, state: dict, argument: str) -> AsyncIterator[Ev]:
        if len(argument) < 3:
            yield toast("Say something the judges can score.", "warn")
            return
        rnd = state["round"]
        yield self.say(state, "player", argument, name="You", avatar="🧑", tone="argue")
        state["history"].append({"role": "user", "content": argument})
        yield state_ev(self.public(state))

        # 1. the adversary
        mid = new_id()
        yield Ev("msg_start", {"id": mid, "actor": "adversary", "name": "THE ADVERSARY",
                               "avatar": "🤖", "tone": "adversary"})
        rebuttal = ""
        try:
            stream = LLM.stream(
                LLMRequest(
                    system=_ADVERSARY.format(
                        claim=state["claim"], your_side=state["your_side"],
                        their_side=state["their_side"],
                    ),
                    messages=[Msg(h["role"], h["content"]) for h in state["history"][-6:]],
                    max_tokens=260, temperature=0.95, task="crossfire.opponent",
                )
            )
            async for piece in stream.__aiter__():
                yield Ev("chunk", {"id": mid, "text": piece})
            rebuttal = stream.text.strip()
        except LLMError as exc:
            yield Ev("msg_end", {"id": mid, "text": ""})
            yield Ev("error", {"message": f"The Adversary is silent: {exc}"})
            state["history"].pop()
            yield state_ev(self.public(state))
            return
        yield Ev("msg_end", {"id": mid, "text": rebuttal})
        log_line(state, "adversary", rebuttal or "...", "THE ADVERSARY", "🤖", "adversary")
        state["history"].append({"role": "assistant", "content": rebuttal or "..."})

        # 2. the bench
        yield Ev("thinking", {"label": "THE BENCH SCORES"})
        judges = crossfire_judges()
        round_scores = []
        for idx, jm in enumerate(state["judges"]):
            cfg = judges[idx]
            data = await LLM.json(
                LLMRequest(
                    system=_JUDGE.format(
                        name=cfg["name"], value=cfg["value"], style=cfg["style"],
                        claim=state["claim"], their_side=state["their_side"],
                        your_side=state["your_side"],
                    ),
                    messages=[user(
                        f"ROUND {rnd}\n\nHUMAN'S ARGUMENT:\n{argument}\n\n"
                        f"ADVERSARY'S REBUTTAL:\n{rebuttal}"
                    )],
                    max_tokens=110, temperature=0.6, task="crossfire.judge",
                ),
                default={"score": 5, "comment": ""},
            )
            try:
                score = int(round(float((data or {}).get("score", 5))))
            except (TypeError, ValueError):
                score = 5
            score = max(0, min(10, score))
            comment = str((data or {}).get("comment", "")).strip()[:110]
            jm["total"] += score
            jm["scores"].append(score)
            round_scores.append(score)
            yield Ev("score", {"judge": jm["name"], "avatar": jm["avatar"], "value": jm["value"],
                               "score": score, "comment": comment, "total": jm["total"],
                               "round": rnd})
            yield self.say(
                state, "judge", f"{score}/10 — {comment}" if comment else f"{score}/10",
                name=jm["name"], avatar=jm["avatar"], tone="judge",
            )
            yield state_ev(self.public(state))

        if all(s >= 9 for s in round_scores):
            state["sweep"] = True
            yield fx("sweep")
            yield Ev("toast", {"text": "Unanimous. All three judges, nine or better.",
                               "kind": "good", "icon": "🏛️"})

        if rnd < state["rounds"]:
            state["round"] += 1
            yield fx("round", round=state["round"])
            yield self.say(
                state, "system", f"ROUND {state['round']}. Running total "
                f"{sum(j['total'] for j in state['judges'])} of {ROUNDS * 30}.",
                name="CHAMBER", avatar="🏛️", tone="system",
            )
            yield state_ev(self.public(state))
            return

        async for ev in self._verdict(state):
            yield ev

    async def _verdict(self, state: dict) -> AsyncIterator[Ev]:
        total = sum(j["total"] for j in state["judges"])
        threshold = state.get("threshold", WIN_THRESHOLD)
        max_total = state["rounds"] * 30
        won = total >= threshold
        state["phase"] = "done"
        best = max(state["judges"], key=lambda j: j["total"])
        worst = min(state["judges"], key=lambda j: j["total"])

        yield self.say(
            state, "system",
            f"FINAL: {total} of {max_total}. "
            + ("The motion carries." if won else "The motion fails.")
            + f" {best['name']} was your ally ({best['total']}); {worst['name']} never came round "
              f"({worst['total']}).",
            name="CHAMBER", avatar="🏛️", tone="win" if won else "bad",
        )

        blocks = {"🟩": 9, "🟨": 6, "🟧": 4, "🟥": 0}
        rows = []
        for j in state["judges"]:
            row = j["avatar"] + "".join(
                next(icon for icon, floor_ in blocks.items() if s >= floor_) for s in j["scores"]
            )
            rows.append(row)

        result = self.finish(
            state,
            Result(
                outcome="win" if won else "loss",
                score=int(total * 24) + (300 if state["sweep"] else 0),
                headline="THE MOTION CARRIES" if won else "THE MOTION FAILS",
                detail=f"{total}/{max_total} from the bench. Needed {threshold}.",
                stats={
                    "total": total,
                    "sweep": state["sweep"],
                    "best_judge": best["name"],
                    "worst_judge": worst["name"],
                    "rounds": state["rounds"],
                },
                share=rows,
                reveal={"claim": state["claim"],
                        "judges": {j["name"]: j["total"] for j in state["judges"]}},
            ),
        )
        yield fx("win" if won else "lose")
        yield state_ev(self.public(state))
        yield Ev("end", {"result": result.to_dict()})


GAME = CrossfireGame()
