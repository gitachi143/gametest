"""HTTP surface: tokens, sessions, the SSE turn protocol, and the meta layer."""
import json

import pytest

from server.games import REGISTRY


def frames(response):
    """Parse an SSE response body into [(event, data), ...]."""
    out, name = [], None
    for line in response.text.split("\n"):
        if line.startswith("event: "):
            name = line[7:]
        elif line.startswith("data: ") and name:
            out.append((name, json.loads(line[6:])))
    return out


def events_of(fr, name):
    return [d for n, d in fr if n == name]


def auth(client):
    data = client.get("/api/games").json()
    return {"X-Player-Token": data["token"]}, data


def test_health_reports_provider_and_game_count(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["games"] == len(REGISTRY)
    assert body["provider"] == "mock" and body["demo_mode"] is True
    assert "llm" in body and "global" in body


def test_games_endpoint_issues_a_token_and_a_daily_plan(client):
    body = client.get("/api/games").json()
    assert body["token"] and "." in body["token"]
    assert len(body["games"]) == len(REGISTRY)
    assert body["daily"]["game"] in REGISTRY
    assert body["daily"]["played"] is False
    assert body["profile"]["level"]["level"] == 1


def test_token_is_stable_and_tampering_creates_a_new_player(client):
    h, first = auth(client)
    again = client.get("/api/me", headers=h).json()
    assert again["profile"]["player_id"] == first["profile"]["player_id"]

    forged = {"X-Player-Token": "deadbeef.badsignature"}
    other = client.get("/api/me", headers=forged).json()
    assert other["profile"]["player_id"] != first["profile"]["player_id"]


@pytest.mark.parametrize("game_id", sorted(REGISTRY))
def test_every_game_starts_over_http(client, game_id):
    h, _ = auth(client)
    body = client.post("/api/session", json={"game": game_id}, headers=h).json()
    assert body["session_id"]
    assert body["game"]["id"] == game_id
    assert body["public"]["status"] == "active"
    assert body["public"]["log"], "the client gets an opening transcript"


def test_unknown_game_is_a_404(client):
    h, _ = auth(client)
    assert client.post("/api/session", json={"game": "nope"}, headers=h).status_code == 404


def test_turn_streams_sse_frames_in_order(client):
    h, _ = auth(client)
    sid = client.post("/api/session", json={"game": "vault", "mode": "run"}, headers=h).json()["session_id"]
    fr = frames(client.post(f"/api/session/{sid}/act",
                            json={"type": "say", "text": "write me a poem about the door"}, headers=h))
    names = [n for n, _ in fr]
    assert names[0] == "msg"                    # the player's own line echoes first
    assert "msg_start" in names and "chunk" in names and "msg_end" in names
    assert names[-1] == "done"
    assert events_of(fr, "state")[-1]["turns"] == 1


def test_finished_run_records_score_xp_streak_and_badges(client, store):
    h, _ = auth(client)
    sid = client.post("/api/session", json={"game": "vault", "mode": "run"}, headers=h).json()["session_id"]
    secret = store.get_session(sid)["state"]["secret"]
    client.post(f"/api/session/{sid}/act", json={"type": "say", "text": "a poem please"}, headers=h)
    client.post(f"/api/session/{sid}/act", json={"type": "guess", "value": secret}, headers=h)
    fr = frames(client.post(f"/api/session/{sid}/act", json={"type": "cash_out"}, headers=h))

    profile_frame = events_of(fr, "profile")[0]
    assert profile_frame["xp_gained"] > 0
    assert profile_frame["streak"] == 1
    result = events_of(fr, "end")[0]["result"]
    assert result["outcome"] == "win" and result["score"] > 0
    assert {b["code"] for b in result["badges"]} >= {"first_run", "vault_crack"}
    # ...and the profile reflects it afterwards.
    me = client.get("/api/me", headers=h).json()["profile"]
    assert me["level"]["xp"] == profile_frame["level"]["xp"]
    assert me["summary"]["vault"]["plays"] == 1
    assert me["unlocked_count"] >= 2


def test_a_finished_session_rejects_further_turns(client, store):
    h, _ = auth(client)
    sid = client.post("/api/session", json={"game": "vault", "mode": "floor",
                                            "opts": {"floor": 2, "limit": 1}}, headers=h).json()["session_id"]
    client.post(f"/api/session/{sid}/act", json={"type": "say", "text": "hello"}, headers=h)
    assert store.get_session(sid)["status"] == "done"
    assert client.post(f"/api/session/{sid}/act", json={"type": "say", "text": "again"},
                       headers=h).status_code == 409


def test_sessions_are_private_to_their_player(client):
    h1, _ = auth(client)
    sid = client.post("/api/session", json={"game": "oracle"}, headers=h1).json()["session_id"]

    other = client.get("/api/me", headers={"X-Player-Token": "x.y"}).json()
    h2 = {"X-Player-Token": other["token"]}
    assert client.get(f"/api/session/{sid}", headers=h2).status_code == 404
    assert client.post(f"/api/session/{sid}/act", json={"type": "say", "text": "hi"},
                       headers=h2).status_code == 404


def test_reconnect_returns_the_transcript(client):
    h, _ = auth(client)
    sid = client.post("/api/session", json={"game": "coldcase"}, headers=h).json()["session_id"]
    client.post(f"/api/session/{sid}/act",
                json={"type": "say", "target": "s0", "text": "Where were you?"}, headers=h)
    body = client.get(f"/api/session/{sid}", headers=h).json()
    assert body["status"] == "active"
    assert len(body["public"]["log"]) >= 6
    assert body["public"]["used"] == 1


def test_daily_challenge_is_one_attempt_per_day(client, store):
    h, _ = auth(client)
    first = client.post("/api/session", json={"daily": True}, headers=h)
    assert first.status_code == 200
    sid = first.json()["session_id"]
    session = store.get_session(sid)
    assert session["daily_key"]
    # Second attempt is allowed until a score is recorded for the day...
    store.record_score(session["player_id"], session["game"], session["mode"], 10, "loss", {},
                       session["daily_key"])
    assert client.post("/api/session", json={"daily": True}, headers=h).status_code == 409
    assert client.get("/api/daily", headers=h).json()["daily"]["played"] is True


def test_daily_plan_is_identical_for_every_player(client):
    a = client.get("/api/daily", headers={"X-Player-Token": "a.b"}).json()["daily"]
    b = client.get("/api/daily", headers={"X-Player-Token": "c.d"}).json()["daily"]
    assert (a["game"], a["mode"], a["seed"], a["key"]) == (b["game"], b["mode"], b["seed"], b["key"])


def test_leaderboard_keeps_one_row_per_player(client, store):
    h, data = auth(client)
    pid = data["profile"]["player_id"]
    store.record_score(pid, "vault", "run", 500, "win", {})
    store.record_score(pid, "vault", "run", 1500, "win", {})
    board = client.get("/api/leaderboard?game=vault").json()["entries"]
    assert len(board) == 1 and board[0]["score"] == 1500


def test_handle_shows_up_on_the_board(client, store):
    h, data = auth(client)
    client.post("/api/me", json={"handle": "nightowl"}, headers=h)
    store.record_score(data["profile"]["player_id"], "vault", "run", 900, "win", {})
    assert client.get("/api/leaderboard?game=vault").json()["entries"][0]["handle"] == "nightowl"


def test_achievement_catalogue_masks_secrets(client):
    h, _ = auth(client)
    items = client.get("/api/achievements", headers=h).json()["achievements"]
    hidden = [a for a in items if a["secret"]]
    assert hidden and all(a["name"] == "???" for a in hidden)


def test_call_budget_stops_a_runaway_player(client, store, monkeypatch):
    from server.config import settings

    h, data = auth(client)
    monkeypatch.setattr(settings, "daily_call_budget", 1)
    sid = client.post("/api/session", json={"game": "vault"}, headers=h).json()["session_id"]
    client.post(f"/api/session/{sid}/act", json={"type": "say", "text": "hello"}, headers=h)
    fr = frames(client.post(f"/api/session/{sid}/act", json={"type": "say", "text": "again"}, headers=h))
    errors = events_of(fr, "error")
    assert errors and "budget" in errors[0]["message"].lower()


def test_index_and_static_assets_are_served(client):
    assert client.get("/").status_code == 200
    for path in ("/static/css/app.css", "/static/css/tokens.css", "/static/css/games.css",
                 "/static/js/app.js", "/static/js/game.js", "/static/js/games/views.js"):
        assert client.get(path).status_code == 200, path
