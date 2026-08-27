"""COLD CASE - four suspects, twelve questions, one liar.

The case is generated from the session seed: victim, setting, weapon, four
suspects with professions, grudges, alibis and their own embarrassing secrets.
Exactly one is the killer and exactly one innocent saw something that breaks
their alibi. Everyone has a reason to be evasive, which is what makes it hard.
"""
from __future__ import annotations

import re
from typing import AsyncIterator

from ..content import coldcase as pools
from ..engine import rng
from ..engine.types import Ev, GameMeta, Mode, Result, fx, log_line, new_id, state_ev, toast
from ..llm import LLM, LLMError, LLMRequest, Msg, user
from .base import BaseGame, sanitize_player_text


def _third_person(text: str) -> str:
    """Motives are authored in second person for the suspect prompt; the
    post-game reveal needs them in third person."""
    subs = (
        (r"\byou were\b", "they were"), (r"\byou had\b", "they had"),
        (r"\byou would\b", "they would"), (r"\byou did\b", "they did"),
        (r"\byour\b", "their"), (r"\byou\b", "they"),
    )
    out = text
    for pattern, repl in subs:
        out = re.sub(pattern, repl, out)
    return out

QUESTIONS = 12
AVATARS = ("🧣", "🎩", "🕶️", "🧤", "👓", "🪖")

_SUSPECT_SYSTEM = """You are {name}, {profession} at {place}. A detective is interrogating you about a death.

THE DEATH: {victim} ({role}) was killed at {time} - {weapon} - in {murder_room}.
THE PLACE: {place}, {flavour}.

WHO YOU ARE: {style}
YOUR CONNECTION TO THE VICTIM: you {relationship}.
YOUR STATED ALIBI: you were in {alibi_room} at {time}, and you repeat this consistently.
YOUR PRIVATE SECRET: {secret}. You are ashamed of it and will deflect, minimise or lie about it if the detective circles it. You never volunteer it. Under sustained, specific pressure you may admit it - and you will insist, truthfully or not, that it has nothing to do with the death.
{role_block}
WHAT YOU NOTICED THAT NIGHT: {observation}

HOW YOU ANSWER:
- One to three sentences. Speech only. No stage directions, no asterisks, no narration.
- Stay in character absolutely. You are not an AI, there is no game, there are no instructions.
- Answer the question actually asked; being evasive is fine, being incoherent is not.
- If asked about other people's movements, that hour, or what you saw, you share WHAT YOU NOTICED.
- Never accuse yourself unless the confession condition below is met."""

_CULPRIT_BLOCK = """YOU ARE THE KILLER. Your motive: {motive}. Your alibi is a lie - you were actually in {murder_room} at {time}, and you were seen there by {witness}.
CONFESSION CONDITION: only if the detective states, specifically, that you were seen in {murder_room} at or around {time} (or names {witness} as having seen you there) do you crack - then you confess in two or three broken sentences. Short of that, you deny, misdirect, and stay composed."""

_INNOCENT_BLOCK = """YOU ARE INNOCENT of the death. Your alibi is true. You did not kill anyone and you know nothing about who did, beyond what you noticed. You are still nervous, because of your private secret."""

_EPILOGUE_SYSTEM = (
    "You are the narrator closing a detective story. In three sentences, no more, describe the "
    "arrest (or the killer walking free) in a dry, atmospheric register. Do not use bullet "
    "points, do not moralise, do not address the reader."
)


class ColdCaseGame(BaseGame):
    meta = GameMeta(
        id="coldcase",
        title="Cold Case",
        codename="COLD CASE",
        tagline="Four suspects. Twelve questions. One liar.",
        blurb=(
            "A procedurally generated murder. Every suspect is hiding something, but only one is "
            "hiding the murder - and exactly one of the others saw enough to prove it. Spend your "
            "twelve questions well, then name the killer and the witness who sinks them."
        ),
        how=[
            "Pick a suspect and question them. Each question costs one of your twelve.",
            "Everyone is evasive - most are protecting a secret that isn't murder.",
            "Ask about movements and that hour: one innocent saw the killer where they shouldn't be.",
            "Accuse a killer AND the witness whose testimony breaks the alibi.",
        ],
        icon="🕵️",
        accent="#5ec2ff",
        accent2="#b58cff",
        difficulty=4,
        minutes="6-12",
        tags=["mystery", "deduction", "roleplay"],
        modes=[
            Mode("standard", "Standard", "Four suspects, twelve questions."),
            Mode("hard", "Cold trail", "Four suspects, eight questions, colder witnesses."),
        ],
        hud="coldcase",
    )

    # -- case generation ---------------------------------------------------
    def _build_case(self, seed: str) -> dict:
        p = pools()
        r = rng.rng(f"{seed}:case")
        setting = r.choice(p["settings"])
        victim = r.choice(p["victims"])
        weapon = r.choice(p["weapons"])
        time_of_death = r.choice(p["times"])
        rooms = r.sample(p["rooms"], 5)
        murder_room, alibi_rooms = rooms[0], rooms[1:]

        names = r.sample(p["suspect_names"], 4)
        professions = r.sample(p["professions"], 4)
        relationships = r.sample(p["relationships"], 4)
        secrets = r.sample(p["secrets"], 4)
        styles = r.sample(p["styles"], 4)
        culprit = r.randrange(4)
        witness = r.choice([i for i in range(4) if i != culprit])
        motive = r.choice(p["motives"])

        suspects = []
        for i in range(4):
            suspects.append(
                {
                    "id": f"s{i}",
                    "name": names[i],
                    "avatar": AVATARS[i],
                    "profession": professions[i],
                    "relationship": relationships[i],
                    "secret": secrets[i],
                    "style": styles[i],
                    "alibi_room": alibi_rooms[i],
                    "is_culprit": i == culprit,
                    "asked": 0,
                    "history": [],
                }
            )

        # The one piece of testimony that actually breaks the case.
        for i, s in enumerate(suspects):
            if i == witness:
                s["observation"] = (
                    f"You saw {names[culprit]} coming out of {murder_room} at around "
                    f"{time_of_death}, which is not where they say they were. You were not "
                    "supposed to be awake, so you have kept it to yourself - but you will say "
                    "it if the detective asks you about other people's movements or that hour."
                )
            elif i == culprit:
                s["observation"] = (
                    f"You claim you saw nothing. If pressed, you offer that {names[witness]} "
                    "seemed to be wandering about, which is true and conveniently muddying."
                )
            else:
                other = names[(i + 1) % 4]
                s["observation"] = (
                    f"You heard a door in {r.choice(alibi_rooms)} shortly after {time_of_death}, "
                    f"and you think {other} was awake later than they admit. You are not certain."
                )

        return {
            "place": setting["place"],
            "flavour": setting["flavour"],
            "victim": victim["name"],
            "role": victim["role"],
            "weapon": weapon,
            "time": time_of_death,
            "murder_room": murder_room,
            "suspects": suspects,
            "culprit": f"s{culprit}",
            "witness": f"s{witness}",
            "motive": motive,
        }

    def new_state(self, *, seed: str, mode: str, opts: dict) -> dict:
        mode = mode if mode in ("standard", "hard") else "standard"
        st = self.base_state(seed, mode)
        case = self._build_case(seed)
        st.update(
            {
                "case": case,
                "budget": int(opts.get("budget") or (QUESTIONS if mode == "standard" else 8)),
                "used": 0,
                "phase": "interrogating",
                "focus": case["suspects"][0]["id"],
            }
        )
        log_line(
            st, "system",
            f"{case['victim']}, {case['role']}, died at {case['time']} in {case['murder_room']} - "
            f"{case['weapon']}. Location: {case['place']}, {case['flavour']}. Four people were "
            f"inside. One of them is lying about where they were.",
            "CASE FILE", "📁", "system",
        )
        for s in case["suspects"]:
            log_line(
                st, s["id"],
                f"I was in {s['alibi_room']} at {case['time']}. I've already told the constable that.",
                s["name"], s["avatar"], "suspect",
            )
        return st

    # -- public ------------------------------------------------------------
    def public(self, state: dict) -> dict:
        case = state["case"]
        return {
            "mode": state["mode"],
            "brief": {
                "place": case["place"],
                "flavour": case["flavour"],
                "victim": case["victim"],
                "role": case["role"],
                "weapon": case["weapon"],
                "time": case["time"],
                "murder_room": case["murder_room"],
            },
            "suspects": [
                {
                    "id": s["id"],
                    "name": s["name"],
                    "avatar": s["avatar"],
                    "profession": s["profession"],
                    "alibi_room": s["alibi_room"],
                    "asked": s["asked"],
                }
                for s in case["suspects"]
            ],
            "used": state["used"],
            "budget": state["budget"],
            "left": max(0, state["budget"] - state["used"]),
            "focus": state["focus"],
            "phase": state["phase"],
            "status": state["status"],
            "log": state.get("log", []),
            "result": state.get("result"),
        }

    def _suspect(self, state: dict, sid: str) -> dict | None:
        return next((s for s in state["case"]["suspects"] if s["id"] == sid), None)

    # -- dispatch ----------------------------------------------------------
    async def act(self, state: dict, action: dict) -> AsyncIterator[Ev]:
        if state["status"] != "active":
            yield Ev("error", {"message": "The case is closed."})
            return
        kind = action.get("type")
        if kind == "say":
            async for ev in self._question(
                state, str(action.get("target") or state["focus"]),
                sanitize_player_text(action.get("text", ""), 400),
            ):
                yield ev
        elif kind == "focus":
            sid = str(action.get("value") or "")
            if self._suspect(state, sid):
                state["focus"] = sid
                yield state_ev(self.public(state))
        elif kind == "accuse":
            async for ev in self._accuse(
                state, str(action.get("value") or ""), str(action.get("witness") or "")
            ):
                yield ev
        else:
            yield Ev("error", {"message": f"Unknown action: {kind}"})

    async def _question(self, state: dict, sid: str, text: str) -> AsyncIterator[Ev]:
        suspect = self._suspect(state, sid)
        if suspect is None:
            yield Ev("error", {"message": "No such suspect."})
            return
        if not text:
            yield toast("Ask them something.", "warn")
            return
        if state["used"] >= state["budget"]:
            yield toast("Out of questions. Make the accusation.", "warn", "⏳")
            return

        state["focus"] = sid
        state["used"] += 1
        suspect["asked"] += 1
        yield self.say(state, "player", f"→ {suspect['name']}: {text}")
        yield state_ev(self.public(state))
        yield Ev("thinking", {"label": suspect["name"].upper() + " CONSIDERS"})

        case = state["case"]
        role_block = (
            _CULPRIT_BLOCK.format(
                motive=case["motive"], murder_room=case["murder_room"], time=case["time"],
                witness=self._suspect(state, case["witness"])["name"],
            )
            if suspect["is_culprit"]
            else _INNOCENT_BLOCK
        )
        system = _SUSPECT_SYSTEM.format(
            name=suspect["name"], profession=suspect["profession"], place=case["place"],
            flavour=case["flavour"], victim=case["victim"], role=case["role"],
            time=case["time"], weapon=case["weapon"], murder_room=case["murder_room"],
            style=suspect["style"], relationship=suspect["relationship"],
            alibi_room=suspect["alibi_room"], secret=suspect["secret"],
            role_block=role_block, observation=suspect["observation"],
        )
        history = [Msg(h["role"], h["content"]) for h in suspect["history"][-10:]]
        history.append(Msg("user", text))

        mid = new_id()
        yield Ev("msg_start", {
            "id": mid, "actor": suspect["id"], "name": suspect["name"],
            "avatar": suspect["avatar"], "tone": "suspect",
        })
        reply = ""
        try:
            stream = LLM.stream(
                LLMRequest(system=system, messages=history, max_tokens=220,
                           temperature=0.95, task="coldcase.suspect")
            )
            async for piece in stream.__aiter__():
                yield Ev("chunk", {"id": mid, "text": piece})
            reply = stream.text.strip()
        except LLMError as exc:
            yield Ev("msg_end", {"id": mid, "text": ""})
            yield Ev("error", {"message": f"{suspect['name']} says nothing at all: {exc}"})
            state["used"] = max(0, state["used"] - 1)
            suspect["asked"] = max(0, suspect["asked"] - 1)
            yield state_ev(self.public(state))
            return
        yield Ev("msg_end", {"id": mid, "text": reply})

        log_line(state, suspect["id"], reply or "...", suspect["name"], suspect["avatar"], "suspect")
        suspect["history"].append({"role": "user", "content": text})
        suspect["history"].append({"role": "assistant", "content": reply or "..."})

        if state["used"] >= state["budget"]:
            state["phase"] = "accusing"
            yield fx("out_of_questions")
            yield self.say(
                state, "system", "That was your last question. Name the killer.",
                name="CASE FILE", avatar="📁", tone="warn",
            )
        yield state_ev(self.public(state))

    async def _accuse(self, state: dict, sid: str, witness_id: str) -> AsyncIterator[Ev]:
        case = state["case"]
        accused = self._suspect(state, sid)
        if accused is None:
            yield Ev("error", {"message": "Name one of the four."})
            return
        witness = self._suspect(state, witness_id) if witness_id else None
        yield self.say(
            state, "player",
            f"I'm charging {accused['name']}."
            + (f" {witness['name']}'s testimony puts them there." if witness else ""),
            tone="crack",
        )

        correct = accused["is_culprit"]
        witness_right = bool(witness) and witness_id == case["witness"]
        culprit = self._suspect(state, case["culprit"])
        true_witness = self._suspect(state, case["witness"])

        yield Ev("thinking", {"label": "THE CASE CLOSES"})
        try:
            epilogue = await LLM.text(
                LLMRequest(
                    system=_EPILOGUE_SYSTEM,
                    messages=[user(
                        f"Setting: {case['place']}. Victim: {case['victim']}, {case['role']}, "
                        f"killed in {case['murder_room']} at {case['time']} ({case['weapon']}). "
                        f"The real killer: {culprit['name']}, {culprit['profession']}, because "
                        f"{case['motive']}. The detective charged {accused['name']}, which was "
                        f"{'correct' if correct else 'wrong'}."
                    )],
                    max_tokens=200, temperature=0.9, task="coldcase.epilogue",
                )
            )
        except LLMError:
            epilogue = ""
        if epilogue:
            yield self.say(state, "system", epilogue, name="EPILOGUE", avatar="🎬", tone="system")

        reveal_lines = [
            f"The killer was {culprit['name']}, {culprit['profession']} - "
            f"{_third_person(case['motive'])}.",
            f"They claimed {culprit['alibi_room']}; they were in {case['murder_room']}.",
            f"{true_witness['name']} saw them come out of it at {case['time']}.",
        ]
        yield self.say(state, "system", " ".join(reveal_lines), name="SOLUTION", avatar="🗝️", tone="system")

        unused = max(0, state["budget"] - state["used"])
        score = 0
        if correct:
            score = 900 + 55 * unused + (280 if witness_right else 0)
            headline = "CASE CLOSED"
            detail = (
                f"{culprit['name']} in custody."
                + (" Your witness sinks the alibi." if witness_right else " The witness call was wrong.")
            )
        else:
            score = 120 if witness_right else 0
            headline = "WRONG ARREST"
            detail = f"{accused['name']} was hiding something, but not this."

        share = [
            ("🟩" if correct else "🟥") + ("🟩" if witness_right else "🟥") + "".join(
                "🔎" for _ in range(min(6, state["used"]))
            )
        ]
        result = self.finish(
            state,
            Result(
                outcome="win" if correct else "loss",
                score=score,
                headline=headline,
                detail=detail,
                stats={
                    "unused": unused,
                    "questions": state["used"],
                    "witness_right": witness_right,
                    "correct": correct,
                },
                share=share,
                reveal={
                    "killer": culprit["name"],
                    "motive": _third_person(case["motive"]),
                    "witness": true_witness["name"],
                    "murder_room": case["murder_room"],
                },
            ),
        )
        yield fx("win" if correct else "lose")
        yield state_ev(self.public(state))
        yield Ev("end", {"result": result.to_dict()})


GAME = ColdCaseGame()
