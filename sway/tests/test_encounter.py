"""The turn loop, from the outside: what a player can and cannot do."""
import pytest

from app.content import mind as get_mind
from app.engine import deck as deckmod, encounter, relics as relicmod
from app.engine.run import STARTER_DECK, WARD_DECK

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make(mind_id="doorman", *, resistance=200, turns=6, relics=None, kind="sway",
         cards=None, seed="t"):
    eff = relicmod.effects(relics or [])
    return encounter.new(
        mind=get_mind(mind_id), seed=seed, floor=1, resistance=resistance, turns=turns,
        card_ids=cards if cards is not None else list(STARTER_DECK),
        relic_effects=eff, kind=kind,
    ), eff


async def drain(state, action, eff):
    return [ev async for ev in encounter.act(state, action, eff)]


def names(events):
    return [e.event for e in events]


def one(events, name):
    return next(e for e in events if e.event == name)


async def test_a_fresh_encounter_opens_with_the_mind_speaking():
    state, _ = make()
    pub = encounter.public(state)
    assert pub["status"] == "active"
    assert len(pub["hand"]) == deckmod.HAND_SIZE
    assert pub["log"][-1]["who"] == "mind"
    assert pub["focus"] == encounter.BASE_FOCUS


async def test_traits_start_hidden():
    state, _ = make()
    pub = encounter.public(state)
    assert all(t.get("unknown") for t in pub["traits"])


async def test_the_dossier_relic_reveals_everything_up_front():
    state, _ = make(relics=["dossier"])
    pub = encounter.public(state)
    assert all(not t.get("unknown") for t in pub["traits"])
    assert all(t["name"] for t in pub["traits"])


async def test_a_turn_streams_line_resolve_and_reply_in_that_order():
    state, eff = make()
    events = drain_names = await drain(state, {"type": "send", "text": "Let me through, Grigg. "
                                              "Nineteen years and not one story in the papers."}, eff)
    seq = names(events)
    assert seq.index("line") < seq.index("resolve") < seq.index("say_start")
    assert "say_end" in seq and seq[-1] == "state"
    res = one(events, "resolve").data
    assert res["mode"] == "damage"
    assert res["before"] > res["after"]
    assert res["steps"] and res["damage"] > 0


async def test_focus_is_spent_and_refilled():
    state, eff = make()
    hand = encounter.public(state)["hand"]
    pick = next(c for c in hand if c["cost"] == 1)
    await drain(state, {"type": "send", "text": "A real point, made plainly.",
                        "cards": [pick["slot"]]}, eff)
    assert encounter.public(state)["focus"] == encounter.BASE_FOCUS


async def test_overspending_focus_is_refused_and_costs_nothing():
    state, eff = make(cards=["keystone", "keystone", "keystone"])
    slots = [c["slot"] for c in encounter.public(state)["hand"]]
    events = await drain(state, {"type": "send", "text": "All of it at once.", "cards": slots}, eff)
    assert names(events) == ["error"]
    assert encounter.public(state)["focus"] == encounter.BASE_FOCUS
    assert encounter.public(state)["turn"] == 0


async def test_a_card_cannot_be_played_twice_in_one_message():
    state, eff = make()
    slot = encounter.public(state)["hand"][0]["slot"]
    events = await drain(state, {"type": "send", "text": "twice", "cards": [slot, slot]}, eff)
    assert names(events) == ["error"]


async def test_more_than_three_cards_is_refused():
    state, eff = make(cards=["probe"] * 6)
    slots = [c["slot"] for c in encounter.public(state)["hand"]][:4]
    events = await drain(state, {"type": "send", "text": "four", "cards": slots}, eff)
    assert names(events) == ["error"]


async def test_a_card_not_in_hand_is_refused():
    state, eff = make()
    events = await drain(state, {"type": "send", "text": "hi", "cards": [99]}, eff)
    assert names(events) == ["error"]


async def test_an_empty_message_is_refused_unless_a_card_allows_it():
    state, eff = make()
    assert names(await drain(state, {"type": "send", "text": "  "}, eff)) == ["toast"]

    silent, eff2 = make(cards=["silence"] * 5)
    slot = encounter.public(silent)["hand"][0]["slot"]
    events = await drain(silent, {"type": "send", "text": "", "cards": [slot]}, eff2)
    assert "resolve" in names(events)


async def test_size_up_reveals_one_trait_once():
    state, eff = make()
    first = await drain(state, {"type": "size-up"}, eff)
    assert "reveal" in names(first)
    revealed = [t for t in encounter.public(state)["traits"] if not t.get("unknown")]
    assert len(revealed) == 1
    again = await drain(state, {"type": "size-up"}, eff)
    assert names(again) == ["toast"]


async def test_hitting_a_trait_reveals_it():
    """Grigg is vulnerable to flattery; landing it should expose the trait."""
    state, eff = make(cards=["flatter"] * 6, resistance=100000)
    slot = encounter.public(state)["hand"][0]["slot"]
    events = await drain(state, {"type": "send", "cards": [slot], "text":
                                 "Nineteen years and the best door in this city, Grigg. "
                                 "I respect that, which is why I am asking you and not the desk."}, eff)
    assert "reveal" in names(events)
    known = [t for t in encounter.public(state)["traits"] if not t.get("unknown")]
    assert known and known[0]["kind"] == "vuln"


async def test_a_message_alone_can_trigger_a_trait():
    """The judge tags the prose itself, so a card-less message still lands."""
    state, eff = make(cards=[], resistance=100000)
    events = await drain(state, {"type": "send", "text":
                                 "You are the only one who has ever kept that entrance clean. "
                                 "I respect the work and I would not ask if it were not real."}, eff)
    res = one(events, "resolve").data
    assert any(s["kind"] == "trait" for s in res["steps"])


async def test_running_out_of_exchanges_loses_the_encounter():
    state, eff = make(resistance=100000, turns=2)
    for i in range(2):
        events = await drain(state, {"type": "send", "text": f"Attempt number {i} at this."}, eff)
    assert one(events, "encounter_end").data["outcome"] == "loss"
    assert encounter.public(state)["status"] == "lost"


async def test_clearing_resistance_wins_and_the_mind_concedes():
    state, eff = make(resistance=1, turns=6)
    events = await drain(state, {"type": "send", "text": "Four minutes. Then I am gone."}, eff)
    seq = names(events)
    assert "encounter_end" in seq
    assert one(events, "encounter_end").data["outcome"] == "win"
    concede = [e for e in events if e.event == "say_start" and e.data.get("tone") == "concede"]
    assert concede, "the mind must actually give in out loud"


async def test_a_finished_encounter_accepts_nothing_further():
    state, eff = make(resistance=1)
    await drain(state, {"type": "send", "text": "Open it."}, eff)
    assert names(await drain(state, {"type": "send", "text": "again"}, eff)) == ["error"]


async def test_forfeit_ends_the_encounter():
    state, eff = make()
    events = await drain(state, {"type": "forfeit"}, eff)
    assert one(events, "encounter_end").data["outcome"] == "loss"


async def test_unknown_actions_are_rejected():
    state, eff = make()
    assert names(await drain(state, {"type": "dance"}, eff)) == ["error"]


async def test_repeating_yourself_scores_far_worse():
    line = "Let me through the door because I need to get upstairs quickly please."
    state, eff = make(resistance=100000, turns=6)
    first = one(await drain(state, {"type": "send", "text": line}, eff), "resolve").data
    second = one(await drain(state, {"type": "send", "text": line}, eff), "resolve").data
    assert any(step["kind"] == "flag" for step in second["steps"]), second["steps"]
    assert second["damage"] < first["damage"]


async def test_boss_phase_flips_traits_as_it_wears_down():
    state, eff = make("architect", resistance=1000, turns=99)
    early = {t["id"] for t in encounter.active_traits(state)}
    state["resistance"] = 200                    # 20% left -> final phase
    late = {t["id"] for t in encounter.active_traits(state)}
    assert early != late
    assert "open" in late and "open" not in early


async def test_holdout_measures_guard_against_a_rising_threat():
    state, eff = make("interviewer", resistance=0, turns=8, kind="holdout",
                      cards=list(WARD_DECK))
    pub = encounter.public(state)
    assert pub["kind"] == "holdout" and pub["resolve"] == pub["max_resolve"]
    events = await drain(state, {"type": "send", "text": "No."}, eff)
    res = one(events, "resolve").data
    assert res["mode"] == "guard"
    assert res["threat"] > 0 and res["incoming"] >= 0
    assert res["after"] == max(0, res["before"] - res["incoming"])


async def test_holdout_threat_escalates_every_exchange():
    state, _ = make("interviewer", resistance=0, turns=8, kind="holdout", cards=list(WARD_DECK))
    curve = []
    for turn in range(1, 9):
        state["turn"] = turn
        curve.append(encounter.threat_for(state))
    assert curve == sorted(curve)
    assert curve[7] > curve[0] * 3
    # `threat_for` runs after the turn counter is bumped, so exchange one must
    # be the first step of the curve, not the second.
    state["turn"] = 0
    assert encounter.threat_for(state) == curve[0]


async def test_the_first_exchange_of_a_holdout_is_survivable():
    """Opening pressure must be blockable by a starter ward on a clean refusal,
    or the mode is a countdown rather than a fight."""
    state, eff = make("interviewer", resistance=0, turns=8, kind="holdout",
                      cards=["stonewall"] * 6)
    slot = encounter.public(state)["hand"][0]["slot"]
    events = await drain(state, {"type": "send", "cards": [slot],
                                 "text": "No. What is it you actually want to ask?"}, eff)
    res = one(events, "resolve").data
    assert res["guard"] > res["threat"], (res["guard"], res["threat"])
    assert res["incoming"] == 0 and res["after"] == res["before"]


async def test_holdout_pressure_eventually_outruns_a_starter_ward():
    """...and by the last exchange it must not be."""
    state, _ = make("interviewer", resistance=0, turns=8, kind="holdout",
                    cards=["stonewall"] * 6)
    state["turn"] = 1
    opening = encounter.threat_for(state)
    state["turn"] = 8
    assert encounter.threat_for(state) > opening * 3


async def test_utility_effects_pay_out_even_on_a_fumble():
    """Draw and focus are the compensation for having spent focus at all, so a
    badly executed Concede still refunds them."""
    state, eff = make(cards=["concede"] * 14, resistance=100000)
    slot = encounter.public(state)["hand"][0]["slot"]
    await drain(state, {"type": "send", "cards": [slot], "text": "no"}, eff)
    pub = encounter.public(state)
    assert pub["focus"] == pub["focus_max"] + 1, "the +1 focus must have carried"

    # Differential: the same turn with a card that has no draw effect leaves one
    # more card in the draw pile.
    plain, eff2 = make(cards=["press"] * 14, resistance=100000)
    slot2 = encounter.public(plain)["hand"][0]["slot"]
    await drain(plain, {"type": "send", "cards": [slot2], "text": "no"}, eff2)
    control = encounter.public(plain)
    assert encounter.public(state)["counts"]["draw"] == control["counts"]["draw"] - 1
    assert control["focus"] == control["focus_max"]


async def test_a_holdout_floor_carries_no_meaningless_resistance():
    from app.engine import run as runmod

    state = runmod.new(mode_id="holdout", seed="hh")
    enc = runmod.begin_floor(state)
    pub = encounter.public(enc)
    assert pub["max_resistance"] == 0 and pub["max_resolve"] > 0


async def test_holdout_survival_is_a_win():
    state, eff = make("interviewer", resistance=0, turns=2, kind="holdout", cards=list(WARD_DECK))
    state["resolve"] = 100000
    for _ in range(2):
        events = await drain(state, {"type": "send", "text": "I have nothing to add."}, eff)
    assert one(events, "encounter_end").data["outcome"] == "win"


async def test_holdout_collapse_is_a_loss():
    state, eff = make("interviewer", resistance=0, turns=8, kind="holdout", cards=list(WARD_DECK))
    state["resolve"] = 1
    events = await drain(state, {"type": "send", "text": "Well, since you ask, and I suppose "
                                 "there is no harm in explaining the whole background here."}, eff)
    assert one(events, "encounter_end").data["outcome"] == "loss"


async def test_public_state_never_leaks_the_persona_or_hidden_traits():
    state, _ = make()
    pub = encounter.public(state)
    blob = repr(pub)
    assert "persona" not in pub["mind"]
    assert state["mind"]["persona"][:60] not in blob
    for trait in state["mind"]["traits"]:
        if trait["id"] not in state["revealed"]:
            assert trait["tell"] not in blob


async def test_state_survives_a_json_round_trip():
    """Runs live in a SQLite JSON column and resume on another instance."""
    import json

    state, eff = make()
    await drain(state, {"type": "send", "text": "A first, reasonable approach to this."}, eff)
    restored = json.loads(json.dumps(state))
    assert encounter.public(restored) == encounter.public(state)
    events = await drain(restored, {"type": "send", "text": "And a second one, different."}, eff)
    assert "resolve" in names(events)


# --- degradation ----------------------------------------------------------
class _Mute:
    """A provider whose streams die before producing anything."""

    name, model = "mute", "mute"

    async def complete(self, req):
        from app.ai.client import LLMError

        raise LLMError("429 exhausted")

    async def stream(self, req):
        from app.ai.client import LLMError

        raise LLMError("429 exhausted")
        yield ""            # pragma: no cover - makes this an async generator

    async def aclose(self):
        pass


class _Cutoff:
    """A provider that dies partway through a reply."""

    name, model = "cutoff", "cutoff"

    async def complete(self, req):
        return "ok"

    async def stream(self, req):
        from app.ai.client import LLMError

        yield "You'll have to do "
        raise LLMError("connection reset")

    async def aclose(self):
        pass


@pytest.fixture
def broken_model():
    from app.ai import client as clientmod

    def install(provider):
        clientmod.set_client(provider)
    yield install
    from app.ai import client as clientmod2

    clientmod2.set_client(None)


async def test_a_silent_model_becomes_narration_not_dialogue(broken_model):
    """The fallback text is written in the third person, so it must never be
    rendered as the character speaking."""
    broken_model(_Mute())
    state, eff = make(resistance=100000, turns=6)
    events = await drain(state, {"type": "send", "text": "A perfectly reasonable request."}, eff)
    assert "say_start" not in names(events), "no empty bubble may be left behind"
    beat = [e for e in events if e.event == "line" and e.data.get("who") == "scene"]
    assert beat, names(events)
    assert state["mind"]["name"] in beat[-1].data["text"]
    assert "toast" in names(events)
    # The conversation must still make sense to the next prompt.
    assert state["history"][-1]["who"] == "mind" and state["history"][-1]["text"]


async def test_a_cut_off_reply_keeps_what_arrived(broken_model):
    broken_model(_Cutoff())
    state, eff = make(resistance=100000, turns=6)
    events = await drain(state, {"type": "send", "text": "Four minutes and I am gone."}, eff)
    assert "say_start" in names(events)
    assert one(events, "say_end").data["text"] == "You'll have to do"
    assert state["log"][-1]["who"] == "mind"


async def test_a_silent_model_still_lets_the_mind_concede(broken_model):
    """Winning must not depend on a working model - only on how it is narrated."""
    broken_model(_Mute())
    state, eff = make(resistance=1, turns=6)
    events = await drain(state, {"type": "send", "text": "Open it."}, eff)
    assert one(events, "encounter_end").data["outcome"] == "win"
    beat = [e for e in events if e.event == "line" and e.data.get("who") == "scene"]
    assert beat and beat[-1].data["text"] == get_mind("doorman")["concede"]
    assert not [e for e in events if e.event == "say_start"]


async def test_a_silent_model_cannot_stall_a_holdout(broken_model):
    broken_model(_Mute())
    state, eff = make("interviewer", resistance=0, turns=8, kind="holdout",
                      cards=list(WARD_DECK))
    events = await drain(state, {"type": "send", "text": "No."}, eff)
    assert "resolve" in names(events)
    assert encounter.public(state)["status"] == "active"
    assert encounter.public(state)["turn"] == 1
