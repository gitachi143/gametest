"""Helpers every game shares: text matching, turn bookkeeping, result building."""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Iterable

from ..engine.types import Ev, GameMeta, Result, log_line, new_id

_PUNCT = re.compile(r"[^\w\s]+", re.UNICODE)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = _PUNCT.sub(" ", text.lower())
    return _WS.sub(" ", text).strip()


def variants(word: str) -> set[str]:
    """Cheap morphological net for banned-word checks - no stemmer dependency.

    Deliberately over-generates: in HOTWIRE a false positive costs the player a
    clue, a false negative lets them say the target outright.
    """
    w = normalize(word)
    out = {w}
    if not w:
        return out
    out |= {w + s for s in ("s", "es", "ed", "d", "ing", "er", "ers", "y", "ies", "al", "ic")}
    if w.endswith("e"):
        out |= {w[:-1] + s for s in ("ing", "ed", "es", "y")}
    if w.endswith("y"):
        out |= {w[:-1] + s for s in ("ies", "ied", "ily")}
    if len(w) > 3 and w[-1] == w[-2]:  # running -> run
        out.add(w[:-1])
    if len(w) > 4:
        out.add(w[:-1] + "ping")
        out.add(w + w[-1] + "ing")
    return out


def contains_term(haystack: str, term: str) -> bool:
    """True when `term` (or a near variant) appears as a word in `haystack`."""
    hay = f" {normalize(haystack)} "
    for form in variants(term):
        if f" {form} " in hay:
            return True
    # Multi-word terms: require the phrase, allowing any separator.
    parts = normalize(term).split()
    if len(parts) > 1 and re.search(r"\b" + r"[\s\-]*".join(map(re.escape, parts)) + r"\b", hay):
        return True
    # Guard against smashed-together evasion ("volcanoes" vs "volcano!!!")
    squashed = re.sub(r"[^a-z0-9]", "", normalize(haystack))
    core = re.sub(r"[^a-z0-9]", "", normalize(term))
    return len(core) >= 5 and core in squashed


def banned_hits(text: str, terms: Iterable[str]) -> list[str]:
    return [t for t in terms if contains_term(text, t)]


def levenshtein(a: str, b: str, cap: int = 3) -> int:
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
        if min(prev) > cap:
            return cap + 1
    return prev[-1]


def answer_matches(guess: str, target: str) -> bool:
    """Forgiving equality for free-text answers ('a lighthouse' ~ 'lighthouse!')."""
    g, t = normalize(guess), normalize(target)
    if not g:
        return False
    for article in ("a ", "an ", "the "):
        if g.startswith(article):
            g = g[len(article):]
        if t.startswith(article):
            t = t[len(article):]
    if g == t or t in g.split(" and ") :
        return True
    if len(t) >= 5 and (t in g or g in t):
        return True
    tol = 1 if len(t) <= 6 else 2
    return levenshtein(g, t, tol) <= tol


def mentions_answer(text: str, target: str) -> bool:
    """Did a model reply leak the target anywhere in it?"""
    t = normalize(target)
    parts = [p for p in re.split(r"[\s\-]+", t) if len(p) > 2]
    if not parts:
        return False
    hay = normalize(text)
    squashed = re.sub(r"[^a-z0-9]", "", hay)
    return all(p in hay or p in squashed for p in parts)


class BaseGame:
    """Small amount of shared plumbing; games stay mostly self-contained."""

    meta: GameMeta

    # -- state helpers ------------------------------------------------------
    @staticmethod
    def now() -> float:
        return time.time()

    def base_state(self, seed: str, mode: str) -> dict:
        return {
            "game": self.meta.id,
            "seed": seed,
            "mode": mode,
            "status": "active",
            "score": 0,
            "log": [],
            "started": time.time(),
        }

    @staticmethod
    def elapsed(state: dict) -> int:
        return int(time.time() - state.get("started", time.time()))

    @staticmethod
    def say(state: dict, actor: str, text: str, name: str = "", avatar: str = "", tone: str = "") -> Ev:
        entry = log_line(state, actor, text, name, avatar, tone)
        return Ev("msg", entry)

    @staticmethod
    def finish(state: dict, result: Result) -> Result:
        state["status"] = "won" if result.outcome == "win" else "lost"
        result.stats.setdefault("seconds", BaseGame.elapsed(state))
        state["result"] = result.to_dict()
        return result

    # -- transcript for the model ------------------------------------------
    @staticmethod
    def trim(history: list[dict], keep: int | None) -> list[dict]:
        if keep is None or keep <= 0:
            return history
        return history[-keep:]


def sanitize_player_text(text: str, limit: int = 1200) -> str:
    text = (text or "").strip()
    if len(text) > limit:
        text = text[:limit]
    return text


def emoji_bar(value: int, out_of: int, filled: str = "🟩", empty: str = "⬜") -> str:
    value = max(0, min(out_of, value))
    return filled * value + empty * (out_of - value)


__all__ = [
    "BaseGame", "answer_matches", "banned_hits", "contains_term", "emoji_bar",
    "levenshtein", "mentions_answer", "new_id", "normalize", "sanitize_player_text",
    "variants",
]
