"""Turning a message into a verdict.

The judge is the one place a language model decides something mechanical, so it
is wrapped in three layers of not-trusting-it:

1. A **schema** so the shape is right.
2. **Normalisation** that clamps every number and drops unknown tags/flags.
3. A **deterministic heuristic** that scores the turn when the model is
   throttled, unparseable, or absent (DEMO MODE). The verdict then carries
   `fallback: true` and the client says so - a silent fallback that looks like
   real scoring is worse than an honest one that looks degraded.

Repetition is checked here in Python, not asked of the model, because the whole
game collapses if resending the same paragraph keeps scoring.
"""
from __future__ import annotations

import re

from ..config import settings
from . import prompts
from .client import LLM, Req, user

_WORD = re.compile(r"[a-z0-9']+")
_PROPER = re.compile(r"\b[A-Z][a-z]{2,}")
_DIGIT = re.compile(r"\d")
_SENTENCE = re.compile(r"[.!?]+(?:\s|$)")

PRAISE = ("good", "best", "impressive", "respect", "sharp", "careful", "thorough",
          "right about", "know your", "nobody else", "you're the", "you are the",
          "you've", "you have", "credit", "admire", "trust you", "only one")
CONCESSION = ("you're right", "you are right", "i know", "i understand", "fair",
              "i get it", "i was wrong", "my mistake", "i don't blame", "i realise",
              "i realize", "no argument")
AUTHORITY = ("rule", "policy", "section", "clause", "law", "statute", "precedent",
             "regulation", "code", "authorised", "authorized", "signed", "director",
             "board", "chapter", "paragraph", "order", "warrant", "protocol")
OFFER = ("i'll", "i will", "let me", "in exchange", "in return", "you can have",
         "take", "i can give", "yours", "on me", "i'd give", "trade", "deal")
STAKES = ("if you don't", "if you do not", "otherwise", "then you", "you'll lose",
          "you will lose", "costs you", "on your desk", "your name", "monday",
          "tomorrow", "by then", "consequence", "they'll come", "you'll be the one")
URGENCY = ("tonight", "an hour", "minutes", "before", "deadline", "closing", "leaves at",
           "by the time", "already on its way", "now", "today")


def words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def similarity(a: str, b: str) -> float:
    """Jaccard over word sets. Cheap, order-blind, and good enough to catch a
    player resending a paragraph with two words changed."""
    wa, wb = set(words(a)), set(words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def repeats(message: str, history: list[dict], threshold: float = 0.82) -> bool:
    mine = [t["text"] for t in history if t.get("who") == "player"]
    return any(similarity(message, prev) >= threshold for prev in mine[-6:])


# ------------------------------------------------------------- heuristics
def _quotes_from(message: str, sources: list[str], run: int = 3) -> bool:
    """Does the message reuse a run of `run` consecutive words from a source?"""
    mine = words(message)
    if len(mine) < run:
        return False
    grams = {" ".join(mine[i:i + run]) for i in range(len(mine) - run + 1)}
    for src in sources:
        s = words(src)
        for i in range(max(0, len(s) - run + 1)):
            if " ".join(s[i:i + run]) in grams:
                return True
    return False


def _has(text: str, needles: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(n in low for n in needles)


def _grade(card_id: str, message: str, history: list[dict]) -> int:
    """Deterministic stand-in for contract grading. Deliberately conservative:
    it never awards 3, so a fallback turn cannot mint a crit."""
    n_words = len(words(message))
    sentences = len([s for s in _SENTENCE.split(message) if s.strip()])
    theirs = [t["text"] for t in history if t.get("who") != "player"]
    earlier = [t["text"] for t in history]

    if card_id in ("silence", "cold-shoulder"):
        return 2 if n_words <= 12 else 0
    if card_id == "perfect-word":
        return 2 if 0 < n_words <= 5 else 0
    if card_id == "keystone":
        return 2 if sentences <= 1 and n_words >= 8 else 0
    if card_id == "long-con":
        return 2 if "?" in message or n_words <= 40 else 1
    if card_id in ("probe", "socratic", "counter"):
        asked = "?" in message
        if card_id == "socratic":
            return 2 if asked and not _has(message, ("i need", "i want", "give me", "let me in")) else 0
        return 2 if asked else 0
    if card_id == "flatter":
        if _has(message, PRAISE):
            return 2
        # Addressing them by name or in the second person is at least an attempt.
        return 1 if ("you" in message.lower() or _PROPER.search(message)) else 0
    if card_id in ("mirror", "understudy"):
        return 2 if _quotes_from(message, theirs) else 1 if theirs else 0
    if card_id == "callback":
        return 2 if _quotes_from(message, earlier, run=4) else 0
    if card_id in ("disarm", "concede", "confession"):
        return 2 if _has(message, CONCESSION) else 1
    if card_id in ("authority", "precedent"):
        return 2 if _has(message, AUTHORITY) else 0
    if card_id == "reciprocity":
        return 2 if _has(message, OFFER) else 1
    if card_id == "stakes":
        return 2 if _has(message, STAKES) else 1
    if card_id == "false-urgency":
        return 2 if _has(message, URGENCY) else 1
    if card_id == "specificity":
        hits = len(_DIGIT.findall(message)) + len(_PROPER.findall(message))
        return 2 if hits >= 3 else 1 if hits else 0
    if card_id in ("press", "stonewall"):
        return 2 if n_words >= 6 else 1
    return 2 if n_words >= 22 else 1


# Keyword -> rhetorical mode. Only used on the fallback path, where the real
# judge's `tags` are unavailable: without this, a message with no cards attached
# could never trigger a mind's vulnerability in DEMO MODE.
_TAG_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("flatter", PRAISE),
    ("authority", AUTHORITY),
    ("trade", OFFER),
    ("pressure", STAKES + URGENCY),
    ("concede", CONCESSION),
    ("story", ("when i", "years ago", "last week", "i once", "i remember",
               "there was", "used to", "the night", "back then")),
    ("emotion", ("i know how", "scared", "afraid", "hurts", "grief", "lost",
                 "you must feel", "tired", "alone", "sorry")),
    ("read", ("you don't", "you do not", "you never", "you always", "what you really",
              "you're the kind", "you are the kind", "nobody ever")),
)


def infer_tags(message: str, cards: list[dict]) -> list[str]:
    tags: list[str] = []
    for c in cards:
        for t in c.get("tags") or []:
            if t not in tags:
                tags.append(t)
    for tag, needles in _TAG_HINTS:
        if tag not in tags and _has(message, needles):
            tags.append(tag)
    if "?" in message and "question" not in tags:
        tags.append("question")
    hits = len(_DIGIT.findall(message)) + len(_PROPER.findall(message))
    if hits >= 3 and "detail" not in tags:
        tags.append("detail")
    if len(words(message)) <= 12 and "patience" not in tags:
        tags.append("patience")
    return tags


def heuristic(message: str, cards: list[dict], history: list[dict], *, holdout: bool = False) -> dict:
    n_words = len(words(message))
    score = 2
    if n_words >= 14:
        score += 1
    if n_words >= 35:
        score += 1
    if _DIGIT.search(message) or len(_PROPER.findall(message)) >= 2:
        score += 1
    if "?" in message:
        score += 1
    if _quotes_from(message, [t["text"] for t in history if t.get("who") != "player"]):
        score += 1
    if holdout:
        # Under pressure, brevity *is* the defence and over-explaining is the tell.
        if n_words <= 14:
            score += 2
        elif n_words > 70:
            score -= 2
    flags: list[str] = []
    if repeats(message, history):
        flags.append("repeat")
        score -= 3
    terse_ok = holdout or any(
        c["id"] in ("silence", "perfect-word", "cold-shoulder") for c in cards)
    if n_words < 4 and not terse_ok:
        flags.append("generic")
    return {
        "persuasion": max(0, min(7, score)),
        "exec": {c["id"]: _grade(c["id"], message, history) for c in cards},
        "flags": flags,
        "tags": infer_tags(message, cards)[:4],
        "leaked": False,
        "read": "Scored offline - the read is rough.",
        "fallback": True,
    }


# ----------------------------------------------------------- normalisation
def _as_list(value) -> list:
    """A model can return a string, a number or null where a list belongs."""
    if isinstance(value, list):
        return value
    return [] if value is None or isinstance(value, (int, float, bool)) else [value]


READ_LIMIT = 150


def clean_read(raw_read: str, message: str) -> str:
    """The judge's one-line explanation, defended against two real failures:
    echoing the player's own message back, and being cut off mid-word."""
    read = " ".join(str(raw_read or "").split())
    if not read:
        return ""
    if similarity(read, message) >= 0.6:
        return ""                     # it quoted the player instead of judging
    if len(read) > READ_LIMIT:
        cut = read[:READ_LIMIT].rsplit(" ", 1)[0]
        read = (cut or read[:READ_LIMIT]).rstrip(",;:.") + "…"
    return read


def normalise(raw: dict, cards: list[dict], *, holdout: bool = False,
              message: str = "") -> dict:
    grades: dict[str, int] = {}
    entries = raw.get("exec")
    if isinstance(entries, dict):                     # tolerate the obvious shape
        entries = [{"card": k, "grade": v} for k, v in entries.items()]
    ids = {c["id"] for c in cards}
    for entry in _as_list(entries):
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("card") or "").strip()
        if cid in ids:
            try:
                grades[cid] = max(0, min(3, int(entry.get("grade", 0))))
            except (TypeError, ValueError):
                grades[cid] = 0
    for cid in ids:                                   # an ungraded card fumbled
        grades.setdefault(cid, 0)

    try:
        persuasion = max(0, min(10, int(raw.get("persuasion", 0))))
    except (TypeError, ValueError):
        persuasion = 0

    return {
        "persuasion": persuasion,
        "exec": grades,
        "flags": [f for f in _as_list(raw.get("flags")) if f in prompts.FLAGS][:4],
        "tags": [t for t in _as_list(raw.get("tags")) if t in prompts.TAGS][:4],
        "leaked": bool(raw.get("leaked")) if holdout else False,
        "read": clean_read(raw.get("read"), message),
        "fallback": bool(raw.get("fallback") or raw.get("_fallback")),
    }


async def judge_turn(
    *, mind: dict, objective: str, history: list[dict], message: str,
    cards: list[dict], holdout: bool = False,
) -> dict:
    """Score one player message. Never raises: a broken judge degrades to
    heuristics rather than eating the turn."""
    fallback = heuristic(message, cards, history, holdout=holdout)
    if settings.demo_mode:
        return (normalise(fallback, cards, holdout=holdout, message=message)
                | {"fallback": True, "read": fallback["read"]})

    raw = await LLM.json(
        Req(
            system=prompts.HOLDOUT_JUDGE_SYSTEM if holdout else prompts.JUDGE_SYSTEM,
            messages=[user(prompts.judge_user(
                mind=mind, objective=objective, history=history,
                message=message, cards=cards, holdout=holdout,
            ))],
            max_tokens=420,
            retries=7,
            temperature=settings.judge_temperature,
            task="judge.holdout" if holdout else "judge.sway",
            schema=prompts.HOLDOUT_SCHEMA if holdout else prompts.JUDGE_SCHEMA,
            mock={"kind": "judge", "message": message, "cards": [c["id"] for c in cards]},
        ),
        default=fallback,
    )
    verdict = normalise(raw, cards, holdout=holdout, message=message)

    # Repetition is enforced locally whatever the model thought.
    if repeats(message, history) and "repeat" not in verdict["flags"]:
        verdict["flags"].append("repeat")
    if verdict["fallback"]:
        verdict["read"] = fallback["read"]
        verdict["persuasion"] = min(verdict["persuasion"] or fallback["persuasion"],
                                    fallback["persuasion"])
        verdict["exec"] = fallback["exec"]
    return verdict
