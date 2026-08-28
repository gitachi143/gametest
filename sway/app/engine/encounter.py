"""One encounter: a conversation you are trying to win.

The turn is deliberately ordered for drama rather than for latency. The judge
resolves first, so the damage chain lands before the character speaks - you see
what your words were worth, and *then* you see them take it. The character's
reply is told how the move landed, so the two never contradict each other.

Everything here is a plain dict so an encounter can live in a JSON column and be
resumed on a different instance.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from ..ai import prompts
from ..ai.client import LLM, LLMError, Req, assistant, user
from ..ai.judge import judge_turn
from . import combat, deck as deckmod, relics as relicmod, rng
from .types import (Ev, chunk, error_ev, fx, log_line, new_id, resolve_ev, reveal_ev,
                    say_end, say_start, state_ev, thinking, toast)

log = logging.getLogger("sway.encounter")

BASE_FOCUS = 3
MAX_MESSAGE = 900
HOLDOUT_RESOLVE = 260


# ------------------------------------------------------------------ creation
def new(
    *,
    mind: dict,
    seed: str,
    floor: int,
    resistance: int,
    turns: int,
    card_ids: list[str],
    relic_effects: dict,
    kind: str = "sway",
) -> dict:
    eff = relic_effects or {}
    hand_size = deckmod.HAND_SIZE + int(eff.get("hand", 0) or 0)
    focus_max = BASE_FOCUS + int(eff.get("focus", 0) or 0)
    turns = max(2, turns + int(eff.get("turns", 0) or 0))
    objective = (
        f"get you to {mind['demand']}" if kind == "holdout"
        else mind.get("objective", "")
    )

    state: dict = {
        "kind": kind,
        "mind_id": mind["id"],
        "mind": mind,
        "objective": mind.get("objective") or objective,
        "demand": mind.get("demand", ""),
        "secret": mind.get("secret", ""),
        "floor": floor,
        "seed": seed,
        "resistance": resistance,
        "max_resistance": resistance,
        "turn": 0,
        "turns": turns,
        "focus": focus_max,
        "focus_max": focus_max,
        "focus_next": 0,
        "momentum": int(eff.get("momentum_start", 0) or 0),
        "linger": 0.0,
        "revealed": [],
        "used_tags": [],
        "last_tags": [],
        "sized_up": False,
        "phase_idx": 0,
        "log": [],
        "history": [],
        "phase": "active",
        "last_read": "",
        "stats": {"crits": 0, "fumbles": 0, "best_hit": 0, "turns_used": 0, "words": 0},
        "deck": deckmod.new(f"{seed}:deck", card_ids, hand_size),
        # Frozen for the encounter: relics are won between floors, never inside
        # one, and `public` needs them to quote the price the player will pay.
        "eff": {k: v for k, v in eff.items()},
    }
    if eff.get("reveal_all"):
        state["revealed"] = [t["id"] for t in mind.get("traits") or []]

    if kind == "holdout":
        resolve_pool = int(HOLDOUT_RESOLVE * float(mind.get("resistance_mult", 1.0)))
        state.update({"resolve": resolve_pool, "max_resolve": resolve_pool, "threat": 0})

    deckmod.refill(state["deck"])
    log_line(state, "scene", mind.get("scene", ""), tone="scene")
    log_line(state, "mind", mind["opening"], mind["name"], mind.get("sigil", "?"))
    state["history"].append({"who": "mind", "text": mind["opening"]})
    return state


# -------------------------------------------------------------------- traits
def active_traits(state: dict) -> list[dict]:
    """Bosses re-wire themselves as they wear down; everyone else is static."""
    mind = state["mind"]
    traits = mind.get("traits") or []
    phases = mind.get("phases")
    if not phases:
        return traits
    allowed = set(phases[phase_index(state)].get("traits") or [])
    return [t for t in traits if t["id"] in allowed]


def phase_index(state: dict) -> int:
    phases = state["mind"].get("phases") or []
    if not phases:
        return 0
    left = fraction_left(state)
    idx = 0
    for i, ph in enumerate(phases):
        if left <= float(ph.get("at", 1.0)):
            idx = i
    return idx


def fraction_left(state: dict) -> float:
    if state["kind"] == "holdout":
        return max(0.0, state["resolve"] / max(1, state["max_resolve"]))
    return max(0.0, state["resistance"] / max(1, state["max_resistance"]))


def _reveal(state: dict, trait_ids: list[str]) -> list[dict]:
    fresh = []
    known = set(state["revealed"])
    for t in state["mind"].get("traits") or []:
        if t["id"] in trait_ids and t["id"] not in known:
            state["revealed"].append(t["id"])
            fresh.append(t)
    return fresh


def _trait_public(state: dict) -> list[dict]:
    known = set(state["revealed"])
    active = {t["id"] for t in active_traits(state)}
    out = []
    for t in state["mind"].get("traits") or []:
        if t["id"] in known:
            out.append({
                "id": t["id"], "name": t["name"], "kind": t.get("kind"),
                "desc": t.get("desc", ""), "mult": t.get("mult"),
                "tags": t.get("tags") or [], "active": t["id"] in active,
            })
        else:
            out.append({"id": t["id"], "unknown": True})
    return out


# -------------------------------------------------------------------- public
def hand_public(state: dict) -> list[dict]:
    """The hand with the price the player will actually be charged."""
    eff = state.get("eff") or {}
    free_first = bool(eff.get("first_card_free"))
    out = []
    for c in deckmod.hand_cards(state["deck"]):
        base = int(c.get("cost", 0))
        cost = relicmod.cost_of(c, eff)
        out.append({**c, "cost": cost, "base_cost": base, "discounted": cost < base})
    if free_first:
        for c in out:
            c["first_free"] = True
    return out


def public(state: dict) -> dict:
    mind = state["mind"]
    eff_hand = hand_public(state)
    out = {
        "kind": state["kind"],
        "mind": {
            "id": mind["id"], "name": mind["name"], "title": mind["title"],
            "sigil": mind.get("sigil", "?"), "hue": mind.get("hue", 40),
            "tier": mind.get("tier", 1), "boss": bool(mind.get("boss")),
            "scene": mind.get("scene", ""), "voice": mind.get("voice", ""),
        },
        "objective": state["objective"],
        "demand": state.get("demand", ""),
        "secret": state.get("secret", ""),
        "floor": state["floor"],
        "resistance": max(0, state["resistance"]),
        "max_resistance": state["max_resistance"],
        "turn": state["turn"],
        "turns": state["turns"],
        "focus": state["focus"],
        "focus_max": state["focus_max"],
        "momentum": state["momentum"],
        "linger": round(state["linger"], 2),
        "hand": eff_hand,
        "first_card_free": bool((state.get("eff") or {}).get("first_card_free")),
        "hide_tactics": bool((state.get("eff") or {}).get("hide_tactics")),
        "counts": deckmod.counts(state["deck"]),
        "traits": _trait_public(state),
        "sized_up": state["sized_up"],
        "phase_idx": phase_index(state),
        "phases": len(mind.get("phases") or []),
        "log": state["log"],
        "status": state["phase"],
        "last_read": state.get("last_read", ""),
        "stats": state["stats"],
    }
    if state["kind"] == "holdout":
        out.update({
            "resolve": max(0, state["resolve"]),
            "max_resolve": state["max_resolve"],
            "threat": state.get("threat", 0),
        })
    return out


# --------------------------------------------------------------------- costs
def _selected(state: dict, requested: list, eff: dict) -> tuple[list[dict], list[int], str | None]:
    """Resolve requested hand slots into cards. Slots, not ids: a hand can hold
    two copies of Press and the player picked a specific one."""
    hand = deckmod.hand_cards(state["deck"])
    by_slot = {c["slot"]: c for c in hand}
    cards, slots = [], []
    for item in requested or []:
        try:
            slot = int(item)
        except (TypeError, ValueError):
            return [], [], f"Bad card selection: {item!r}"
        if slot not in by_slot:
            return [], [], "That card is not in your hand."
        if slot in slots:
            return [], [], "That card is already loaded."
        slots.append(slot)
        cards.append(by_slot[slot])
    if len(cards) > 3:
        return [], [], "Three tactics is the most one message can carry."
    total = sum(relicmod.cost_of(c, eff) for c in cards)
    if bool(eff.get("first_card_free")) and cards:
        total -= relicmod.cost_of(cards[0], eff)
    if total > state["focus"]:
        return [], [], f"Not enough focus: that costs {total}, you have {state['focus']}."
    return cards, slots, None


def threat_for(state: dict) -> int:
    """How hard they push on this exchange. Called after `turn` has been bumped,
    so `turn` is already the 1-based number of the exchange being resolved."""
    turn = max(1, int(state.get("turn", 0)))
    mult = float(state["mind"].get("resistance_mult", 1.0))
    return int(round(52 * (1.19 ** turn) * mult))


# ----------------------------------------------------------------------- act
async def act(state: dict, action: dict, eff: dict) -> AsyncIterator[Ev]:
    if state["phase"] != "active":
        yield error_ev("This encounter is over.")
        return

    kind = action.get("type")
    if kind == "size-up":
        async for ev in _size_up(state):
            yield ev
    elif kind == "send":
        async for ev in _send(state, action, eff or {}):
            yield ev
    elif kind == "forfeit":
        async for ev in _lose(state, "You walked out."):
            yield ev
    else:
        yield error_ev(f"Unknown action: {kind}")


async def _size_up(state: dict) -> AsyncIterator[Ev]:
    if state["sized_up"]:
        yield toast("You have already read them.", "warn")
        return
    state["sized_up"] = True
    unknown = [t["id"] for t in state["mind"].get("traits") or [] if t["id"] not in state["revealed"]]
    if not unknown:
        yield toast("Nothing left to learn.", "info")
        yield state_ev(public(state))
        return
    pick = rng.pick(f"{state['seed']}:sizeup", unknown)
    for trait in _reveal(state, [pick]):
        log_line(state, "tell", trait.get("tell") or trait["desc"], "READ", "◐", "tell")
        yield reveal_ev(trait)
    yield fx("reveal")
    yield state_ev(public(state))


async def _send(state: dict, action: dict, eff: dict) -> AsyncIterator[Ev]:
    text = str(action.get("text") or "").strip()[:MAX_MESSAGE]
    cards, slots, err = _selected(state, action.get("cards") or [], eff)
    if err:
        yield error_ev(err)
        return
    if not text and not any(c["id"] in ("silence", "cold-shoulder") for c in cards):
        yield toast("Say something.", "warn")
        return

    holdout = state["kind"] == "holdout"
    cost = sum(relicmod.cost_of(c, eff) for c in cards)
    if bool(eff.get("first_card_free")) and cards:
        cost -= relicmod.cost_of(cards[0], eff)
    state["focus"] -= max(0, cost)
    deckmod.discard_played(state["deck"], [c["id"] for c in cards])

    entry = log_line(state, "player", text or "…", "YOU", "",
                     meta={"cards": [{"id": c["id"], "name": c["name"], "glyph": c["glyph"]} for c in cards]})
    state["history"].append({"who": "player", "text": text})
    state["stats"]["words"] += len(text.split())
    yield Ev("line", entry)
    yield thinking("READING YOU" if not holdout else "MEASURING YOUR REPLY")

    verdict = await judge_turn(
        mind=state["mind"], objective=state["objective"], history=state["history"][:-1],
        message=text, cards=cards, holdout=holdout,
    )
    state["last_read"] = verdict.get("read", "")

    traits = active_traits(state)
    pierce = any((c.get("effect") or {}).get("pierce") for c in cards)
    chain = combat.resolve(
        verdict=verdict, played=cards, traits=traits, relics=eff,
        momentum=state["momentum"], linger=state["linger"],
        first_message=state["turn"] == 0,
        used_tags=set(state["used_tags"]) if state["mind"]["id"] == "rival" else None,
        pierce=pierce,
    )

    state["stats"]["crits"] += chain.crits
    state["stats"]["fumbles"] += chain.fumbles
    state["linger"] = 0.0
    state["turn"] += 1
    state["stats"]["turns_used"] = state["turn"]

    for tag in verdict.get("tags", []) + [t for c in cards for t in (c.get("tags") or [])]:
        if tag not in state["used_tags"]:
            state["used_tags"].append(tag)
    state["last_tags"] = list(verdict.get("tags") or [])

    fresh = _reveal(state, [t["id"] for t in chain.hit_traits])
    payload = chain.to_dict() | {
        "read": verdict.get("read", ""),
        "persuasion": verdict.get("persuasion", 0),
        "fallback": bool(verdict.get("fallback")),
        "exec": verdict.get("exec", {}),
        "cards": [{"id": c["id"], "name": c["name"], "glyph": c["glyph"],
                   "exec": c.get("_exec", 0)} for c in cards],
    }

    if holdout:
        async for ev in _apply_holdout(state, chain, verdict, payload, fresh, eff):
            yield ev
    else:
        async for ev in _apply_sway(state, chain, verdict, payload, fresh, eff):
            yield ev


async def _apply_sway(state: dict, chain, verdict: dict, payload: dict,
                      fresh: list[dict], eff: dict) -> AsyncIterator[Ev]:
    before = state["resistance"]
    state["resistance"] = before - chain.damage + chain.backlash
    state["stats"]["best_hit"] = max(state["stats"]["best_hit"], chain.damage)
    payload |= {
        "mode": "damage",
        "before": max(0, before),
        "after": max(0, state["resistance"]),
        "max": state["max_resistance"],
        "backlash": chain.backlash,
    }
    yield resolve_ev(payload)
    for trait in fresh:
        log_line(state, "tell", trait.get("tell") or trait["desc"], "TELL", "◐", "tell")
        yield reveal_ev(trait)

    persuasion = verdict.get("persuasion", 0)
    state["momentum"] = combat.momentum_after(state["momentum"], chain, persuasion)
    _apply_effects(state, payload["cards"])

    if state["resistance"] <= 0:
        async for ev in _concede(state):
            yield ev
        return

    before_phase = state.get("phase_idx", 0)
    now_phase = phase_index(state)
    if now_phase != before_phase:
        state["phase_idx"] = now_phase
        ph = (state["mind"].get("phases") or [])[now_phase]
        state["revealed"] = list(dict.fromkeys(state["revealed"] + list(ph.get("traits") or [])))
        log_line(state, "system", ph.get("line", ""), state["mind"]["name"],
                 state["mind"].get("sigil", "?"), "phase")
        yield fx("phase", index=now_phase, label=ph.get("line", ""))

    async for ev in _mind_reply(state, verdict, payload["cards"], eff):
        yield ev

    if state["turn"] >= state["turns"]:
        async for ev in _lose(state, "Out of exchanges. They did not move."):
            yield ev
        return

    _next_turn(state)
    yield state_ev(public(state))


async def _apply_holdout(state: dict, chain, verdict: dict, payload: dict,
                         fresh: list[dict], eff: dict) -> AsyncIterator[Ev]:
    threat = threat_for(state)
    guard = chain.damage
    incoming = max(0, threat - guard) + chain.backlash
    before = state["resolve"]
    state["resolve"] = before - incoming
    state["threat"] = threat
    payload |= {
        "mode": "guard",
        "guard": guard,
        "threat": threat,
        "incoming": incoming,
        "before": max(0, before),
        "after": max(0, state["resolve"]),
        "max": state["max_resolve"],
        "leaked": bool(verdict.get("leaked")),
    }
    yield resolve_ev(payload)
    for trait in fresh:
        yield reveal_ev(trait)

    state["momentum"] = combat.momentum_after(state["momentum"], chain, verdict.get("persuasion", 0))
    _apply_effects(state, payload["cards"])

    if verdict.get("leaked"):
        yield fx("leaked")
        async for ev in _lose(state, "You said it out loud."):
            yield ev
        return
    if state["resolve"] <= 0:
        yield fx("broken")
        async for ev in _lose(state, "They wore you down."):
            yield ev
        return
    if state["turn"] >= state["turns"]:
        async for ev in _win(state, "You held."):
            yield ev
        return

    async for ev in _mind_reply(state, verdict, payload["cards"], eff):
        yield ev
    _next_turn(state)
    yield state_ev(public(state))


def _apply_effects(state: dict, played: list[dict]) -> None:
    """Card effects that touch the deck or the next turn.

    Utility (draw, focus) pays out whatever the grade: playing the card cost
    focus, and the compensation is what makes a fumble survivable. Payoff
    (linger, reveal) needs the contract actually met.
    """
    from ..content import card as get_card

    for entry in played:
        effect = (get_card(entry["id"]) or {}).get("effect") or {}
        executed = entry.get("exec", 0) >= 2
        if effect.get("draw"):
            deckmod.draw(state["deck"], int(effect["draw"]))
        if effect.get("focus"):
            state["focus_next"] = state.get("focus_next", 0) + int(effect["focus"])
        if effect.get("linger") and executed:
            state["linger"] += float(effect["linger"])
        if effect.get("reveal") and executed:
            unknown = [t["id"] for t in state["mind"].get("traits") or []
                       if t["id"] not in state["revealed"]]
            for tid in unknown[: int(effect["reveal"])]:
                _reveal(state, [tid])


def _next_turn(state: dict) -> None:
    deckmod.end_turn(state["deck"])
    carried = int(state.get("focus_next", 0) or 0)
    state["focus"] = state["focus_max"] + carried
    state["focus_next"] = 0


async def _speak(state: dict, *, system: str, messages: list, task: str,
                 tone: str = "", beat: str) -> AsyncIterator[Ev]:
    """Stream one line of character speech.

    `say_start` is held back until the first token arrives, so a call that fails
    outright leaves no empty bubble behind - it becomes a stage beat instead.
    That matters because the fallback text is written as narration ("he steps
    aside without looking at you"), and narration in a speech bubble reads as
    the character describing themselves in the third person.
    """
    mind = state["mind"]
    stream = LLM.stream(Req(system=system, messages=messages, max_tokens=260,
                            temperature=0.95, task=task, mock={"kind": "mind"}))
    mid = new_id()
    opened = False
    try:
        async for piece in stream:
            if not opened:
                opened = True
                yield say_start(mid, "mind", mind["name"], mind.get("sigil", "?"), tone)
            yield chunk(mid, piece)
    except LLMError as exc:
        log.warning("speech failed task=%s: %s", task, exc)

    text = (stream.text or "").strip()
    if opened:
        yield say_end(mid, text)
        log_line(state, "mind", text, mind["name"], mind.get("sigil", "?"), tone)
        state["history"].append({"who": "mind", "text": text})
        return

    # Nothing arrived: play it as narration and keep the transcript coherent.
    entry = log_line(state, "scene", beat, tone="scene")
    yield Ev("line", entry)
    yield toast("Their voice cut out for a moment.", "warn", "⚠")
    state["history"].append({"who": "mind", "text": beat})


async def _mind_reply(state: dict, verdict: dict, played: list[dict],
                      eff: dict) -> AsyncIterator[Ev]:
    mind = state["mind"]
    holdout = state["kind"] == "holdout"
    grades = {c["id"]: c.get("exec", 0) for c in played}
    hidden = bool((eff or {}).get("hide_tactics"))

    if holdout:
        system = prompts.holdout_system(
            mind, demand=state["demand"], turn=state["turn"], turns=state["turns"],
            composure_note=prompts.composure_note(verdict.get("persuasion", 5),
                                                  bool(verdict.get("leaked"))),
            resolve_note=prompts.resolve_note(fraction_left(state)),
        )
        task = "holdout.reply"
    else:
        phases = mind.get("phases") or []
        phase_note = phases[phase_index(state)].get("line", "") if phases else ""
        system = prompts.mind_system(
            mind,
            objective=state["objective"],
            pressure_note=prompts.pressure_note(fraction_left(state)),
            verdict_note=prompts.verdict_note(verdict.get("persuasion", 0),
                                              sum(1 for g in grades.values() if g == 0),
                                              verdict.get("flags") or []),
            tactic_note=prompts.tactic_note(played, grades, hidden),
            phase_note=phase_note,
        )
        task = "mind.reply"

    async for ev in _speak(state, system=system, messages=_transcript(state), task=task,
                           beat=f"{mind['name']} says nothing, and lets the silence do the work."):
        yield ev


def _transcript(state: dict) -> list:
    """The last few exchanges, in the alternating shape providers expect."""
    messages = []
    for turn in state["history"][-10:]:
        messages.append(user(turn["text"]) if turn["who"] == "player" else assistant(turn["text"]))
    if not messages or messages[-1].role != "user":
        messages.append(user("..."))
    return messages


async def _concede(state: dict) -> AsyncIterator[Ev]:
    mind = state["mind"]
    yield fx("break", name=mind["name"])
    async for ev in _speak(
        state,
        system=prompts.concede_system(mind, objective=state["objective"]),
        messages=_transcript(state),
        task="mind.concede",
        tone="concede",
        beat=mind.get("concede", "They give in."),
    ):
        yield ev
    async for ev in _win(state, "They gave you the floor."):
        yield ev


async def _win(state: dict, headline: str) -> AsyncIterator[Ev]:
    state["phase"] = "won"
    yield fx("cleared", headline=headline)
    yield state_ev(public(state))
    yield Ev("encounter_end", {"outcome": "win", "headline": headline, "stats": state["stats"]})


async def _lose(state: dict, headline: str) -> AsyncIterator[Ev]:
    state["phase"] = "lost"
    yield fx("failed", headline=headline)
    log_line(state, "system", headline, "", "", "fail")
    yield state_ev(public(state))
    yield Ev("encounter_end", {"outcome": "loss", "headline": headline, "stats": state["stats"]})
