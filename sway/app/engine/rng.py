"""Deterministic content selection.

The daily run must hand every player identical minds, identical card offers and
identical reward rolls, and tests need reproducible encounters - so nothing in
the engine touches the global RNG.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import random
import secrets
from typing import Sequence, TypeVar

T = TypeVar("T")


def daily_key(now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%d")


def fresh_seed() -> str:
    return secrets.token_hex(8)


def rng(seed: str) -> random.Random:
    return random.Random(hashlib.sha256(str(seed).encode()).hexdigest())


def pick(seed: str, items: Sequence[T]) -> T:
    return rng(seed).choice(list(items))


def sample(seed: str, items: Sequence[T], k: int) -> list[T]:
    pool = list(items)
    return rng(seed).sample(pool, min(k, len(pool)))


def shuffled(seed: str, items: Sequence[T]) -> list[T]:
    pool = list(items)
    rng(seed).shuffle(pool)
    return pool


def weighted(seed: str, items: Sequence[T], weights: Sequence[float], k: int = 1) -> list[T]:
    """Sample `k` distinct items with the given weights."""
    pool, wts = list(items), list(weights)
    r = rng(seed)
    out: list[T] = []
    for _ in range(min(k, len(pool))):
        total = sum(wts)
        if total <= 0:
            out.append(pool.pop(0))
            wts.pop(0)
            continue
        roll = r.random() * total
        acc = 0.0
        for i, w in enumerate(wts):
            acc += w
            if roll <= acc:
                out.append(pool.pop(i))
                wts.pop(i)
                break
        else:
            out.append(pool.pop())
            wts.pop()
    return out
