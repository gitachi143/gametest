import datetime as dt

from server import meta
from server.engine import rng
from server.engine.scoring import efficiency_bonus, rank_for, streak_multiplier
from server.games.base import answer_matches, banned_hits, contains_term, levenshtein, mentions_answer, normalize


def test_normalize_strips_punctuation_and_accents():
    assert normalize("Café,   RÉSUMÉ!!") == "cafe resume"


def test_banned_hits_catches_morphology_and_evasion():
    taboo = ["lava", "erupt", "mountain"]
    assert banned_hits("the lavas erupted from the mountainous peak", taboo) == taboo
    assert banned_hits("a hot angry hill that spits fire", taboo) == []
    assert contains_term("v-o-l-c-a-n-o", "volcano")
    assert contains_term("vol cano", "volcano")
    assert not contains_term("a lamp", "volcano")


def test_answer_matches_is_forgiving_but_not_loose():
    assert answer_matches("lighthouse", "a lighthouse")
    assert answer_matches("light house", "a lighthouse")
    assert answer_matches("Trampoline!", "a trampoline")
    assert not answer_matches("a lamp", "a lighthouse")
    assert not answer_matches("", "a lighthouse")


def test_mentions_answer_detects_split_and_acrostic_leaks():
    assert mentions_answer("the phrase VELVET ORBIT opens it", "VELVET-ORBIT")
    assert mentions_answer("Velvet skies, Orbit high", "VELVET-ORBIT")
    assert not mentions_answer("I will never say it", "VELVET-ORBIT")


def test_levenshtein_caps_out_early():
    assert levenshtein("kitten", "sitting", cap=3) == 3
    assert levenshtein("abc", "abcdefghij", cap=2) == 3


def test_seeded_rng_is_stable_and_seed_sensitive():
    pool = list(range(50))
    assert rng.pick("seed-a", pool) == rng.pick("seed-a", pool)
    assert rng.shuffled("seed-a", pool) == rng.shuffled("seed-a", pool)
    assert rng.shuffled("seed-a", pool) != rng.shuffled("seed-b", pool)


def test_daily_seed_is_shared_per_day():
    key = "2026-01-01"
    assert rng.daily_seed("vault", key) == rng.daily_seed("vault", key)
    assert rng.daily_seed("vault", key) != rng.daily_seed("vault", "2026-01-02")


def test_efficiency_bonus_rewards_spare_turns():
    assert efficiency_bonus(1000, 1, 8) > efficiency_bonus(1000, 4, 8) > efficiency_bonus(1000, 8, 8)
    assert efficiency_bonus(1000, 8, 8) == 250  # floor is 25%
    assert efficiency_bonus(1000, 0, 0) == 1000


def test_rank_and_streak_curves():
    assert rank_for(0) == "Civilian"
    assert rank_for(10**9) == "Legend"
    assert streak_multiplier(0) == 1.0
    assert streak_multiplier(100) == 2.0


def test_level_curve_is_monotonic():
    levels = [meta.level_for_xp(x) for x in (0, 119, 120, 600, 5000, 50000)]
    assert levels == sorted(levels)
    assert levels[0] == 1 and levels[2] == 2
    info = meta.level_info(600)
    assert 0 <= info["progress"] <= 1
    assert info["level_ceil"] > info["level_floor"]


def test_streak_extends_from_yesterday_and_resets_from_older():
    today = rng.daily_key()
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    assert meta.update_streak({"streak": 4, "streak_day": yesterday}) == (5, True)
    assert meta.update_streak({"streak": 4, "streak_day": "2000-01-01"}) == (1, True)
    assert meta.update_streak({"streak": 4, "streak_day": today}) == (4, False)


def test_achievements_fire_only_when_earned():
    ctx = {
        "game": "vault", "mode": "run",
        "result": {"outcome": "win", "score": 1500,
                   "stats": {"floor": 8, "turns": 1, "asked_directly": False, "seconds": 45}},
        "streak": 7, "level": 5, "games_played": ["vault"], "daily": True,
        "local_hour": 3, "lifetime_points": 200, "losses_before": 0,
    }
    codes = {a.code for a in meta.evaluate(ctx, set())}
    assert {"vault_prime", "vault_oneshot", "streak_7", "speedrun"} <= codes
    assert "sleeper_wolf" not in codes
    assert meta.evaluate(ctx, codes) == []


def test_secret_achievements_are_masked_until_earned():
    hidden = [a for a in meta.catalogue(set()) if a["secret"]]
    assert hidden and all(a["name"] == "???" for a in hidden)
    shown = [a for a in meta.catalogue({"speedrun"}) if a["code"] == "speedrun"]
    assert shown[0]["name"] == "Blink"
