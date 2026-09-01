"""Ingests weekly per-player stats (rushing/receiving/passing yards and TDs,
usage share) from nflreadpy -- the historical signal the player-projection
model trains on, and later the ground truth it's graded against."""

import nflreadpy as nfl
import polars as pl
from sqlalchemy.orm import Session

from app.models import PlayerGameStat

SKILL_POSITIONS = {"QB", "RB", "WR", "TE", "FB"}


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # filter NaN


def ingest_player_stats(db: Session, seasons: list[int]) -> int:
    total = 0
    for season in seasons:
        df = nfl.load_player_stats(seasons=[season], summary_level="week").filter(
            pl.col("position").is_in(list(SKILL_POSITIONS))
            & (
                (pl.col("carries").fill_null(0) > 0)
                | (pl.col("targets").fill_null(0) > 0)
                | (pl.col("attempts").fill_null(0) > 0)
            )
        )

        db.query(PlayerGameStat).filter(PlayerGameStat.season == season).delete(synchronize_session=False)

        rows_to_insert = []
        for row in df.iter_rows(named=True):
            rows_to_insert.append(
                {
                    "player_id": row["player_id"],
                    "player_name": row.get("player_display_name") or row.get("player_name"),
                    "position": row["position"],
                    "team": row["team"],
                    "season": season,
                    "week": row["week"],
                    "game_id": row.get("game_id"),
                    "carries": _safe_float(row.get("carries")),
                    "rushing_yards": _safe_float(row.get("rushing_yards")),
                    "rushing_tds": _safe_float(row.get("rushing_tds")),
                    "targets": _safe_float(row.get("targets")),
                    "receptions": _safe_float(row.get("receptions")),
                    "receiving_yards": _safe_float(row.get("receiving_yards")),
                    "receiving_tds": _safe_float(row.get("receiving_tds")),
                    "pass_attempts": _safe_float(row.get("attempts")),
                    "passing_yards": _safe_float(row.get("passing_yards")),
                    "passing_tds": _safe_float(row.get("passing_tds")),
                    "target_share": _safe_float(row.get("target_share")),
                    "air_yards_share": _safe_float(row.get("air_yards_share")),
                }
            )

        db.bulk_insert_mappings(PlayerGameStat, rows_to_insert)
        db.commit()
        total += len(rows_to_insert)

    return total
