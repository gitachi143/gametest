"""Shared scoring maths, tuned so scores feel readable at a glance."""
from __future__ import annotations

RANKS = (
    (0, "Civilian"),
    (500, "Rookie"),
    (1500, "Operative"),
    (3500, "Specialist"),
    (7000, "Ghost"),
    (12000, "Mastermind"),
    (20000, "Legend"),
)


def rank_for(score: int) -> str:
    label = RANKS[0][1]
    for threshold, name in RANKS:
        if score >= threshold:
            label = name
    return label


def efficiency_bonus(base: int, used: int, allowed: int, *, floor: float = 0.25) -> int:
    """Reward finishing with turns to spare, never below `floor` of base."""
    if allowed <= 0:
        return base
    spare = max(0, allowed - used)
    factor = floor + (1.0 - floor) * (spare / allowed)
    return int(round(base * factor))


def streak_multiplier(streak: int) -> float:
    """1.0, 1.1, 1.2 ... capped at 2.0 so day-500 players aren't unreachable."""
    return min(2.0, 1.0 + 0.1 * max(0, streak))


def stars(score: int, thresholds: tuple[int, int, int]) -> int:
    return sum(1 for t in thresholds if score >= t)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
