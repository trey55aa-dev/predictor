import polars as pl
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingestion import injuries as injuries_module
from app.ingestion.injuries import NoInjuryDataError, ingest_injuries
from app.models import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class _RaisingOnFilter:
    """Stands in for a polars DataFrame whose schema no longer matches what
    this module expects -- reproduces the real bug: load_depth_charts()
    succeeded (so the initial try/except around the load calls never
    caught anything) but calling .filter() on the result raised because a
    column it used to have (here: "week") is gone."""

    def filter(self, *a, **k):
        raise Exception('unable to find column "week"')  # noqa: TRY002 -- reproducing the real upstream shape


def test_ingest_injuries_reports_unavailable_when_load_succeeds_but_schema_is_broken(db, monkeypatch):
    """The bug this guards against: nflverse's load calls can succeed (no
    exception) while returning data whose schema has changed underneath
    this module, and the failure only surfaces later, inside .filter() --
    outside the original try/except's scope, so it used to crash the whole
    ingest-injuries command instead of triggering the ESPN fallback."""
    monkeypatch.setattr(injuries_module.nfl, "load_injuries", lambda seasons: _RaisingOnFilter())
    monkeypatch.setattr(injuries_module.nfl, "load_depth_charts", lambda seasons: _RaisingOnFilter())

    with pytest.raises(NoInjuryDataError):
        ingest_injuries(db, season=2026, week=1)


def test_ingest_injuries_still_propagates_a_load_failure_as_no_injury_data_error(db, monkeypatch):
    def _raise(seasons):
        raise ValueError("Season must be between 2009 and 2025")

    monkeypatch.setattr(injuries_module.nfl, "load_injuries", _raise)

    with pytest.raises(NoInjuryDataError):
        ingest_injuries(db, season=2026, week=1)


def test_ingest_injuries_falls_back_when_feed_has_no_rows_for_the_requested_week(db, monkeypatch):
    """The real bug this guards against: nflverse's load calls can succeed
    with real injury data for OTHER weeks (the feed lags behind the
    season's actual current week) while returning zero rows that match
    the week this routine actually needs. The old code treated that
    exactly like 'this week has zero reportable injuries league-wide' --
    essentially impossible across 32 teams -- and returned 0 instead of
    raising, which silently skipped the ESPN fallback that exists for
    precisely this 'injury data not usable' case."""
    injuries_df = pl.DataFrame(
        {
            "week": [1, 1],
            "report_status": ["Out", "Questionable"],
            "team": ["KC", "BUF"],
            "gsis_id": ["g1", "g2"],
            "full_name": ["Player One", "Player Two"],
            "position": ["WR", "RB"],
            "report_primary_injury": ["Ankle", "Knee"],
            "practice_status": ["DNP", "Limited"],
        }
    )
    depth_charts_df = pl.DataFrame(
        {
            "week": [1, 1],
            "gsis_id": ["g1", "g2"],
            "depth_team": [1, 1],
        }
    )
    monkeypatch.setattr(injuries_module.nfl, "load_injuries", lambda seasons: injuries_df)
    monkeypatch.setattr(injuries_module.nfl, "load_depth_charts", lambda seasons: depth_charts_df)

    with pytest.raises(NoInjuryDataError):
        ingest_injuries(db, season=2026, week=3)
