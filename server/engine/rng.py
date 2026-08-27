"""Deterministic content selection.

Daily challenges must hand every player the identical puzzle, and tests need
reproducible cases, so all content picking goes through a seeded Random built
from a string seed rather than the global RNG.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import secrets
from typing import Sequence, TypeVar

T = TypeVar("T")


def daily_key(now: dt.datetime | None = None) -> str:
    """UTC day stamp used to seed and scope the daily challenge."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%d")


def daily_seed(game: str, key: str | None = None) -> str:
    return f"daily:{key or daily_key()}:{game}"


def fresh_seed() -> str:
    return secrets.token_hex(8)


def rng(seed: str) -> random.Random:
    return random.Random(hashlib.sha256(seed.encode()).hexdigest())


def pick(seed: str, items: Sequence[T]) -> T:
    return rng(seed).choice(list(items))


def sample(seed: str, items: Sequence[T], k: int) -> list[T]:
    pool = list(items)
    k = min(k, len(pool))
    return rng(seed).sample(pool, k)


def shuffled(seed: str, items: Sequence[T]) -> list[T]:
    pool = list(items)
    rng(seed).shuffle(pool)
    return pool
