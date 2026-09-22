import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.strength_of_schedule import strength_of_schedule
from app.models import Base, Game, Stadium, Team, TeamRating


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_teams(db, *abbrs):
    for abbr in abbrs:
        db.add(Stadium(stadium_id=f"{abbr}01", name=f"{abbr} stadium", roof_type="outdoor"))
        db.add(Team(team_abbr=abbr, name=abbr, stadium_id=f"{abbr}01"))
    db.commit()


def _game(db, week, home, away, game_id, final=True):
    db.add(
        Game(
            game_id=game_id, season=2026, week=week, game_type="REG", gameday="2026-09-09",
            home_team=home, away_team=away,
            home_score=20 if final else None, away_score=10 if final else None,
            status="final" if final else "scheduled", stadium_id=f"{home}01",
        )
    )


def test_splits_played_and_remaining_opponents(db):
    _seed_teams(db, "SEA", "NE", "KC", "DEN")
    _game(db, 1, "SEA", "NE", "g1", final=True)
    _game(db, 2, "SEA", "KC", "g2", final=True)
    _game(db, 3, "SEA", "DEN", "g3", final=False)  # not played yet
    # Each rating must be strictly BEFORE the game it applies to, matching
    # latest_rating's own anti-leakage rule -- these represent each
    # opponent's incoming rating for that week, i.e. as of the prior week.
    db.add(TeamRating(team_abbr="NE", season=2025, week=18, elo=1400))
    db.add(TeamRating(team_abbr="KC", season=2026, week=1, elo=1700))
    db.add(TeamRating(team_abbr="DEN", season=2026, week=1, elo=1550))
    db.commit()

    sos = strength_of_schedule(db, "SEA", 2026, 3)

    assert sos["opponents_played"] == 2
    assert sos["avg_opponent_elo_played"] == pytest.approx((1400 + 1700) / 2)
    assert sos["opponents_remaining"] == 1
    assert sos["avg_opponent_elo_remaining"] == pytest.approx(1550)


def test_no_games_at_all_returns_none_not_a_guessed_average(db):
    _seed_teams(db, "SEA")
    sos = strength_of_schedule(db, "SEA", 2026, 1)

    assert sos["opponents_played"] == 0
    assert sos["avg_opponent_elo_played"] is None
    assert sos["opponents_remaining"] == 0
    assert sos["avg_opponent_elo_remaining"] is None


def test_works_for_away_games_too(db):
    _seed_teams(db, "SEA", "NE")
    _game(db, 1, "NE", "SEA", "g1", final=True)  # SEA is the away team
    db.add(TeamRating(team_abbr="NE", season=2025, week=18, elo=1450))
    db.commit()

    sos = strength_of_schedule(db, "SEA", 2026, 2)

    assert sos["opponents_played"] == 1
    assert sos["avg_opponent_elo_played"] == pytest.approx(1450)
