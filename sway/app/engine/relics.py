"""Relic bookkeeping.

Relics are stored on a run as a list of ids; combat wants one flat dict. Numeric
effects accumulate, booleans latch on, and `turns` may be negative (the Hollow
Crown trades a turn for a multiplier), so nothing here clamps.
"""
from __future__ import annotations

from ..content import relic as get_relic

_ADDITIVE = {
    "focus", "hand", "power_per_card", "turns", "revive", "reroll",
    "crit_bonus", "fumble_penalty", "momentum_start", "score_compound",
}
_MAX = {"first_message_mult", "vuln_mult", "trio_mult", "base_mult", "exec_floor"}
_FLAGS = {"hide_tactics", "reveal_all", "first_card_free"}
_TAGS = {"cost_free_tag"}


def effects(relic_ids: list[str]) -> dict:
    out: dict = {}
    for rid in relic_ids or []:
        r = get_relic(rid)
        if not r:
            continue
        for key, value in (r.get("effect") or {}).items():
            if key in _ADDITIVE:
                out[key] = out.get(key, 0) + value
            elif key in _MAX:
                out[key] = max(out.get(key, 0), value)
            elif key in _FLAGS:
                out[key] = bool(value) or bool(out.get(key))
            elif key in _TAGS:
                out.setdefault("free_tags", set())
                out["free_tags"].add(value)
    if "free_tags" in out:
        out["free_tags"] = sorted(out["free_tags"])
    return out


def hydrate(relic_ids: list[str]) -> list[dict]:
    return [r for r in (get_relic(rid) for rid in relic_ids or []) if r]


def cost_of(card: dict, eff: dict) -> int:
    """A relic can zero out a whole family of cards."""
    free = set(eff.get("free_tags") or [])
    if free & set(card.get("tags") or []):
        return 0
    return int(card.get("cost", 0))
