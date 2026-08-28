"""Wire vocabulary: the SSE events the client understands, and Result."""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Ev:
    """One server-sent event. `event` is the SSE event name."""

    event: str
    data: dict = field(default_factory=dict)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# --- streamed character speech -------------------------------------------
def say_start(mid: str, who: str, name: str = "", sigil: str = "", tone: str = "") -> Ev:
    return Ev("say_start", {"id": mid, "who": who, "name": name, "sigil": sigil, "tone": tone})


def chunk(mid: str, text: str) -> Ev:
    return Ev("chunk", {"id": mid, "text": text})


def say_end(mid: str, text: str = "") -> Ev:
    return Ev("say_end", {"id": mid, "text": text})


def line(entry: dict) -> Ev:
    return Ev("line", entry)


# --- game surface ---------------------------------------------------------
def state_ev(public: dict) -> Ev:
    return Ev("state", public)


def resolve_ev(payload: dict) -> Ev:
    """The damage chain. The client animates every step of this."""
    return Ev("resolve", payload)


def fx(kind: str, **payload: Any) -> Ev:
    return Ev("fx", {"kind": kind, **payload})


def toast(text: str, kind: str = "info", icon: str = "") -> Ev:
    return Ev("toast", {"text": text, "kind": kind, "icon": icon})


def thinking(label: str) -> Ev:
    return Ev("thinking", {"label": label})


def reveal_ev(trait: dict) -> Ev:
    return Ev("reveal", trait)


def error_ev(message: str) -> Ev:
    return Ev("error", {"message": message})


def end_ev(result: dict) -> Ev:
    return Ev("end", {"result": result})


@dataclass
class Result:
    outcome: str                  # "win" | "loss"
    score: int
    headline: str
    detail: str = ""
    stats: dict = field(default_factory=dict)
    share: list[str] = field(default_factory=list)
    reveal: dict = field(default_factory=dict)
    xp: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def log_line(state: dict, who: str, text: str, name: str = "", sigil: str = "", tone: str = "",
             meta: dict | None = None) -> dict:
    entry = {
        "id": new_id(),
        "who": who,
        "name": name,
        "sigil": sigil,
        "tone": tone,
        "text": text,
        "t": round(time.time(), 3),
    }
    if meta:
        entry["meta"] = meta
    state.setdefault("log", []).append(entry)
    return entry
