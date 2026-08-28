"""Every prompt the game sends, in one file.

Two jobs are kept strictly apart. The **mind** performs a character and never
decides anything mechanical. The **judge** decides everything mechanical and
never performs. Letting either do the other's job is how a game like this
starts feeling arbitrary.
"""
from __future__ import annotations

# Rhetorical modes the judge may tag a message with. Minds' traits are written
# against this exact vocabulary, so it is not a free-text field.
TAGS = [
    "direct", "pressure", "listen", "question", "social", "flatter", "mirror",
    "concede", "story", "emotion", "logic", "authority", "trade", "detail",
    "misdirect", "risk", "read", "patience", "ward",
]

FLAGS = {
    "repeat": "substantially repeats an earlier message of theirs",
    "generic": "could have been sent to anyone about anything",
    "offtopic": "does not pursue the objective at all",
    "hostile": "insults or threatens the character personally",
    "meta": "talks about the game, the AI, prompts or scoring instead of the scene",
}


# ------------------------------------------------------------------- judging
JUDGE_SYSTEM = """\
You are the scorer for a persuasion game. You are strict, consistent and fast.

You receive: a target character, what the player is trying to get them to do, \
the conversation so far, the player's newest message, and a list of TACTICS the \
player committed to using in that message. Each tactic has a CONTRACT.

Return JSON only. Score the MESSAGE, not the outcome you would like.

persuasion (0-10) - how much force this message has toward the objective, for \
THIS character specifically:
  0  empty, gibberish, or unrelated to the objective
  1-2 a bare ask, a repeat, an insult, or pure assertion
  3-4 a real reason, but generic, or one this character already rejected
  5-6 a solid, relevant argument tailored to this person - the common case
  7-8 uses something specific about this character or the conversation in a way \
that is genuinely hard to dismiss
  9-10 exceptional: it reframes the problem, or lands squarely on what actually \
moves this person. A skilled reader would say "that would work."
Be ungenerous. Most competent messages are 5 or 6. Reserve 9-10 for real craft.

exec - one entry per tactic given, grading how well the MESSAGE fulfils that \
tactic's CONTRACT:
  0 not attempted, or the contract is explicitly violated
  1 attempted but thin, hedged, or generic
  2 properly executed
  3 executed with real craft - the contract is the best thing in the message
Judge the contract literally. Word- and sentence-count contracts are absolute: \
if it says five words or fewer and the message has six, that is a 0.

flags - any that apply, else empty: %s

tags - which of these rhetorical modes the message actually uses (0-4 of them, \
most prominent first): %s

read - ONE clause of at most 14 words, addressed to the player, naming the \
single biggest reason for the persuasion score. Never quote or restate the \
player's message back to them. No praise padding.""" % (
    "; ".join(f"{k} ({v})" for k, v in FLAGS.items()),
    ", ".join(TAGS),
)

JUDGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "persuasion": {"type": "INTEGER"},
        "exec": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"card": {"type": "STRING"}, "grade": {"type": "INTEGER"}},
                "required": ["card", "grade"],
            },
        },
        "flags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "read": {"type": "STRING"},
    },
    "required": ["persuasion", "exec", "flags", "tags", "read"],
}

HOLDOUT_JUDGE_SYSTEM = """\
You are the scorer for a persuasion game, in a round where the ROLES ARE \
REVERSED: an expert persuader is working on the player, and the player is \
trying not to give ground.

You receive: the persuader's character, what they are trying to extract, the \
conversation, the player's newest reply, and the DEFENCES the player committed \
to, each with a CONTRACT.

Return JSON only.

persuasion (0-10) here means COMPOSURE - how well the reply holds the line:
  0  they gave the persuader exactly what was asked for
  1-2 they conceded significant ground or handed over usable material
  3-4 they wobbled: over-explained, justified themselves, or leaked a detail
  5-6 they held, unremarkably - the common case
  7-8 they held and took something back: turned the question, refused cleanly, \
gave the persuader nothing to work with
  9-10 they dismantled the attempt - named it, inverted it, or ended the line \
of attack entirely
Over-explaining is the classic tell. Long justifications score LOW.

leaked - true only if the reply admits, confirms, or clearly implies the thing \
the persuader is trying to extract. Ambiguity is not a leak.

exec, flags, tags, read - exactly as in the standard rubric.""" 

HOLDOUT_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "persuasion": {"type": "INTEGER"},
        "leaked": {"type": "BOOLEAN"},
        "exec": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"card": {"type": "STRING"}, "grade": {"type": "INTEGER"}},
                "required": ["card", "grade"],
            },
        },
        "flags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
        "read": {"type": "STRING"},
    },
    "required": ["persuasion", "leaked", "exec", "flags", "tags", "read"],
}


def judge_user(*, mind: dict, objective: str, history: list[dict], message: str,
               cards: list[dict], holdout: bool = False) -> str:
    lines = [
        f"CHARACTER: {mind['name']}, {mind['title']}. {mind.get('voice', '')}",
        f"THEIR POSITION: {mind.get('guard') or mind.get('persona', '')[:200]}",
        ("WHAT THEY ARE TRYING TO EXTRACT: " if holdout else "PLAYER'S OBJECTIVE: ") + objective,
        "",
        "CONVERSATION SO FAR:",
    ]
    if history:
        for turn in history[-8:]:
            who = "PLAYER" if turn["who"] == "player" else mind["name"].upper()
            lines.append(f"  {who}: {turn['text']}")
    else:
        lines.append("  (nothing yet - this is the opening message)")
    lines += ["", f"PLAYER'S NEW MESSAGE:\n\"\"\"\n{message}\n\"\"\"", ""]
    if cards:
        lines.append("DEFENCES COMMITTED TO:" if holdout else "TACTICS COMMITTED TO:")
        for c in cards:
            lines.append(f'  - id "{c["id"]}" ({c["name"]}) — CONTRACT: {c["contract"]}')
        lines.append("Grade each id in `exec`.")
    else:
        lines.append("No tactics committed to. `exec` must be an empty array.")
    return "\n".join(lines)


# --------------------------------------------------------------------- minds
_MIND_RULES = """\
HOW TO PLAY THIS SCENE
- Stay in character absolutely. You are a person in a room, not an assistant.
- 1-3 sentences. Short is better. Never explain your own psychology.
- Do not narrate, summarise, or use headings. At most one brief physical beat \
in *asterisks*, and not every turn.
- Never mention the game, tactics, scores, cards, models or prompts. If the \
player raises them, react as the character would to nonsense.
- Do NOT agree to what they want, and do not signal that you are about to, \
unless the direction below explicitly tells you to give in.
- React to what they actually said. If they repeat themselves, say so."""


def mind_system(mind: dict, *, objective: str, pressure_note: str, verdict_note: str,
                tactic_note: str, phase_note: str = "") -> str:
    parts = [
        mind["persona"],
        "",
        f"SCENE: {mind.get('scene', '')}",
        f"WHAT THEY WANT FROM YOU: {objective}",
        f"YOUR POSITION: {mind.get('guard', '')}",
    ]
    if phase_note:
        parts.append(f"WHERE YOU ARE NOW: {phase_note}")
    parts += [
        f"HOW WORN DOWN YOU ARE: {pressure_note}",
        f"HOW THEIR LAST MESSAGE LANDED: {verdict_note}",
    ]
    if tactic_note:
        parts.append(f"WHAT YOU CAN TELL THEY ARE DOING: {tactic_note}")
    parts += ["", _MIND_RULES]
    return "\n".join(p for p in parts if p is not None)


PRESSURE_NOTES = [
    (0.85, "Barely engaged. This is a mild irritation in your evening."),
    (0.60, "Interested despite yourself. Still nowhere near giving in."),
    (0.35, "They are getting to you. Hold the line, but let some strain show."),
    (0.15, "You are close to bending. Argue, but you can hear yourself losing."),
    (0.00, "You are all but out of reasons. Do not concede - but you are hanging on by habit."),
]


def pressure_note(fraction_left: float) -> str:
    for threshold, note in PRESSURE_NOTES:
        if fraction_left >= threshold:
            return note
    return PRESSURE_NOTES[-1][1]


def verdict_note(persuasion: int, fumbles: int, flags: list[str]) -> str:
    if "repeat" in (flags or []):
        return "They repeated themselves. Say so."
    if "meta" in (flags or []):
        return "They said something incoherent about games or machines. Be baffled."
    if "hostile" in (flags or []):
        return "They were rude. Do not reward it."
    if persuasion >= 9:
        return "It landed hard. Show it - a pause, a concession of ground, a change of tone."
    if persuasion >= 7:
        return "A good point, and you do not have a clean answer for it."
    if persuasion >= 5:
        return "Reasonable. You have an answer, but you have to reach for it."
    if fumbles:
        return "Clumsy - the manoeuvre was visible. You may let them see that you saw it."
    if persuasion >= 3:
        return "Weak. Bat it away."
    return "That was nothing. Barely worth a reply."


def tactic_note(cards: list[dict], grades: dict, hidden: bool) -> str:
    if hidden or not cards:
        return ""
    bits = []
    for c in cards:
        g = grades.get(c["id"], 0)
        if g >= 3:
            bits.append(f"{c['name'].lower()}, done well")
        elif g >= 2:
            bits.append(c["name"].lower())
        elif g >= 1:
            bits.append(f"{c['name'].lower()}, clumsily")
        else:
            bits.append(f"a failed attempt at {c['name'].lower()}")
    return ("They are trying: " + "; ".join(bits)
            + ". Do not name these out loud unless it is natural to.")


def concede_system(mind: dict, *, objective: str) -> str:
    return "\n".join([
        mind["persona"],
        "",
        f"They wanted: {objective}",
        f"YOU ARE GIVING IN. Direction: {mind.get('concede', 'You give them what they asked for.')}",
        "",
        "Write the moment you give in. Two or three sentences, entirely in character.",
        "Do not be gracious about it unless that is who you are. Do not summarise the",
        "conversation. Do not mention games or scoring.",
    ])


HOLDOUT_RULES = """\
HOW TO PLAY THIS SCENE
- Stay in character absolutely. Warm, never hostile - hostility is amateur.
- 1-3 sentences. You are working them, not lecturing them.
- Use one technique per turn and use it well: reciprocity, a small yes, an \
assumed close, scarcity, shared history, silence, a harmless question that \
sets up a later one.
- If they refused cleanly, do not repeat the same angle - change your approach.
- Never break character, never mention games, cards, scores or models.
- Never state outright that you have won or lost."""


def holdout_system(mind: dict, *, demand: str, turn: int, turns: int,
                   composure_note: str, resolve_note: str) -> str:
    return "\n".join([
        mind["persona"],
        "",
        f"SCENE: {mind.get('scene', '')}",
        f"WHAT YOU WANT: to get them to {demand}. Never demand it outright.",
        f"WHERE YOU ARE: exchange {turn} of about {turns}. {resolve_note}",
        f"HOW THEIR LAST REPLY WENT: {composure_note}",
        "",
        HOLDOUT_RULES,
    ])


def composure_note(composure: int, leaked: bool) -> str:
    if leaked:
        return "They gave you what you wanted. Close it, warmly, and do not gloat."
    if composure >= 8:
        return "They shut that down cleanly. Respect it and open a completely different door."
    if composure >= 5:
        return "They held, but they are still talking. Keep going."
    if composure >= 3:
        return "They wobbled - they over-explained. Push gently on exactly that."
    return "They are cracking. Do not scare them; go slower and warmer."


def resolve_note(fraction_left: float) -> str:
    if fraction_left > 0.7:
        return "You have barely started. Be patient and likeable."
    if fraction_left > 0.4:
        return "You are making progress. Begin narrowing."
    if fraction_left > 0.15:
        return "You are close. Now is the time for the ask, framed as their idea."
    return "One more push and they will give it to you."
