"""SQLite persistence. One file, no migrations framework, WAL for concurrency.

Everything the arcade remembers lives here: anonymous players, in-flight game
sessions, finished scores, unlocked achievements. Swapping this for Firestore or
Cloud SQL later means reimplementing this module only.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Iterable

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id          TEXT PRIMARY KEY,
    handle      TEXT NOT NULL DEFAULT '',
    created     REAL NOT NULL,
    last_seen   REAL NOT NULL,
    xp          INTEGER NOT NULL DEFAULT 0,
    streak      INTEGER NOT NULL DEFAULT 0,
    streak_day  TEXT NOT NULL DEFAULT '',
    best_streak INTEGER NOT NULL DEFAULT 0,
    calls_day   TEXT NOT NULL DEFAULT '',
    calls_today INTEGER NOT NULL DEFAULT 0,
    prefs       TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS sessions (
    id        TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    game      TEXT NOT NULL,
    mode      TEXT NOT NULL DEFAULT '',
    seed      TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL DEFAULT 'active',
    daily_key TEXT NOT NULL DEFAULT '',
    state     TEXT NOT NULL,
    created   REAL NOT NULL,
    updated   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_player ON sessions(player_id, updated DESC);
CREATE TABLE IF NOT EXISTS scores (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id  TEXT NOT NULL,
    game       TEXT NOT NULL,
    mode       TEXT NOT NULL DEFAULT '',
    score      INTEGER NOT NULL DEFAULT 0,
    outcome    TEXT NOT NULL DEFAULT '',
    stats      TEXT NOT NULL DEFAULT '{}',
    daily_key  TEXT NOT NULL DEFAULT '',
    created    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_game ON scores(game, score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_daily ON scores(daily_key, score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_player ON scores(player_id, created DESC);
CREATE TABLE IF NOT EXISTS achievements (
    player_id TEXT NOT NULL,
    code      TEXT NOT NULL,
    created   REAL NOT NULL,
    PRIMARY KEY (player_id, code)
);
"""


class Store:
    def __init__(self, path: str | None = None) -> None:
        target = path or str(settings.absolute_db_path())
        if target != ":memory:":
            settings.absolute_db_path().parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(target, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            if target != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.executescript(_SCHEMA)
            self._db.commit()

    # -- low level ----------------------------------------------------------
    def _q(self, sql: str, args: Iterable = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._db.execute(sql, tuple(args)))

    def _x(self, sql: str, args: Iterable = ()) -> None:
        with self._lock:
            self._db.execute(sql, tuple(args))
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- players ------------------------------------------------------------
    def create_player(self) -> dict:
        pid = uuid.uuid4().hex
        now = time.time()
        self._x(
            "INSERT INTO players (id, created, last_seen) VALUES (?,?,?)", (pid, now, now)
        )
        return self.get_player(pid)  # type: ignore[return-value]

    def get_player(self, pid: str) -> dict | None:
        rows = self._q("SELECT * FROM players WHERE id=?", (pid,))
        if not rows:
            return None
        p = dict(rows[0])
        p["prefs"] = json.loads(p.get("prefs") or "{}")
        return p

    def touch_player(self, pid: str) -> None:
        self._x("UPDATE players SET last_seen=? WHERE id=?", (time.time(), pid))

    def update_player(self, pid: str, **fields: Any) -> None:
        if not fields:
            return
        if "prefs" in fields and not isinstance(fields["prefs"], str):
            fields["prefs"] = json.dumps(fields["prefs"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE players SET {cols} WHERE id=?", (*fields.values(), pid))

    def add_xp(self, pid: str, amount: int) -> int:
        with self._lock:
            self._db.execute("UPDATE players SET xp = xp + ? WHERE id=?", (amount, pid))
            self._db.commit()
            row = list(self._db.execute("SELECT xp FROM players WHERE id=?", (pid,)))
        return int(row[0]["xp"]) if row else 0

    def bump_calls(self, pid: str, day: str, limit: int) -> bool:
        """Rolling per-day LLM call budget. Returns False when exhausted."""
        if limit <= 0:
            return True
        with self._lock:
            rows = list(
                self._db.execute("SELECT calls_day, calls_today FROM players WHERE id=?", (pid,))
            )
            if not rows:
                return True
            cur_day, count = rows[0]["calls_day"], int(rows[0]["calls_today"])
            if cur_day != day:
                cur_day, count = day, 0
            if count >= limit:
                return False
            self._db.execute(
                "UPDATE players SET calls_day=?, calls_today=? WHERE id=?",
                (cur_day, count + 1, pid),
            )
            self._db.commit()
        return True

    # -- sessions -----------------------------------------------------------
    def create_session(
        self, player_id: str, game: str, mode: str, seed: str, state: dict, daily_key: str = ""
    ) -> str:
        sid = uuid.uuid4().hex
        now = time.time()
        self._x(
            "INSERT INTO sessions (id, player_id, game, mode, seed, status, daily_key, state,"
            " created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, player_id, game, mode, seed, "active", daily_key, json.dumps(state), now, now),
        )
        return sid

    def get_session(self, sid: str) -> dict | None:
        rows = self._q("SELECT * FROM sessions WHERE id=?", (sid,))
        if not rows:
            return None
        s = dict(rows[0])
        s["state"] = json.loads(s["state"])
        return s

    def save_session(self, sid: str, state: dict, status: str = "active") -> None:
        self._x(
            "UPDATE sessions SET state=?, status=?, updated=? WHERE id=?",
            (json.dumps(state), status, time.time(), sid),
        )

    def active_sessions(self, player_id: str, limit: int = 6) -> list[dict]:
        rows = self._q(
            "SELECT id, game, mode, updated FROM sessions WHERE player_id=? AND status='active'"
            " ORDER BY updated DESC LIMIT ?",
            (player_id, limit),
        )
        return [dict(r) for r in rows]

    def has_played_daily(self, player_id: str, daily_key: str) -> bool:
        rows = self._q(
            "SELECT 1 FROM scores WHERE player_id=? AND daily_key=? LIMIT 1",
            (player_id, daily_key),
        )
        return bool(rows)

    # -- scores -------------------------------------------------------------
    def record_score(
        self, player_id: str, game: str, mode: str, score: int, outcome: str,
        stats: dict, daily_key: str = "",
    ) -> None:
        self._x(
            "INSERT INTO scores (player_id, game, mode, score, outcome, stats, daily_key, created)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (player_id, game, mode, score, outcome, json.dumps(stats), daily_key, time.time()),
        )

    def player_scores(self, player_id: str) -> list[dict]:
        rows = self._q(
            "SELECT game, mode, score, outcome, stats, created FROM scores WHERE player_id=?"
            " ORDER BY created DESC LIMIT 300",
            (player_id,),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d["stats"])
            out.append(d)
        return out

    def player_summary(self, player_id: str) -> dict:
        rows = self._q(
            "SELECT game, COUNT(*) n, MAX(score) best, SUM(outcome='win') wins"
            " FROM scores WHERE player_id=? GROUP BY game",
            (player_id,),
        )
        return {r["game"]: {"plays": r["n"], "best": r["best"] or 0, "wins": r["wins"] or 0} for r in rows}

    def leaderboard(self, game: str | None = None, daily_key: str = "", limit: int = 20) -> list[dict]:
        """Best score per player, so one obsessive player can't fill the board."""
        where, args = [], []
        if game:
            where.append("s.game=?")
            args.append(game)
        if daily_key:
            where.append("s.daily_key=?")
            args.append(daily_key)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        rows = self._q(
            f"""SELECT p.handle, s.player_id, MAX(s.score) score, s.game,
                       MIN(s.created) first_at
                FROM scores s LEFT JOIN players p ON p.id = s.player_id
                {clause}
                GROUP BY s.player_id
                ORDER BY score DESC, first_at ASC
                LIMIT ?""",
            (*args, limit),
        )
        return [
            {
                "rank": i + 1,
                "handle": r["handle"] or "anon",
                "player_id": r["player_id"],
                "score": r["score"],
                "game": r["game"],
            }
            for i, r in enumerate(rows)
        ]

    def global_stats(self) -> dict:
        rows = self._q("SELECT COUNT(*) n, COALESCE(SUM(score),0) pts FROM scores")
        players = self._q("SELECT COUNT(*) n FROM players")
        return {
            "runs": rows[0]["n"] if rows else 0,
            "points": rows[0]["pts"] if rows else 0,
            "players": players[0]["n"] if players else 0,
        }

    # -- achievements -------------------------------------------------------
    def unlocked(self, player_id: str) -> set[str]:
        return {r["code"] for r in self._q("SELECT code FROM achievements WHERE player_id=?", (player_id,))}

    def unlock(self, player_id: str, code: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO achievements (player_id, code, created) VALUES (?,?,?)",
                (player_id, code, time.time()),
            )
            self._db.commit()
            return cur.rowcount > 0


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store
