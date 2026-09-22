"""Shared per-game lookups for the sidecar advanced-play-data tables
(PlayAdvancedStat, PlayCoverageStat). Pulled out into their own module,
separate from keys_to_victory.py, so keys_to_victory.py (box-score stats),
efficiency_stats.py (EPA/CPOE/pressure), and pass_defense_stats.py
(pass-defense-allowed stats) can all depend on these lookups without a
circular import between each other -- keys_to_victory.py's own breakdown
surfaces pass-defense-allowed stats too, so it needs to reach
pass_defense_stats.py, which itself needs these lookups.
"""

from sqlalchemy.orm import Session

from app.models import PlayAdvancedStat, PlayCoverageStat


def advanced_stats_by_play_key(db: Session, game_id: str) -> dict[str, PlayAdvancedStat]:
    """CPOE/air-yards rows for `game_id`, keyed by play_key -- see
    PlayAdvancedStat's docstring for why this is a separate table from
    `plays`, fetched once per game rather than joined per-play."""
    rows = db.query(PlayAdvancedStat).filter(PlayAdvancedStat.game_id == game_id).all()
    return {row.play_key: row for row in rows}


def coverage_stats_by_play_key(db: Session, game_id: str) -> dict[str, PlayCoverageStat]:
    """complete_pass/yards_after_catch/pass-defensed rows for `game_id`,
    keyed by play_key -- same shape and purpose as advanced_stats_by_play_key
    above, for the separate play_coverage_stats table."""
    rows = db.query(PlayCoverageStat).filter(PlayCoverageStat.game_id == game_id).all()
    return {row.play_key: row for row in rows}
