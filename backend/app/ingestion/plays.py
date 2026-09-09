"""Ingests run/pass plays (2021+) from nflverse pbp, joined with participation
(coverage/pressure/personnel, 2021+ partial, near-complete 2023+) and FTN
charting (play-action/RPO/motion/blitz counts, 2022+ only) into the `plays`
fact table, tagged with each play's offense/defense scheme family.

Scope note: only run/pass plays are ingested. Special-teams plays (field
goals, punts, kickoffs) aren't part of an offensive/defensive coaching
system in the sense this feature means, so they're left out.
"""

import re

import nflreadpy as nfl
import polars as pl
from sqlalchemy.orm import Session

from app.models import Play, TeamSeasonScheme

FTN_MIN_SEASON = 2022


def _personnel_group(personnel: str | None) -> str | None:
    """'1 RB, 1 TE, 3 WR' -> '11' (standard RB-count + TE-count notation)."""
    if not personnel:
        return None
    rb_match = re.search(r"(\d+)\s*RB", personnel)
    te_match = re.search(r"(\d+)\s*TE", personnel)
    if not rb_match or not te_match:
        return None
    return f"{rb_match.group(1)}{te_match.group(1)}"


def _scoring_type(row: dict) -> str | None:
    if not row.get("touchdown"):
        return None
    if row.get("play_type") == "run":
        return "rush_td"
    if row.get("play_type") == "pass":
        return "pass_td"
    return "other_td"


def _load_season_frame(season: int) -> pl.DataFrame:
    pbp = nfl.load_pbp(seasons=[season]).filter(pl.col("play_type").is_in(["run", "pass"]))
    pbp_cols = [
        "game_id", "play_id", "season", "week", "posteam", "defteam", "play_type",
        "down", "ydstogo", "yardline_100", "desc", "yards_gained", "epa", "success",
        "touchdown", "interception", "fumble_lost", "sack", "shotgun", "no_huddle",
        "run_location", "run_gap", "pass_length", "pass_location",
        "rusher_player_id", "rusher_player_name", "receiver_player_id", "receiver_player_name",
        "passer_player_id", "passer_player_name", "pass_touchdown", "rush_touchdown",
    ]
    pbp = pbp.select([c for c in pbp_cols if c in pbp.columns])

    participation = nfl.load_participation(seasons=[season]).select(
        [
            pl.col("nflverse_game_id").alias("game_id"),
            pl.col("play_id").cast(pl.Float64),
            "offense_formation", "offense_personnel", "defenders_in_box",
            "was_pressure", "defense_man_zone_type", "defense_coverage_type", "time_to_throw",
        ]
    )

    frame = pbp.join(participation, on=["game_id", "play_id"], how="left")

    if season >= FTN_MIN_SEASON:
        ftn = nfl.load_ftn_charting(seasons=[season]).select(
            [
                pl.col("nflverse_game_id").alias("game_id"),
                pl.col("nflverse_play_id").cast(pl.Float64).alias("play_id"),
                "is_play_action", "is_screen_pass", "is_rpo", "is_motion",
                "n_blitzers", "n_pass_rushers",
            ]
        )
        frame = frame.join(ftn, on=["game_id", "play_id"], how="left")
    else:
        for col in ["is_play_action", "is_screen_pass", "is_rpo", "is_motion", "n_blitzers", "n_pass_rushers"]:
            frame = frame.with_columns(pl.lit(None).alias(col))

    return frame


def ingest_plays(db: Session, seasons: list[int]) -> int:
    scheme_lookup: dict[tuple[str, int], tuple[str, str]] = {
        (row.team_abbr, row.season): (row.offense_scheme_id, row.defense_scheme_id)
        for row in db.query(TeamSeasonScheme).filter(TeamSeasonScheme.season.in_(seasons)).all()
    }

    total = 0
    for season in seasons:
        frame = _load_season_frame(season)
        db.query(Play).filter(Play.season == season).delete(synchronize_session=False)

        rows_to_insert = []
        for row in frame.iter_rows(named=True):
            posteam = row.get("posteam")
            defteam = row.get("defteam")
            offense_scheme_id, _ = scheme_lookup.get((posteam, season), (None, None))
            _, defense_scheme_id = scheme_lookup.get((defteam, season), (None, None))

            touchdown = bool(row.get("touchdown"))
            scoring_type = _scoring_type(row)

            rows_to_insert.append(
                {
                    "play_key": f"{row['game_id']}_{int(row['play_id'])}",
                    "game_id": row["game_id"],
                    "season": season,
                    "week": row.get("week"),
                    "posteam": posteam,
                    "defteam": defteam,
                    "play_type": row.get("play_type"),
                    "down": row.get("down"),
                    "ydstogo": row.get("ydstogo"),
                    "yardline_100": row.get("yardline_100"),
                    "desc": row.get("desc"),
                    "yards_gained": row.get("yards_gained"),
                    "epa": row.get("epa"),
                    "success": bool(row["success"]) if row.get("success") is not None else None,
                    "touchdown": touchdown,
                    "scoring_play": touchdown,
                    "scoring_type": scoring_type,
                    "interception": bool(row.get("interception")),
                    "fumble_lost": bool(row.get("fumble_lost")),
                    "sack": bool(row.get("sack")),
                    "shotgun": bool(row["shotgun"]) if row.get("shotgun") is not None else None,
                    "no_huddle": bool(row["no_huddle"]) if row.get("no_huddle") is not None else None,
                    "run_location": row.get("run_location"),
                    "run_gap": row.get("run_gap"),
                    "pass_length": row.get("pass_length"),
                    "pass_location": row.get("pass_location"),
                    "offense_formation": row.get("offense_formation"),
                    "personnel_group": _personnel_group(row.get("offense_personnel")),
                    "defenders_in_box": row.get("defenders_in_box"),
                    "was_pressure": row.get("was_pressure"),
                    "man_zone": row.get("defense_man_zone_type") or None,
                    "coverage_type": row.get("defense_coverage_type"),
                    "time_to_throw": row.get("time_to_throw"),
                    "is_play_action": row.get("is_play_action"),
                    "is_screen_pass": row.get("is_screen_pass"),
                    "is_rpo": row.get("is_rpo"),
                    "is_motion": row.get("is_motion"),
                    "n_blitzers": row.get("n_blitzers"),
                    "n_pass_rushers": row.get("n_pass_rushers"),
                    "offense_scheme_id": offense_scheme_id,
                    "defense_scheme_id": defense_scheme_id,
                    "rusher_player_id": row.get("rusher_player_id"),
                    "rusher_player_name": row.get("rusher_player_name"),
                    "receiver_player_id": row.get("receiver_player_id"),
                    "receiver_player_name": row.get("receiver_player_name"),
                    "passer_player_id": row.get("passer_player_id"),
                    "passer_player_name": row.get("passer_player_name"),
                    "pass_touchdown": bool(row.get("pass_touchdown")) if row.get("pass_touchdown") is not None else None,
                    "rush_touchdown": bool(row.get("rush_touchdown")) if row.get("rush_touchdown") is not None else None,
                }
            )

        db.bulk_insert_mappings(Play, rows_to_insert)
        db.commit()
        total += len(rows_to_insert)

    return total
