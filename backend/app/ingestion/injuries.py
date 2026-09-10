"""Ingests weekly injury reports, ranked by starter status via depth charts.

nflreadpy's injury/depth-chart data only covers seasons that have actually
happened (practice reports don't exist for a season that hasn't started
yet) -- callers should expect `NoInjuryDataError` for a future season and
handle it the same way `ingest_odds` handles a missing API key: report it,
don't crash the pipeline.
"""

import datetime as dt

import nflreadpy as nfl
import polars as pl
from sqlalchemy.orm import Session

from app.models import Injury

REPORTABLE_STATUSES = {"Out", "Doubtful", "Questionable"}


class NoInjuryDataError(Exception):
    pass


def ingest_injuries(db: Session, season: int, week: int) -> int:
    # Wraps the whole function, not just the initial load calls: nflverse's
    # own injury/depth-chart pipeline has been independently confirmed
    # broken for the current season (a real, acknowledged upstream issue,
    # not specific to this app) -- the load calls themselves can succeed
    # while returning a DataFrame with a changed/missing schema (confirmed
    # live: load_depth_charts(seasons=[2026]) returned data with no "week"
    # column at all, raising deep inside the filter below). Any failure
    # anywhere in this pipeline gets the same treatment: report it as
    # unavailable and let the caller's ESPN fallback (see
    # ingestion/espn_injuries.py) take over, rather than crashing.
    try:
        injuries = nfl.load_injuries(seasons=[season])
        depth_charts = nfl.load_depth_charts(seasons=[season])

        week_injuries = injuries.filter(
            (pl.col("week") == week) & pl.col("report_status").is_in(list(REPORTABLE_STATUSES))
        )
        if week_injuries.height == 0:
            return 0

        starters = (
            depth_charts.filter(pl.col("week") == week)
            .with_columns(pl.col("depth_team").cast(pl.Int32, strict=False))
            .group_by("gsis_id")
            .agg(pl.col("depth_team").min().alias("best_depth"))
        )
        starter_ids = set(starters.filter(pl.col("best_depth") == 1)["gsis_id"].to_list())
    except NoInjuryDataError:
        raise
    except Exception as e:
        raise NoInjuryDataError(f"Injury data not usable for season {season}: {e}") from e

    db.query(Injury).filter(Injury.season == season, Injury.week == week).delete(synchronize_session=False)

    now = dt.datetime.utcnow()
    count = 0
    for row in week_injuries.iter_rows(named=True):
        db.add(
            Injury(
                season=season,
                week=week,
                team_abbr=row["team"],
                gsis_id=row["gsis_id"],
                player_name=row["full_name"],
                position=row.get("position"),
                report_status=row["report_status"],
                practice_status=row.get("practice_status"),
                primary_injury=row.get("report_primary_injury"),
                is_starter=row["gsis_id"] in starter_ids,
                updated_at=now,
            )
        )
        count += 1

    db.commit()
    return count
