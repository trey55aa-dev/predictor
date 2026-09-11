import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.venue import is_true_home_game
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
    db.add(Stadium(stadium_id="MEL00", name="Melbourne Cricket Ground", roof_type="outdoor"))
    db.add(Team(team_abbr="LA", name="Rams", stadium_id="LAX01"))
    db.add(Team(team_abbr="SF", name="49ers", stadium_id="SFO01"))
    db.commit()


def test_true_home_game_when_stadium_matches_home_team(db):
    _seed_stadiums_and_teams(db)
    game = Game(
        game_id="g1", season=2026, week=1, game_type="REG", gameday="2026-09-10",
        home_team="LA", away_team="SF", stadium_id="LAX01",
    )
    db.add(game)
    db.commit()

    assert is_true_home_game(db, game) is True


def test_neutral_site_game_is_not_a_true_home_game(db):
    _seed_stadiums_and_teams(db)
    game = Game(
        game_id="2026_01_SF_LA", season=2026, week=1, game_type="REG", gameday="2026-09-10",
        home_team="LA", away_team="SF", stadium_id="MEL00",
    )
    db.add(game)
    db.commit()

    assert is_true_home_game(db, game) is False


def test_missing_stadium_data_defaults_to_true_home_game(db):
    game = Game(
        game_id="g1", season=2026, week=1, game_type="REG", gameday="2026-09-10",
        home_team="LA", away_team="SF", stadium_id=None,
    )
    db.add(game)
    db.commit()

    assert is_true_home_game(db, game) is True
