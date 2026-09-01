import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.parlays import grade_parlays, log_parlays, parlay_performance_summary
from app.models import Base, Game, OddsSnapshot, ParlayPick, Prediction


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(db, game_id, home, away, season=2024, week=1, home_score=None, away_score=None):
    game = Game(
        game_id=game_id,
        season=season,
        week=week,
        game_type="REG",
        gameday="2024-09-08",
        home_team=home,
        away_team=away,
        home_score=home_score,
        away_score=away_score,
        status="final" if home_score is not None else "scheduled",
    )
    db.add(game)
    return game


def _prediction(db, game_id, home_win_prob, odds_snapshot_id=None):
    pred = Prediction(
        game_id=game_id,
        model_version="test",
        created_at=dt.datetime.utcnow(),
        odds_snapshot_id=odds_snapshot_id,
        home_elo=1500,
        away_elo=1500,
        home_win_prob=home_win_prob,
        predicted_home_score=24,
        predicted_away_score=20,
        predicted_margin=4,
        predicted_total=44,
        margin_range_low=-9.5,
        margin_range_high=17.5,
        total_range_low=34,
        total_range_high=54,
    )
    db.add(pred)
    return pred


def _odds(db, game_id, home_ml, away_ml):
    odds = OddsSnapshot(game_id=game_id, fetched_at=dt.datetime.utcnow(), bookmaker="consensus", home_moneyline=home_ml, away_moneyline=away_ml)
    db.add(odds)
    db.flush()
    return odds


def test_log_parlays_persists_picks_and_legs(db):
    _game(db, "g1", "KC", "DEN")
    _prediction(db, "g1", 0.8)
    db.commit()

    logged = log_parlays(db, 2024, 1, legs=1)
    assert logged >= 1
    picks = db.query(ParlayPick).all()
    assert len(picks) >= 1
    safest = next(p for p in picks if p.parlay_type == "safest")
    assert len(safest.legs) == 1
    assert safest.legs[0].team == "KC"


def test_log_parlays_replaces_ungraded_picks_not_duplicates(db):
    _game(db, "g1", "KC", "DEN")
    _prediction(db, "g1", 0.8)
    db.commit()

    log_parlays(db, 2024, 1, legs=1)
    log_parlays(db, 2024, 1, legs=1)  # re-run same week, e.g. odds updated

    safest_picks = db.query(ParlayPick).filter(ParlayPick.parlay_type == "safest").all()
    assert len(safest_picks) == 1


def test_grade_parlays_marks_hit_when_all_legs_win(db):
    _game(db, "g1", "KC", "DEN", home_score=27, away_score=20)
    _prediction(db, "g1", 0.8)
    db.commit()
    log_parlays(db, 2024, 1, legs=1)

    graded = grade_parlays(db, 2024, 1)
    assert graded >= 1
    pick = db.query(ParlayPick).filter(ParlayPick.parlay_type == "safest").first()
    assert pick.all_legs_hit is True
    assert pick.graded_at is not None


def test_grade_parlays_marks_miss_when_a_leg_loses(db):
    _game(db, "g1", "KC", "DEN", home_score=17, away_score=24)  # KC (picked, home favored) loses
    _prediction(db, "g1", 0.8)
    db.commit()
    log_parlays(db, 2024, 1, legs=1)

    grade_parlays(db, 2024, 1)
    pick = db.query(ParlayPick).filter(ParlayPick.parlay_type == "safest").first()
    assert pick.all_legs_hit is False


def test_grade_parlays_skips_unfinished_games(db):
    _game(db, "g1", "KC", "DEN")  # no score yet
    _prediction(db, "g1", 0.8)
    db.commit()
    log_parlays(db, 2024, 1, legs=1)

    graded = grade_parlays(db, 2024, 1)
    assert graded == 0
    pick = db.query(ParlayPick).first()
    assert pick.graded_at is None


def test_parlay_performance_summary_computes_hit_rate(db):
    _game(db, "g1", "KC", "DEN", home_score=27, away_score=20)
    _prediction(db, "g1", 0.8)
    _game(db, "g2", "SF", "SEA", week=2, home_score=10, away_score=24)  # SF favored but loses
    _prediction(db, "g2", 0.7)
    db.commit()

    log_parlays(db, 2024, 1, legs=1)
    log_parlays(db, 2024, 2, legs=1)
    grade_parlays(db, 2024, 1)
    grade_parlays(db, 2024, 2)

    summary = parlay_performance_summary(db)
    assert summary["safest"]["graded_parlays"] == 2
    assert abs(summary["safest"]["hit_rate"] - 0.5) < 1e-9
