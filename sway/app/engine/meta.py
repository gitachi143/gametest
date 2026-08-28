"""Progression: XP, levels, the unlock ladder, achievements.

Unlocks are *derived from level*, not stored. That means the collection screen
can always answer "how do I get this" with a number, and a wiped database costs
a player their level rather than a mystery set of missing cards.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

from .rng import daily_key

LEVEL_TITLES = (
    "Mark", "Talker", "Hustler", "Closer", "Operator", "Confidant",
    "Persuader", "Silvertongue", "Architect of Small Lies", "Kingmaker",
    "The Voice", "Nobody's Fool",
)


def xp_for_level(level: int) -> int:
    if level <= 1:
        return 0
    return int(100 * (level - 1) ** 1.5)


def level_for_xp(xp: int) -> int:
    level = 1
    while level < 99 and xp >= xp_for_level(level + 1):
        level += 1
    return level


def level_info(xp: int) -> dict:
    level = level_for_xp(xp)
    floor_, ceil_ = xp_for_level(level), xp_for_level(level + 1)
    span = max(1, ceil_ - floor_)
    return {
        "level": level,
        "title": LEVEL_TITLES[min(level - 1, len(LEVEL_TITLES) - 1)],
        "xp": xp,
        "floor": floor_,
        "ceil": ceil_,
        "progress": round(min(1.0, (xp - floor_) / span), 4),
        "to_next": max(0, ceil_ - xp),
    }


def xp_for_run(result: dict) -> int:
    stats = result.get("stats") or {}
    base = 40 + int(result.get("score", 0)) // 22
    base += 22 * int(stats.get("cleared", 0))
    base += 6 * int(stats.get("crits", 0))
    if result.get("outcome") == "win":
        base += 90
    if stats.get("daily"):
        base = int(base * 1.25)
    return max(25, min(900, base))


# ------------------------------------------------------------- unlock ladder
UNLOCKS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (2, ("name-the-fear", "second-wind")),
    (3, ("socratic", "silver-thread")),
    (4, ("cold-read", "the-mask")),
    (5, ("bluff", "keystone")),
    (6, ("empathy-lock", "long-lunch")),
    (7, ("chain", "dossier")),
    (8, ("false-urgency", "glass-tongue")),
    (9, ("precedent", "metronome")),
    (10, ("shared-enemy", "three-card-monte")),
    (11, ("specificity", "understanding")),
    (12, ("long-con", "patron")),
    (13, ("reframe", "hollow-crown")),
    (14, ("confession", "the-original")),
    (15, ("double-bind",)),
    (16, ("the-turn",)),
    (17, ("ghost-in-the-room",)),
    (18, ("perfect-word",)),
    (19, ("understudy",)),
)

GATED: dict[str, int] = {item: level for level, items in UNLOCKS for item in items}


def unlocked_for_level(level: int) -> set[str]:
    return {item for lvl, items in UNLOCKS for item in items if level >= lvl}


def is_available(item_id: str, unlocked: set[str]) -> bool:
    required = GATED.get(item_id)
    return required is None or item_id in unlocked


def unlock_level(item_id: str) -> int:
    return GATED.get(item_id, 1)


def newly_unlocked(before: int, after: int) -> list[str]:
    return [item for lvl, items in UNLOCKS if before < lvl <= after for item in items]


# ------------------------------------------------------------------ streaks
def update_streak(player: dict) -> tuple[int, bool]:
    today = daily_key()
    last = player.get("streak_day") or ""
    streak = int(player.get("streak") or 0)
    if last == today:
        return max(1, streak), False
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    return (streak + 1 if last == yesterday else 1), True


# ------------------------------------------------------------- achievements
@dataclass(frozen=True)
class Badge:
    code: str
    name: str
    desc: str
    glyph: str
    check: Callable[[dict], bool]
    secret: bool = False


def _s(ctx: dict, key: str, default=0):
    return (ctx.get("result", {}).get("stats") or {}).get(key, default)


def _won(ctx: dict) -> bool:
    return ctx.get("result", {}).get("outcome") == "win"


def _mode(ctx: dict, name: str) -> bool:
    return _s(ctx, "mode", "") == name


BADGES: tuple[Badge, ...] = (
    Badge("first-word", "First Word", "Finish your first run.", "❖", lambda c: True),
    Badge("first-floor", "Through The Door", "Clear a floor.", "⌸", lambda c: _s(c, "cleared") >= 1),
    Badge("ascent", "The Ninth Door", "Complete a full Ascent.", "◈",
          lambda c: _mode(c, "ascent") and _won(c)),
    Badge("blitz-win", "Three Lines", "Win a Blitz.", "⚡",
          lambda c: _mode(c, "blitz") and _won(c)),
    Badge("holdout-win", "Gave Them Nothing", "Survive a full Holdout.", "▮",
          lambda c: _mode(c, "holdout") and _won(c)),
    Badge("endless-10", "Still Climbing", "Reach floor 10 in Endless.", "∞",
          lambda c: _mode(c, "endless") and _s(c, "floor") >= 10),
    Badge("endless-15", "Thin Air", "Reach floor 15 in Endless.", "☁",
          lambda c: _mode(c, "endless") and _s(c, "floor") >= 15),
    Badge("daily-first", "Everyone's Puzzle", "Play a Daily.", "◉",
          lambda c: bool(_s(c, "daily"))),
    Badge("no-fumble", "Clean Hands", "Clear 4+ floors without fumbling a card.", "⌇",
          lambda c: _s(c, "cleared") >= 4 and _s(c, "fumbles") == 0),
    Badge("crit-10", "Craft", "Land ten masterful cards in one run.", "✦",
          lambda c: _s(c, "crits") >= 10),
    Badge("big-hit", "One Sentence", "Land a single hit of 1,500 or more.", "↯",
          lambda c: _s(c, "best_hit") >= 1500),
    Badge("huge-hit", "Devastating", "Land a single hit of 4,000 or more.", "☄",
          lambda c: _s(c, "best_hit") >= 4000),
    Badge("relic-5", "Collector", "Hold five relics at once.", "♛",
          lambda c: _s(c, "relics") >= 5),
    Badge("score-5k", "Worth Listening To", "Score 5,000 in a run.", "◍",
          lambda c: c.get("result", {}).get("score", 0) >= 5000),
    Badge("score-15k", "Worth Fearing", "Score 15,000 in a run.", "♜",
          lambda c: c.get("result", {}).get("score", 0) >= 15000),
    Badge("streak-3", "Habit", "Three-day streak.", "⌗", lambda c: c.get("streak", 0) >= 3),
    Badge("streak-7", "Ritual", "Seven-day streak.", "⌘", lambda c: c.get("streak", 0) >= 7),
    Badge("streak-30", "Vocation", "Thirty-day streak.", "⧖", lambda c: c.get("streak", 0) >= 30),
    Badge("level-5", "Operator", "Reach level 5.", "◐", lambda c: c.get("level", 1) >= 5),
    Badge("level-10", "Kingmaker", "Reach level 10.", "◑", lambda c: c.get("level", 1) >= 10),
    Badge("all-modes", "Every Room", "Finish a run in all five modes.", "⁂",
          lambda c: len(set(c.get("modes_played") or [])) >= 5),
    Badge("architect", "Predicted Nothing", "Beat THE ARCHITECT.", "◈",
          lambda c: "architect" in (c.get("beaten") or []), True),
    Badge("nobody", "Gave Nobody A Want", "Beat NOBODY.", "○",
          lambda c: "nobody" in (c.get("beaten") or []), True),
    Badge("choir", "Unanimous", "Beat THE CHOIR.", "⁂",
          lambda c: "choir" in (c.get("beaten") or []), True),
    Badge("terse", "Economy", "Clear 3+ floors in under 200 words total.", "…",
          lambda c: _s(c, "cleared") >= 3 and 0 < _s(c, "words", 9999) < 200, True),
    Badge("small-hours", "Small Hours", "Play between 2am and 4am.", "☾",
          lambda c: 2 <= c.get("local_hour", 12) <= 4, True),
)

BY_CODE = {b.code: b for b in BADGES}


def badge_catalogue(unlocked: set[str]) -> list[dict]:
    out = []
    for b in BADGES:
        got = b.code in unlocked
        out.append({
            "code": b.code,
            "name": b.name if (got or not b.secret) else "???",
            "desc": b.desc if (got or not b.secret) else "Hidden.",
            "glyph": b.glyph if (got or not b.secret) else "·",
            "secret": b.secret,
            "unlocked": got,
        })
    return out


def evaluate(ctx: dict, already: set[str]) -> list[Badge]:
    fresh = []
    for b in BADGES:
        if b.code in already:
            continue
        try:
            if b.check(ctx):
                fresh.append(b)
        except Exception:      # a bad predicate must never break a run ending
            continue
    return fresh
