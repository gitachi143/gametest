"""FastAPI app: static frontend, JSON API, and one SSE endpoint for turns."""
from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .ai.client import close_client, get_client, usage
from .config import ROOT, settings
from .content import all_cards, all_relics
from .engine import meta, run as runmod
from .engine.rng import daily_key, fresh_seed
from .session import daily_plan, lock_for, release_lock, run_action
from .store import get_store

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("sway")

WEB = ROOT / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    client = get_client()
    log.info("SWAY up - provider=%s model=%s demo=%s db=%s",
             client.name, client.model, settings.demo_mode, settings.absolute_db_path())
    yield
    await close_client()
    store.close()


app = FastAPI(title="SWAY", version="1.0.0", lifespan=lifespan)


# ----------------------------------------------------- anonymous player token
def _sign(pid: str) -> str:
    return hmac.new(settings.app_secret.encode(), pid.encode(), hashlib.sha256).hexdigest()[:32]


def make_token(pid: str) -> str:
    return f"{pid}.{_sign(pid)}"


def parse_token(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    pid, _, sig = token.partition(".")
    if not hmac.compare_digest(sig, _sign(pid)):
        return None
    return pid


def current_player(token: str | None, *, create: bool = True) -> tuple[dict, str]:
    store = get_store()
    pid = parse_token(token)
    player = store.get_player(pid) if pid else None
    if player is None:
        if not create:
            raise HTTPException(401, "No player token.")
        player = store.create_player()
    else:
        store.touch_player(player["id"])
    return player, make_token(player["id"])


def profile_payload(player: dict) -> dict:
    store = get_store()
    pid = player["id"]
    xp = int(player.get("xp") or 0)
    info = meta.level_info(xp)
    have = store.badges(pid)
    return {
        "player_id": pid,
        "handle": player.get("handle") or "",
        "level": info,
        "streak": int(player.get("streak") or 0),
        "best_streak": int(player.get("best_streak") or 0),
        "seen_intro": bool(player.get("seen_intro")),
        "unlocked": sorted(meta.unlocked_for_level(info["level"])),
        "badges": meta.badge_catalogue(have),
        "badge_count": len(have),
        "badge_total": len(meta.BADGES),
        "beaten": sorted(store.beaten(pid)),
        "summary": store.player_summary(pid),
        "recent": store.player_scores(pid, limit=12),
        "active": store.active_runs(pid),
    }


def collection_payload(level: int) -> dict:
    unlocked = meta.unlocked_for_level(level)

    def entry(item: dict, kind: str) -> dict:
        available = meta.is_available(item["id"], unlocked)
        return {
            **item, "kind": kind, "locked": not available,
            "unlock_level": meta.unlock_level(item["id"]),
        }

    return {
        "cards": [entry(c, "card") for c in all_cards("tactic")],
        "wards": [entry(c, "ward") for c in all_cards("ward")],
        "relics": [entry(r, "relic") for r in all_relics()],
        "level": level,
    }


# ------------------------------------------------------------------------ API
@app.get("/api/health")
async def health():
    client = get_client()
    return {
        "ok": True,
        "app": "sway",
        "provider": client.name,
        "model": client.model,
        "demo_mode": settings.demo_mode,
        "modes": len(runmod.MODES),
        "llm": usage.snapshot(),
        "global": get_store().global_stats(),
    }


@app.get("/api/boot")
async def boot(x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    store = get_store()
    plan = daily_plan()
    profile = profile_payload(player)
    return {
        "token": token,
        "modes": runmod.catalogue(),
        "profile": profile,
        "collection": collection_payload(profile["level"]["level"]),
        "daily": {**plan, "played": store.has_played_daily(player["id"], plan["key"])},
        "demo_mode": settings.demo_mode,
        "provider": get_client().name,
        "global": store.global_stats(),
        "leaders": store.leaderboard(None, "", limit=8),
    }


@app.get("/api/me")
async def me(x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    profile = profile_payload(player)
    return {"token": token, "profile": profile,
            "collection": collection_payload(profile["level"]["level"])}


@app.post("/api/me")
async def set_handle(body: dict = Body(default={}),
                     x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    store = get_store()
    fields: dict = {}
    handle = str(body.get("handle") or "").strip()[:18]
    if handle:
        fields["handle"] = handle
    if body.get("seen_intro"):
        fields["seen_intro"] = 1
    if fields:
        store.update_player(player["id"], **fields)
    fresh = store.get_player(player["id"]) or player
    return {"token": token, "profile": profile_payload(fresh)}


@app.get("/api/leaderboard")
async def leaderboard(mode: str | None = None, daily: int = 0):
    store = get_store()
    by = "floor" if mode == "endless" else "score"
    return {
        "mode": mode or "all",
        "daily": bool(daily),
        "by": by,
        "entries": store.leaderboard(mode, daily_key() if daily else "", limit=25, by=by),
    }


@app.post("/api/run")
async def create_run(body: dict = Body(default={}),
                     x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    store = get_store()
    mode_id = str(body.get("mode") or "ascent")
    mode = runmod.MODES.get(mode_id)
    if mode is None:
        raise HTTPException(404, f"No such mode: {mode_id}")

    dk = ""
    if mode.one_shot:
        plan = daily_plan()
        # `has_daily_run` and not just `has_played_daily`: an abandoned attempt
        # still counts, or the run could be restarted until the opening looked good.
        if store.has_played_daily(player["id"], plan["key"]) or \
                store.has_daily_run(player["id"], plan["key"]):
            raise HTTPException(409, "Today's run is already played. Come back tomorrow.")
        seed, dk = plan["seed"], plan["key"]
    else:
        seed = str(body.get("seed") or "").strip()[:64] or fresh_seed()

    level = meta.level_for_xp(int(player.get("xp") or 0))
    store.abandon_runs(player["id"], mode_id)
    state = runmod.new(mode_id=mode_id, seed=seed, daily_key=dk,
                       unlocked=meta.unlocked_for_level(level))
    runmod.begin_floor(state)
    rid = store.create_run(player["id"], mode_id, seed, state, dk)
    return {"token": token, "run_id": rid, "run": runmod.public(state)}


@app.get("/api/run/{rid}")
async def read_run(rid: str, x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token, create=False)
    record = get_store().get_run(rid)
    if record is None or record["player_id"] != player["id"]:
        raise HTTPException(404, "No such run.")
    return {"token": token, "run_id": rid, "status": record["status"],
            "run": runmod.public(record["state"])}


@app.post("/api/run/{rid}/abandon")
async def abandon(rid: str, x_player_token: str | None = Header(default=None)):
    player, _ = current_player(x_player_token, create=False)
    store = get_store()
    record = store.get_run(rid)
    if record is None or record["player_id"] != player["id"]:
        raise HTTPException(404, "No such run.")
    store.save_run(rid, record["state"], "abandoned")
    release_lock(rid)
    return {"ok": True}


@app.post("/api/run/{rid}/act")
async def act(rid: str, request: Request, body: dict = Body(default={}),
              x_player_token: str | None = Header(default=None)):
    player, _ = current_player(x_player_token, create=False)
    store = get_store()
    record = store.get_run(rid)
    if record is None or record["player_id"] != player["id"]:
        raise HTTPException(404, "No such run.")
    if record["status"] != "active":
        raise HTTPException(409, "That run is finished.")

    action = body.get("action") if isinstance(body.get("action"), dict) else body
    lock = lock_for(rid)
    if lock.locked():
        raise HTTPException(429, "A turn is already in flight.")

    async def stream():
        async with lock:
            live = store.get_run(rid) or record       # the previous turn moved state
            try:
                async for frame in run_action(store, player, live, action):
                    if await request.is_disconnected():
                        break
                    yield frame
                yield "event: done\ndata: {}\n\n"
            finally:
                fresh = store.get_run(rid)
                if fresh is None or fresh["status"] != "active":
                    release_lock(rid)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


# ------------------------------------------------------------ static frontend
@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(WEB / "favicon.svg")


@app.get("/manifest.webmanifest")
async def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


if WEB.exists():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
