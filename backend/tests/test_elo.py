import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.elo import build_ratings, expected_win_prob, _mov_multiplier
from app.models import Base, Game, Stadium, Team, TeamRating


def test_expected_win_prob_equal_ratings():
    assert expected_win_prob(1500, 1500) == 0.5


def test_expected_win_prob_favors_higher_rating():
    prob = expected_win_prob(1600, 1500)
    assert 0.5 < prob < 1.0


def test_expected_win_prob_symmetric():
    p1 = expected_win_prob(1600, 1400)
    p2 = expected_win_prob(1400, 1600)
    assert abs(p1 + p2 - 1.0) < 1e-9


def test_mov_multiplier_increases_with_margin():
    small = _mov_multiplier(3, 0)
    large = _mov_multiplier(28, 0)
    assert large > small


def test_mov_multiplier_dampened_for_expected_blowout():
    # A big favorite winning big should move ratings less than an underdog
    # winning by the same margin (both directions must stay well-behaved).
    fav_mult = _mov_multiplier(21, 400)
    dampened_only = _mov_multiplier(21, 0)
    assert fav_mult < dampened_only


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def test_build_ratings_does_not_bake_in_home_field_advantage_for_neutral_site_games(db):
    """A neutral-site game (e.g. the Melbourne international game) must not
    move the "home" team's rating as though it enjoyed a real home-field
    edge, or every future week's Elo for both teams stays permanently
    skewed by a boost that was never actually earned."""
    db.add(Stadium(stadium_id="LAX01", name="SoFi Stadium", roof_type="dome"))
    db.add(Stadium(stadium_id="SFO01", name="Levi's Stadium", roof_type="outdoor"))
    db.add(Stadium(stadium_id="MEL00", name="Melbourne Cricket Ground", roof_type="outdoor"))
    db.add(Team(team_abbr="LA", name="Rams", stadium_id="LAX01"))
    db.add(Team(team_abbr="SF", name="49ers", stadium_id="SFO01"))
    db.add(
        Game(
            game_id="2026_01_SF_LA", season=2026, week=1, game_type="REG", gameday="2026-09-05",
            home_team="LA", away_team="SF", home_score=7, away_score=27, stadium_id="MEL00",
        )
    )
    db.commit()

    build_ratings(db, [2026])

    la_rating = db.query(TeamRating).filter_by(team_abbr="LA", season=2026, week=1).one().elo
    sf_rating = db.query(TeamRating).filter_by(team_abbr="SF", season=2026, week=1).one().elo

    # Both teams started at the same rating with no home-field bonus in play,
    # so the post-game ratings must be symmetric around the start rating.
    assert la_rating + sf_rating == pytest.approx(3000.0)
    assert la_rating < 1500.0 < sf_rating
