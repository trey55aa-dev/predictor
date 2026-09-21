import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.league_context import league_pass_rate_by_week
from app.models import Base, Play


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _play(db, play_id, week, play_type, posteam="SEA", defteam="NE"):
    db.add(
        Play(
            play_key=f"g_{week}_{play_id}", game_id=f"g_{week}", season=2026, week=week,
            posteam=posteam, defteam=defteam, play_type=play_type,
        )
    )


def test_league_pass_rate_combines_every_team_in_the_week(db):
    _play(db, 1, 1, "pass", posteam="SEA", defteam="NE")
    _play(db, 2, 1, "run", posteam="NE", defteam="SEA")
    _play(db, 3, 1, "pass", posteam="KC", defteam="DEN")
    _play(db, 4, 1, "pass", posteam="DEN", defteam="KC")
    db.commit()

    rates = league_pass_rate_by_week(db, 2026)

    assert rates == {1: pytest.approx(0.75)}  # 3 pass / 4 total, across all four teams


def test_league_pass_rate_omits_weeks_with_no_ingested_plays(db):
    _play(db, 1, 1, "pass")
    db.commit()

    rates = league_pass_rate_by_week(db, 2026)

    assert 2 not in rates


def test_league_pass_rate_ignores_special_teams_play_types(db):
    """Only run/pass plays are ever ingested into this table (see
    ingestion/plays.py's scope note), but this guards the filter in case
    that ever changes without this function being updated."""
    _play(db, 1, 1, "pass")
    _play(db, 2, 1, "punt")
    db.commit()

    rates = league_pass_rate_by_week(db, 2026)

    assert rates[1] == pytest.approx(1.0)
