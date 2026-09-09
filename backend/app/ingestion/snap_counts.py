"""Ingests real per-player-per-game snap shares (nflverse) as the
load-management signal for player projections and simulation-based props.

nflverse's snap-count data keys players by PFR id, while every other player
table in this app (PlayerGameStat, PlayerProjection) keys by GSIS id -- two
different ID namespaces for the same people. `load_ff_playerids()` is the
real crosswalk; it's applied once at ingest time so every other module can
just join on gsis_id like it already does everywhere else.
"""

import polars as pl
from sqlalchemy.orm import Session

from app.models import SnapCount


def _pfr_to_gsis_map() -> dict[str, str]:
    import nflreadpy as nfl

    ids = nfl.load_ff_playerids().select(["gsis_id", "pfr_id"]).drop_nulls()
    return {row["pfr_id"]: row["gsis_id"] for row in ids.iter_rows(named=True)}


def ingest_snap_counts(db: Session, seasons: list[int]) -> int:
    import nflreadpy as nfl

    pfr_to_gsis = _pfr_to_gsis_map()
    total = 0

    for season in seasons:
        frame = nfl.load_snap_counts(seasons=[season]).select(
            [
                "game_id", "season", "week", "pfr_player_id", "player", "position",
                "team", "opponent", "offense_snaps", "offense_pct",
            ]
        )
        db.query(SnapCount).filter(SnapCount.season == season).delete(synchronize_session=False)

        rows_to_insert = []
        unmapped = 0
        for row in frame.iter_rows(named=True):
            pfr_id = row.get("pfr_player_id")
            gsis_id = pfr_to_gsis.get(pfr_id)
            id_mapped = gsis_id is not None
            if not id_mapped:
                gsis_id = pfr_id  # row still counts toward team-snap totals
                unmapped += 1
            rows_to_insert.append(
                {
                    "game_id": row["game_id"],
                    "season": season,
                    "week": row.get("week"),
                    "player_id": gsis_id,
                    "id_mapped": id_mapped,
                    "player_name": row.get("player"),
                    "position": row.get("position"),
                    "team": row.get("team"),
                    "opponent": row.get("opponent"),
                    "offense_snaps": row.get("offense_snaps"),
                    "offense_pct": row.get("offense_pct"),
                }
            )

        db.bulk_insert_mappings(SnapCount, rows_to_insert)
        db.commit()
        total += len(rows_to_insert)
        if unmapped:
            print(f"  season {season}: {unmapped}/{len(rows_to_insert)} snap rows had no GSIS id match")

    return total
