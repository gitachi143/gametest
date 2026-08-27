"""FastAPI app: static frontend, JSON API, and SSE game turns."""
from __future__ import annotations

import hashlib
import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import meta
from .config import ROOT, settings
from .engine.rng import daily_key, fresh_seed
from .games import catalogue, get_game
from .llm import close_llm, get_llm, usage
from .session import daily_plan, lock_for, run_action
from .store import get_store

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
log = logging.getLogger("arcade")

WEB = ROOT / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    client = get_llm()
    log.info(
        "NEXUS ARCADE up - provider=%s model=%s demo=%s db=%s",
        client.name, client.model, settings.demo_mode, settings.absolute_db_path(),
    )
    yield
    await close_llm()
    store.close()


app = FastAPI(title="Nexus Arcade", version="1.0.0", lifespan=lifespan)


# --- anonymous player tokens ---------------------------------------------
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
    unlocked = store.unlocked(pid)
    return {
        "player_id": pid,
        "handle": player.get("handle") or "",
        "level": meta.level_info(xp),
        "streak": int(player.get("streak") or 0),
        "best_streak": int(player.get("best_streak") or 0),
        "summary": store.player_summary(pid),
        "achievements": meta.catalogue(unlocked),
        "unlocked_count": len(unlocked),
        "achievement_total": len(meta.ACHIEVEMENTS),
        "active": store.active_sessions(pid),
        "recent": store.player_scores(pid)[:12],
    }


# --- API ------------------------------------------------------------------
@app.get("/api/health")
async def health():
    client = get_llm()
    return {
        "ok": True,
        "provider": client.name,
        "model": client.model,
        "demo_mode": settings.demo_mode,
        "games": len(catalogue()),
        "llm": usage.snapshot(),
        "global": get_store().global_stats(),
    }


@app.get("/api/games")
async def games(x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    return {
        "games": catalogue(),
        "daily": {**daily_plan(), "played": get_store().has_played_daily(player["id"], daily_key())},
        "demo_mode": settings.demo_mode,
        "provider": get_llm().name,
        "token": token,
        "profile": profile_payload(player),
    }


@app.get("/api/me")
async def me(x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    return {"token": token, "profile": profile_payload(player)}


@app.post("/api/me")
async def set_handle(
    body: dict = Body(default={}), x_player_token: str | None = Header(default=None)
):
    player, token = current_player(x_player_token)
    handle = str(body.get("handle") or "").strip()[:18]
    if handle:
        get_store().update_player(player["id"], handle=handle)
        player["handle"] = handle
    return {"token": token, "profile": profile_payload(get_store().get_player(player["id"]) or player)}


@app.get("/api/leaderboard")
async def leaderboard(game: str | None = None, daily: int = 0):
    store = get_store()
    key = daily_key() if daily else ""
    return {
        "game": game or "all",
        "daily": bool(daily),
        "entries": store.leaderboard(game, key, limit=20),
    }


@app.get("/api/daily")
async def daily(x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    plan = daily_plan()
    return {
        "token": token,
        "daily": {**plan, "played": get_store().has_played_daily(player["id"], plan["key"])},
        "leaderboard": get_store().leaderboard(None, plan["key"], limit=20),
    }


@app.post("/api/session")
async def create_session(
    body: dict = Body(default={}), x_player_token: str | None = Header(default=None)
):
    player, token = current_player(x_player_token)
    store = get_store()
    is_daily = bool(body.get("daily"))

    if is_daily:
        plan = daily_plan()
        if store.has_played_daily(player["id"], plan["key"]):
            raise HTTPException(409, "Today's challenge is already played. Come back tomorrow.")
        game_id, mode, seed, opts, dk = (
            plan["game"], plan["mode"], plan["seed"], plan["opts"], plan["key"]
        )
    else:
        game_id = str(body.get("game") or "")
        mode = str(body.get("mode") or "")
        opts = body.get("opts") if isinstance(body.get("opts"), dict) else {}
        seed = str(body.get("seed") or "") or fresh_seed()
        dk = ""

    game = get_game(game_id)
    if game is None:
        raise HTTPException(404, f"No such game: {game_id}")
    if not mode:
        mode = game.meta.modes[0].id if game.meta.modes else "standard"

    state = game.new_state(seed=seed, mode=mode, opts=opts or {})
    sid = store.create_session(player["id"], game_id, mode, seed, state, dk)
    return {
        "token": token,
        "session_id": sid,
        "game": game.meta.to_dict(),
        "daily": bool(dk),
        "public": game.public(state),
    }


@app.get("/api/session/{sid}")
async def read_session(sid: str, x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token, create=False)
    session = get_store().get_session(sid)
    if session is None or session["player_id"] != player["id"]:
        raise HTTPException(404, "No such session.")
    game = get_game(session["game"])
    if game is None:
        raise HTTPException(404, "Unknown game.")
    return {
        "token": token,
        "session_id": sid,
        "game": game.meta.to_dict(),
        "daily": bool(session.get("daily_key")),
        "status": session["status"],
        "public": game.public(session["state"]),
    }


@app.post("/api/session/{sid}/act")
async def act(
    sid: str,
    request: Request,
    body: dict = Body(default={}),
    x_player_token: str | None = Header(default=None),
):
    player, _ = current_player(x_player_token, create=False)
    store = get_store()
    session = store.get_session(sid)
    if session is None or session["player_id"] != player["id"]:
        raise HTTPException(404, "No such session.")
    if session["status"] != "active":
        raise HTTPException(409, "Session finished.")

    action = body.get("action") if isinstance(body.get("action"), dict) else body
    lock = lock_for(sid)
    if lock.locked():
        raise HTTPException(429, "A turn is already in flight.")

    async def stream():
        async with lock:
            # Re-read inside the lock: the previous turn may have advanced state.
            live = store.get_session(sid) or session
            async for frame in run_action(store, player, live, action):
                if await request.is_disconnected():
                    break
                yield frame
            yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/achievements")
async def achievements(x_player_token: str | None = Header(default=None)):
    player, token = current_player(x_player_token)
    return {"token": token, "achievements": meta.catalogue(get_store().unlocked(player["id"]))}


# --- static frontend -----------------------------------------------------
@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.get("/favicon.svg")
async def favicon():
    return FileResponse(WEB / "favicon.svg")


if WEB.exists():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)
