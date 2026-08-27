"""SLEEPER - five players, one secret word, one of you does not have it.

Four AI players with fixed personalities, plus you. Everybody except the sleeper
sees the word; the sleeper sees only the category and has to bluff. Two clue
rounds, one discussion round, then a vote. Sometimes the sleeper is you.
"""
from __future__ import annotations

from typing import AsyncIterator

from ..content import sleeper_personas, sleeper_rounds
from ..engine import rng
from ..engine.types import Ev, GameMeta, Mode, Result, fx, log_line, new_id, state_ev, toast
from ..llm import LLM, LLMError, LLMRequest, user
from .base import BaseGame, contains_term, sanitize_player_text

YOU = "you"
CLUE_ROUNDS = 2

_CLUE_INFORMED = (
    "You are {name}, a player in a word game. {style}\n\n"
    "THE SECRET WORD: {word}   (category: {category})\n\n"
    "Everyone here knows this word except one player - the sleeper - who knows only the "
    "category and is bluffing. Give ONE clue about the secret word: one to three words, no "
    "sentence. It must prove to the others that you know the word, without being so obvious "
    "that the sleeper can work it out from your clue alone.\n\n"
    "Never write the secret word itself or any part of it. Never explain. Output the clue only, "
    "with no quotes and no punctuation at the end.\n\n"
    "Clues already given this game:\n{clues}"
)

_CLUE_SLEEPER = (
    "You are {name}, a player in a word game. {style}\n\n"
    "YOU ARE THE SLEEPER. You do NOT know the secret word. You know only the category: "
    "{category}.\n\n"
    "Everyone else knows the word and will give clues about it. Your job is to blend in. Read "
    "the clues already given, infer what the word probably is, and give ONE clue - one to three "
    "words - that sounds like the clue of someone who knows. Vague enough to survive being "
    "wrong, specific enough not to look empty. If nobody has spoken yet, stay broad.\n\n"
    "Output the clue only, no quotes, no explanation.\n\n"
    "Clues already given this game:\n{clues}"
)

_DISCUSS = (
    "You are {name}, a player in a word game about to vote out whoever they think is the "
    "sleeper - the one player who does not know the secret word. {style}\n\n"
    "{knowledge}\n\n"
    "Here is everything said so far:\n{transcript}\n\n"
    "Say ONE line, at most 25 words, in your own voice: accuse someone by name, defend yourself, "
    "or point at a clue that felt wrong. Speech only. Never reveal the secret word. Never say "
    "you are an AI."
)

_VOTE = (
    "You are {name}, voting on who the sleeper is - the one player who never knew the secret "
    "word. {style}\n\n{knowledge}\n\nPlayers you may vote for:\n{ballot}\n\n"
    "Everything said this game:\n{transcript}\n\n"
    "Weigh the clues: the sleeper's clues tend to be generic, safely reusable, or slightly off "
    "the mark. Do not vote for yourself.\n"
    'Reply as JSON: {{"vote": "<exact name>", "reason": "at most 18 words"}}'
)


class SleeperGame(BaseGame):
    meta = GameMeta(
        id="sleeper",
        title="Sleeper",
        codename="SLEEPER",
        tagline="Four AI players. One secret word. One faker.",
        blurb=(
            "Everyone gets the word except one player, who gets only the category and has to "
            "fake it. Two clue rounds, one round of accusations, then the vote. Roughly two "
            "games in five, the faker is you."
        ),
        how=[
            "Give short clues that prove you know the word - without handing it to the sleeper.",
            "If you ARE the sleeper you only see the category. Bluff, and read the room.",
            "One line of discussion each, then everyone votes at once.",
            "Crew wins by ejecting the sleeper. The sleeper wins by surviving.",
        ],
        icon="🐺",
        accent="#7ee787",
        accent2="#ff6b9d",
        difficulty=3,
        minutes="4-7",
        tags=["social deduction", "bluffing", "multi-agent"],
        modes=[
            Mode("standard", "Standard", "Five players, two clue rounds."),
            Mode("paranoid", "Paranoid", "The sleeper is always you. Survive."),
        ],
        hud="sleeper",
    )

    # -- state -------------------------------------------------------------
    def new_state(self, *, seed: str, mode: str, opts: dict) -> dict:
        mode = mode if mode in ("standard", "paranoid") else "standard"
        st = self.base_state(seed, mode)
        r = rng.rng(f"{seed}:sleeper")
        card = r.choice(sleeper_rounds())
        personas = r.sample(sleeper_personas(), 4)
        players = [{"id": YOU, "name": "You", "avatar": "🧑", "style": "", "is_ai": False}]
        for i, p in enumerate(personas):
            players.append(
                {"id": f"p{i + 1}", "name": p["name"], "avatar": p["avatar"],
                 "style": p["style"], "is_ai": True}
            )
        r.shuffle(players)  # speaking order
        sleeper = YOU if mode == "paranoid" else r.choice([p["id"] for p in players] * 1)
        # In standard mode the player is the sleeper ~40% of the time; without
        # this weighting a 5-player table would only hand it to them 20%.
        if mode == "standard" and r.random() < 0.40:
            sleeper = YOU
        st.update(
            {
                "word": card["word"],
                "category": card["category"],
                "players": players,
                "sleeper": sleeper,
                "round": 1,
                "clue_rounds": max(1, int(opts.get("rounds") or CLUE_ROUNDS)),
                "phase": "clue",            # clue -> discuss -> vote -> done
                "clues": [],                # [{"round":n,"id":..,"name":..,"clue":..}]
                "discussion": [],           # [{"id":..,"name":..,"text":..}]
                "votes": {},                # voter_id -> target_id
                "vote_reasons": {},
            }
        )
        log_line(
            st, "system",
            f"Five players. Category: {card['category']}. "
            + ("You are the SLEEPER - you do not get the word." if sleeper == YOU
               else f"The word is “{card['word']}”. One player here does not have it."),
            "TABLE", "🕯️", "system",
        )
        return st

    def _player(self, state: dict, pid: str) -> dict | None:
        return next((p for p in state["players"] if p["id"] == pid), None)

    def _is_sleeper(self, state: dict, pid: str) -> bool:
        return state["sleeper"] == pid

    def public(self, state: dict) -> dict:
        you_sleeper = self._is_sleeper(state, YOU)
        return {
            "mode": state["mode"],
            "category": state["category"],
            "word": "" if you_sleeper else state["word"],
            "you_are_sleeper": you_sleeper,
            "players": [
                {"id": p["id"], "name": p["name"], "avatar": p["avatar"], "is_ai": p["is_ai"]}
                for p in state["players"]
            ],
            "round": state["round"],
            "rounds": state.get("clue_rounds", CLUE_ROUNDS),
            "phase": state["phase"],
            "clues": state["clues"],
            "discussion": state["discussion"],
            "votes": state["votes"] if state["phase"] in ("done",) else {},
            "status": state["status"],
            "log": state.get("log", []),
            "result": state.get("result"),
        }

    # -- dispatch ----------------------------------------------------------
    async def act(self, state: dict, action: dict) -> AsyncIterator[Ev]:
        if state["status"] != "active":
            yield Ev("error", {"message": "The table has broken up."})
            return
        kind = action.get("type")
        if kind == "clue" and state["phase"] == "clue":
            async for ev in self._clue_round(state, sanitize_player_text(action.get("value", ""), 60)):
                yield ev
        elif kind == "say" and state["phase"] == "discuss":
            async for ev in self._discussion(state, sanitize_player_text(action.get("text", ""), 240)):
                yield ev
        elif kind == "vote" and state["phase"] == "vote":
            async for ev in self._vote(state, str(action.get("value") or "")):
                yield ev
        else:
            yield Ev("error", {"message": f"Not now: {kind} during {state['phase']}"})

    # -- clue rounds -------------------------------------------------------
    def _clue_text(self, state: dict) -> str:
        if not state["clues"]:
            return "(nothing yet)"
        return "\n".join(
            f"Round {c['round']} - {c['name']}: {c['clue']}" for c in state["clues"]
        )

    async def _clue_round(self, state: dict, clue: str) -> AsyncIterator[Ev]:
        if not clue:
            yield toast("Give a clue - one to three words.", "warn")
            return
        if len(clue.split()) > 4:
            yield toast("Three words maximum. This is a clue, not a speech.", "warn", "✂️")
            return
        if not self._is_sleeper(state, YOU) and contains_term(clue, state["word"]):
            yield toast("That is the word itself. Try again.", "warn", "🚫")
            return

        order = [p for p in state["players"]]
        # Everyone speaks once per round, in table order, you included.
        for p in order:
            if p["id"] == YOU:
                state["clues"].append({"round": state["round"], "id": YOU, "name": "You", "clue": clue})
                yield self.say(state, YOU, clue, name="You", avatar="🧑", tone="clue")
                yield state_ev(self.public(state))
                continue
            text = await self._ai_clue(state, p)
            state["clues"].append({"round": state["round"], "id": p["id"], "name": p["name"], "clue": text})
            yield self.say(state, p["id"], text, name=p["name"], avatar=p["avatar"], tone="clue")
            yield state_ev(self.public(state))

        if state["round"] < state.get("clue_rounds", CLUE_ROUNDS):
            state["round"] += 1
            yield fx("round", round=state["round"])
            yield self.say(
                state, "system", f"Round {state['round']}. Say something better.",
                name="TABLE", avatar="🕯️", tone="system",
            )
            yield state_ev(self.public(state))
            return

        state["phase"] = "discuss"
        yield fx("phase", phase="discuss")
        yield self.say(
            state, "system", "Clues are in. One line each - then the vote.",
            name="TABLE", avatar="🕯️", tone="system",
        )
        yield state_ev(self.public(state))

    async def _ai_clue(self, state: dict, p: dict) -> str:
        sleeper = self._is_sleeper(state, p["id"])
        template = _CLUE_SLEEPER if sleeper else _CLUE_INFORMED
        system = template.format(
            name=p["name"], style=p["style"], word=state["word"],
            category=state["category"], clues=self._clue_text(state),
        )
        try:
            raw = await LLM.text(
                LLMRequest(
                    system=system,
                    messages=[user("Your clue:")],
                    max_tokens=30,
                    temperature=1.0,
                    task="sleeper.clue",
                    mock_hint="" if sleeper else state["word"],
                )
            )
        except LLMError:
            return "..."
        clue = " ".join(raw.strip().strip('"').split()[:4]) or "..."
        # A model that blurts the word out would end the game instantly.
        if not sleeper and contains_term(clue, state["word"]):
            clue = "hard to say"
        return clue

    # -- discussion --------------------------------------------------------
    def _transcript(self, state: dict) -> str:
        lines = [f"{c['name']} (round {c['round']} clue): {c['clue']}" for c in state["clues"]]
        lines += [f"{d['name']}: {d['text']}" for d in state["discussion"]]
        return "\n".join(lines)

    def _knowledge(self, state: dict, pid: str) -> str:
        if self._is_sleeper(state, pid):
            return (
                f"You are the SLEEPER. You never knew the word; you only know the category "
                f"({state['category']}). Deflect suspicion and push it onto somebody else."
            )
        return (
            f"You know the secret word: {state['word']}. You are not the sleeper. Never say the "
            "word out loud."
        )

    async def _discussion(self, state: dict, text: str) -> AsyncIterator[Ev]:
        if not text:
            yield toast("Say something - even 'I have no idea' is information.", "warn")
            return
        state["discussion"].append({"id": YOU, "name": "You", "text": text})
        yield self.say(state, YOU, text, name="You", avatar="🧑", tone="talk")
        yield state_ev(self.public(state))

        for p in [x for x in state["players"] if x["is_ai"]]:
            try:
                line = await LLM.text(
                    LLMRequest(
                        system=_DISCUSS.format(
                            name=p["name"], style=p["style"],
                            knowledge=self._knowledge(state, p["id"]),
                            transcript=self._transcript(state),
                        ),
                        messages=[user("Your line:")],
                        max_tokens=90,
                        temperature=1.0,
                        task="sleeper.discuss",
                    )
                )
            except LLMError:
                line = "..."
            line = line.strip().strip('"')[:220] or "..."
            state["discussion"].append({"id": p["id"], "name": p["name"], "text": line})
            yield self.say(state, p["id"], line, name=p["name"], avatar=p["avatar"], tone="talk")
            yield state_ev(self.public(state))

        state["phase"] = "vote"
        yield fx("phase", phase="vote")
        yield self.say(
            state, "system", "Vote. No abstaining.", name="TABLE", avatar="🗳️", tone="system",
        )
        yield state_ev(self.public(state))

    # -- vote --------------------------------------------------------------
    async def _vote(self, state: dict, target: str) -> AsyncIterator[Ev]:
        if not self._player(state, target) or target == YOU:
            yield toast("Vote for one of the other four.", "warn")
            return
        state["votes"][YOU] = target
        yield self.say(
            state, YOU, f"I vote {self._player(state, target)['name']}.",
            name="You", avatar="🧑", tone="vote",
        )
        yield state_ev(self.public(state))

        for p in [x for x in state["players"] if x["is_ai"]]:
            ballot = "\n".join(
                f"- {q['name']}" for q in state["players"] if q["id"] != p["id"]
            )
            data = await LLM.json(
                LLMRequest(
                    system=_VOTE.format(
                        name=p["name"], style=p["style"],
                        knowledge=self._knowledge(state, p["id"]),
                        ballot=ballot, transcript=self._transcript(state),
                    ),
                    messages=[user("Your vote:")],
                    max_tokens=110,
                    temperature=0.8,
                    task="sleeper.vote",
                ),
                default={},
            )
            name = str((data or {}).get("vote", "")).strip()
            reason = str((data or {}).get("reason", "")).strip()[:120]
            pick = next(
                (q for q in state["players"] if q["name"].lower() == name.lower() and q["id"] != p["id"]),
                None,
            )
            if pick is None:  # model named nobody valid - fall back deterministically
                others = [q for q in state["players"] if q["id"] != p["id"]]
                pick = rng.pick(f"{state['seed']}:{p['id']}:fallback", others)
            state["votes"][p["id"]] = pick["id"]
            state["vote_reasons"][p["id"]] = reason
            yield self.say(
                state, p["id"],
                f"I vote {pick['name']}." + (f" {reason}" if reason else ""),
                name=p["name"], avatar=p["avatar"], tone="vote",
            )
            yield state_ev(self.public(state))

        async for ev in self._resolve(state):
            yield ev

    async def _resolve(self, state: dict) -> AsyncIterator[Ev]:
        tally: dict[str, int] = {}
        for target in state["votes"].values():
            tally[target] = tally.get(target, 0) + 1
        top = max(tally.values()) if tally else 0
        leaders = [pid for pid, n in tally.items() if n == top]
        ejected = leaders[0] if len(leaders) == 1 else None

        sleeper = state["sleeper"]
        sleeper_p = self._player(state, sleeper)
        you_sleeper = sleeper == YOU
        crew_win = ejected == sleeper
        you_win = (not crew_win) if you_sleeper else crew_win

        board = ", ".join(
            f"{self._player(state, pid)['name']} {n}" for pid, n in sorted(tally.items(), key=lambda kv: -kv[1])
        )
        yield self.say(state, "system", f"Votes - {board}.", name="TABLE", avatar="🗳️", tone="system")
        if ejected is None:
            yield self.say(
                state, "system", "Deadlock. Nobody is ejected, and that is a gift to the sleeper.",
                name="TABLE", avatar="⚖️", tone="warn",
            )
        else:
            yield self.say(
                state, "system",
                f"{self._player(state, ejected)['name']} is ejected"
                + (" - and was the sleeper." if crew_win else f" - and was not the sleeper."),
                name="TABLE", avatar="🚪", tone="win" if crew_win else "bad",
            )
        yield self.say(
            state, "system",
            f"The sleeper was {sleeper_p['name']}. The word was “{state['word']}”.",
            name="REVEAL", avatar="🕯️", tone="system",
        )

        votes_against_you = sum(1 for t in state["votes"].values() if t == YOU)
        your_vote_right = state["votes"].get(YOU) == sleeper
        if you_sleeper:
            score = max(150, 1300 - 120 * votes_against_you) if you_win else 160
            headline = "YOU SURVIVED THE VOTE" if you_win else "THE TABLE READ YOU"
            detail = (
                f"{votes_against_you} of four pointed at you and it wasn't enough."
                if you_win else "Bluffing is harder than it looks."
            )
        else:
            score = (800 + (250 if your_vote_right else 0)) if you_win else (90 if your_vote_right else 0)
            headline = "SLEEPER EJECTED" if you_win else "THE SLEEPER WALKS"
            detail = (
                ("You called it correctly." if your_vote_right else "The table got there without you.")
                if you_win
                else (f"You had {sleeper_p['name']}, but the table didn't listen." if your_vote_right
                      else f"{sleeper_p['name']} played you.")
            )

        share = [
            ("🐺" if you_sleeper else "👥") + ("🟩" if you_win else "🟥")
            + "".join("🗳️" for _ in range(min(5, len(state["votes"]))))
        ]
        state["phase"] = "done"
        result = self.finish(
            state,
            Result(
                outcome="win" if you_win else "loss",
                score=score,
                headline=headline,
                detail=detail,
                stats={
                    "was_impostor": you_sleeper,
                    "votes_against": votes_against_you,
                    "vote_correct": your_vote_right,
                    "crew_win": crew_win,
                },
                share=share,
                reveal={"word": state["word"], "sleeper": sleeper_p["name"], "tally": tally},
            ),
        )
        yield fx("win" if you_win else "lose")
        yield state_ev(self.public(state))
        yield Ev("end", {"result": result.to_dict()})


GAME = SleeperGame()
