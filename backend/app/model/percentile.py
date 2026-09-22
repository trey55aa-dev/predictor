"""Shared percentile-rank helper for the season-to-date ranking signals
(stat_rankings.py, efficiency_stats.py, pass_defense_stats.py). Pulled out
of stat_rankings.py into its own module so pass_defense_stats.py can use it
without importing stat_rankings.py, which itself imports team_stats from
keys_to_victory.py -- keys_to_victory.py's own breakdown needs to reach
pass_defense_stats.py, so that chain would otherwise be a circular import.
"""


def percentile(value: float, all_values: list[float], higher_is_better: bool) -> float:
    """0.0 (worst in the league) .. 1.0 (best), ties split evenly. Needs at
    least 2 values to mean anything; callers guard the n<2 case."""
    n = len(all_values)
    better = sum(1 for v in all_values if (v > value if higher_is_better else v < value))
    tied = sum(1 for v in all_values if v == value) - 1  # exclude self
    rank = better + tied / 2 + 1  # 1-indexed, 1 = best
    return (n - rank) / (n - 1)
