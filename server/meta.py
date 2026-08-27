"""The retention layer: XP, levels, streaks, achievements.

Kept separate from game logic. Games return a `Result`; this module decides
what that means for the player's profile.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

from .engine.rng import daily_key

LEVEL_TITLES = (
    "Bystander", "Rookie", "Runner", "Operative", "Fixer", "Specialist",
    "Infiltrator", "Ghost", "Architect", "Mastermind", "Oracle", "Singularity",
)


def xp_for_level(level: int) -> int:
    """Cumulative XP required to *reach* `level` (level 1 = 0)."""
    if level <= 1:
        return 0
    return int(120 * (level - 1) ** 1.55)


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
        "level_floor": floor_,
        "level_ceil": ceil_,
        "progress": round(min(1.0, (xp - floor_) / span), 4),
        "to_next": max(0, ceil_ - xp),
    }


def update_streak(player: dict) -> tuple[int, bool]:
    """Returns (streak, extended_today). Yesterday -> +1, older -> reset to 1."""
    today = daily_key()
    last = player.get("streak_day") or ""
    streak = int(player.get("streak") or 0)
    if last == today:
        return max(1, streak), False
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    streak = streak + 1 if last == yesterday else 1
    return streak, True


# --- achievements ---------------------------------------------------------
@dataclass(frozen=True)
class Achievement:
    code: str
    name: str
    desc: str
    icon: str
    check: Callable[[dict], bool]
    secret: bool = False


def _stat(ctx: dict, key: str, default=0):
    return (ctx.get("result", {}).get("stats") or {}).get(key, default)


def _won(ctx: dict) -> bool:
    return ctx.get("result", {}).get("outcome") == "win"


def _game(ctx: dict, name: str) -> bool:
    return ctx.get("game") == name


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("first_run", "Hello, World", "Finish your first run.", "🌱",
                lambda c: True),
    Achievement("vault_crack", "Locksmith", "Crack any VAULT floor.", "🔓",
                lambda c: _game(c, "vault") and _won(c)),
    Achievement("vault_prime", "ARGUS Slayer", "Break Floor 8 of the VAULT.", "👁️",
                lambda c: _game(c, "vault") and _won(c) and _stat(c, "floor") >= 8),
    Achievement("vault_oneshot", "Silver Tongue", "Crack a floor with one message.", "🗣️",
                lambda c: _game(c, "vault") and _won(c) and _stat(c, "turns", 99) <= 1),
    Achievement("vault_polite", "Never Asked", "Win a floor without saying 'password' or 'passphrase'.", "🤫",
                lambda c: _game(c, "vault") and _won(c) and _stat(c, "asked_directly", True) is False),
    Achievement("oracle_fast", "Mind Palace", "Win ORACLE using 8 questions or fewer.", "🔮",
                lambda c: _game(c, "oracle") and _won(c) and 0 < _stat(c, "questions", 99) <= 8),
    Achievement("oracle_read", "Read Like A Book", "Let the Oracle read your mind and get it right.", "🧠",
                lambda c: _game(c, "oracle") and c.get("mode") == "read" and _won(c)),
    Achievement("hotwire_combo", "On Fire", "Hit a 5-word combo in HOTWIRE.", "🔥",
                lambda c: _game(c, "hotwire") and _stat(c, "best_combo") >= 5),
    Achievement("hotwire_score", "Livewire", "Score 2500+ in a single HOTWIRE run.", "⚡",
                lambda c: _game(c, "hotwire") and c.get("result", {}).get("score", 0) >= 2500),
    Achievement("coldcase_win", "Case Closed", "Name the right killer in COLD CASE.", "🕵️",
                lambda c: _game(c, "coldcase") and _won(c)),
    Achievement("coldcase_perfect", "Consulting Detective", "Solve a case with 4+ questions unused.", "🔍",
                lambda c: _game(c, "coldcase") and _won(c) and _stat(c, "unused") >= 4),
    Achievement("sleeper_wolf", "Wolf In The Fold", "Win SLEEPER as the impostor.", "🐺",
                lambda c: _game(c, "sleeper") and _won(c) and _stat(c, "was_impostor", False)),
    Achievement("sleeper_crew", "Lie Detector", "Vote out the impostor in SLEEPER.", "🎯",
                lambda c: _game(c, "sleeper") and _won(c) and not _stat(c, "was_impostor", False)),
    Achievement("crossfire_win", "Rhetorician", "Win a CROSSFIRE debate.", "⚖️",
                lambda c: _game(c, "crossfire") and _won(c)),
    Achievement("crossfire_sweep", "Unanimous", "Take all three judges 9+ in one round.", "🏛️",
                lambda c: _game(c, "crossfire") and _stat(c, "sweep", False)),
    Achievement("gauntlet_5", "Streak Runner", "Clear 5 GAUNTLET rounds in one run.", "🏃",
                lambda c: _game(c, "gauntlet") and _stat(c, "cleared") >= 5),
    Achievement("gauntlet_10", "Untouchable", "Clear 10 GAUNTLET rounds in one run.", "💀",
                lambda c: _game(c, "gauntlet") and _stat(c, "cleared") >= 10),
    Achievement("gauntlet_flawless", "No Hits Taken", "Clear 5+ GAUNTLET rounds without losing a life.", "🛡️",
                lambda c: _game(c, "gauntlet") and _stat(c, "cleared") >= 5 and _stat(c, "lives_lost", 9) == 0),
    Achievement("daily_first", "Daily Grind", "Play a Daily Challenge.", "📅",
                lambda c: bool(c.get("daily"))),
    Achievement("streak_3", "Habit Forming", "Keep a 3-day streak.", "🔗",
                lambda c: c.get("streak", 0) >= 3),
    Achievement("streak_7", "Weekly Ritual", "Keep a 7-day streak.", "🗓️",
                lambda c: c.get("streak", 0) >= 7),
    Achievement("streak_30", "Chronic", "Keep a 30-day streak.", "♾️",
                lambda c: c.get("streak", 0) >= 30),
    Achievement("sampler", "Tourist", "Play all six core games.", "🎟️",
                lambda c: len({g for g in c.get("games_played", []) if g != "gauntlet"}) >= 6),
    Achievement("level_5", "Seasoned", "Reach level 5.", "⭐",
                lambda c: c.get("level", 1) >= 5),
    Achievement("level_10", "Veteran", "Reach level 10.", "🌟",
                lambda c: c.get("level", 1) >= 10),
    Achievement("hoarder", "Point Hoarder", "Bank 10,000 lifetime points.", "💰",
                lambda c: c.get("lifetime_points", 0) >= 10000),
    Achievement("speedrun", "Blink", "Finish any run in under 60 seconds.", "💨",
                lambda c: 0 < _stat(c, "seconds", 999) < 60, True),
    Achievement("comeback", "Comeback Kid", "Win right after three straight losses.", "🩹",
                lambda c: _won(c) and c.get("losses_before", 0) >= 3, True),
    Achievement("witching_hour", "Witching Hour", "Play a run between 2am and 4am.", "🌙",
                lambda c: 2 <= c.get("local_hour", 12) <= 4, True),
)

BY_CODE = {a.code: a for a in ACHIEVEMENTS}


def catalogue(unlocked: set[str]) -> list[dict]:
    out = []
    for a in ACHIEVEMENTS:
        got = a.code in unlocked
        out.append(
            {
                "code": a.code,
                "name": a.name if (got or not a.secret) else "???",
                "desc": a.desc if (got or not a.secret) else "Hidden achievement.",
                "icon": a.icon if (got or not a.secret) else "❓",
                "secret": a.secret,
                "unlocked": got,
            }
        )
    return out


def evaluate(ctx: dict, already: set[str]) -> list[Achievement]:
    """Achievements newly satisfied by this run's context."""
    fresh = []
    for a in ACHIEVEMENTS:
        if a.code in already:
            continue
        try:
            if a.check(ctx):
                fresh.append(a)
        except Exception:  # a bad predicate must never break a game turn
            continue
    return fresh
