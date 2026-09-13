import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cli import HISTORY_SEASONS
from app.model.elo import build_ratings, latest_rating
from app.model.grade import grade_week
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


def _seed_teams(db):
    db.add(Stadium(stadium_id="LAX01", name="SoFi Stadium", roof_type="dome"))
    db.add(Stadium(stadium_id="SFO01", name="Levi's Stadium", roof_type="outdoor"))
    db.add(Team(team_abbr="LA", name="Rams", stadium_id="LAX01"))
    db.add(Team(team_abbr="SF", name="49ers", stadium_id="SFO01"))
    db.commit()


def _seed_and_grade_week1_blowout(db):
    game = Game(
        game_id="2026_01_SF_LA", season=2026, week=1, game_type="REG", gameday="2026-09-10",
        home_team="LA", away_team="SF", home_score=7, away_score=27, stadium_id="LAX01",
    )
    db.add(game)
    db.commit()
    predict_game(db, game)
    db.commit()
    assert grade_week(db, 2026, 1) == 1


def test_grading_a_final_game_alone_does_not_move_next_weeks_elo(db):
    """README documents 'Team strength (Elo) has always updated automatically
    from results,' but run-routine's grading step never rebuilds Elo for the
    live season -- only the manual `build-history` command calls
    build_ratings(), and it's hardcoded to HISTORY_SEASONS (2021-2025), which
    deliberately excludes the current season (see the note in cli.py -- that
    exclusion is about nflreadpy's play-by-play feed rejecting in-progress
    seasons, a limitation that has nothing to do with Elo, which only reads
    Game.home_score/away_score). Net effect: a real, graded 20-point road
    blowout leaves next week's Elo completely untouched."""
    _seed_teams(db)
    _seed_and_grade_week1_blowout(db)

    assert latest_rating(db, "LA", 2026, 2) == 1500.0
    assert latest_rating(db, "SF", 2026, 2) == 1500.0


def test_rebuilding_ratings_with_the_live_season_included_reflects_the_result(db):
    """The fix: rebuild Elo across HISTORY_SEASONS + the live season (not
    HISTORY_SEASONS alone) after grading, so a just-graded result actually
    moves next week's rating -- matching the documented behavior."""
    _seed_teams(db)
    _seed_and_grade_week1_blowout(db)

    build_ratings(db, HISTORY_SEASONS + [2026])

    la_next_week = latest_rating(db, "LA", 2026, 2)
    sf_next_week = latest_rating(db, "SF", 2026, 2)
    assert la_next_week < 1500.0 < sf_next_week
