"""Run orchestration: the seam between a game and a player.

The engine knows nothing about players, XP, leaderboards or budgets. This module
forwards an action into the run, relays its event stream to the browser, and when
a run ends it records the score, awards XP, extends the streak and evaluates
badges - emitting those as extra frames on the same stream so the result screen
arrives already knowing everything.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
from typing import AsyncIterator

from .ai.client import usage
from .config import settings
from .content import card as get_card, relic as get_relic
from .engine import encounter, meta, run as runmod
from .engine.rng import daily_key
from .engine.types import Ev
from .store import Store

log = logging.getLogger("sway.session")

_locks: dict[str, asyncio.Lock] = {}


def lock_for(rid: str) -> asyncio.Lock:
    lock = _locks.get(rid)
    if lock is None:
        lock = _locks[rid] = asyncio.Lock()
    return lock


def release_lock(rid: str) -> None:
    _locks.pop(rid, None)


def sse(ev: Ev) -> str:
    return f"event: {ev.event}\ndata: {json.dumps(ev.data, ensure_ascii=False)}\n\n"


def _describe(item_id: str) -> dict:
    """An unlock the player can read: name and glyph, not a slug."""
    found = get_card(item_id) or get_relic(item_id) or {}
    return {
        "id": item_id,
        "name": found.get("name") or item_id.replace("-", " ").title(),
        "glyph": found.get("glyph", "✦"),
        "kind": "relic" if get_relic(item_id) else "card",
    }


def daily_plan(key: str | None = None) -> dict:
    key = key or daily_key()
    day = dt.datetime.strptime(key, "%Y-%m-%d").date()
    return {
        "key": key,
        "mode": "daily",
        "seed": f"daily:{key}",
        "expires": (day + dt.timedelta(days=1)).isoformat() + "T00:00:00Z",
    }


# --------------------------------------------------------------------- turns
async def run_action(store: Store, player: dict, record: dict, action: dict) -> AsyncIterator[str]:
    state = record["state"]
    pid = player["id"]
    kind = str(action.get("type") or "")

    if kind in ("send", "size-up"):
        if settings.daily_call_budget and not store.bump_calls(pid, daily_key(), settings.daily_call_budget):
            yield sse(Ev("error", {
                "message": "Daily model budget reached for this player. Resets at midnight UTC.",
            }))
            return

    calls_before = usage.calls
    ended: dict | None = None
    try:
        if kind in ("take", "reroll", "continue"):
            async for frame in _meta_action(store, state, action):
                yield frame
        else:
            if state.get("phase") != "encounter" or not state.get("encounter"):
                yield sse(Ev("error", {"message": "No conversation in progress."}))
                return
            eff = runmod.effects(state)
            async for ev in encounter.act(state["encounter"], action, eff):
                if ev.event == "encounter_end":
                    ended = ev.data
                    continue
                yield sse(ev)
    except Exception as exc:                       # never leave the client hanging
        log.exception("turn failed run=%s action=%s", record["id"], kind)
        yield sse(Ev("error", {"message": f"Turn failed: {exc}"}))
    finally:
        # Charge every model call the turn actually made, less the one already
        # counted above.
        extra = max(0, usage.calls - calls_before - (1 if kind in ("send", "size-up") else 0))
        for _ in range(extra):
            store.bump_calls(pid, daily_key(), settings.daily_call_budget)
        store.save_run(record["id"], state, "active" if state.get("phase") != "done" else "done")

    if ended is None:
        return
    for frame in _close_floor(store, player, record, state, ended):
        yield frame


async def _meta_action(store: Store, state: dict, action: dict) -> AsyncIterator[str]:
    kind = action.get("type")
    if kind == "take":
        if state.get("phase") != "reward":
            yield sse(Ev("error", {"message": "Nothing to pick right now."}))
            return
        pick = runmod.take_offer(state, int(action.get("index", -1)))
        if pick is None:
            yield sse(Ev("error", {"message": "That is not on the table."}))
            return
        yield sse(Ev("took", {"pick": pick}))
        async for frame in _next_floor(state):
            yield frame
        return

    if kind == "reroll":
        if state.get("phase") != "reward" or state.get("rerolls", 0) <= 0:
            yield sse(Ev("error", {"message": "No rerolls left."}))
            return
        state["rerolls"] -= 1
        state["rerolls_used"] = state.get("rerolls_used", 0) + 1
        offers = runmod.roll_offers(state, salt=f"r{state['rerolls_used']}")
        yield sse(Ev("reward", {"offers": offers, "rerolls": state["rerolls"],
                                "floor": state["floor"]}))
        return

    if kind == "continue":
        if state.get("phase") not in ("intro", "reward"):
            yield sse(Ev("error", {"message": "Already in a conversation."}))
            return
        state["offers"] = []
        async for frame in _next_floor(state):
            yield frame
        return

    yield sse(Ev("error", {"message": f"Unknown action: {kind}"}))


async def _next_floor(state: dict) -> AsyncIterator[str]:
    enc = runmod.begin_floor(state)
    yield sse(Ev("floor", {
        "floor": state["floor"],
        "slot": enc.get("slot", ""),
        "kind": enc.get("kind", "sway"),
        "mind": encounter.public(enc)["mind"],
        "objective": enc["objective"],
    }))
    yield sse(Ev("run", runmod.public(state)))


def _close_floor(store: Store, player: dict, record: dict, state: dict,
                 ended: dict) -> list[str]:
    """A floor finished. Either roll rewards, or end the run."""
    frames: list[str] = []
    outcome = ended.get("outcome", "loss")
    enc = state.get("encounter") or {}
    row = runmod.finish_floor(state, outcome, ended.get("stats") or {})

    if outcome == "win" and enc.get("mind_id"):
        store.mark_beaten(player["id"], enc["mind_id"])

    revived = False
    if outcome == "loss" and runmod.revive_available(state):
        state["revives_used"] = state.get("revives_used", 0) + 1
        revived = True
        frames.append(sse(Ev("revive", {"floor": state["floor"]})))

    over = runmod.run_over(state, "win" if revived else outcome)
    if not over:
        mode = runmod.mode_of(state)
        state["phase"] = "reward"
        offers = runmod.roll_offers(state) if mode.rewards else []
        if not offers:
            state["phase"] = "reward"          # still a beat: show the floor cleared
        frames.append(sse(Ev("reward", {
            "offers": offers,
            "rerolls": max(0, state.get("rerolls", 0)),
            "floor": state["floor"],
            "row": row,
            "score": state["score"],
        })))
        frames.append(sse(Ev("run", runmod.public(state))))
        store.save_run(record["id"], state, "active")
        return frames

    frames.extend(_finish_run(store, player, record, state,
                             "win" if outcome == "win" else "loss"))
    return frames


def _finish_run(store: Store, player: dict, record: dict, state: dict,
                outcome: str) -> list[str]:
    frames: list[str] = []
    pid = player["id"]
    result = runmod.build_result(state, outcome).to_dict()
    state["phase"] = "done"
    state["result"] = result

    gained = meta.xp_for_run(result)
    result["xp"] = gained
    dk = state.get("daily_key") or ""

    store.record_score(pid, state["mode"], int(result["score"]),
                       int(result["stats"].get("floor", 0)), outcome,
                       result["stats"], dk)
    xp_before = int((store.get_player(pid) or {}).get("xp") or 0)
    total_xp = store.add_xp(pid, gained)
    level_before, level_after = meta.level_for_xp(xp_before), meta.level_for_xp(total_xp)
    info = meta.level_info(total_xp)

    fresh = store.get_player(pid) or player
    streak, extended = meta.update_streak(fresh)
    if extended:
        store.update_player(pid, streak=streak, streak_day=daily_key(),
                            best_streak=max(int(fresh.get("best_streak") or 0), streak))

    scores = store.player_scores(pid)
    ctx = {
        "result": result,
        "streak": streak,
        "level": info["level"],
        "modes_played": [s["mode"] for s in scores],
        "beaten": sorted(store.beaten(pid)),
        "local_hour": dt.datetime.now().hour,
    }
    have = store.badges(pid)
    new_badges = []
    for badge in meta.evaluate(ctx, have):
        if store.award(pid, badge.code):
            new_badges.append({"code": badge.code, "name": badge.name,
                               "desc": badge.desc, "glyph": badge.glyph})

    unlocked_now = [_describe(item) for item in meta.newly_unlocked(level_before, level_after)]
    levelled = level_after > level_before
    frames.append(sse(Ev("profile", {
        "xp_gained": gained, "level": info, "streak": streak,
        "streak_extended": extended, "levelled": levelled,
        "unlocked": unlocked_now,
    })))
    for badge in new_badges:
        frames.append(sse(Ev("badge", badge)))
    result["badges"] = new_badges
    result["unlocked"] = unlocked_now
    result["levelled"] = levelled
    result["level"] = info

    store.save_run(record["id"], state, "done")
    frames.append(sse(Ev("end", {"result": result, "run": runmod.public(state)})))
    return frames
