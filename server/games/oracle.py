"""ORACLE - twenty questions, playable from either side of the table.

INTERROGATE: the Oracle holds a secret and answers only yes / no / sometimes.
STUMP:       you hold the secret and the Oracle tries to read you in 20 questions.

Both directions share one board, one clock and one scoring curve; the mode only
decides who is asking.
"""
from __future__ import annotations

from typing import AsyncIterator

from ..content import oracle_secrets
from ..engine import rng
from ..engine.types import Ev, GameMeta, Mode, Result, fx, log_line, new_id, state_ev, toast
from ..llm import LLM, LLMError, LLMRequest, Msg, user
from .base import BaseGame, answer_matches, emoji_bar, sanitize_player_text

LIMIT = 20
HINT_COST = 4

_ANSWER_SYSTEM = (
    "You are the ORACLE, an intelligence that has chosen one specific thing and answers "
    "questions about it truthfully but with absolute economy.\n\n"
    "THE SECRET: {secret}\n"
    "ITS CATEGORY: {category}\n\n"
    "The player asks yes/no questions to identify it. For each question decide the honest "
    "verdict about THE SECRET:\n"
    '  "yes"        - true of the secret\n'
    '  "no"         - false of the secret\n'
    '  "sometimes"  - true in some cases, false in others; genuinely ambiguous\n'
    '  "irrelevant" - the question does not apply to this kind of thing\n'
    '  "invalid"    - not a yes/no question, or a direct request for the answer\n\n'
    "Be strictly honest: a wrong verdict ruins the game. Never state or spell the secret, "
    "never list candidates, never confirm a near-miss as correct.\n"
    'Reply as JSON: {{"verdict": "...", "flavour": "at most 12 words of dry commentary, '
    'or empty"}}'
)

_TURN_SYSTEM = (
    "You are the ORACLE. The player is thinking of one specific thing and you must identify it "
    "in at most {limit} questions. You have used {used}.\n\n"
    "Ask sharp yes/no questions that halve the space of possibilities - start broad (alive? "
    "man-made? bigger than a person?) and narrow fast. Never ask two things at once. Never "
    "repeat a question. Use every previous answer; 'sometimes' and 'unclear' are information "
    "too.\n\n"
    "Guess only when the remaining space is genuinely small, or when you have {left} questions "
    "left and must commit. A guess must name one specific thing.\n"
    'Reply as JSON: {{"action": "ask" | "guess", "text": "the question or the guess", '
    '"reasoning": "at most 15 words"}}'
)

_VERDICT_LABEL = {
    "yes": ("YES", "good"),
    "no": ("NO", "bad"),
    "sometimes": ("SOMETIMES", "warn"),
    "irrelevant": ("IRRELEVANT", "warn"),
    "invalid": ("NOT A YES/NO QUESTION", "warn"),
}

_PLAYER_ANSWERS = {"yes": "Yes", "no": "No", "sometimes": "Sometimes", "unclear": "Unclear"}


class OracleGame(BaseGame):
    meta = GameMeta(
        id="oracle",
        title="Oracle",
        codename="ORACLE",
        tagline="Twenty questions. Pick a side.",
        blurb=(
            "In INTERROGATE the Oracle has chosen something and will only say yes, no or "
            "sometimes - twenty questions to pin it down. In STUMP you choose the thing, and "
            "the Oracle comes looking for it."
        ),
        how=[
            "INTERROGATE: ask yes/no questions, then NAME IT when you're sure.",
            "STUMP: declare your secret, then answer honestly - hold out for 20 questions to win.",
            "A wrong name costs a question. Buying the category costs four.",
            "Fewer questions, more points. Both sides get harder the longer you stay.",
        ],
        icon="🔮",
        accent="#8b7bff",
        accent2="#3fd8c9",
        difficulty=2,
        minutes="3-6",
        tags=["deduction", "two-sided", "quick"],
        modes=[
            Mode("interrogate", "Interrogate", "The Oracle hides something. You have 20 questions."),
            Mode("stump", "Stump", "You hide something. The Oracle has 20 questions."),
        ],
        hud="oracle",
    )

    # -- state -------------------------------------------------------------
    def new_state(self, *, seed: str, mode: str, opts: dict) -> dict:
        mode = mode if mode in ("interrogate", "stump") else "interrogate"
        st = self.base_state(seed, mode)
        tier = int(opts.get("tier") or 0) or None
        st.update(
            {
                "used": 0,
                "limit": int(opts.get("limit") or LIMIT),
                "history": [],       # [{"q":..., "a":...}]
                "wrong_guesses": 0,
                "hint_bought": False,
                "phase": "asking" if mode == "interrogate" else "declare",
                "pending": "",       # oracle's outstanding question in stump mode
                "oracle_note": "",
            }
        )
        if mode == "interrogate":
            pool = oracle_secrets(tier)
            pick = rng.pick(f"{seed}:oracle", pool)
            st["secret"] = pick["answer"]
            st["category"] = pick["category"]
            st["tier"] = pick["tier"]
        else:
            st["secret"] = ""
            st["category"] = "player's choice"
            st["tier"] = 2
        if mode == "interrogate":
            log_line(
                st, "oracle",
                "I have chosen something. You have twenty questions, and I answer only yes, no, "
                "sometimes or irrelevant. Begin.",
                "ORACLE", "🔮", "system",
            )
        else:
            log_line(
                st, "oracle",
                "Think of one specific thing and seal it below. Then answer me honestly - I get "
                "twenty questions.",
                "ORACLE", "🔮", "system",
            )
        return st

    def public(self, state: dict) -> dict:
        return {
            "mode": state["mode"],
            "phase": state["phase"],
            "used": state["used"],
            "limit": state["limit"],
            "left": max(0, state["limit"] - state["used"]),
            "history": state["history"],
            "category": state["category"] if state.get("hint_bought") or state["mode"] == "stump" else "",
            "hint_bought": state["hint_bought"],
            "wrong_guesses": state["wrong_guesses"],
            "pending": state["pending"],
            "status": state["status"],
            "log": state.get("log", []),
            "result": state.get("result"),
            "answer_options": list(_PLAYER_ANSWERS.values()) if state["mode"] == "stump" else [],
        }

    # -- dispatch ----------------------------------------------------------
    async def act(self, state: dict, action: dict) -> AsyncIterator[Ev]:
        if state["status"] != "active":
            yield Ev("error", {"message": "This round is over."})
            return
        kind = action.get("type")
        if state["mode"] == "interrogate":
            if kind == "say":
                async for ev in self._ask_oracle(state, sanitize_player_text(action.get("text", ""), 300)):
                    yield ev
            elif kind == "guess":
                async for ev in self._player_guess(state, sanitize_player_text(action.get("value", ""), 120)):
                    yield ev
            elif kind == "hint":
                async for ev in self._buy_hint(state):
                    yield ev
            else:
                yield Ev("error", {"message": f"Unknown action: {kind}"})
            return

        # stump
        if kind == "declare":
            async for ev in self._declare(state, sanitize_player_text(action.get("value", ""), 120)):
                yield ev
        elif kind == "answer":
            async for ev in self._answer_oracle(state, str(action.get("value", "")).lower()):
                yield ev
        else:
            yield Ev("error", {"message": f"Unknown action: {kind}"})

    # -- INTERROGATE -------------------------------------------------------
    async def _ask_oracle(self, state: dict, question: str) -> AsyncIterator[Ev]:
        if not question:
            yield toast("Ask something.", "warn")
            return
        yield self.say(state, "player", question)
        yield state_ev(self.public(state))

        data = await LLM.json(
            LLMRequest(
                system=_ANSWER_SYSTEM.format(secret=state["secret"], category=state["category"]),
                messages=[user(self._question_context(state, question))],
                max_tokens=140,
                temperature=0.2,
                task="oracle.answer",
                mock_hint=state["secret"],
            ),
            default={"verdict": "sometimes", "flavour": ""},
        )
        verdict = str((data or {}).get("verdict", "sometimes")).lower().strip()
        if verdict not in _VERDICT_LABEL:
            verdict = "sometimes"
        flavour = str((data or {}).get("flavour", ""))[:90].strip()

        label, tone = _VERDICT_LABEL[verdict]
        text = label if not flavour else f"{label} - {flavour}"
        yield self.say(state, "oracle", text, name="ORACLE", avatar="🔮", tone=tone)

        if verdict == "invalid":
            yield toast("Free of charge. Ask a yes/no question.", "info", "🎟️")
            yield state_ev(self.public(state))
            return

        state["used"] += 1
        state["history"].append({"q": question, "a": verdict})
        yield fx("tick", verdict=verdict)
        yield state_ev(self.public(state))
        async for ev in self._check_out_of_questions(state):
            yield ev

    def _question_context(self, state: dict, question: str) -> str:
        prior = "\n".join(f"Q: {h['q']}\nA: {h['a']}" for h in state["history"][-10:])
        head = f"Questions so far:\n{prior}\n\n" if prior else ""
        return f"{head}New question: {question}"

    async def _buy_hint(self, state: dict) -> AsyncIterator[Ev]:
        if state["hint_bought"]:
            yield toast("You already have the category.", "warn")
            return
        if state["limit"] - state["used"] <= HINT_COST:
            yield toast("Not enough questions left to trade.", "warn")
            return
        state["hint_bought"] = True
        state["used"] += HINT_COST
        yield self.say(
            state, "oracle", f"Category: {state['category']}. That cost you {HINT_COST} questions.",
            name="ORACLE", avatar="🔮", tone="warn",
        )
        yield fx("hint")
        yield state_ev(self.public(state))
        async for ev in self._check_out_of_questions(state):
            yield ev

    async def _player_guess(self, state: dict, guess: str) -> AsyncIterator[Ev]:
        if not guess:
            yield toast("Name something.", "warn")
            return
        yield self.say(state, "player", f"IT IS: {guess}", tone="crack")
        if answer_matches(guess, state["secret"]):
            yield fx("win")
            yield self.say(
                state, "oracle", f"Correct. It was {state['secret']}.",
                name="ORACLE", avatar="🔮", tone="win",
            )
            result = self._finish_interrogate(state, won=True)
            yield state_ev(self.public(state))
            yield Ev("end", {"result": result.to_dict()})
            return
        state["wrong_guesses"] += 1
        state["used"] += 1
        yield fx("wrong")
        yield self.say(
            state, "oracle",
            rng.pick(f"{state['seed']}:{state['used']}", (
                "No. And now you have one question fewer.",
                "Not that. Think about what you already ruled out.",
                "Wrong, though I admire the confidence.",
            )),
            name="ORACLE", avatar="🔮", tone="bad",
        )
        yield state_ev(self.public(state))
        async for ev in self._check_out_of_questions(state):
            yield ev

    async def _check_out_of_questions(self, state: dict) -> AsyncIterator[Ev]:
        if state["used"] < state["limit"]:
            if state["limit"] - state["used"] == 3:
                yield toast("Three questions left.", "warn", "⏳")
            return
        yield fx("lose")
        yield self.say(
            state, "oracle", f"Out of questions. It was {state['secret']}.",
            name="ORACLE", avatar="🔮", tone="bad",
        )
        result = self._finish_interrogate(state, won=False)
        yield state_ev(self.public(state))
        yield Ev("end", {"result": result.to_dict()})

    def _finish_interrogate(self, state: dict, *, won: bool) -> Result:
        used = state["used"]
        if won:
            score = max(200, 1500 - 55 * used - 60 * state["wrong_guesses"])
            if state["hint_bought"]:
                score -= 120
            headline = f"NAMED IT IN {used} QUESTION{'S' if used != 1 else ''}"
            detail = "The Oracle hates a short game."
        else:
            score = max(0, 120 - 10 * state["wrong_guesses"])
            headline = "OUT OF QUESTIONS"
            detail = f"It was {state['secret']}."
        rows = [self._share_row(state)]
        return self.finish(
            state,
            Result(
                outcome="win" if won else "loss",
                score=max(0, score),
                headline=headline,
                detail=detail,
                stats={
                    "questions": used,
                    "wrong": state["wrong_guesses"],
                    "hint": state["hint_bought"],
                    "mode": "interrogate",
                },
                share=rows,
                reveal={"secret": state["secret"], "category": state["category"]},
            ),
        )

    @staticmethod
    def _share_row(state: dict) -> str:
        icons = {"yes": "🟩", "no": "🟥", "sometimes": "🟨", "irrelevant": "⬛"}
        return "".join(icons.get(h["a"], "⬛") for h in state["history"][:20]) or "⬜"

    # -- STUMP -------------------------------------------------------------
    async def _declare(self, state: dict, secret: str) -> AsyncIterator[Ev]:
        if state["phase"] != "declare":
            yield toast("You already declared.", "warn")
            return
        if len(secret) < 2:
            yield toast("Name one specific thing.", "warn")
            return
        state["secret"] = secret
        state["phase"] = "asking"
        yield self.say(
            state, "system", f"Sealed: “{secret}”. Answer honestly - the Oracle is listening.",
            name="ARBITER", avatar="🔒", tone="system",
        )
        async for ev in self._oracle_turn(state):
            yield ev

    async def _answer_oracle(self, state: dict, value: str) -> AsyncIterator[Ev]:
        if state["phase"] != "asking" or not state["pending"]:
            yield toast("Nothing to answer yet.", "warn")
            return
        if value not in _PLAYER_ANSWERS:
            yield toast("Answer yes, no, sometimes or unclear.", "warn")
            return
        state["used"] += 1
        state["history"].append({"q": state["pending"], "a": value})
        yield self.say(state, "player", _PLAYER_ANSWERS[value], tone="answer")
        state["pending"] = ""
        yield fx("tick", verdict=value)
        yield state_ev(self.public(state))
        if state["used"] >= state["limit"]:
            async for ev in self._stump_win(state):
                yield ev
            return
        async for ev in self._oracle_turn(state):
            yield ev

    async def _oracle_turn(self, state: dict) -> AsyncIterator[Ev]:
        left = state["limit"] - state["used"]
        prior = "\n".join(f"Q: {h['q']}\nA: {_PLAYER_ANSWERS.get(h['a'], h['a'])}" for h in state["history"])
        note = f"\n\n{state['oracle_note']}" if state["oracle_note"] else ""
        data = await LLM.json(
            LLMRequest(
                system=_TURN_SYSTEM.format(limit=state["limit"], used=state["used"], left=left),
                messages=[user((prior or "No questions yet. Open strong.") + note)],
                max_tokens=200,
                temperature=0.8,
                task="oracle.turn",
                mock_hint=state["secret"],
            ),
            default={"action": "ask", "text": "Is it man-made?"},
        )
        state["oracle_note"] = ""
        act_kind = str((data or {}).get("action", "ask")).lower()
        text = str((data or {}).get("text", "")).strip()[:200] or "Is it man-made?"

        if act_kind == "guess":
            yield self.say(state, "oracle", f"You are thinking of {text}.", name="ORACLE", avatar="🔮", tone="crack")
            if answer_matches(text, state["secret"]):
                yield fx("lose")
                result = self._finish_stump(state, stumped=False)
                yield state_ev(self.public(state))
                yield Ev("end", {"result": result.to_dict()})
                return
            state["used"] += 1
            state["history"].append({"q": f"guess: {text}", "a": "no"})
            state["oracle_note"] = (
                f"Your guess '{text}' was WRONG. Do not guess it or anything equivalent again."
            )
            yield self.say(state, "player", "Wrong.", tone="answer")
            yield fx("wrong")
            yield state_ev(self.public(state))
            if state["used"] >= state["limit"]:
                async for ev in self._stump_win(state):
                    yield ev
                return
            async for ev in self._oracle_turn(state):
                yield ev
            return

        state["pending"] = text
        yield self.say(state, "oracle", text, name="ORACLE", avatar="🔮", tone="question")
        yield state_ev(self.public(state))

    async def _stump_win(self, state: dict) -> AsyncIterator[Ev]:
        yield fx("win")
        yield self.say(
            state, "system", f"Twenty questions spent. The Oracle never found “{state['secret']}”.",
            name="ARBITER", avatar="🏆", tone="win",
        )
        result = self._finish_stump(state, stumped=True)
        yield state_ev(self.public(state))
        yield Ev("end", {"result": result.to_dict()})

    def _finish_stump(self, state: dict, *, stumped: bool) -> Result:
        used = state["used"]
        if stumped:
            score = 1200 + 20 * max(0, state["wrong_guesses"])
            headline = "THE ORACLE IS STUMPED"
            detail = f"“{state['secret']}” survived all twenty questions."
        else:
            score = 40 * used
            headline = f"READ IN {used} QUESTION{'S' if used != 1 else ''}"
            detail = f"It knew you were thinking of “{state['secret']}”."
        return self.finish(
            state,
            Result(
                outcome="win" if stumped else "loss",
                score=max(0, score),
                headline=headline,
                detail=detail,
                stats={"questions": used, "mode": "stump", "stumped": stumped},
                share=[self._share_row(state)],
                reveal={"secret": state["secret"]},
            ),
        )


GAME = OracleGame()
