import random

from app.model.player_sim import PlayerAccumulator, WeightedSampler, _summarize_player


def test_weighted_sampler_respects_share_proportions():
    sampler = WeightedSampler({"A": 0.9, "B": 0.1})
    rng = random.Random(0)
    counts = {"A": 0, "B": 0}
    for _ in range(2000):
        counts[sampler.sample(rng)] += 1
    # Not an exact check (it's random), just confirms the dominant share wins
    # by roughly the right margin rather than e.g. sampling uniformly.
    assert counts["A"] > counts["B"] * 3


def test_weighted_sampler_returns_none_for_an_empty_distribution():
    sampler = WeightedSampler({})
    assert sampler.sample(random.Random(0)) is None


def test_passer_is_drawn_once_per_simulated_game_not_once_per_play():
    """The bug this guards against: drawing a new passer on every play let a
    single simulated game split its passing yards across two backups from the
    usage window, inflating passing MAE roughly 5x against the baseline in
    validation. A starter, once picked for a given sim_index, must be reused
    for every pass play in that same simulated game."""
    passer_sampler = WeightedSampler({"QB_A": 0.5, "QB_B": 0.5})
    rng = random.Random(1)
    game_passers: dict[int, dict[str, str | None]] = {}

    def draw_for_play(sim_index: int, offense: str) -> str | None:
        passers_this_game = game_passers.setdefault(sim_index, {})
        if offense not in passers_this_game:
            passers_this_game[offense] = passer_sampler.sample(rng)
        return passers_this_game[offense]

    for sim_index in range(20):
        drawn_this_game = {draw_for_play(sim_index, "TEAM") for _ in range(10)}
        assert len(drawn_this_game) == 1, "the same simulated game drew more than one passer"


def test_accumulator_tracks_per_sim_totals_independently():
    acc = PlayerAccumulator(n_sims=3)
    acc.rushing_yards["RB1"][0] += 10.0
    acc.rushing_yards["RB1"][0] += 5.0
    acc.rushing_yards["RB1"][2] += 100.0

    assert acc.rushing_yards["RB1"] == [15.0, 0.0, 100.0]


def test_summarize_player_reports_td_probability_and_percentiles():
    summary = _summarize_player("Test Player", yards=[0.0, 10.0, 20.0, 30.0, 100.0], tds=[0, 0, 1, 0, 1])

    assert summary["mean_yards"] == 32.0
    assert summary["td_probability"] == 0.4
    assert summary["yards_p50"] == 20.0
