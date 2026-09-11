import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.elo import expected_win_prob
from app.model.predict import predict_game
from app.models import Base, Game, Stadium, Team


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_stadiums_and_teams(db):
    db.add(Stadium(stadium_id="LAX01", name="SoFi Stadium", roof_type="dome"))
    db.add(Stadium(stadium_id="SFO01", name="Levi's Stadium", roof_type="outdoor"))
    db.add(Stadium(stadium_id="MEL00", name="Melbourne Cricket Ground", roof_type="outdoor"))
    db.add(Team(team_abbr="LA", name="Rams", stadium_id="LAX01"))
    db.add(Team(team_abbr="SF", name="49ers", stadium_id="SFO01"))
    db.commit()


def test_predict_game_applies_home_field_advantage_for_a_real_home_game(db):
    _seed_stadiums_and_teams(db)
    game = Game(
        game_id="g_home", season=2026, week=2, game_type="REG", gameday="2026-09-17",
        home_team="LA", away_team="SF", stadium_id="LAX01",
    )
    db.add(game)
    db.commit()

    prediction = predict_game(db, game)

    expected = expected_win_prob(1500 + settings.elo_home_field_advantage, 1500)
    assert prediction.elo_win_prob == pytest.approx(expected)


def test_predict_game_withholds_home_field_advantage_for_a_neutral_site_game(db):
    """Reproduces the 2026_01_SF_LA game played at the Melbourne Cricket
    Ground: the Rams (LA) were still the schedule's nominal "home" team but
    the game was a neutral site -- LA got no real crowd advantage, and per
    real-world reporting LA actually had the worse end of the travel (a
    ~28-hour turnaround vs. SF arriving a week early). Elo should score this
    as a pick'em matchup between equal ratings, not hand LA the usual +60
    home-field Elo bump on top of an already-disadvantageous trip."""
    _seed_stadiums_and_teams(db)
    game = Game(
        game_id="2026_01_SF_LA", season=2026, week=1, game_type="REG", gameday="2026-09-05",
        home_team="LA", away_team="SF", stadium_id="MEL00",
    )
    db.add(game)
    db.commit()

    prediction = predict_game(db, game)

    assert prediction.elo_win_prob == pytest.approx(expected_win_prob(1500, 1500))
    assert prediction.elo_win_prob == pytest.approx(0.5)
