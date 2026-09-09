import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.gameplan import _injury_data_available, _team_injuries
from app.models import Base, Injury


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _injury(db, season, week, team, gsis_id, name, status="Questionable", starter=False):
    db.add(
        Injury(
            season=season, week=week, team_abbr=team, gsis_id=gsis_id, player_name=name,
            position="RB", report_status=status, is_starter=starter, updated_at=dt.datetime.utcnow(),
        )
    )


def test_injury_data_available_is_false_for_a_season_with_no_rows_at_all(db):
    """The bug this guards against: an empty team injury list reading as
    'checked, genuinely clean' when it actually means 'no data source for
    this season exists yet' -- confirmed live for the 2026 season, where
    nflverse's injury feed has zero rows for any team."""
    db.commit()
    assert _injury_data_available(db, 2026) is False


def test_injury_data_available_is_true_once_any_row_exists_for_the_season(db):
    _injury(db, 2025, 3, "NE", "p1", "Some Player")
    db.commit()

    assert _injury_data_available(db, 2025) is True
    # A different season with no rows is still correctly reported as unknown.
    assert _injury_data_available(db, 2024) is False


def test_team_injuries_returns_empty_list_regardless_of_data_availability(db):
    """_team_injuries itself doesn't know about data availability -- that's
    a separate, season-level check (_injury_data_available), combined at
    the build_gameplan level. This just confirms the team-level query is
    unaffected by the new flag's existence."""
    db.commit()
    assert _team_injuries(db, "NE", 2026, 1) == []


def test_team_injuries_sorts_starters_and_severity_first(db):
    _injury(db, 2025, 1, "NE", "p1", "Bench Guy", status="Questionable", starter=False)
    _injury(db, 2025, 1, "NE", "p2", "Star Player", status="Out", starter=True)
    db.commit()

    result = _team_injuries(db, "NE", 2025, 1)

    assert result[0]["player_name"] == "Star Player"
