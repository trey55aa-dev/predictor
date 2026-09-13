import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.recent_form import recent_form_adjustment
from app.models import Base, Game, Play, Stadium, Team


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
    db.add(Stadium(stadium_id="NE01", name="Gillette Stadium", roof_type="outdoor"))
    db.add(Team(team_abbr="SEA", name="Seahawks", stadium_id="SEA01"))
    db.add(Team(team_abbr="NE", name="Patriots", stadium_id="NE01"))
    db.commit()


def _play(db, game_id, posteam, play_type, yards_gained=0, down=None, ydstogo=None,
          interception=False, fumble_lost=False, sack=False, play_id=1):
    db.add(
        Play(
            play_key=f"{game_id}_{play_id}", game_id=game_id, season=2026, week=1,
            posteam=posteam, defteam="NE" if posteam == "SEA" else "SEA", play_type=play_type,
            down=down, ydstogo=ydstogo, yards_gained=yards_gained,
            interception=interception, fumble_lost=fumble_lost, sack=sack,
        )
    )


def _seed_seahawks_sweep(db):
    """SEA wins every tracked key against NE in a finished week-1 game."""
    db.add(
        Game(
            game_id="2026_01_NE_SEA", season=2026, week=1, game_type="REG", gameday="2026-09-09",
            home_team="SEA", away_team="NE", home_score=13, away_score=10, status="final",
            stadium_id="SEA01",
        )
    )
    db.commit()
    _play(db, "2026_01_NE_SEA", "SEA", "run", yards_gained=100, play_id=1)
    _play(db, "2026_01_NE_SEA", "NE", "run", yards_gained=20, play_id=2)
    _play(db, "2026_01_NE_SEA", "SEA", "pass", yards_gained=200, play_id=3)
    _play(db, "2026_01_NE_SEA", "NE", "pass", yards_gained=50, play_id=4)
    _play(db, "2026_01_NE_SEA", "NE", "pass", yards_gained=0, interception=True, play_id=5)
    _play(db, "2026_01_NE_SEA", "SEA", "run", yards_gained=5, down=3, ydstogo=3, play_id=6)
    _play(db, "2026_01_NE_SEA", "NE", "run", yards_gained=0, down=3, ydstogo=3, play_id=7)
    db.commit()


def test_no_adjustment_when_team_has_no_prior_game_this_season(db):
    _seed_teams(db)
    delta, note = recent_form_adjustment(db, "SEA", 2026, 1)
    assert delta == 0.0
    assert note is None


def test_no_adjustment_when_prior_games_plays_not_yet_ingested(db):
    _seed_teams(db)
    db.add(
        Game(
            game_id="2026_01_NE_SEA", season=2026, week=1, game_type="REG", gameday="2026-09-09",
            home_team="SEA", away_team="NE", home_score=13, away_score=10, status="final",
            stadium_id="SEA01",
        )
    )
    db.commit()

    delta, note = recent_form_adjustment(db, "SEA", 2026, 2)

    assert delta == 0.0
    assert note is None


def test_winning_team_gets_a_positive_capped_adjustment(db):
    _seed_teams(db)
    _seed_seahawks_sweep(db)

    delta, note = recent_form_adjustment(db, "SEA", 2026, 2)

    assert delta == pytest.approx(settings.recent_form_max_adjustment)
    assert "SEA won" in note
    assert "NE" in note


def test_losing_team_gets_a_negative_capped_adjustment(db):
    _seed_teams(db)
    _seed_seahawks_sweep(db)

    delta, note = recent_form_adjustment(db, "NE", 2026, 2)

    assert delta == pytest.approx(-settings.recent_form_max_adjustment)


def test_adjustment_never_looks_at_a_future_or_same_week_game(db):
    """The most-recent-game lookup must be strictly before the target week,
    or a team's own upcoming game could leak into its own recent-form
    signal."""
    _seed_teams(db)
    _seed_seahawks_sweep(db)

    delta, note = recent_form_adjustment(db, "SEA", 2026, 1)

    assert delta == 0.0
    assert note is None
