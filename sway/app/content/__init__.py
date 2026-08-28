"""Content packs, parsed once at import and never mutated.

Cards, relics and minds live in JSON so the game can be rebalanced or extended
without touching engine code, and so a reviewer can read the whole design in
three files.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def _pack(name: str) -> dict:
    return json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))


# --- cards ----------------------------------------------------------------
@lru_cache(maxsize=None)
def _card_index() -> dict[str, dict]:
    p = _pack("cards")
    out: dict[str, dict] = {}
    for c in p["cards"]:
        out[c["id"]] = {**c, "kind": "tactic"}
    for c in p["wards"]:
        out[c["id"]] = {**c, "kind": "ward"}
    return out


def card(card_id: str) -> dict | None:
    got = _card_index().get(card_id)
    return copy.deepcopy(got) if got else None


def all_cards(kind: str = "tactic") -> list[dict]:
    return [copy.deepcopy(c) for c in _card_index().values() if c["kind"] == kind]


def cards_by_rarity(rarity: str, kind: str = "tactic") -> list[dict]:
    return [c for c in all_cards(kind) if c["rarity"] == rarity]


# --- relics ---------------------------------------------------------------
@lru_cache(maxsize=None)
def _relic_index() -> dict[str, dict]:
    return {r["id"]: r for r in _pack("relics")["relics"]}


def relic(relic_id: str) -> dict | None:
    got = _relic_index().get(relic_id)
    return copy.deepcopy(got) if got else None


def all_relics() -> list[dict]:
    return [copy.deepcopy(r) for r in _relic_index().values()]


# --- minds ----------------------------------------------------------------
@lru_cache(maxsize=None)
def _mind_index() -> dict[str, dict]:
    p = _pack("minds")
    out = {m["id"]: {**m, "kind": "mind"} for m in p["minds"]}
    out.update({m["id"]: {**m, "kind": "holdout"} for m in p["holdouts"]})
    return out


def mind(mind_id: str) -> dict | None:
    got = _mind_index().get(mind_id)
    return copy.deepcopy(got) if got else None


def all_minds(kind: str = "mind") -> list[dict]:
    return [copy.deepcopy(m) for m in _mind_index().values() if m["kind"] == kind]


def minds_by_tier(tier: int, kind: str = "mind") -> list[dict]:
    return [m for m in all_minds(kind) if m["tier"] == tier and not m.get("boss")]


def bosses() -> list[dict]:
    return [m for m in all_minds("mind") if m.get("boss")]
