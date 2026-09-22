import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.team_home_field_advantage import team_hfa_adjustment, team_hfa_excess
from app.models import Base, Game, Stadium, Team


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


def _home_game(db, week, home, away, home_score, away_score, game_id, season=2025, stadium_id=None):
    db.add(
        Game(
            game_id=game_id, season=season, week=week, game_type="REG", gameday="2025-09-09",
            home_team=home, away_team=away, home_score=home_score, away_score=away_score,
            status="final", stadium_id=stadium_id or f"{home}01",
        )
    )
    db.commit()


def test_no_adjustment_with_too_few_home_games(db):
    _seed_teams(db, "SEA", "NE")
    for week in range(1, settings.team_hfa_min_home_games):  # one short of the minimum
        _home_game(db, week, "SEA", "NE", 30, 10, f"g{week}")

    excess, n = team_hfa_excess(db, "SEA", 2026, 1)

    assert excess is None
    assert n == settings.team_hfa_min_home_games - 1


def test_team_that_always_wins_at_home_gets_a_positive_capped_delta(db):
    _seed_teams(db, "SEA", "NE")
    # Evenly-matched teams (both start at the same Elo, since no prior games
    # exist), but SEA wins every one of these "home" games outright --
    # far more than a 50/50 + league-average-HFA game would predict.
    for week in range(1, settings.team_hfa_min_home_games + 2):
        _home_game(db, week, "SEA", "NE", 30, 10, f"g{week}")

    delta, note = team_hfa_adjustment(db, "SEA", 2026, 1)

    assert delta == pytest.approx(settings.team_hfa_max_adjustment)
    assert "SEA" in note


def test_team_that_always_loses_at_home_gets_a_negative_capped_delta(db):
    _seed_teams(db, "SEA", "NE")
    for week in range(1, settings.team_hfa_min_home_games + 2):
        _home_game(db, week, "SEA", "NE", 10, 30, f"g{week}")

    delta, note = team_hfa_adjustment(db, "SEA", 2026, 1)

    assert delta == pytest.approx(-settings.team_hfa_max_adjustment)


def test_neutral_site_games_excluded_from_the_sample(db):
    _seed_teams(db, "SEA", "NE")
    db.add(Stadium(stadium_id="LON01", name="Wembley", roof_type="outdoor"))
    db.commit()
    for week in range(1, settings.team_hfa_min_home_games + 2):
        # "home" games, but actually played at a neutral site -- must not count.
        _home_game(db, week, "SEA", "NE", 30, 10, f"g{week}", stadium_id="LON01")

    excess, n = team_hfa_excess(db, "SEA", 2026, 1)

    assert excess is None
    assert n == 0


def test_anti_leakage_only_games_strictly_before_cutoff(db):
    _seed_teams(db, "SEA", "NE")
    for week in range(1, settings.team_hfa_min_home_games + 1):
        _home_game(db, week, "SEA", "NE", 30, 10, f"g{week}")

    # Asking as-of week 3 of the SAME season this history was seeded in
    # (2025) should only see games before week 3 -- too few to trust yet,
    # even though the full history (seen from season 2026) would qualify.
    excess, n = team_hfa_excess(db, "SEA", 2025, 3)

    assert n == 2
    assert excess is None
