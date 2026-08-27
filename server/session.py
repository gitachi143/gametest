"""Session orchestration: runs a game turn, streams it, then applies the meta layer.

Games know nothing about players, XP or leaderboards. This module owns that
seam: it forwards an action to the game, relays the event stream to the client,
and when a game ends it records the score, awards XP, extends the streak and
evaluates achievements - emitting those as extra events on the same stream.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import AsyncIterator

from . import meta
from .config import settings
from .engine.rng import daily_key
from .engine.types import Ev
from .games import get_game
from .llm import usage
from .store import Store

log = logging.getLogger("arcade.session")

_locks: dict[str, asyncio.Lock] = {}


def lock_for(sid: str) -> asyncio.Lock:
    lock = _locks.get(sid)
    if lock is None:
        lock = _locks[sid] = asyncio.Lock()
    return lock


def release_lock(sid: str) -> None:
    """Drop a finished session's lock so a long-lived process doesn't accumulate
    one per session ever played. The caller still holds its own reference."""
    _locks.pop(sid, None)


def sse(ev: Ev) -> str:
    return f"event: {ev.event}\ndata: {json.dumps(ev.data, ensure_ascii=False)}\n\n"


def xp_for(result: dict) -> int:
    base = max(8, int(result.get("score", 0)) // 12)
    if result.get("outcome") == "win":
        base += 30
    return min(600, base)


async def run_action(
    store: Store, player: dict, session: dict, action: dict
) -> AsyncIterator[str]:
    """Yields SSE frames for one player action."""
    game = get_game(session["game"])
    if game is None:
        yield sse(Ev("error", {"message": "Unknown game."}))
        return

    pid = player["id"]
    if settings.daily_call_budget and not store.bump_calls(pid, daily_key(), settings.daily_call_budget):
        yield sse(Ev("error", {
            "message": "Daily model budget reached for this player. It resets at midnight UTC.",
        }))
        return

    state = session["state"]
    calls_before = usage.calls
    ended: dict | None = None

    try:
        async for ev in game.act(state, action):
            if ev.event == "end":
                ended = ev.data.get("result")
                continue  # held back until the meta layer has enriched it
            yield sse(ev)
    except Exception as exc:  # noqa: BLE001 - never leave the client hanging
        log.exception("game turn failed game=%s session=%s", session["game"], session["id"])
        yield sse(Ev("error", {"message": f"Turn failed: {exc}"}))
    finally:
        # Charge the real number of model calls this turn made, less the one
        # already counted above.
        extra = max(0, usage.calls - calls_before - 1)
        for _ in range(extra):
            store.bump_calls(pid, daily_key(), settings.daily_call_budget)
        store.save_session(session["id"], state, "done" if ended else "active")

    if ended is None:
        return

    for frame in _apply_meta(store, player, session, ended):
        yield frame


def _apply_meta(store: Store, player: dict, session: dict, result: dict) -> list[str]:
    """Score, XP, streak and achievements - emitted as trailing SSE frames."""
    frames: list[str] = []
    pid = player["id"]
    game_id = session["game"]
    dk = session.get("daily_key") or ""

    gained_xp = xp_for(result)
    result["xp"] = gained_xp

    store.record_score(
        pid, game_id, session.get("mode") or "", int(result.get("score", 0)),
        result.get("outcome", ""), result.get("stats") or {}, dk,
    )
    total_xp = store.add_xp(pid, gained_xp)

    fresh = store.get_player(pid) or player
    streak, extended = meta.update_streak(fresh)
    if extended:
        store.update_player(
            pid, streak=streak, streak_day=daily_key(),
            best_streak=max(int(fresh.get("best_streak") or 0), streak),
        )

    scores = store.player_scores(pid)
    lifetime = sum(int(s["score"]) for s in scores)
    losses_before = 0
    for s in scores[1:]:  # scores[0] is the run we just recorded
        if s["outcome"] == "win":
            break
        losses_before += 1

    info = meta.level_info(total_xp)
    ctx = {
        "game": game_id,
        "mode": session.get("mode") or "",
        "result": result,
        "streak": streak,
        "level": info["level"],
        "lifetime_points": lifetime,
        "games_played": [s["game"] for s in scores],
        "daily": bool(dk),
        "losses_before": losses_before,
        "local_hour": dt.datetime.now().hour,
    }
    unlocked = store.unlocked(pid)
    new_badges = []
    for ach in meta.evaluate(ctx, unlocked):
        if store.unlock(pid, ach.code):
            new_badges.append({"code": ach.code, "name": ach.name, "desc": ach.desc, "icon": ach.icon})

    frames.append(sse(Ev("profile", {
        "xp_gained": gained_xp,
        "level": info,
        "streak": streak,
        "streak_extended": extended,
        "lifetime_points": lifetime,
    })))
    for badge in new_badges:
        frames.append(sse(Ev("unlock", badge)))
    result["badges"] = new_badges
    frames.append(sse(Ev("end", {"result": result})))
    return frames


def daily_plan(key: str | None = None) -> dict:
    """Today's challenge: a fixed game/mode pair derived from the date.

    Everybody gets the same puzzle, one attempt, and the seed is the date - so
    two players can compare share cards without trusting each other.
    """
    key = key or daily_key()
    from .engine.rng import rng as make_rng

    rotation = [
        ("vault", "run"), ("coldcase", "standard"), ("hotwire", "sprint"),
        ("sleeper", "standard"), ("oracle", "interrogate"), ("crossfire", "defend"),
        ("gauntlet", "standard"),
    ]
    day = dt.datetime.strptime(key, "%Y-%m-%d").date()
    idx = day.toordinal() % len(rotation)
    game, mode = rotation[idx]
    r = make_rng(f"daily:{key}")
    tier = r.choice([1, 2, 2, 3])
    return {
        "key": key,
        "game": game,
        "mode": mode,
        "seed": f"daily:{key}:{game}",
        "opts": {"tier": tier},
        "expires": (day + dt.timedelta(days=1)).isoformat() + "T00:00:00Z",
    }
