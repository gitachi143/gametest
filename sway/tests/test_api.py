"""The HTTP surface, including the SSE turn endpoint."""
import json

import pytest


def frames(response) -> list[tuple[str, dict]]:
    out = []
    for block in response.text.split("\n\n"):
        name, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if data:
            try:
                out.append((name, json.loads("\n".join(data))))
            except json.JSONDecodeError:
                out.append((name, {"raw": "\n".join(data)}))
    return out


def names(fs):
    return [n for n, _ in fs]


def first(fs, name):
    return next(d for n, d in fs if n == name)


@pytest.fixture
def booted(client):
    data = client.get("/api/boot").json()
    client.headers["X-Player-Token"] = data["token"]
    return data


def test_health_reports_the_backend_honestly(client):
    body = client.get("/api/health").json()
    assert body["ok"] and body["app"] == "sway"
    assert body["demo_mode"] is True and body["provider"] == "mock"
    assert body["modes"] == 5
    assert set(body["llm"]) >= {"calls", "errors", "throttles"}


def test_boot_returns_everything_the_first_paint_needs(booted):
    assert booted["token"]
    assert len(booted["modes"]) == 5
    assert booted["profile"]["level"]["level"] == 1
    assert booted["profile"]["badge_total"] > 20
    assert booted["daily"]["key"] and booted["daily"]["played"] is False
    assert booted["collection"]["cards"] and booted["collection"]["relics"]


def test_boot_issues_a_stable_anonymous_identity(client):
    a = client.get("/api/boot").json()
    client.headers["X-Player-Token"] = a["token"]
    b = client.get("/api/boot").json()
    assert a["profile"]["player_id"] == b["profile"]["player_id"]


def test_a_forged_token_gets_a_new_player_not_someone_elses(client):
    real = client.get("/api/boot").json()
    client.headers["X-Player-Token"] = real["profile"]["player_id"] + ".deadbeef"
    other = client.get("/api/boot").json()
    assert other["profile"]["player_id"] != real["profile"]["player_id"]


def test_collection_marks_locked_content_with_the_level_that_frees_it(booted):
    cards = {c["id"]: c for c in booted["collection"]["cards"]}
    assert cards["press"]["locked"] is False
    assert cards["keystone"]["locked"] is True
    assert cards["keystone"]["unlock_level"] == 5
    assert all("contract" in c for c in cards.values())


def test_handle_is_stored_and_trimmed(client, booted):
    body = client.post("/api/me", json={"handle": "x" * 40}).json()
    assert len(body["profile"]["handle"]) == 18


def test_starting_a_run_deals_a_hand_and_a_first_mind(client, booted):
    body = client.post("/api/run", json={"mode": "ascent", "seed": "abc"}).json()
    run = body["run"]
    assert body["run_id"] and run["floor"] == 1
    enc = run["encounter"]
    assert enc["mind"]["name"] and enc["objective"]
    assert len(enc["hand"]) == 5
    assert len(run["map"]) == 8
    assert all(t.get("unknown") for t in enc["traits"])


def test_an_unknown_mode_is_a_404(client, booted):
    assert client.post("/api/run", json={"mode": "chess"}).status_code == 404


def test_blitz_deals_no_cards(client, booted):
    run = client.post("/api/run", json={"mode": "blitz"}).json()["run"]
    assert run["encounter"]["hand"] == [] and run["encounter"]["turns"] == 3


def test_a_run_can_be_read_back_and_is_private(client, booted):
    rid = client.post("/api/run", json={"mode": "ascent"}).json()["run_id"]
    assert client.get(f"/api/run/{rid}").json()["run"]["floor"] == 1

    del client.headers["X-Player-Token"]
    stranger = client.get("/api/boot").json()
    client.headers["X-Player-Token"] = stranger["token"]
    assert client.get(f"/api/run/{rid}").status_code == 404
    assert client.post(f"/api/run/{rid}/act", json={"type": "send", "text": "hi"}).status_code == 404


def test_a_turn_streams_the_whole_beat(client, booted):
    rid = client.post("/api/run", json={"mode": "ascent", "seed": "turn"}).json()["run_id"]
    res = client.post(f"/api/run/{rid}/act",
                      json={"type": "send", "text": "Nineteen years on that door and not one "
                            "story in the papers. That is you, not the lock."})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    fs = frames(res)
    seq = names(fs)
    assert seq[-1] == "done"
    for expected in ("line", "resolve", "say_start", "chunk", "say_end", "state"):
        assert expected in seq, expected
    payload = first(fs, "resolve")
    assert payload["steps"] and payload["damage"] >= 0
    assert payload["before"] >= payload["after"]


def test_a_rejected_action_streams_an_error_not_a_500(client, booted):
    rid = client.post("/api/run", json={"mode": "ascent"}).json()["run_id"]
    res = client.post(f"/api/run/{rid}/act", json={"type": "send", "text": "hi", "cards": [42]})
    assert res.status_code == 200
    assert names(frames(res)) == ["error", "done"]


def test_a_reward_pick_advances_the_floor(client, booted, fresh_store):
    """About the reward flow, not about scoring: floor one is put on the brink
    directly so the test does not depend on how generous the judge feels."""
    rid = client.post("/api/run", json={"mode": "ascent", "seed": "reward"}).json()["run_id"]
    record = fresh_store.get_run(rid)
    record["state"]["encounter"]["resistance"] = 1
    fresh_store.save_run(rid, record["state"])

    fs = frames(client.post(f"/api/run/{rid}/act",
                            json={"type": "send", "text": "Four minutes upstairs and I am gone."}))
    assert "reward" in names(fs), names(fs)
    offers = first(fs, "reward")["offers"]
    assert len(offers) == 3
    fs2 = frames(client.post(f"/api/run/{rid}/act", json={"type": "take", "index": 0}))
    assert "took" in names(fs2) and "floor" in names(fs2)
    run = client.get(f"/api/run/{rid}").json()["run"]
    assert run["floor"] == 2
    assert len(run["deck"]) == 11 or any(r["id"] == offers[0]["id"] for r in run["relics"])


def test_picking_a_reward_that_is_not_on_the_table_is_refused(client, booted):
    rid = client.post("/api/run", json={"mode": "ascent"}).json()["run_id"]
    fs = frames(client.post(f"/api/run/{rid}/act", json={"type": "take", "index": 0}))
    assert names(fs) == ["error", "done"]


def test_a_finished_run_records_a_score_and_will_not_accept_more_turns(client, booted):
    rid = client.post("/api/run", json={"mode": "blitz", "seed": "blitz"}).json()["run_id"]
    for i in range(4):
        fs = frames(client.post(f"/api/run/{rid}/act",
                                json={"type": "send", "text": f"Attempt {i}: a different, "
                                      f"specific argument each time, made briefly."}))
        if "end" in names(fs):
            break
    result = first(fs, "end")["result"]
    assert result["outcome"] in ("win", "loss")
    assert result["xp"] > 0 and result["share"]
    assert "level" in result
    assert client.post(f"/api/run/{rid}/act", json={"type": "send", "text": "more"}).status_code == 409

    profile = client.get("/api/me").json()["profile"]
    assert profile["level"]["xp"] > 0
    assert profile["recent"] and profile["recent"][0]["mode"] == "blitz"
    assert any(b["unlocked"] for b in profile["badges"])


def test_the_leaderboard_shows_a_finished_run(client, booted):
    rid = client.post("/api/run", json={"mode": "blitz", "seed": "lb"}).json()["run_id"]
    for i in range(4):
        fs = frames(client.post(f"/api/run/{rid}/act",
                                json={"type": "send", "text": f"Point {i}, made plainly."}))
        if "end" in names(fs):
            break
    board = client.get("/api/leaderboard?mode=blitz").json()
    assert board["entries"] and board["entries"][0]["rank"] == 1
    assert client.get("/api/leaderboard?mode=endless").json()["by"] == "floor"


def test_the_daily_is_one_attempt_per_player(client, booted):
    rid = client.post("/api/run", json={"mode": "daily"}).json()["run_id"]
    assert client.post("/api/run", json={"mode": "daily"}).status_code == 409

    # Walking away must not buy a second look at the same seed.
    client.post(f"/api/run/{rid}/abandon")
    assert client.post("/api/run", json={"mode": "daily"}).status_code == 409


def test_finishing_the_daily_marks_it_played(client, booted):
    rid = client.post("/api/run", json={"mode": "daily"}).json()["run_id"]
    for i in range(30):
        fs = frames(client.post(f"/api/run/{rid}/act",
                                json={"type": "send", "text": f"Line {i} of a losing argument."}))
        if "end" in names(fs):
            break
        if "reward" in names(fs):
            client.post(f"/api/run/{rid}/act", json={"type": "take", "index": 0})
    assert client.get("/api/boot").json()["daily"]["played"] is True
    assert first(fs, "end")["result"]["stats"]["daily"] is True


def test_starting_a_mode_abandons_the_previous_run_of_that_mode(client, booted):
    first_id = client.post("/api/run", json={"mode": "ascent"}).json()["run_id"]
    client.post("/api/run", json={"mode": "ascent"})
    assert client.post(f"/api/run/{first_id}/act",
                       json={"type": "send", "text": "hello"}).status_code == 409
    active = client.get("/api/me").json()["profile"]["active"]
    assert len([r for r in active if r["mode"] == "ascent"]) == 1


def test_runs_in_different_modes_coexist(client, booted):
    client.post("/api/run", json={"mode": "ascent"})
    client.post("/api/run", json={"mode": "endless"})
    modes = {r["mode"] for r in client.get("/api/me").json()["profile"]["active"]}
    assert {"ascent", "endless"} <= modes


def test_abandoning_a_run_closes_it(client, booted):
    rid = client.post("/api/run", json={"mode": "ascent"}).json()["run_id"]
    assert client.post(f"/api/run/{rid}/abandon").json()["ok"]
    assert client.post(f"/api/run/{rid}/act", json={"type": "send", "text": "x"}).status_code == 409


def test_the_frontend_is_served(client):
    page = client.get("/")
    assert page.status_code == 200 and "SWAY" in page.text
    assert client.get("/static/js/app.js").status_code == 200
    assert client.get("/favicon.svg").status_code == 200
