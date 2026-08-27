"""Core engine vocabulary shared by every game."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator, Protocol


# --- SSE events ------------------------------------------------------------
@dataclass
class Ev:
    """One server-sent event. `event` is the SSE event name."""

    event: str
    data: dict = field(default_factory=dict)


def msg_start(mid: str, actor: str, name: str = "", avatar: str = "", tone: str = "") -> Ev:
    return Ev("msg_start", {"id": mid, "actor": actor, "name": name, "avatar": avatar, "tone": tone})


def chunk(mid: str, text: str) -> Ev:
    return Ev("chunk", {"id": mid, "text": text})


def msg_end(mid: str, text: str = "") -> Ev:
    return Ev("msg_end", {"id": mid, "text": text})


def state_ev(public: dict) -> Ev:
    return Ev("state", public)


def fx(kind: str, **payload: Any) -> Ev:
    return Ev("fx", {"kind": kind, **payload})


def toast(text: str, kind: str = "info", icon: str = "") -> Ev:
    return Ev("toast", {"text": text, "kind": kind, "icon": icon})


def end_ev(result: dict) -> Ev:
    return Ev("end", {"result": result})


def error_ev(message: str) -> Ev:
    return Ev("error", {"message": message})


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --- game metadata --------------------------------------------------------
@dataclass
class Mode:
    id: str
    name: str
    desc: str


@dataclass
class GameMeta:
    id: str
    title: str
    codename: str
    tagline: str
    blurb: str
    how: list[str]
    icon: str
    accent: str          # CSS hue anchor, e.g. "#ff5470"
    accent2: str
    difficulty: int      # 1..5
    minutes: str         # "2-4"
    tags: list[str]
    modes: list[Mode] = field(default_factory=list)
    hud: str = "generic"  # frontend panel component key

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# --- result --------------------------------------------------------------
@dataclass
class Result:
    outcome: str                 # "win" | "loss" | "draw"
    score: int
    headline: str
    detail: str = ""
    stats: dict = field(default_factory=dict)
    share: list[str] = field(default_factory=list)   # emoji grid rows
    reveal: dict = field(default_factory=dict)       # secrets, shown post-game
    xp: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


class Game(Protocol):
    meta: GameMeta

    def new_state(self, *, seed: str, mode: str, opts: dict) -> dict: ...

    def public(self, state: dict) -> dict: ...

    def act(self, state: dict, action: dict) -> AsyncIterator[Ev]: ...


# --- transcript helpers ---------------------------------------------------
def log_line(state: dict, actor: str, text: str, name: str = "", avatar: str = "", tone: str = "") -> dict:
    entry = {
        "id": new_id(),
        "actor": actor,
        "name": name,
        "avatar": avatar,
        "tone": tone,
        "text": text,
        "t": round(time.time(), 3),
    }
    state.setdefault("log", []).append(entry)
    return entry
