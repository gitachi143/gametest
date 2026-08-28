"""A run: the thing a player actually sits down to do.

Modes are not five separate games - they are five shapes of the same run, and
`MODES` is the whole difference between them. Everything downstream (encounters,
rewards, scoring, share cards) reads that table.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..content import all_cards, all_relics, bosses, card as get_card, mind as get_mind, minds_by_tier
from . import combat, encounter, meta, relics as relicmod, rng
from .types import Result

STARTER_DECK = ["press"] * 4 + ["probe"] * 3 + ["flatter", "mirror", "disarm"]
WARD_DECK = ["stonewall"] * 3 + ["deflect"] * 3 + ["counter"] * 2 + ["cold-shoulder", "call-it"]

GROWTH = 1.5
BASE_RESISTANCE = 150


@dataclass(frozen=True)
class Mode:
    id: str
    name: str
    tagline: str
    blurb: str
    glyph: str
    floors: int
    ladder: tuple            # per-floor tier, or "gate" / "boss"
    turns: int
    rewards: bool
    cards: bool
    minutes: str
    difficulty: int
    accent: str
    endless: bool = False
    one_shot: bool = False


MODES: dict[str, Mode] = {
    "ascent": Mode(
        id="ascent", name="Ascent", glyph="◈",
        tagline="Eight minds. One conversation each.",
        blurb=("The full run. Climb eight floors, collect tactics and relics between "
               "them, and finish with something that has read four hundred transcripts "
               "of people just like you."),
        floors=8, ladder=(1, 1, 2, "gate", 2, 3, 4, "boss"),
        turns=6, rewards=True, cards=True, minutes="12-18", difficulty=3,
        accent="#e8b44a",
    ),
    "blitz": Mode(
        id="blitz", name="Blitz", glyph="⚡",
        tagline="Three lines. No tools. Go.",
        blurb=("One mind, three exchanges, no cards, no reading them first. Pure "
               "persuasion at speed. The fastest way to find out whether you can "
               "actually write."),
        floors=1, ladder=(1,), turns=3, rewards=False, cards=False,
        minutes="2", difficulty=2, accent="#5fd3c4",
    ),
    "holdout": Mode(
        id="holdout", name="Holdout", glyph="▮",
        tagline="They work on you. Say nothing.",
        blurb=("The board flipped. An expert is going to spend eight exchanges getting "
               "one thing out of you, and they are very good, and very likeable. Hold "
               "your resolve to the end."),
        floors=1, ladder=("gate",), turns=8, rewards=False, cards=True,
        minutes="6-9", difficulty=4, accent="#7f8cff",
    ),
    "daily": Mode(
        id="daily", name="The Daily", glyph="◉",
        tagline="Same five minds for everybody. One attempt.",
        blurb=("Identical minds, identical card offers, identical rolls - for every "
               "player, everywhere, until midnight UTC. One run. Then a share card "
               "somebody else can read."),
        floors=5, ladder=(1, 2, "gate", 3, "boss"),
        turns=6, rewards=True, cards=True, minutes="8-12", difficulty=3,
        accent="#e0533f", one_shot=True,
    ),
    "endless": Mode(
        id="endless", name="Endless", glyph="∞",
        tagline="How far up does it go?",
        blurb=("It does not stop and it does not stop scaling. Every fourth floor is a "
               "holdout, every fifth is a boss, and the leaderboard is the floor you "
               "died on."),
        floors=99, ladder=(), turns=6, rewards=True, cards=True,
        minutes="15+", difficulty=5, accent="#c07ff0", endless=True,
    ),
}

ORDER = ["ascent", "blitz", "holdout", "daily", "endless"]


def catalogue() -> list[dict]:
    return [
        {
            "id": m.id, "name": m.name, "tagline": m.tagline, "blurb": m.blurb,
            "glyph": m.glyph, "floors": m.floors if not m.endless else 0,
            "minutes": m.minutes, "difficulty": m.difficulty, "accent": m.accent,
            "cards": m.cards, "rewards": m.rewards, "one_shot": m.one_shot,
            "endless": m.endless,
        }
        for m in (MODES[k] for k in ORDER)
    ]


# ------------------------------------------------------------- floor plan
def _ladder_slot(mode: Mode, floor: int) -> object:
    if not mode.endless:
        return mode.ladder[min(floor, len(mode.ladder)) - 1]
    if floor % 5 == 0:
        return "boss"
    if floor % 4 == 0:
        return "gate"
    return min(4, 1 + (floor - 1) // 3)


def plan_floor(seed: str, mode: Mode, floor: int, used: list[str]) -> dict:
    """Which mind stands on this floor. Deterministic in (seed, floor)."""
    slot = _ladder_slot(mode, floor)
    if slot == "gate":
        pool = [m for m in (get_mind(i) for i in ("interviewer", "closer", "old-friend")) if m]
        pool.sort(key=lambda m: m["tier"])
        idx = min(len(pool) - 1, (floor - 1) // 3)
        options = [m for m in pool if m["id"] not in used] or pool
        choice = rng.pick(f"{seed}:gate:{floor}", options[: idx + 1] or options)
        return {"mind": choice["id"], "kind": "holdout", "slot": "gate"}
    if slot == "boss":
        options = [b for b in bosses() if b["id"] not in used] or bosses()
        return {"mind": rng.pick(f"{seed}:boss:{floor}", options)["id"],
                "kind": "sway", "slot": "boss"}
    tier = int(slot)
    options = [m for m in minds_by_tier(tier) if m["id"] not in used] or minds_by_tier(tier)
    return {"mind": rng.pick(f"{seed}:mind:{floor}", options)["id"],
            "kind": "sway", "slot": str(tier)}


def resistance_for(mode: Mode, floor: int, mind: dict) -> int:
    if mode.id == "blitz":
        return int(round(BASE_RESISTANCE * 1.15 * float(mind.get("resistance_mult", 1.0))))
    return int(round(BASE_RESISTANCE * (GROWTH ** (floor - 1)) * float(mind.get("resistance_mult", 1.0))))


def turns_for(mode: Mode, plan: dict, mind: dict) -> int:
    if plan["kind"] == "holdout":
        return 8 if mode.id == "holdout" else 6
    if plan["slot"] == "boss":
        return mode.turns + 1
    return mode.turns


# ------------------------------------------------------------------- create
def new(*, mode_id: str, seed: str, daily_key: str = "", unlocked: set[str] | None = None) -> dict:
    mode = MODES.get(mode_id) or MODES["ascent"]
    return {
        "mode": mode.id,
        "seed": seed,
        "daily_key": daily_key,
        "floor": 0,
        "floors": mode.floors,
        "deck": list(STARTER_DECK) if mode.cards else [],
        "relics": [],
        "score": 0,
        "rerolls": 0,
        "revives": 0,
        "phase": "intro",
        "used_minds": [],
        "offers": [],
        "encounter": None,
        "history": [],
        "result": None,
        "unlocked": sorted(unlocked or set()),
        "stats": {"crits": 0, "fumbles": 0, "best_hit": 0, "words": 0, "turns": 0},
    }


def mode_of(state: dict) -> Mode:
    return MODES.get(state.get("mode") or "ascent") or MODES["ascent"]


def effects(state: dict) -> dict:
    return relicmod.effects(state.get("relics") or [])


def begin_floor(state: dict) -> dict:
    """Advance to the next floor and build its encounter."""
    mode = mode_of(state)
    state["floor"] += 1
    floor = state["floor"]
    plan = plan_floor(state["seed"], mode, floor, state["used_minds"])
    mind = get_mind(plan["mind"])
    state["used_minds"].append(plan["mind"])
    eff = effects(state)

    if plan["kind"] == "holdout":
        card_ids = list(WARD_DECK)
    elif mode.cards:
        card_ids = list(state["deck"])
    else:
        card_ids = []

    state["encounter"] = encounter.new(
        mind=mind,
        seed=f"{state['seed']}:f{floor}",
        floor=floor,
        # A holdout is scored against the player's resolve, so a resistance
        # figure here would be a number in the payload that means nothing.
        resistance=0 if plan["kind"] == "holdout" else resistance_for(mode, floor, mind),
        turns=turns_for(mode, plan, mind),
        card_ids=card_ids,
        relic_effects=eff,
        kind=plan["kind"],
    )
    state["encounter"]["slot"] = plan["slot"]
    state["phase"] = "encounter"
    return state["encounter"]


# ------------------------------------------------------------------ rewards
RARITY_WEIGHTS = {
    #                 common uncommon rare legendary
    1: (62.0, 30.0, 7.0, 1.0),
    2: (50.0, 35.0, 13.0, 2.0),
    3: (38.0, 40.0, 18.0, 4.0),
    4: (28.0, 40.0, 25.0, 7.0),
    5: (20.0, 38.0, 30.0, 12.0),
}
RARITIES = ("common", "uncommon", "rare", "legendary")


def _band(floor: int) -> tuple:
    return RARITY_WEIGHTS[max(1, min(5, 1 + (floor - 1) // 2))]


def roll_offers(state: dict, *, salt: str = "") -> list[dict]:
    """Three things to pick from. Locked content never appears, so the
    collection screen is a promise rather than a tease."""
    mode = mode_of(state)
    floor = state["floor"]
    seed = f"{state['seed']}:offer:{floor}:{salt}"
    unlocked = set(state.get("unlocked") or [])
    weights = _band(floor)

    pool = [c for c in all_cards("tactic")
            if c["rarity"] != "starter" and meta.is_available(c["id"], unlocked)]
    counts = {r: [c for c in pool if c["rarity"] == r] for r in RARITIES}

    offers: list[dict] = []
    want_relic = mode.rewards and (floor % 3 == 0 or state["encounter"].get("slot") == "boss")
    if want_relic:
        relic_pool = [r for r in all_relics()
                      if r["id"] not in (state.get("relics") or [])
                      and meta.is_available(r["id"], unlocked)]
        if relic_pool:
            weighted = rng.weighted(
                f"{seed}:relic", relic_pool,
                [_relic_weight(r, weights) for r in relic_pool], 1,
            )
            offers.append({"type": "relic", **weighted[0]})

    need = 3 - len(offers)
    taken: set[str] = set()
    for i in range(need):
        rarity = rng.weighted(f"{seed}:rarity:{i}", list(RARITIES), list(weights), 1)[0]
        chain = [rarity] + [r for r in RARITIES if r != rarity]
        for r in chain:
            options = [c for c in counts[r] if c["id"] not in taken]
            if options:
                pick = rng.pick(f"{seed}:card:{i}", options)
                taken.add(pick["id"])
                offers.append({"type": "card", **pick})
                break
    state["offers"] = offers
    return offers


def _relic_weight(relic: dict, weights: tuple) -> float:
    try:
        return weights[RARITIES.index(relic["rarity"])]
    except ValueError:
        return weights[0]


def take_offer(state: dict, index: int) -> dict | None:
    offers = state.get("offers") or []
    if not 0 <= index < len(offers):
        return None
    pick = offers[index]
    if pick["type"] == "relic":
        state["relics"].append(pick["id"])
        eff = effects(state)
        state["revives"] = int(eff.get("revive", 0) or 0) - state.get("revives_used", 0)
        state["rerolls"] = int(eff.get("reroll", 0) or 0) - state.get("rerolls_used", 0)
    else:
        state["deck"].append(pick["id"])
    state["offers"] = []
    return pick


# -------------------------------------------------------------------- floors
def finish_floor(state: dict, outcome: str, enc_stats: dict) -> dict:
    """Fold a finished encounter into the run. Returns a summary row."""
    enc = state["encounter"] or {}
    mode = mode_of(state)
    eff = effects(state)
    turns_spare = max(0, enc.get("turns", 0) - enc.get("turn", 0))
    boss = enc.get("slot") == "boss"

    points = 0
    if outcome == "win":
        points = combat.floor_points(
            state["floor"], turns_spare=turns_spare, crits=enc_stats.get("crits", 0),
            boss=boss, compound=float(eff.get("score_compound", 0) or 0),
        )
        if enc.get("kind") == "holdout":
            points = int(points * 1.25)      # holding is worth more than winning
        state["score"] += points

    for key in ("crits", "fumbles", "words"):
        state["stats"][key] = state["stats"].get(key, 0) + enc_stats.get(key, 0)
    state["stats"]["best_hit"] = max(state["stats"]["best_hit"], enc_stats.get("best_hit", 0))
    state["stats"]["turns"] = state["stats"].get("turns", 0) + enc.get("turn", 0)

    row = {
        "floor": state["floor"],
        "mind": enc.get("mind", {}).get("name", ""),
        "mind_id": enc.get("mind_id", ""),
        "sigil": enc.get("mind", {}).get("sigil", "?"),
        "slot": enc.get("slot", ""),
        "kind": enc.get("kind", "sway"),
        "outcome": outcome,
        "points": points,
        "turns": enc.get("turn", 0),
        "of": enc.get("turns", 0),
        "best_hit": enc_stats.get("best_hit", 0),
        "crits": enc_stats.get("crits", 0),
    }
    state["history"].append(row)
    return row


def revive_available(state: dict) -> bool:
    eff = effects(state)
    return int(eff.get("revive", 0) or 0) > state.get("revives_used", 0)


def run_over(state: dict, outcome: str) -> bool:
    mode = mode_of(state)
    if outcome == "loss":
        return True
    return not mode.endless and state["floor"] >= mode.floors


# --------------------------------------------------------------------- result
BLOCK = {"clear": "🟩", "tight": "🟨", "hold": "🟦", "fail": "🟥", "miss": "⬛"}


def share_rows(state: dict) -> list[str]:
    mode = mode_of(state)
    cells = []
    for row in state["history"]:
        if row["outcome"] != "win":
            cells.append(BLOCK["fail"])
        elif row["kind"] == "holdout":
            cells.append(BLOCK["hold"])
        elif row["of"] and row["turns"] <= max(1, row["of"] // 2):
            cells.append(BLOCK["clear"])
        else:
            cells.append(BLOCK["tight"])
    total = mode.floors if not mode.endless else len(cells)
    cells += [BLOCK["miss"]] * max(0, total - len(cells))
    rows = ["".join(cells[i:i + 8]) for i in range(0, len(cells), 8)] or [BLOCK["miss"]]
    return rows


def build_result(state: dict, outcome: str) -> Result:
    mode = mode_of(state)
    cleared = sum(1 for r in state["history"] if r["outcome"] == "win")
    stats = state["stats"]
    if outcome == "win":
        headline = "THE NINTH DOOR" if mode.id == "ascent" else f"{mode.name.upper()} CLEARED"
        if mode.id == "holdout":
            headline = "YOU GAVE THEM NOTHING"
        if mode.id == "blitz":
            headline = "TALKED THROUGH"
    else:
        headline = f"FLOOR {state['floor']}" if cleared else "NO GROUND GAINED"
    last = state["history"][-1] if state["history"] else {}
    detail = " · ".join(filter(None, [
        f"{cleared} floor{'s' if cleared != 1 else ''} cleared",
        f"best hit {stats['best_hit']}" if stats.get("best_hit") else "",
        f"{stats['crits']} masterful" if stats.get("crits") else "",
        f"stopped by {last.get('mind')}" if outcome != "win" and last.get("mind") else "",
    ]))
    return Result(
        outcome=outcome,
        score=state["score"],
        headline=headline,
        detail=detail,
        stats={
            "mode": mode.id,
            "cleared": cleared,
            "floor": state["floor"],
            "crits": stats.get("crits", 0),
            "fumbles": stats.get("fumbles", 0),
            "best_hit": stats.get("best_hit", 0),
            "words": stats.get("words", 0),
            "relics": len(state.get("relics") or []),
            "deck": len(state.get("deck") or []),
            "daily": bool(state.get("daily_key")),
        },
        share=share_rows(state),
        reveal={
            "history": state["history"],
            "relics": [r for r in (state.get("relics") or [])],
            "deck": sorted(state.get("deck") or []),
        },
    )


# --------------------------------------------------------------------- public
def public(state: dict) -> dict:
    mode = mode_of(state)
    enc = state.get("encounter")
    upcoming = []
    if not mode.endless:
        for floor in range(1, mode.floors + 1):
            done = next((r for r in state["history"] if r["floor"] == floor), None)
            if done:
                upcoming.append({"floor": floor, "slot": done["slot"], "kind": done["kind"],
                                 "sigil": done["sigil"], "name": done["mind"],
                                 "outcome": done["outcome"]})
            elif enc and state["floor"] == floor:
                upcoming.append({"floor": floor, "slot": enc.get("slot", ""),
                                 "kind": enc.get("kind", "sway"),
                                 "sigil": enc["mind"].get("sigil", "?"),
                                 "name": enc["mind"]["name"], "outcome": "now"})
            else:
                slot = _ladder_slot(mode, floor)
                upcoming.append({"floor": floor, "slot": str(slot),
                                 "kind": "holdout" if slot == "gate" else "sway",
                                 "sigil": "?", "name": "", "outcome": ""})
    return {
        "mode": mode.id,
        "mode_name": mode.name,
        "accent": mode.accent,
        "floor": state["floor"],
        "floors": 0 if mode.endless else mode.floors,
        "endless": mode.endless,
        "score": state["score"],
        "phase": state["phase"],
        "deck": [{"id": cid, **(get_card(cid) or {})} for cid in state.get("deck") or []],
        "relics": relicmod.hydrate(state.get("relics") or []),
        "offers": state.get("offers") or [],
        "rerolls": max(0, state.get("rerolls", 0)),
        "revives": max(0, int(effects(state).get("revive", 0) or 0) - state.get("revives_used", 0)),
        "map": upcoming,
        "history": state["history"],
        "daily": bool(state.get("daily_key")),
        "encounter": encounter.public(enc) if enc else None,
        "result": state.get("result"),
        "stats": state["stats"],
    }
