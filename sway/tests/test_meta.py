"""Progression: levels, unlocks, streaks, badges."""
import datetime as dt

from app.engine import meta


def test_levels_are_monotonic_and_start_at_one():
    assert meta.level_for_xp(0) == 1
    assert meta.xp_for_level(1) == 0
    last = -1
    for level in range(1, 40):
        need = meta.xp_for_level(level)
        assert need > last
        last = need
        assert meta.level_for_xp(need) == level
        assert meta.level_for_xp(need - 1) == max(1, level - 1)


def test_level_info_reports_a_usable_progress_bar():
    info = meta.level_info(meta.xp_for_level(4) + 1)
    assert info["level"] == 4 and info["title"]
    assert 0.0 <= info["progress"] < 1.0
    assert info["to_next"] > 0
    assert meta.level_info(0)["progress"] == 0.0


def test_titles_never_run_out():
    assert meta.level_info(10 ** 9)["title"] == meta.LEVEL_TITLES[-1]


def test_xp_rewards_going_deeper_more_than_winning_early():
    shallow = meta.xp_for_run({"outcome": "win", "score": 400,
                               "stats": {"cleared": 1, "crits": 0}})
    deep = meta.xp_for_run({"outcome": "loss", "score": 4000,
                            "stats": {"cleared": 6, "crits": 4}})
    assert deep > shallow
    assert 25 <= shallow <= 900 and 25 <= deep <= 900


def test_the_daily_pays_a_bonus():
    plain = {"outcome": "win", "score": 2000, "stats": {"cleared": 4}}
    daily = {"outcome": "win", "score": 2000, "stats": {"cleared": 4, "daily": True}}
    assert meta.xp_for_run(daily) > meta.xp_for_run(plain)


def test_xp_is_capped_so_one_run_cannot_finish_the_ladder():
    huge = meta.xp_for_run({"outcome": "win", "score": 10 ** 9,
                            "stats": {"cleared": 99, "crits": 99}})
    assert huge == 900
    # Even an impossible run lands inside the first handful of levels, so the
    # unlock ladder stays a ladder rather than a single step.
    assert meta.level_for_xp(huge) <= 5


def test_unlocks_accumulate_and_never_regress():
    sizes = [len(meta.unlocked_for_level(level)) for level in range(1, 25)]
    assert sizes == sorted(sizes)
    assert sizes[0] == 0
    assert sizes[-1] == len(meta.GATED)


def test_newly_unlocked_reports_exactly_the_span_crossed():
    assert meta.newly_unlocked(1, 1) == []
    crossed = meta.newly_unlocked(2, 4)
    assert set(crossed) == set(meta.unlocked_for_level(4)) - set(meta.unlocked_for_level(2))


def test_availability_gates_only_gated_content():
    assert meta.is_available("press", set())
    assert not meta.is_available("keystone", set())
    assert meta.is_available("keystone", {"keystone"})
    assert meta.unlock_level("press") == 1 and meta.unlock_level("keystone") == 5


def test_streaks_extend_reset_and_do_not_double_count():
    today = meta.daily_key()
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%d")
    assert meta.update_streak({"streak": 4, "streak_day": yesterday}) == (5, True)
    assert meta.update_streak({"streak": 4, "streak_day": today}) == (4, False)
    assert meta.update_streak({"streak": 9, "streak_day": "2001-01-01"}) == (1, True)
    assert meta.update_streak({}) == (1, True)


def test_badge_catalogue_hides_secrets_until_earned():
    hidden = {b["code"]: b for b in meta.badge_catalogue(set())}
    secret = next(b for b in meta.BADGES if b.secret)
    assert hidden[secret.code]["name"] == "???"
    shown = {b["code"]: b for b in meta.badge_catalogue({secret.code})}
    assert shown[secret.code]["name"] == secret.name


def test_badge_codes_are_unique():
    codes = [b.code for b in meta.BADGES]
    assert len(codes) == len(set(codes))


def test_evaluate_awards_the_obvious_ones_and_skips_the_owned():
    ctx = {"result": {"outcome": "win", "score": 6000,
                      "stats": {"mode": "ascent", "cleared": 8, "crits": 11,
                                "best_hit": 2000, "fumbles": 0, "relics": 5}},
           "streak": 3, "level": 5, "modes_played": ["ascent"], "beaten": ["architect"],
           "local_hour": 15}
    codes = {b.code for b in meta.evaluate(ctx, set())}
    assert {"ascent", "crit-10", "big-hit", "relic-5", "score-5k",
            "streak-3", "level-5", "no-fumble", "architect"} <= codes
    assert "endless-10" not in codes
    assert not meta.evaluate(ctx, codes)


def test_a_broken_predicate_cannot_break_a_run_ending():
    bad = meta.Badge("boom", "Boom", "", "!", lambda c: 1 / 0)
    original = meta.BADGES
    meta.BADGES = original + (bad,)
    try:
        assert all(b.code != "boom" for b in meta.evaluate({"result": {}}, set()))
    finally:
        meta.BADGES = original
