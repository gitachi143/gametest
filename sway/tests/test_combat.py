"""The damage maths. These are the numbers players will argue about."""
import pytest

from app.content import card
from app.engine import combat, relics as relicmod

VAIN = {"id": "vain", "name": "Vain", "kind": "vuln", "tags": ["flatter"], "mult": 2.0}
STUBBORN = {"id": "stub", "name": "Stubborn", "kind": "resist", "tags": ["pressure"], "mult": 0.5}


def verdict(persuasion=6, **grades):
    return {"persuasion": persuasion, "exec": grades, "flags": [], "tags": []}


def test_naked_message_scores_only_persuasion():
    ch = combat.resolve(verdict=verdict(7), played=[], traits=[], relics={})
    assert ch.base == 7 * combat.PERSUASION_SCALE
    assert ch.mult == 1.0
    assert ch.damage == 77


def test_execution_grade_scales_power_and_multiplier():
    out = {}
    for grade in (0, 1, 2, 3):
        ch = combat.resolve(verdict=verdict(5, flatter=grade), played=[card("flatter")],
                            traits=[], relics={})
        out[grade] = ch.damage
    assert out[0] < out[1] < out[2] < out[3], out
    assert out[0] == 55, "a fumbled card must contribute nothing at all"


def test_fumble_does_not_earn_the_multiplier():
    ch = combat.resolve(verdict=verdict(5, keystone=1), played=[card("keystone")],
                        traits=[], relics={})
    assert ch.mult == 1.0, "a weak execution earns power, never the multiplier"


def test_crit_is_counted_and_boosted():
    ch = combat.resolve(verdict=verdict(6, flatter=3), played=[card("flatter")],
                        traits=[], relics={})
    assert ch.crits == 1
    assert ch.mult == pytest.approx(1.45 + combat.CRIT_MULT_BONUS)


def test_vulnerability_and_resistance_both_apply():
    played = [card("flatter"), card("stakes")]
    ch = combat.resolve(verdict=verdict(6, flatter=2, stakes=2), played=played,
                        traits=[VAIN, STUBBORN], relics={})
    kinds = [t["hit"] for t in ch.hit_traits]
    assert sorted(kinds) == ["resist", "vuln"]
    # 0.5 resistance is softened to 0.75 because a vulnerability also landed.
    assert ch.mult == pytest.approx(1.45 * 2.0 * 0.75)


def test_resistance_bites_at_full_strength_when_nothing_landed():
    ch = combat.resolve(verdict=verdict(6, stakes=2), played=[card("stakes")],
                        traits=[VAIN, STUBBORN], relics={})
    assert ch.mult == pytest.approx(0.5)
    step = next(s for s in ch.steps if s.kind == "trait")
    assert step.note == "resists"


def test_finding_the_way_in_always_beats_not_finding_it():
    """Whatever else is in the message, hitting the vulnerability must pay."""
    blind = combat.resolve(verdict=verdict(6, stakes=2), played=[card("stakes")],
                           traits=[VAIN, STUBBORN], relics={})
    found = combat.resolve(verdict=verdict(6, stakes=2, flatter=2),
                           played=[card("stakes"), card("flatter")],
                           traits=[VAIN, STUBBORN], relics={})
    assert found.damage > blind.damage * 2


def test_only_the_strongest_vulnerability_counts():
    """Two matching vulnerabilities must not multiply into nonsense."""
    other = {**VAIN, "id": "v2", "name": "Also Vain", "mult": 1.5}
    ch = combat.resolve(verdict=verdict(6, flatter=2), played=[card("flatter")],
                        traits=[VAIN, other], relics={})
    assert ch.mult == pytest.approx(1.45 * 2.0)


def test_pierce_skips_resistance():
    played = [card("hard-pivot"), card("stakes")]
    ch = combat.resolve(verdict=verdict(6, **{"hard-pivot": 2, "stakes": 2}), played=played,
                        traits=[STUBBORN], relics={}, pierce=True)
    assert [t["hit"] for t in ch.hit_traits] == ["pierced"]
    assert 0.5 not in [s.value for s in ch.steps]


def test_flags_punish_repetition_hardest():
    clean = combat.resolve(verdict=verdict(6, press=2), played=[card("press")],
                          traits=[], relics={})
    repeated = combat.resolve(
        verdict={"persuasion": 6, "exec": {"press": 2}, "flags": ["repeat"], "tags": []},
        played=[card("press")], traits=[], relics={})
    assert repeated.damage < clean.damage * 0.3


def test_backlash_only_fires_on_a_botched_risky_card():
    played = [card("bluff")]
    assert combat.resolve(verdict=verdict(6, bluff=2), played=played, traits=[],
                          relics={}).backlash == 0
    assert combat.resolve(verdict=verdict(3, bluff=0), played=[card("bluff")], traits=[],
                          relics={}).backlash == 28


def test_brass_nerve_lifts_a_fumble_to_weak():
    eff = relicmod.effects(["brass-nerve"])
    ch = combat.resolve(verdict=verdict(5, flatter=0), played=[card("flatter")],
                        traits=[], relics=eff)
    assert ch.fumbles == 0 and ch.base > 55


def test_the_original_makes_the_first_card_unfumbleable():
    eff = relicmod.effects(["the-original"])
    ch = combat.resolve(verdict=verdict(5, flatter=0, press=0),
                        played=[card("flatter"), card("press")], traits=[], relics=eff)
    assert ch.fumbles == 1, "only the first card is protected"


def test_chain_scales_with_the_rest_of_the_message():
    played = [card("chain"), card("press"), card("flatter")]
    grades = {"chain": 2, "press": 2, "flatter": 2}
    ch = combat.resolve(verdict=verdict(6, **grades), played=played, traits=[], relics={})
    link = next(s for s in ch.steps if "link" in s.label)
    assert link.value == pytest.approx(1.6)


def test_echo_copies_the_strongest_multiplier():
    played = [card("the-turn"), card("keystone")]
    ch = combat.resolve(verdict=verdict(6, **{"the-turn": 2, "keystone": 2}),
                        played=played, traits=[], relics={})
    echo = next(s for s in ch.steps if "echo" in s.label)
    assert echo.value == pytest.approx(2.8)


def test_adaptive_trait_resists_what_you_used_last():
    adaptive = {"id": "a", "name": "Adaptive", "kind": "adaptive", "tags": [], "mult": 0.45}
    played = [card("flatter")]
    hot = combat.resolve(verdict=verdict(6, flatter=2), played=played, traits=[adaptive],
                         relics={}, used_tags={"flatter"})
    cold = combat.resolve(verdict=verdict(6, flatter=2), played=[card("flatter")],
                          traits=[adaptive], relics={}, used_tags={"logic"})
    assert hot.damage < cold.damage


def test_novelty_trait_rewards_an_unused_tag():
    novelty = {"id": "n", "name": "Novelty", "kind": "vuln", "tags": [], "mult": 2.0}
    fresh = combat.resolve(verdict=verdict(6, flatter=2), played=[card("flatter")],
                           traits=[novelty], relics={}, used_tags=set())
    stale = combat.resolve(verdict=verdict(6, flatter=2), played=[card("flatter")],
                           traits=[novelty], relics={}, used_tags={"flatter", "social"})
    assert fresh.damage > stale.damage


def test_every_step_carries_a_replayable_running_total():
    played = [card("flatter"), card("press")]
    ch = combat.resolve(verdict=verdict(8, flatter=3, press=2), played=played,
                        traits=[VAIN], relics=relicmod.effects(["worn-ledger"]))
    assert ch.steps[0].op == "set"
    base, mult = 0.0, 1.0
    for step in ch.steps:
        if step.op == "set":
            base = step.value
        elif step.op == "add":
            base += step.value
        else:
            mult *= step.value
        assert step.base_after == pytest.approx(base)
        assert step.mult_after == pytest.approx(mult)
    assert ch.damage == round(base * mult)


def test_no_step_is_a_pointless_multiply_by_one():
    ch = combat.resolve(verdict=verdict(6, press=2), played=[card("press")],
                        traits=[], relics={})
    assert all(not (s.op == "mul" and s.value == 1.0) for s in ch.steps)


def test_momentum_climbs_on_good_turns_and_collapses_on_bad():
    ch = combat.resolve(verdict=verdict(7, press=2), played=[card("press")], traits=[], relics={})
    assert combat.momentum_after(2, ch, 7) == 3
    assert combat.momentum_after(4, ch, 2) == 0
    assert combat.momentum_after(combat.MOMENTUM_CAP, ch, 9) == combat.MOMENTUM_CAP


def test_floor_points_reward_efficiency_and_double_bosses():
    tight = combat.floor_points(4, turns_spare=0, crits=0, boss=False)
    fast = combat.floor_points(4, turns_spare=3, crits=2, boss=False)
    boss = combat.floor_points(4, turns_spare=0, crits=0, boss=True)
    assert fast > tight and boss == tight * 2


def test_the_hollow_crown_trade_is_real():
    """×1.5 on everything, one fewer turn - both halves must actually apply."""
    eff = relicmod.effects(["hollow-crown"])
    plain = combat.resolve(verdict=verdict(6, press=2), played=[card("press")], traits=[], relics={})
    crowned = combat.resolve(verdict=verdict(6, press=2), played=[card("press")],
                             traits=[], relics=eff)
    assert crowned.damage == pytest.approx(plain.damage * 1.5, rel=0.02)
    assert eff["turns"] == -1
