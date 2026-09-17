import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.grade import grade_week, over_under_summary
from app.models import Base, Game, OddsSnapshot, Prediction, Stadium, Team


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_teams(db):
    db.add(Stadium(stadium_id="SEA01", name="Lumen Field", roof_type="outdoor"))
    db.add(Team(team_abbr="SEA", name="Seahawks", stadium_id="SEA01"))
    db.add(Team(team_abbr="NE", name="Patriots", stadium_id="SEA01"))
    db.commit()


def _seed_graded_game(db, game_id, week, home_score, away_score, predicted_total, total_line):
    db.add(
        Game(
            game_id=game_id, season=2026, week=week, game_type="REG", gameday="2026-09-09",
            home_team="SEA", away_team="NE", home_score=home_score, away_score=away_score,
            status="final", stadium_id="SEA01",
        )
    )
    odds = OddsSnapshot(
        game_id=game_id, fetched_at=dt.datetime.utcnow(), bookmaker="test-book",
        home_moneyline=-150, away_moneyline=130, spread_line=-3.0, total_line=total_line,
    )
    db.add(odds)
    db.commit()

    prediction = Prediction(
        game_id=game_id, model_version="test", created_at=dt.datetime.utcnow(),
        odds_snapshot_id=odds.id, home_elo=1550, away_elo=1500, home_win_prob=0.6,
        elo_win_prob=0.6, market_win_prob=0.58,
        predicted_home_score=predicted_total / 2 + 3, predicted_away_score=predicted_total / 2 - 3,
        predicted_margin=6, predicted_total=predicted_total,
        margin_range_low=-5, margin_range_high=17, total_range_low=predicted_total - 10,
        total_range_high=predicted_total + 10,
    )
    db.add(prediction)
    db.commit()
    grade_week(db, 2026, week)


def test_no_data_returns_zero_sample(db):
    summary = over_under_summary(db)
    assert summary == {"graded_with_line": 0, "decided": 0, "pushes": 0, "over_under_accuracy": None}


def test_correct_over_call(db):
    _seed_teams(db)
    # Model predicted 50, line was 44, actual was 51 -- model correctly leaned over.
    _seed_graded_game(db, "2026_01_NE_SEA", 1, home_score=27, away_score=24, predicted_total=50, total_line=44)

    summary = over_under_summary(db, season=2026)

    assert summary["graded_with_line"] == 1
    assert summary["decided"] == 1
    assert summary["pushes"] == 0
    assert summary["over_under_accuracy"] == 1.0


def test_incorrect_under_call(db):
    _seed_teams(db)
    # Model predicted 38 (leaning under a 44 line), actual was 51 -- model called it wrong.
    _seed_graded_game(db, "2026_01_NE_SEA", 1, home_score=27, away_score=24, predicted_total=38, total_line=44)

    summary = over_under_summary(db, season=2026)

    assert summary["decided"] == 1
    assert summary["over_under_accuracy"] == 0.0


def test_real_push_excluded_from_decided(db):
    """The actual score landing exactly on the market line is a push in
    real betting -- neither a win nor a loss for the model's call, so it
    shouldn't count toward accuracy."""
    _seed_teams(db)
    _seed_graded_game(db, "2026_01_NE_SEA", 1, home_score=27, away_score=17, predicted_total=50, total_line=44)

    summary = over_under_summary(db, season=2026)

    assert summary["graded_with_line"] == 1
    assert summary["pushes"] == 1
    assert summary["decided"] == 0
    assert summary["over_under_accuracy"] is None


def test_prediction_without_odds_snapshot_is_excluded(db):
    """A prediction made with no market line available at the time
    (odds_snapshot_id is None) has nothing to grade over/under against."""
    _seed_teams(db)
    db.add(
        Game(
            game_id="2026_01_NE_SEA", season=2026, week=1, game_type="REG", gameday="2026-09-09",
            home_team="SEA", away_team="NE", home_score=27, away_score=24, status="final",
            stadium_id="SEA01",
        )
    )
    db.commit()
    db.add(
        Prediction(
            game_id="2026_01_NE_SEA", model_version="test", created_at=dt.datetime.utcnow(),
            odds_snapshot_id=None, home_elo=1550, away_elo=1500, home_win_prob=0.6,
            predicted_home_score=27, predicted_away_score=24, predicted_margin=3, predicted_total=51,
            margin_range_low=-10, margin_range_high=16, total_range_low=41, total_range_high=61,
        )
    )
    db.commit()
    grade_week(db, 2026, 1)

    summary = over_under_summary(db, season=2026)

    assert summary["graded_with_line"] == 0


def test_filters_by_week(db):
    _seed_teams(db)
    _seed_graded_game(db, "2026_01_NE_SEA", 1, home_score=27, away_score=24, predicted_total=50, total_line=44)
    _seed_graded_game(db, "2026_02_NE_SEA", 2, home_score=10, away_score=7, predicted_total=30, total_line=44)

    summary = over_under_summary(db, season=2026, week=1)

    assert summary["graded_with_line"] == 1
