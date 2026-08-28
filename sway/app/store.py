"""SQLite persistence. One file, WAL, no migration framework.

Swapping this module for Firestore or Cloud SQL is the only work needed to run
more than one instance - nothing else in the app touches storage.
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
    seen_intro  INTEGER NOT NULL DEFAULT 0,
    prefs       TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS runs (
    id        TEXT PRIMARY KEY,
    player_id TEXT NOT NULL,
    mode      TEXT NOT NULL,
    seed      TEXT NOT NULL DEFAULT '',
    daily_key TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL DEFAULT 'active',
    state     TEXT NOT NULL,
    created   REAL NOT NULL,
    updated   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_player ON runs(player_id, updated DESC);
CREATE TABLE IF NOT EXISTS scores (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id TEXT NOT NULL,
    mode      TEXT NOT NULL,
    score     INTEGER NOT NULL DEFAULT 0,
    floor     INTEGER NOT NULL DEFAULT 0,
    outcome   TEXT NOT NULL DEFAULT '',
    stats     TEXT NOT NULL DEFAULT '{}',
    daily_key TEXT NOT NULL DEFAULT '',
    created   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_mode ON scores(mode, score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_daily ON scores(daily_key, score DESC);
CREATE INDEX IF NOT EXISTS idx_scores_player ON scores(player_id, created DESC);
CREATE TABLE IF NOT EXISTS badges (
    player_id TEXT NOT NULL,
    code      TEXT NOT NULL,
    created   REAL NOT NULL,
    PRIMARY KEY (player_id, code)
);
CREATE TABLE IF NOT EXISTS beaten (
    player_id TEXT NOT NULL,
    mind_id   TEXT NOT NULL,
    created   REAL NOT NULL,
    PRIMARY KEY (player_id, mind_id)
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

    # -- players -----------------------------------------------------------
    def create_player(self) -> dict:
        pid = uuid.uuid4().hex
        now = time.time()
        self._x("INSERT INTO players (id, created, last_seen) VALUES (?,?,?)", (pid, now, now))
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
        """Per-player, per-day model call budget. False when exhausted."""
        if limit <= 0:
            return True
        with self._lock:
            rows = list(self._db.execute(
                "SELECT calls_day, calls_today FROM players WHERE id=?", (pid,)))
            if not rows:
                return True
            day_seen, count = rows[0]["calls_day"], int(rows[0]["calls_today"])
            if day_seen != day:
                day_seen, count = day, 0
            if count >= limit:
                return False
            self._db.execute("UPDATE players SET calls_day=?, calls_today=? WHERE id=?",
                             (day_seen, count + 1, pid))
            self._db.commit()
        return True

    # -- runs --------------------------------------------------------------
    def create_run(self, player_id: str, mode: str, seed: str, state: dict,
                   daily_key: str = "") -> str:
        rid = uuid.uuid4().hex
        now = time.time()
        self._x(
            "INSERT INTO runs (id, player_id, mode, seed, daily_key, status, state, created,"
            " updated) VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, player_id, mode, seed, daily_key, "active", json.dumps(state), now, now),
        )
        return rid

    def get_run(self, rid: str) -> dict | None:
        rows = self._q("SELECT * FROM runs WHERE id=?", (rid,))
        if not rows:
            return None
        r = dict(rows[0])
        r["state"] = json.loads(r["state"])
        return r

    def save_run(self, rid: str, state: dict, status: str = "active") -> None:
        self._x("UPDATE runs SET state=?, status=?, updated=? WHERE id=?",
                (json.dumps(state), status, time.time(), rid))

    def active_runs(self, player_id: str, limit: int = 4) -> list[dict]:
        rows = self._q(
            "SELECT id, mode, updated, state FROM runs WHERE player_id=? AND status='active'"
            " ORDER BY updated DESC LIMIT ?", (player_id, limit))
        out = []
        for r in rows:
            state = json.loads(r["state"])
            out.append({
                "id": r["id"], "mode": r["mode"], "updated": r["updated"],
                "floor": state.get("floor", 0), "score": state.get("score", 0),
                "phase": state.get("phase", ""),
                "mind": ((state.get("encounter") or {}).get("mind") or {}).get("name", ""),
            })
        return out

    def has_daily_run(self, player_id: str, daily_key: str) -> bool:
        """Any run at all for this key - including one walked away from.
        Otherwise a player could restart the Daily until they liked the opening."""
        return bool(self._q(
            "SELECT 1 FROM runs WHERE player_id=? AND daily_key=? LIMIT 1",
            (player_id, daily_key)))

    def abandon_runs(self, player_id: str, mode: str | None = None) -> None:
        """One live run per mode keeps the resume list honest."""
        if mode:
            self._x("UPDATE runs SET status='abandoned' WHERE player_id=? AND mode=?"
                    " AND status='active'", (player_id, mode))
        else:
            self._x("UPDATE runs SET status='abandoned' WHERE player_id=? AND status='active'",
                    (player_id,))

    # -- scores ------------------------------------------------------------
    def record_score(self, player_id: str, mode: str, score: int, floor: int,
                     outcome: str, stats: dict, daily_key: str = "") -> None:
        self._x(
            "INSERT INTO scores (player_id, mode, score, floor, outcome, stats, daily_key,"
            " created) VALUES (?,?,?,?,?,?,?,?)",
            (player_id, mode, score, floor, outcome, json.dumps(stats), daily_key, time.time()),
        )

    def player_scores(self, player_id: str, limit: int = 200) -> list[dict]:
        rows = self._q(
            "SELECT mode, score, floor, outcome, stats, created FROM scores WHERE player_id=?"
            " ORDER BY created DESC LIMIT ?", (player_id, limit))
        out = []
        for r in rows:
            d = dict(r)
            d["stats"] = json.loads(d["stats"])
            out.append(d)
        return out

    def player_summary(self, player_id: str) -> dict:
        rows = self._q(
            "SELECT mode, COUNT(*) n, MAX(score) best, MAX(floor) deepest,"
            " SUM(outcome='win') wins FROM scores WHERE player_id=? GROUP BY mode",
            (player_id,))
        return {
            r["mode"]: {"runs": r["n"], "best": r["best"] or 0,
                        "deepest": r["deepest"] or 0, "wins": r["wins"] or 0}
            for r in rows
        }

    def has_played_daily(self, player_id: str, daily_key: str) -> bool:
        return bool(self._q(
            "SELECT 1 FROM scores WHERE player_id=? AND daily_key=? LIMIT 1",
            (player_id, daily_key)))

    def leaderboard(self, mode: str | None = None, daily_key: str = "", limit: int = 20,
                    by: str = "score") -> list[dict]:
        """One row per player - their best - so a single obsessive run cannot
        occupy the whole board."""
        where, args = [], []
        if mode:
            where.append("s.mode=?")
            args.append(mode)
        if daily_key:
            where.append("s.daily_key=?")
            args.append(daily_key)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        metric = "MAX(s.floor)" if by == "floor" else "MAX(s.score)"
        rows = self._q(
            f"""SELECT p.handle, s.player_id, {metric} metric, MAX(s.score) score,
                       MAX(s.floor) floor, MIN(s.created) first_at, s.mode
                FROM scores s LEFT JOIN players p ON p.id = s.player_id
                {clause}
                GROUP BY s.player_id
                ORDER BY metric DESC, first_at ASC
                LIMIT ?""", (*args, limit))
        return [
            {"rank": i + 1, "handle": r["handle"] or "anon", "player_id": r["player_id"],
             "score": r["score"], "floor": r["floor"], "mode": r["mode"]}
            for i, r in enumerate(rows)
        ]

    def global_stats(self) -> dict:
        rows = self._q("SELECT COUNT(*) n, COALESCE(SUM(score),0) pts,"
                       " COALESCE(MAX(score),0) top FROM scores")
        players = self._q("SELECT COUNT(*) n FROM players")
        return {
            "runs": rows[0]["n"] if rows else 0,
            "points": rows[0]["pts"] if rows else 0,
            "best": rows[0]["top"] if rows else 0,
            "players": players[0]["n"] if players else 0,
        }

    # -- badges & trophies -------------------------------------------------
    def badges(self, player_id: str) -> set[str]:
        return {r["code"] for r in self._q("SELECT code FROM badges WHERE player_id=?", (player_id,))}

    def award(self, player_id: str, code: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO badges (player_id, code, created) VALUES (?,?,?)",
                (player_id, code, time.time()))
            self._db.commit()
            return cur.rowcount > 0

    def beaten(self, player_id: str) -> set[str]:
        return {r["mind_id"] for r in self._q(
            "SELECT mind_id FROM beaten WHERE player_id=?", (player_id,))}

    def mark_beaten(self, player_id: str, mind_id: str) -> None:
        self._x("INSERT OR IGNORE INTO beaten (player_id, mind_id, created) VALUES (?,?,?)",
                (player_id, mind_id, time.time()))


_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store


def set_store(store: Store | None) -> None:
    global _store
    _store = store
