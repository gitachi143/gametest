"""Draw pile / hand / discard, stored as plain lists of card ids.

The whole encounter lives in a JSON column, so nothing here holds objects: the
deck is three lists and a seed, and every shuffle is derived from that seed plus
a counter so a restored session deals exactly the cards it would have dealt.
"""
from __future__ import annotations

from ..content import card as get_card
from . import rng

HAND_SIZE = 5


def new(seed: str, card_ids: list[str], hand_size: int = HAND_SIZE) -> dict:
    return {
        "seed": seed,
        "shuffles": 0,
        "draw": rng.shuffled(f"{seed}:0", card_ids),
        "hand": [],
        "discard": [],
        "exhausted": [],
        "hand_size": hand_size,
    }


def _reshuffle(deck: dict) -> None:
    deck["shuffles"] += 1
    deck["draw"] = rng.shuffled(f"{deck['seed']}:{deck['shuffles']}", deck["discard"])
    deck["discard"] = []


def draw(deck: dict, n: int) -> list[str]:
    drawn: list[str] = []
    for _ in range(n):
        if not deck["draw"]:
            if not deck["discard"]:
                break               # deck is genuinely empty; play on with what you hold
            _reshuffle(deck)
        drawn.append(deck["draw"].pop(0))
    deck["hand"].extend(drawn)
    return drawn


def refill(deck: dict) -> list[str]:
    missing = max(0, deck["hand_size"] - len(deck["hand"]))
    return draw(deck, missing)


def discard_played(deck: dict, card_ids: list[str]) -> None:
    for cid in card_ids:
        if cid in deck["hand"]:
            deck["hand"].remove(cid)
            deck["discard"].append(cid)


def end_turn(deck: dict, keep: bool = False) -> None:
    """Hands do not persist between turns: an unplayed card is a choice not
    taken, not a card banked for later."""
    if not keep:
        deck["discard"].extend(deck["hand"])
        deck["hand"] = []
    refill(deck)


def hand_cards(deck: dict) -> list[dict]:
    """Hydrated hand. Duplicate ids are given distinct slot keys so the client
    can select 'the second Press' without ambiguity."""
    out = []
    for i, cid in enumerate(deck["hand"]):
        c = get_card(cid)
        if c:
            out.append({**c, "slot": i})
    return out


def counts(deck: dict) -> dict:
    return {"draw": len(deck["draw"]), "discard": len(deck["discard"]), "hand": len(deck["hand"])}
