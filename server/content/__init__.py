"""Static content packs, loaded once at import.

Content lives in JSON so it can be edited (or regenerated) without touching
game logic, and so the packs are trivially inspectable in review.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def pack(name: str) -> dict:
    return json.loads((DIR / f"{name}.json").read_text(encoding="utf-8"))


def hotwire_targets(tier: int | None = None) -> list[dict]:
    items = pack("hotwire")["targets"]
    return [t for t in items if tier is None or t["tier"] == tier]


def oracle_secrets(tier: int | None = None) -> list[dict]:
    items = pack("oracle")["secrets"]
    return [s for s in items if tier is None or s["tier"] == tier]


def sleeper_rounds() -> list[dict]:
    return pack("sleeper")["rounds"]


def sleeper_personas() -> list[dict]:
    return pack("sleeper")["personas"]


def crossfire_topics(tier: int | None = None) -> list[dict]:
    items = pack("crossfire")["topics"]
    return [t for t in items if tier is None or t["tier"] == tier]


def crossfire_judges() -> list[dict]:
    return pack("crossfire")["judges"]


def coldcase() -> dict:
    return pack("coldcase")


def vault_words() -> tuple[list[str], list[str]]:
    p = pack("vault")
    return p["adjectives"], p["nouns"]
