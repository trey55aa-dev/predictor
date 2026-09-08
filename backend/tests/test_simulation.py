import random

from app.model.sim_params import field_bucket, togo_bucket
from app.model.simulation import (
    GameSimulator,
    MatchupPools,
    PlayOutcome,
    SimulationResult,
    situation_key,
)


class FakeLibrary:
    """Minimal stand-in for PlayLibrary so pool selection and backoff can be
    tested without a database or 176k rows."""

    def __init__(self, matchup=None, scheme=None, league=None, team_schemes=None):
        self.by_offense = matchup or {}
        self.by_defense = {}
        self.by_scheme = scheme or {}
        self.league = league or {}
        self.team_schemes = team_schemes or {}


def gain(yards: float) -> PlayOutcome:
    return PlayOutcome(yards=yards, touchdown=False, turnover=False, play_type="run")


def test_field_bucket_covers_whole_field():
    assert field_bucket(1) == "1-10"
    assert field_bucket(10) == "1-10"
    assert field_bucket(11) == "11-20"
    assert field_bucket(99) == "81-99"


def test_field_bucket_clamps_toward_the_nearest_end_of_the_field():
    # 0 means "on the goal line", so it must bucket as short field, not as the
    # far end -- an earlier version fell through to the last bucket and filed
    # goal-line snaps as 81+ yard situations.
    assert field_bucket(0) == "1-10"
    assert field_bucket(100) == "81-99"


def test_togo_bucket_clamps_zero_and_negative_distance_to_short_yardage():
    assert togo_bucket(0) == "1-1"
    assert togo_bucket(-3) == "1-1"


def test_togo_bucket_groups_distances():
    assert togo_bucket(1) == "1-1"
    assert togo_bucket(3) == "2-3"
    assert togo_bucket(10) == "7-10"
    assert togo_bucket(25) == "11-99"


def test_situation_key_combines_down_distance_and_field_position():
    assert situation_key(3, 8, 45) == "3|7-10|41-60"


def test_matchup_pool_preferred_over_league_when_deep_enough():
    key = situation_key(1, 10, 75)
    matchup = {("KC", True): {key: [gain(9.0)] * 50}}
    league = {key: [gain(1.0)] * 500}
    lib = FakeLibrary(matchup=matchup, league=league)
    pools = MatchupPools(lib, "KC", "DEN", offense_at_home=True)

    sampled = pools.sample(random.Random(0), 1, 10, 75)
    assert sampled.yards == 9.0


def test_thin_matchup_pool_backs_off_to_league():
    key = situation_key(1, 10, 75)
    # Only 3 plays -- below MIN_POOL, so it must not be trusted on its own.
    matchup = {("KC", True): {key: [gain(80.0)] * 3}}
    league = {key: [gain(4.0)] * 500}
    lib = FakeLibrary(matchup=matchup, league=league)
    pools = MatchupPools(lib, "KC", "DEN", offense_at_home=True)

    sampled = pools.sample(random.Random(0), 1, 10, 75)
    assert sampled.yards == 4.0


def test_sampling_never_raises_on_a_completely_empty_library():
    pools = MatchupPools(FakeLibrary(), "KC", "DEN", offense_at_home=True)
    sampled = pools.sample(random.Random(0), 2, 7, 40)
    assert sampled.yards == 0.0


def test_home_and_away_pools_are_kept_separate():
    key = situation_key(1, 10, 75)
    matchup = {
        ("KC", True): {key: [gain(9.0)] * 50},
        ("KC", False): {key: [gain(2.0)] * 50},
    }
    lib = FakeLibrary(matchup=matchup)
    home = MatchupPools(lib, "KC", "DEN", offense_at_home=True)
    away = MatchupPools(lib, "KC", "DEN", offense_at_home=False)

    assert home.sample(random.Random(0), 1, 10, 75).yards == 9.0
    assert away.sample(random.Random(0), 1, 10, 75).yards == 2.0


def _result(pairs: list[tuple[int, int]]) -> SimulationResult:
    r = SimulationResult("KC", "DEN", len(pairs))
    r.home_scores = [h for h, _ in pairs]
    r.away_scores = [a for _, a in pairs]
    return r


def test_summary_reports_win_probability_and_central_values():
    r = _result([(24, 17), (30, 20), (10, 21), (14, 14)])
    s = r.summary()
    # 2 wins, 1 loss, 1 tie -> ties split, so (2 + 0.5) / 4.
    assert s["home_win_prob"] == 0.625
    assert s["mean_total"] == (41 + 50 + 31 + 28) / 4


def test_prob_total_over_splits_pushes():
    r = _result([(24, 21), (20, 24), (10, 34), (7, 7)])
    # Totals: 45, 44, 44, 14. Over 44 -> one; pushes on 44 -> two.
    assert r.prob_total_over(44) == (1 + 2 / 2) / 4


def test_prob_home_cover_handles_the_spread_sign():
    r = _result([(24, 17), (20, 24)])
    # Margins +7 and -4 against a home handicap of -3.5: covers once.
    assert r.prob_home_cover(-3.5) == 0.5
    # As a 10.5-point favourite it covers neither.
    assert r.prob_home_cover(-10.5) == 0.0


def test_simulator_refuses_to_run_without_measured_parameters():
    """Guards against silently simulating on hardcoded guesses if
    build-sim-params was never run."""
    try:
        GameSimulator.__new__(GameSimulator).__init__(
            db=None, home_team="KC", away_team="DEN", seasons=[2024], params={}
        )
    except RuntimeError as err:
        assert "build-sim-params" in str(err)
    else:
        raise AssertionError("expected a RuntimeError when parameters are missing")


def test_display_distribution_recentres_total_on_the_model_but_not_margin():
    """Backtested behaviour: the model's total is the better centre, the
    simulator's margin is. Only the total should move."""
    from app.model.simulation import distribution_for_display

    summary = _result([(24, 21), (17, 20), (30, 24)]).summary()
    original_margin_p10 = summary["margin_p10"]
    sim_total_spread = summary["total_p90"] - summary["total_p10"]

    out = distribution_for_display(summary, model_total=41.0)

    assert out["mean_total"] == 41.0
    assert out["total_center_source"] == "model"
    # The width is the simulator's; only the centre moved.
    assert out["total_p90"] - out["total_p10"] == sim_total_spread
    # Margin is untouched.
    assert out["margin_p10"] == original_margin_p10
    assert out["margin_center_source"] == "simulation"


def test_display_distribution_keeps_simulated_total_when_no_model_estimate():
    from app.model.simulation import distribution_for_display

    summary = _result([(24, 21), (17, 20)]).summary()
    out = distribution_for_display(summary, model_total=None)

    assert out["mean_total"] == summary["mean_total"]
    assert out["total_center_source"] == "simulation"
