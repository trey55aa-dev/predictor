import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.scoring import matchup_expected_total, team_scoring_averages
from app.models import Base, Game


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(db, game_id, home, away, home_score, away_score, season=2025, week=1):
    db.add(
        Game(
            game_id=game_id,
            season=season,
            week=week,
            game_type="REG",
            gameday="2025-09-08",
            home_team=home,
            away_team=away,
            home_score=home_score,
            away_score=away_score,
            status="final",
        )
    )


def test_team_scoring_averages_no_history_falls_back_to_league_average(db):
    result = team_scoring_averages(db, "KC", 2026, 1)
    assert result["sample_size"] == 0
    assert result["avg_scored"] == 22.0


def test_team_scoring_averages_computes_real_split(db):
    _game(db, "g1", "KC", "DEN", home_score=30, away_score=10, week=1)
    _game(db, "g2", "LAC", "KC", home_score=14, away_score=27, week=2)
    db.commit()

    result = team_scoring_averages(db, "KC", 2025, 3)
    assert result["sample_size"] == 2
    assert abs(result["avg_scored"] - ((30 + 27) / 2)) < 1e-9
    assert abs(result["avg_allowed"] - ((10 + 14) / 2)) < 1e-9


def test_team_scoring_averages_excludes_future_games(db):
    _game(db, "g1", "KC", "DEN", home_score=30, away_score=10, week=5)
    db.commit()

    result = team_scoring_averages(db, "KC", 2025, 3)  # before week 5
    assert result["sample_size"] == 0


def test_team_scoring_averages_crosses_season_boundary(db):
    _game(db, "g1", "KC", "DEN", home_score=24, away_score=17, season=2025, week=17)
    db.commit()

    result = team_scoring_averages(db, "KC", 2026, 1)
    assert result["sample_size"] == 1
    assert result["avg_scored"] == 24


def test_matchup_expected_total_varies_by_matchup(db):
    # High-scoring team vs a team that allows a lot
    _game(db, "g1", "HIGH", "X", home_score=35, away_score=10, week=1)
    _game(db, "g2", "Y", "LEAKY", home_score=10, away_score=40, week=1)
    # Low-scoring, stingy defense pairing
    _game(db, "g3", "LOW", "Z", home_score=10, away_score=7, week=1)
    _game(db, "g4", "W", "STINGY", home_score=6, away_score=9, week=1)
    db.commit()

    shootout_total = matchup_expected_total(db, "HIGH", "LEAKY", 2025, 3)
    defensive_total = matchup_expected_total(db, "LOW", "STINGY", 2025, 3)

    assert shootout_total > defensive_total
    assert shootout_total != defensive_total  # not collapsed to a shared constant
