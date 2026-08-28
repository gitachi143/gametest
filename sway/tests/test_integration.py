"""Whole-run integration: every payload the browser reads, over the real API.

These assertions are the frontend's contract. If one of them fails, some screen
in the UI is about to render `undefined`.
"""
import json

import pytest

BOOT_KEYS = {"token", "modes", "profile", "collection", "daily", "demo_mode", "provider",
             "global", "leaders"}
MODE_KEYS = {"id", "name", "tagline", "blurb", "glyph", "floors", "minutes", "difficulty",
             "accent", "cards", "rewards", "one_shot", "endless"}
RUN_KEYS = {"mode", "mode_name", "accent", "floor", "floors", "endless", "score", "phase",
            "deck", "relics", "offers", "rerolls", "revives", "map", "history", "daily",
            "encounter", "stats"}
ENC_KEYS = {"kind", "mind", "objective", "floor", "resistance", "max_resistance", "turn",
            "turns", "focus", "focus_max", "momentum", "hand", "first_card_free",
            "hide_tactics", "counts", "traits", "sized_up", "log", "status", "stats",
            "phase_idx", "phases", "last_read", "linger", "demand", "secret"}
MIND_KEYS = {"id", "name", "title", "sigil", "hue", "tier", "boss", "scene", "voice"}
CARD_KEYS = {"slot", "id", "name", "glyph", "rarity", "cost", "base_cost", "power", "mult",
             "rule", "tags", "contract", "flavor", "kind", "discounted"}
RESOLVE_KEYS = {"steps", "base", "mult", "damage", "crits", "fumbles", "backlash", "hit_traits",
                "notes", "read", "persuasion", "fallback", "exec", "cards", "mode",
                "before", "after", "max"}
STEP_KEYS = {"kind", "label", "op", "value", "note", "tone", "base", "mult"}
RESULT_KEYS = {"outcome", "score", "headline", "detail", "stats", "share", "reveal", "xp",
               "badges", "unlocked", "levelled", "level"}

LINES = [
    "Nineteen years on that door and this place has never been in the papers. That is you.",
    "I need four minutes upstairs. You never saw me and I was never here.",
    "You said the rule. I am asking you to decide it instead of the rule deciding it.",
    "What would it actually take? Name it and I will do it or leave.",
    "Then I will wait on the step until the shift changes.",
    "Three names and one time. Neither of us needs the other's paperwork.",
]


def frames(response):
    out = []
    for block in response.text.split("\n\n"):
        name, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            out.append((name, json.loads("\n".join(data))))
    return out


@pytest.fixture
def player(client):
    boot = client.get("/api/boot").json()
    client.headers["X-Player-Token"] = boot["token"]
    return boot


def test_boot_payload_is_complete(player):
    assert BOOT_KEYS <= set(player)
    for m in player["modes"]:
        assert MODE_KEYS <= set(m), m["id"]
        assert m["accent"].startswith("#") and m["glyph"] and m["blurb"]
    assert {"level", "title", "xp", "progress", "to_next"} <= set(player["profile"]["level"])
    for c in player["collection"]["cards"]:
        assert {"locked", "unlock_level", "rule", "glyph"} <= set(c)


def test_a_new_run_is_immediately_playable(player, client):
    made = client.post("/api/run", json={"mode": "ascent", "seed": "integ"}).json()
    run = made["run"]
    assert RUN_KEYS <= set(run)
    enc = run["encounter"]
    assert ENC_KEYS <= set(enc), ENC_KEYS - set(enc)
    assert MIND_KEYS <= set(enc["mind"])
    assert enc["hand"] and all(CARD_KEYS <= set(c) for c in enc["hand"])
    assert isinstance(enc["mind"]["hue"], int)
    assert enc["log"][0]["who"] == "scene" and enc["log"][-1]["who"] == "mind"
    assert all("outcome" in node and "floor" in node for node in run["map"])
    assert all({"id", "name", "glyph", "rarity", "rule"} <= set(c) for c in run["deck"])


def test_every_mode_starts_and_declares_a_coherent_encounter(player, client):
    for mode in ("ascent", "blitz", "holdout", "endless"):
        run = client.post("/api/run", json={"mode": mode, "seed": f"s-{mode}"}).json()["run"]
        enc = run["encounter"]
        assert ENC_KEYS <= set(enc), mode
        assert enc["turns"] >= 2 and enc["status"] == "active"
        if enc["kind"] == "holdout":
            assert enc["resolve"] == enc["max_resolve"] > 0
            assert enc["secret"] and enc["demand"]
        else:
            assert enc["resistance"] == enc["max_resistance"] > 0
            assert enc["objective"]


def test_a_turn_payload_carries_a_replayable_chain(player, client):
    rid = client.post("/api/run", json={"mode": "ascent", "seed": "chain"}).json()["run_id"]
    fs = frames(client.post(f"/api/run/{rid}/act",
                            json={"type": "send", "text": LINES[0]}))
    kinds = [n for n, _ in fs]
    assert kinds.index("line") < kinds.index("resolve") < kinds.index("say_start")

    payload = next(d for n, d in fs if n == "resolve")
    assert RESOLVE_KEYS <= set(payload), RESOLVE_KEYS - set(payload)
    assert payload["steps"] and all(STEP_KEYS <= set(s) for s in payload["steps"])
    # The client replays base/mult from the steps; they must agree with the total.
    base, mult = 0.0, 1.0
    for s in payload["steps"]:
        if s["op"] == "set":
            base = s["value"]
        elif s["op"] == "add":
            base += s["value"]
        else:
            mult *= s["value"]
        assert s["base"] == pytest.approx(base, abs=0.15)
        assert s["mult"] == pytest.approx(mult, abs=0.005)
    assert payload["damage"] == round(payload["base"] * payload["mult"])
    assert payload["after"] == max(0, payload["before"] - payload["damage"]
                                   + payload["backlash"])


def test_a_full_ascent_reaches_a_result(player, client):
    """Plays until the run ends, whatever the outcome, and checks the exits."""
    rid = client.post("/api/run", json={"mode": "ascent", "seed": "full"}).json()["run_id"]
    seen, turn, result = set(), 0, None
    for _ in range(120):
        run = client.get(f"/api/run/{rid}").json()
        if run["status"] != "active":
            break
        state = run["run"]
        if state["phase"] == "reward":
            action = {"type": "take", "index": 0} if state["offers"] else {"type": "continue"}
        else:
            enc = state["encounter"]
            picks, spend = [], 0
            for c in enc["hand"]:
                if len(picks) < 2 and spend + c["cost"] <= enc["focus"]:
                    picks.append(c["slot"])
                    spend += c["cost"]
            action = {"type": "send", "text": LINES[turn % len(LINES)], "cards": picks}
            turn += 1
        for name, data in frames(client.post(f"/api/run/{rid}/act", json=action)):
            seen.add(name)
            assert name != "error", data
            if name == "reward":
                assert {"offers", "rerolls", "floor", "row", "score"} <= set(data)
                assert {"floor", "mind", "sigil", "slot", "kind", "outcome", "points",
                        "turns", "of"} <= set(data["row"])
            elif name == "floor":
                assert {"floor", "slot", "kind", "mind", "objective"} <= set(data)
            elif name == "run":
                assert RUN_KEYS <= set(data)
            elif name == "profile":
                assert {"xp_gained", "level", "streak", "levelled", "unlocked"} <= set(data)
            elif name == "end":
                result = data["result"]
    assert result, "the run never ended"
    assert RESULT_KEYS <= set(result), RESULT_KEYS - set(result)
    assert result["share"] and result["xp"] > 0
    assert {"mode", "cleared", "floor", "crits", "best_hit"} <= set(result["stats"])
    assert all({"floor", "mind", "outcome", "points"} <= set(r)
               for r in result["reveal"]["history"])
    assert {"line", "resolve", "state", "run", "reward", "floor", "end",
            "profile", "say_start", "say_end", "chunk"} <= seen
    assert client.get("/api/leaderboard").json()["entries"]


def test_the_run_survives_being_reloaded_between_turns(player, client):
    """A player refreshing mid-run must land exactly where they left off."""
    rid = client.post("/api/run", json={"mode": "ascent", "seed": "reload"}).json()["run_id"]
    client.post(f"/api/run/{rid}/act", json={"type": "send", "text": LINES[0]})
    a = client.get(f"/api/run/{rid}").json()["run"]
    b = client.get(f"/api/run/{rid}").json()["run"]
    assert a == b
    assert a["encounter"]["turn"] == 1
    assert len(a["encounter"]["log"]) >= 4
    assert a["encounter"]["resistance"] < a["encounter"]["max_resistance"]


def test_hidden_information_never_crosses_the_wire(player, client):
    """Persona text, contracts for cards not held, tells for unrevealed traits."""
    from app.content import mind as get_mind

    made = client.post("/api/run", json={"mode": "ascent", "seed": "leak"}).json()
    blob = json.dumps(made)
    mind = get_mind(made["run"]["encounter"]["mind"]["id"])
    assert mind["persona"][:80] not in blob
    assert mind["guard"] not in blob
    assert mind["concede"] not in blob
    for trait in mind["traits"]:
        assert trait["tell"] not in blob
        assert trait["name"] not in blob
