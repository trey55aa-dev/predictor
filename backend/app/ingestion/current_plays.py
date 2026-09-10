"""Ingests real play-by-play for the CURRENT, in-progress season -- a
narrower cousin of ingestion/plays.py, built specifically for the post-game
"keys to victory" breakdown (real turnover margin, rushing/passing yards,
3rd/4th down conversion rate, computed from actual plays, never estimated).

Why this isn't just `ingest_plays(db, [current_season])`: that function also
joins nflverse's participation (coverage/personnel) and FTN charting
(play-action/motion/blitz) feeds, and both of those lag well behind the pbp
feed itself for a season still in progress -- confirmed live:
`load_participation(seasons=[2026])` raises "Season must be between 2016
and 2025" and `load_ftn_charting(seasons=[2026])` 404s, while
`load_pbp(seasons=[2026])` already has real rows for every game that's
actually been played. This module only needs pbp's own columns, so it
doesn't carry those two feeds' lag as a dependency. Rows it writes leave
every scheme/participation/FTN column null -- a real, disclosed narrower
scope, not a schema mismatch -- which is harmless: nothing queries those
columns without also filtering to the historical seasons that populate
them (see model/matchup.py, api/systems.py).

Only ingests games our own Game table already has marked "final" -- pbp for
a game already under way (not yet final) can still change as plays get
appended/corrected, so this deliberately doesn't touch those.
"""

import nflreadpy as nfl
import polars as pl
from sqlalchemy.orm import Session

from app.models import Game, Play

_PBP_COLUMNS = [
    "game_id", "play_id", "week", "posteam", "defteam", "play_type",
    "down", "ydstogo", "yardline_100", "desc", "yards_gained", "epa", "success",
    "touchdown", "interception", "fumble_lost", "sack", "shotgun", "no_huddle",
    "run_location", "run_gap", "pass_length", "pass_location",
    "rusher_player_id", "rusher_player_name", "receiver_player_id", "receiver_player_name",
    "passer_player_id", "passer_player_name", "pass_touchdown", "rush_touchdown",
]


def _scoring_type(row: dict) -> str | None:
    if not row.get("touchdown"):
        return None
    if row.get("play_type") == "run":
        return "rush_td"
    if row.get("play_type") == "pass":
        return "pass_td"
    return "other_td"


def ingest_current_season_plays(db: Session, season: int) -> int:
    final_game_ids = {
        g.game_id for g in db.query(Game.game_id).filter(Game.season == season, Game.status == "final").all()
    }
    if not final_game_ids:
        return 0

    pbp = nfl.load_pbp(seasons=[season]).filter(pl.col("play_type").is_in(["run", "pass"]))
    pbp = pbp.select([c for c in _PBP_COLUMNS if c in pbp.columns])
    pbp = pbp.filter(pl.col("game_id").is_in(list(final_game_ids)))
    if pbp.height == 0:
        return 0

    db.query(Play).filter(Play.season == season, Play.game_id.in_(final_game_ids)).delete(
        synchronize_session=False
    )

    rows_to_insert = []
    for row in pbp.iter_rows(named=True):
        rows_to_insert.append(
            {
                "play_key": f"{row['game_id']}_{int(row['play_id'])}",
                "game_id": row["game_id"],
                "season": season,
                "week": row.get("week"),
                "posteam": row.get("posteam"),
                "defteam": row.get("defteam"),
                "play_type": row.get("play_type"),
                "down": row.get("down"),
                "ydstogo": row.get("ydstogo"),
                "yardline_100": row.get("yardline_100"),
                "desc": row.get("desc"),
                "yards_gained": row.get("yards_gained"),
                "epa": row.get("epa"),
                "success": row.get("success"),
                "touchdown": bool(row.get("touchdown")),
                "scoring_play": bool(row.get("touchdown")),
                "scoring_type": _scoring_type(row),
                "interception": bool(row.get("interception")),
                "fumble_lost": bool(row.get("fumble_lost")),
                "sack": bool(row.get("sack")),
                "shotgun": row.get("shotgun"),
                "no_huddle": row.get("no_huddle"),
                "run_location": row.get("run_location"),
                "run_gap": row.get("run_gap"),
                "pass_length": row.get("pass_length"),
                "pass_location": row.get("pass_location"),
                "rusher_player_id": row.get("rusher_player_id"),
                "rusher_player_name": row.get("rusher_player_name"),
                "receiver_player_id": row.get("receiver_player_id"),
                "receiver_player_name": row.get("receiver_player_name"),
                "passer_player_id": row.get("passer_player_id"),
                "passer_player_name": row.get("passer_player_name"),
                "pass_touchdown": row.get("pass_touchdown"),
                "rush_touchdown": row.get("rush_touchdown"),
            }
        )

    db.bulk_insert_mappings(Play, rows_to_insert)
    db.commit()
    return len(rows_to_insert)
