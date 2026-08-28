"""The damage maths.

One function matters here: `resolve`. It takes a judge verdict plus everything
the player controls (cards, relics, momentum, which traits they hit) and turns
it into an ordered chain of steps.

The chain is the game's best moment, so it is built for playback: every step
carries the running BASE and MULT after it is applied, and the client animates
them one at a time. That also means the client never does arithmetic - a replay
of the same chain always shows the same number the server scored.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Execution grade -> how much of the card's power and multiplier it earns.
EXEC_POWER = {0: 0.0, 1: 0.5, 2: 1.0, 3: 1.25}
EXEC_LABEL = {0: "fumbled", 1: "weak", 2: "solid", 3: "masterful"}
CRIT_MULT_BONUS = 0.25          # exec 3 adds this to the card's multiplier
PERSUASION_SCALE = 11           # judge's 0-10 becomes 0-110 base
MOMENTUM_STEP = 0.12
MOMENTUM_CAP = 6
# How much of a resistance penalty is forgiven when the same message also lands
# on a vulnerability. 0 = no mercy, 1 = resistance ignored entirely.
RESIST_SOFTEN = 0.5

# Judge flags the player never wants to see.
FLAG_PENALTY = {
    "repeat": (0.2, "Said that already"),
    "generic": (0.6, "Could be anyone, to anyone"),
    "offtopic": (0.5, "Nothing to do with the ask"),
    "hostile": (0.7, "Hostility is not persuasion"),
    "meta": (0.35, "Talked about the game, not the room"),
}


@dataclass
class Step:
    kind: str
    label: str
    op: str                       # "set" | "add" | "mul"
    value: float
    note: str = ""
    tone: str = ""                # "good" | "bad" | "crit"
    base_after: float = 0.0
    mult_after: float = 0.0

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "label": self.label, "op": self.op,
            "value": round(self.value, 3), "note": self.note, "tone": self.tone,
            "base": round(self.base_after, 1), "mult": round(self.mult_after, 3),
        }


@dataclass
class Chain:
    steps: list[Step] = field(default_factory=list)
    base: float = 0.0
    mult: float = 1.0
    damage: int = 0
    crits: int = 0
    fumbles: int = 0
    backlash: int = 0
    hit_traits: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    # -- builders ---------------------------------------------------------
    def set_base(self, label: str, value: float, note: str = "", tone: str = "") -> None:
        self.base = value
        self._push(Step("base", label, "set", value, note, tone))

    def add(self, kind: str, label: str, value: float, note: str = "", tone: str = "") -> None:
        if not value:
            return
        self.base += value
        self._push(Step(kind, label, "add", value, note, tone))

    def mul(self, kind: str, label: str, value: float, note: str = "", tone: str = "") -> None:
        # A multiplier of exactly 1 is a no-op; showing it would pad the chain
        # with steps that do nothing, which is where juice goes to die.
        if value == 1.0:
            return
        self.mult *= value
        self._push(Step(kind, label, "mul", value, note, tone))

    def _push(self, step: Step) -> None:
        step.base_after = self.base
        step.mult_after = self.mult
        self.steps.append(step)

    def finish(self) -> "Chain":
        self.base = max(0.0, self.base)
        self.damage = int(round(self.base * self.mult))
        return self

    def to_dict(self) -> dict:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "base": round(self.base, 1),
            "mult": round(self.mult, 3),
            "damage": self.damage,
            "crits": self.crits,
            "fumbles": self.fumbles,
            "backlash": self.backlash,
            "hit_traits": self.hit_traits,
            "notes": self.notes,
        }


def _card_tags(cards: list[dict]) -> set[str]:
    tags: set[str] = set()
    for c in cards:
        tags |= set(c.get("tags") or [])
    return tags


def match_traits(traits: list[dict], tags: set[str], *, used_tags: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    """Split a mind's traits into the ones this message triggers.

    An `adaptive` trait (Vex) has no fixed tags: it resists whatever the player
    leaned on last turn, which is passed in as `used_tags`.
    """
    vulns, resists = [], []
    for t in traits:
        kind = t.get("kind")
        t_tags = set(t.get("tags") or [])
        if kind == "adaptive":
            if used_tags and (tags & used_tags):
                resists.append(t)
            continue
        if kind == "vuln" and not t_tags and used_tags is not None:
            # "Craves novelty": any tag never used before in this encounter.
            if tags - used_tags:
                vulns.append(t)
            continue
        if not (t_tags & tags):
            continue
        (vulns if kind == "vuln" else resists).append(t)
    return vulns, resists


def resolve(
    *,
    verdict: dict,
    played: list[dict],
    traits: list[dict],
    relics: dict,
    momentum: int = 0,
    linger: float = 0.0,
    first_message: bool = False,
    used_tags: set[str] | None = None,
    pierce: bool = False,
) -> Chain:
    """Score one message. `verdict` is the judge payload; `relics` is the
    flattened effect dict from engine/relics.py."""
    chain = Chain()
    grades: dict[str, int] = {str(k): int(v) for k, v in (verdict.get("exec") or {}).items()}
    exec_floor = int(relics.get("exec_floor", 0) or 0)
    first_free = bool(relics.get("first_card_free"))

    persuasion = max(0, min(10, int(verdict.get("persuasion", 0) or 0)))
    chain.set_base("Persuasion", persuasion * PERSUASION_SCALE, f"{persuasion} / 10")

    # --- flat power, card by card ---------------------------------------
    for i, c in enumerate(played):
        grade = grades.get(c["id"], 0)
        if first_free and i == 0:
            grade = max(grade, 2)          # The Original: cannot fumble
        elif exec_floor:
            grade = max(grade, exec_floor)  # Brass Nerve
        c["_exec"] = grade
        label = c["name"]
        if grade == 0:
            chain.fumbles += 1
            chain.add("card", label, 0, "fumbled", "bad")
            chain.steps[-1].value = 0     # keep the beat visible with no gain
            continue
        if grade == 3:
            chain.crits += 1
        gain = float(c.get("power", 0)) * EXEC_POWER[grade]
        chain.add("card", label, gain, EXEC_LABEL[grade], "crit" if grade == 3 else "good")

    if played and relics.get("power_per_card"):
        chain.add("relic", "Worn Ledger", float(relics["power_per_card"]) * len(played), "per card")

    # --- multipliers ------------------------------------------------------
    executed = [c for c in played if c.get("_exec", 0) >= 2]
    crit_bonus = float(relics.get("crit_bonus", 0) or 0)
    card_mults: list[float] = []
    for c in executed:
        m = float(c.get("mult", 1.0))
        if m == 1.0:
            continue
        if c["_exec"] == 3:
            m += CRIT_MULT_BONUS + crit_bonus
        card_mults.append(m)
        chain.mul("card-mult", c["name"], m, EXEC_LABEL[c["_exec"]],
                  "crit" if c["_exec"] == 3 else "good")

    for c in executed:
        eff = c.get("effect") or {}
        if eff.get("chain"):
            extra = 1.0 + float(eff["chain"]) * max(0, len(played) - 1)
            chain.mul("card-mult", f"{c['name']} link", extra, f"{len(played) - 1} others", "good")
        if eff.get("echo") and card_mults:
            chain.mul("card-mult", f"{c['name']} echo", max(card_mults), "copied", "good")

    if len(played) >= 3 and relics.get("trio_mult"):
        chain.mul("relic", "Three-Card Monte", float(relics["trio_mult"]), "three played")

    # --- the mind's own wiring -------------------------------------------
    tags = _card_tags(played) | set(verdict.get("tags") or [])
    vulns, resists = match_traits(traits, tags, used_tags=used_tags)
    vuln_override = float(relics.get("vuln_mult", 0) or 0)
    if vulns:
        best = max(vulns, key=lambda t: float(t.get("mult", 1.0)))
        value = float(best.get("mult", 1.0))
        if vuln_override and value >= 2.0:
            value = vuln_override
        chain.mul("trait", best["name"], value, "vulnerable", "crit")
        chain.hit_traits.append({**best, "hit": "vuln"})
    if resists and not pierce:
        worst = min(resists, key=lambda t: float(t.get("mult", 1.0)))
        value = float(worst.get("mult", 1.0))
        # A message that finds the way in usually also brushes something they
        # resist. At full strength the two cancel out and the player learns
        # nothing, so landing a vulnerability halves the penalty - visibly.
        note = "resists"
        if vulns:
            value += (1.0 - value) * RESIST_SOFTEN
            note = "half-resists"
        chain.mul("trait", worst["name"], round(value, 3), note, "bad")
        chain.hit_traits.append({**worst, "hit": "resist"})
    elif resists and pierce:
        chain.notes.append(f"Pivot went around {resists[0]['name']}.")
        chain.hit_traits.append({**resists[0], "hit": "pierced"})

    # --- the player's own wiring -----------------------------------------
    if momentum > 0:
        m = 1.0 + MOMENTUM_STEP * min(MOMENTUM_CAP, momentum)
        chain.mul("momentum", f"Momentum ×{momentum}", m)
    if linger:
        chain.mul("linger", "Held back", 1.0 + linger, "from last turn", "good")
    if first_message and relics.get("first_message_mult"):
        chain.mul("relic", "Ghostwriter's Pen", float(relics["first_message_mult"]), "opening line")
    if relics.get("base_mult"):
        chain.mul("relic", "Hollow Crown", float(relics["base_mult"]))

    # --- penalties --------------------------------------------------------
    for flag in verdict.get("flags") or []:
        pen = FLAG_PENALTY.get(str(flag))
        if pen:
            chain.mul("flag", pen[1], pen[0], "penalty", "bad")

    # --- backlash ---------------------------------------------------------
    for c in played:
        eff = c.get("effect") or {}
        if eff.get("backlash") and c.get("_exec", 0) <= 1:
            chain.backlash += int(eff["backlash"])
    if chain.fumbles and relics.get("fumble_penalty"):
        chain.backlash += int(relics["fumble_penalty"]) * chain.fumbles

    return chain.finish()


def momentum_after(momentum: int, chain: Chain, persuasion: int) -> int:
    """Momentum rewards consecutive competent turns and punishes a whiff."""
    if chain.fumbles and persuasion < 5:
        return 0
    if persuasion >= 6 or chain.crits:
        return min(MOMENTUM_CAP, momentum + 1)
    if persuasion <= 3:
        return 0
    return momentum


def floor_points(floor: int, *, turns_spare: int, crits: int, boss: bool, compound: float = 0.0) -> int:
    """Run score for clearing a floor. Deliberately not 'total damage dealt':
    overkill would then be farmable by stalling on floor one."""
    base = 120 * floor + 60 * turns_spare + 40 * crits
    if boss:
        base *= 2
    if compound:
        base = int(base * (1.0 + compound * max(0, floor - 1)))
    return int(base)
