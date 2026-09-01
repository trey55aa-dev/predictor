import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.schedule_context import (
    current_or_next_week,
    current_season,
    is_game_day_or_eve,
    should_run_dense_cadence,
)
from app.models import Base, Game


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(game_id, week, gameday, season=2026):
    return Game(
        game_id=game_id,
        season=season,
        week=week,
        game_type="REG",
        gameday=gameday,
        home_team="AAA",
        away_team="BBB",
        status="scheduled",
    )


def test_current_season_in_fall_is_same_year():
    assert current_season(dt.date(2026, 8, 26)) == 2026


def test_current_season_in_january_is_previous_year():
    assert current_season(dt.date(2026, 1, 15)) == 2025


def test_current_season_in_offseason_is_same_year():
    assert current_season(dt.date(2026, 5, 1)) == 2026


def test_no_games_returns_none(db):
    assert current_or_next_week(db, 2026) is None


def test_today_before_season_returns_week_one(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.add(_game("g2", 2, "2026-09-17"))
    db.commit()
    result = current_or_next_week(db, 2026, today=dt.date(2026, 8, 26))
    assert result == 1


def test_today_between_weeks_returns_next_week(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.add(_game("g2", 2, "2026-09-17"))
    db.commit()
    result = current_or_next_week(db, 2026, today=dt.date(2026, 9, 12))
    assert result == 2


def test_today_within_a_week_returns_that_week(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.add(_game("g2", 2, "2026-09-17"))
    db.commit()
    result = current_or_next_week(db, 2026, today=dt.date(2026, 9, 10))
    assert result == 1


def test_today_after_season_returns_last_week(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.add(_game("g2", 2, "2026-09-17"))
    db.commit()
    result = current_or_next_week(db, 2026, today=dt.date(2027, 1, 1))
    assert result == 2


def test_is_game_day_true_when_game_is_today(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.commit()
    assert is_game_day_or_eve(db, 2026, today=dt.date(2026, 9, 10)) is True


def test_is_game_day_true_when_game_is_tomorrow(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.commit()
    assert is_game_day_or_eve(db, 2026, today=dt.date(2026, 9, 9)) is True


def test_is_game_day_false_on_a_quiet_day(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.commit()
    assert is_game_day_or_eve(db, 2026, today=dt.date(2026, 9, 3)) is False


def test_is_game_day_false_with_no_schedule_data(db):
    assert is_game_day_or_eve(db, 2026, today=dt.date(2026, 9, 3)) is False


def test_dense_cadence_always_true_before_season_opener(db):
    # Well before kickoff and not itself a game day/eve -- still dense,
    # per the "4x/day unconditionally until the season starts" rule.
    db.add(_game("g1", 1, "2026-09-10"))
    db.commit()
    assert should_run_dense_cadence(db, 2026, today=dt.date(2026, 8, 20)) is True


def test_dense_cadence_quiet_on_a_genuine_in_season_off_day(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.add(_game("g2", 2, "2026-09-17"))
    db.commit()
    # Between weeks 1 and 2, several days from either -- a real off-day.
    assert should_run_dense_cadence(db, 2026, today=dt.date(2026, 9, 13)) is False


def test_dense_cadence_true_on_in_season_game_day(db):
    db.add(_game("g1", 1, "2026-09-10"))
    db.add(_game("g2", 2, "2026-09-17"))
    db.commit()
    assert should_run_dense_cadence(db, 2026, today=dt.date(2026, 9, 10)) is True


def test_dense_cadence_true_with_no_schedule_data(db):
    assert should_run_dense_cadence(db, 2026, today=dt.date(2026, 8, 20)) is True
