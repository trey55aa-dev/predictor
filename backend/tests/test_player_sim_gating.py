import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.player_sim import should_refresh_week_props
from app.models import Base, Game, SimPlayerProjection


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_game(db):
    db.add(
        Game(
            game_id="2026_02_SEA_NE", season=2026, week=2, game_type="REG", gameday="2026-09-17",
            home_team="NE", away_team="SEA",
        )
    )
    db.commit()


def test_should_refresh_when_never_simulated(db):
    _seed_game(db)
    assert should_refresh_week_props(db, 2026, 2) is True


def test_should_not_refresh_again_same_utc_day(db):
    _seed_game(db)
    db.add(
        SimPlayerProjection(
            game_id="2026_02_SEA_NE", player_id="p1", player_name="Test Player", team="SEA",
            season=2026, week=2, model_version="player-sim-v1", n_sims=3000,
            created_at=dt.datetime(2026, 9, 15, 8, 0, 0), anytime_td_probability=0.5,
        )
    )
    db.commit()

    assert should_refresh_week_props(db, 2026, 2, today=dt.date(2026, 9, 15)) is False


def test_should_refresh_on_a_later_utc_day(db):
    _seed_game(db)
    db.add(
        SimPlayerProjection(
            game_id="2026_02_SEA_NE", player_id="p1", player_name="Test Player", team="SEA",
            season=2026, week=2, model_version="player-sim-v1", n_sims=3000,
            created_at=dt.datetime(2026, 9, 15, 8, 0, 0), anytime_td_probability=0.5,
        )
    )
    db.commit()

    assert should_refresh_week_props(db, 2026, 2, today=dt.date(2026, 9, 16)) is True


def test_a_different_weeks_projection_does_not_block_this_week(db):
    _seed_game(db)
    db.add(
        SimPlayerProjection(
            game_id="2026_01_SF_LA", player_id="p1", player_name="Test Player", team="SF",
            season=2026, week=1, model_version="player-sim-v1", n_sims=3000,
            created_at=dt.datetime.utcnow(), anytime_td_probability=0.5,
        )
    )
    db.commit()

    assert should_refresh_week_props(db, 2026, 2) is True
