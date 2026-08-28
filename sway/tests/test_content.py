"""Content is data, so the invariants engine code assumes are asserted here."""
import pytest

from app.ai import prompts
from app.content import all_cards, all_minds, all_relics, bosses, mind, minds_by_tier
from app.engine import meta, relics as relicmod

CARD_KEYS = {"id", "name", "glyph", "rarity", "cost", "power", "mult", "tags", "rule",
             "contract", "flavor", "kind"}
RARITIES = {"starter", "common", "uncommon", "rare", "legendary"}


@pytest.mark.parametrize("kind", ["tactic", "ward"])
def test_cards_are_well_formed(kind):
    cards = all_cards(kind)
    assert cards
    seen = set()
    for c in cards:
        assert CARD_KEYS <= set(c), f"{c['id']} missing {CARD_KEYS - set(c)}"
        assert c["id"] not in seen, f"duplicate card id {c['id']}"
        seen.add(c["id"])
        assert c["rarity"] in RARITIES
        assert 0 <= c["cost"] <= 3
        assert c["power"] >= 0 and c["mult"] >= 1.0
        assert c["tags"], f"{c['id']} has no tags, so no trait can ever match it"
        assert set(c["tags"]) <= set(prompts.TAGS), f"{c['id']} uses unknown tags"
        assert len(c["contract"]) > 40, f"{c['id']} contract is too vague to grade"


def test_cards_have_a_reason_to_exist():
    """A card with no power, no multiplier and no effect does nothing."""
    for c in all_cards("tactic") + all_cards("ward"):
        assert c["power"] or c["mult"] > 1.0 or c.get("effect"), f"{c['id']} is inert"


def test_relic_effects_are_all_understood():
    known = relicmod._ADDITIVE | relicmod._MAX | relicmod._FLAGS | relicmod._TAGS
    for r in all_relics():
        assert r["effect"], f"{r['id']} has no effect"
        unknown = set(r["effect"]) - known
        assert not unknown, f"{r['id']} declares effects nothing reads: {unknown}"


def test_minds_are_well_formed():
    for m in all_minds("mind"):
        for key in ("name", "title", "sigil", "tier", "objective", "persona", "guard",
                    "opening", "traits", "scene", "concede"):
            assert m.get(key), f"{m['id']} missing {key}"
        assert 1 <= m["tier"] <= 5
        assert len(m["persona"]) > 200, f"{m['id']} persona is too thin to act"
        kinds = {t.get("kind") for t in m["traits"]}
        assert "vuln" in kinds or "adaptive" in kinds, f"{m['id']} has no way in"
        for t in m["traits"]:
            assert t["id"] and t["name"] and t.get("tell")
            assert set(t.get("tags") or []) <= set(prompts.TAGS)
            if t["kind"] == "vuln":
                assert t["mult"] > 1.0
            elif t["kind"] == "resist":
                assert t["mult"] < 1.0


def test_holdout_minds_declare_what_they_want():
    holdouts = all_minds("holdout")
    assert len(holdouts) >= 3
    for m in holdouts:
        assert m["demand"] and m["secret"] and m["persona"] and m["opening"]


def test_every_ladder_tier_is_populated():
    for tier in (1, 2, 3, 4):
        assert len(minds_by_tier(tier)) >= 2, f"tier {tier} cannot avoid repeats"
    assert len(bosses()) >= 3


def test_boss_phases_reference_real_traits():
    for b in bosses():
        trait_ids = {t["id"] for t in b["traits"]}
        for phase in b.get("phases") or []:
            assert set(phase["traits"]) <= trait_ids
            assert phase["line"]
        phases = b.get("phases") or []
        ats = [p["at"] for p in phases]
        assert ats == sorted(ats, reverse=True), "phases must descend"


def test_unlock_ladder_covers_exactly_the_gated_content():
    ids = {c["id"] for c in all_cards("tactic")} | {r["id"] for r in all_relics()}
    assert set(meta.GATED) <= ids, "unlock ladder names content that does not exist"
    should_gate = {c["id"] for c in all_cards("tactic") if c["rarity"] not in ("starter", "common")}
    should_gate |= {r["id"] for r in all_relics() if r["rarity"] != "common"}
    assert should_gate == set(meta.GATED)


def test_starter_deck_is_all_starter_cards():
    from app.engine.run import STARTER_DECK, WARD_DECK

    starters = {c["id"] for c in all_cards("tactic") if c["rarity"] == "starter"}
    assert set(STARTER_DECK) <= starters
    assert set(WARD_DECK) <= {c["id"] for c in all_cards("ward")}
    assert len(STARTER_DECK) >= 8 and len(WARD_DECK) >= 8


def test_gate_minds_exist():
    for gid in ("interviewer", "closer", "old-friend"):
        assert mind(gid), f"{gid} is referenced by the gate ladder"
