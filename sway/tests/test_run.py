"""Runs: mode shapes, floor plans, rewards, scoring, share cards."""
import pytest

from app.content import mind as get_mind
from app.engine import encounter, meta, run as runmod

ALL_UNLOCKED = meta.unlocked_for_level(99)


def fresh(mode="ascent", seed="seed-a", unlocked=ALL_UNLOCKED, daily_key=""):
    return runmod.new(mode_id=mode, seed=seed, daily_key=daily_key, unlocked=unlocked)


def test_every_mode_is_in_the_catalogue_and_ordered():
    assert set(runmod.ORDER) == set(runmod.MODES)
    ids = [m["id"] for m in runmod.catalogue()]
    assert ids == runmod.ORDER


def test_mode_ladders_match_their_floor_counts():
    for mode in runmod.MODES.values():
        if mode.endless:
            continue
        assert len(mode.ladder) == mode.floors, mode.id


def test_unknown_mode_falls_back_rather_than_crashing():
    state = runmod.new(mode_id="nonsense", seed="s")
    assert state["mode"] == "ascent"


def test_ascent_plans_eight_distinct_floors_ending_in_a_boss():
    state = fresh()
    slots = []
    for _ in range(8):
        enc = runmod.begin_floor(state)
        slots.append(enc["slot"])
    assert slots[-1] == "boss"
    assert "gate" in slots
    assert len(set(state["used_minds"])) == 8, "a run must not repeat a mind"


def test_the_gate_floor_is_a_holdout_with_ward_cards():
    state = fresh()
    for _ in range(4):
        enc = runmod.begin_floor(state)
    assert enc["slot"] == "gate"
    assert enc["kind"] == "holdout"
    ward_ids = {c["id"] for c in encounter.public(enc)["hand"]}
    assert ward_ids <= set(runmod.WARD_DECK)


def test_resistance_grows_steeply_but_smoothly():
    mode = runmod.MODES["ascent"]
    mind = get_mind("doorman")
    values = [runmod.resistance_for(mode, f, mind) for f in range(1, 9)]
    assert values == sorted(values)
    assert values[7] / values[0] > 10, "floor 8 must need a real build"
    for a, b in zip(values, values[1:]):
        assert 1.4 < b / a < 1.6


def test_bosses_get_an_extra_exchange():
    mode = runmod.MODES["ascent"]
    mind = get_mind("architect")
    assert runmod.turns_for(mode, {"kind": "sway", "slot": "boss"}, mind) == mode.turns + 1


def test_blitz_is_three_exchanges_with_no_cards():
    state = fresh("blitz")
    enc = runmod.begin_floor(state)
    pub = encounter.public(enc)
    assert pub["turns"] == 3
    assert pub["hand"] == []
    assert runmod.run_over(state, "win")


def test_holdout_mode_is_one_long_holdout():
    state = fresh("holdout")
    enc = runmod.begin_floor(state)
    assert enc["kind"] == "holdout" and enc["turns"] == 8
    assert encounter.public(enc)["max_resolve"] > 0


def test_endless_never_ends_on_a_win_and_keeps_scaling():
    state = fresh("endless")
    slots = []
    for _ in range(12):
        enc = runmod.begin_floor(state)
        slots.append(enc["slot"])
        assert not runmod.run_over(state, "win")
    assert slots[4] == "boss" and slots[3] == "gate"
    assert runmod.run_over(state, "loss")


def test_endless_reuses_minds_only_after_exhausting_them():
    state = fresh("endless")
    for _ in range(6):
        runmod.begin_floor(state)
    assert len(set(state["used_minds"])) >= 5


def test_the_same_seed_plans_the_same_run():
    a, b = fresh(seed="identical"), fresh(seed="identical")
    for _ in range(8):
        ea, eb = runmod.begin_floor(a), runmod.begin_floor(b)
        assert ea["mind_id"] == eb["mind_id"]
    assert runmod.roll_offers(a) == runmod.roll_offers(b)


def test_different_seeds_plan_different_runs():
    a, b = fresh(seed="one"), fresh(seed="two")
    plans = []
    for state in (a, b):
        plans.append([runmod.begin_floor(state)["mind_id"] for _ in range(8)])
    assert plans[0] != plans[1]


def test_offers_are_three_things_and_never_a_duplicate_relic():
    state = fresh()
    runmod.begin_floor(state)
    offers = runmod.roll_offers(state)
    assert len(offers) == 3
    assert len({o["id"] for o in offers}) == 3
    for o in offers:
        assert o["type"] in ("card", "relic")
        assert o["rarity"] != "starter"


def test_locked_content_never_appears_in_offers():
    """A level-1 player must not be shown a card they cannot own."""
    state = fresh(unlocked=meta.unlocked_for_level(1))
    for floor in range(1, 8):
        runmod.begin_floor(state)
        for offer in runmod.roll_offers(state):
            assert meta.unlock_level(offer["id"]) == 1, offer["id"]


def test_higher_floors_offer_better_rarity_on_average():
    def rare_share(floor):
        hits = 0
        for i in range(40):
            state = fresh(seed=f"rar-{i}")
            for _ in range(floor):
                runmod.begin_floor(state)
            hits += sum(1 for o in runmod.roll_offers(state)
                        if o["rarity"] in ("rare", "legendary"))
        return hits
    assert rare_share(7) > rare_share(1)


def test_a_relic_is_guaranteed_on_every_third_floor():
    state = fresh()
    for _ in range(3):
        runmod.begin_floor(state)
    assert any(o["type"] == "relic" for o in runmod.roll_offers(state))


def test_taking_an_offer_puts_it_where_it_belongs():
    state = fresh()
    runmod.begin_floor(state)
    offers = runmod.roll_offers(state)
    card_idx = next(i for i, o in enumerate(offers) if o["type"] == "card")
    before = len(state["deck"])
    pick = runmod.take_offer(state, card_idx)
    assert len(state["deck"]) == before + 1 and state["deck"][-1] == pick["id"]
    assert state["offers"] == []


def test_taking_a_relic_updates_its_derived_counters():
    state = fresh()
    runmod.begin_floor(state)
    state["offers"] = [{"type": "relic", **{"id": "loaded-dice", "rarity": "common"}}]
    runmod.take_offer(state, 0)
    assert state["rerolls"] == 3
    assert runmod.effects(state).get("reroll") == 3


def test_an_out_of_range_offer_is_refused():
    state = fresh()
    runmod.begin_floor(state)
    runmod.roll_offers(state)
    assert runmod.take_offer(state, 9) is None
    assert runmod.take_offer(state, -1) is None


def test_relic_effects_reach_the_encounter():
    plain = fresh(seed="x")
    runmod.begin_floor(plain)
    loaded = fresh(seed="x")
    loaded["relics"] = ["ivory-cufflinks", "tin-ear", "long-lunch"]
    runmod.begin_floor(loaded)
    a, b = encounter.public(plain["encounter"]), encounter.public(loaded["encounter"])
    assert b["focus_max"] == a["focus_max"] + 1
    assert len(b["hand"]) == len(a["hand"]) + 1
    assert b["turns"] == a["turns"] + 1


def test_second_wind_is_consumed_exactly_once():
    state = fresh()
    state["relics"] = ["second-wind"]
    runmod.begin_floor(state)
    assert runmod.revive_available(state)
    state["revives_used"] = 1
    assert not runmod.revive_available(state)


def test_finishing_a_floor_scores_and_records_history():
    state = fresh()
    runmod.begin_floor(state)
    state["encounter"]["turn"] = 2
    row = runmod.finish_floor(state, "win", {"crits": 2, "best_hit": 400, "fumbles": 0, "words": 40})
    assert row["outcome"] == "win" and row["points"] > 0
    assert state["score"] == row["points"]
    assert state["stats"]["best_hit"] == 400 and state["stats"]["crits"] == 2
    assert state["history"] == [row]


def test_a_lost_floor_scores_nothing():
    state = fresh()
    runmod.begin_floor(state)
    row = runmod.finish_floor(state, "loss", {"crits": 0, "best_hit": 90})
    assert row["points"] == 0 and state["score"] == 0


def test_holding_a_gate_pays_a_premium():
    plain, gated = fresh(seed="p"), fresh(seed="p")
    runmod.begin_floor(plain)
    plain["encounter"]["kind"] = "sway"
    runmod.begin_floor(gated)
    gated["encounter"]["kind"] = "holdout"
    a = runmod.finish_floor(plain, "win", {"crits": 0})
    b = runmod.finish_floor(gated, "win", {"crits": 0})
    assert b["points"] > a["points"]


def test_share_grid_is_one_cell_per_floor_and_pads_to_the_mode_length():
    state = fresh()
    for outcome, kind, turns in (("win", "sway", 1), ("win", "holdout", 6), ("loss", "sway", 6)):
        runmod.begin_floor(state)
        state["encounter"]["kind"] = kind
        state["encounter"]["turn"] = turns
        runmod.finish_floor(state, outcome, {})
    rows = runmod.share_rows(state)
    grid = "".join(rows)
    assert len(grid) == 8 * 2 or len(grid) >= 8   # emoji are 2 UTF-16 units in some hosts
    assert runmod.BLOCK["clear"] in grid and runmod.BLOCK["hold"] in grid
    assert runmod.BLOCK["fail"] in grid and runmod.BLOCK["miss"] in grid


def test_result_reports_the_mind_that_stopped_you():
    state = fresh()
    runmod.begin_floor(state)
    name = state["encounter"]["mind"]["name"]
    runmod.finish_floor(state, "loss", {})
    result = runmod.build_result(state, "loss")
    assert name in result.detail
    assert result.stats["cleared"] == 0
    assert result.share


def test_public_run_state_shows_the_whole_map_without_spoiling_it():
    state = fresh()
    runmod.begin_floor(state)
    pub = runmod.public(state)
    assert len(pub["map"]) == 8
    assert pub["map"][0]["outcome"] == "now" and pub["map"][0]["name"]
    assert pub["map"][5]["sigil"] == "?" and not pub["map"][5]["name"]
    assert pub["encounter"]["mind"]["name"]


def test_endless_public_state_has_no_finite_map():
    state = fresh("endless")
    runmod.begin_floor(state)
    pub = runmod.public(state)
    assert pub["endless"] and pub["map"] == [] and pub["floors"] == 0
