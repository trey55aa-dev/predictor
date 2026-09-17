import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.stat_rankings import league_stat_averages, stat_ranking_adjustment
from app.models import Base, Game, Play, Stadium, Team


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


def _play(db, game_id, posteam, defteam, play_type, yards_gained=0, down=None, ydstogo=None,
          interception=False, fumble_lost=False, sack=False, play_id=1):
    db.add(
        Play(
            play_key=f"{game_id}_{play_id}", game_id=game_id, season=2026, week=1,
            posteam=posteam, defteam=defteam, play_type=play_type,
            down=down, ydstogo=ydstogo, yards_gained=yards_gained,
            interception=interception, fumble_lost=fumble_lost, sack=sack,
        )
    )


def _seed_blowout(db, week, home, away, home_score, away_score, game_id):
    """`home` sweeps every tracked key against `away`."""
    db.add(
        Game(
            game_id=game_id, season=2026, week=week, game_type="REG", gameday="2026-09-09",
            home_team=home, away_team=away, home_score=home_score, away_score=away_score,
            status="final", stadium_id=f"{home}01",
        )
    )
    db.commit()
    _play(db, game_id, home, away, "run", yards_gained=150, play_id=1)
    _play(db, game_id, away, home, "run", yards_gained=20, play_id=2)
    _play(db, game_id, home, away, "pass", yards_gained=250, play_id=3)
    _play(db, game_id, away, home, "pass", yards_gained=50, play_id=4)
    _play(db, game_id, away, home, "pass", yards_gained=0, interception=True, play_id=5)
    _play(db, game_id, home, away, "run", yards_gained=5, down=3, ydstogo=3, play_id=6)
    _play(db, game_id, away, home, "run", yards_gained=0, down=3, ydstogo=3, play_id=7)
    db.commit()


def test_no_adjustment_when_team_has_no_prior_game_this_season(db):
    _seed_teams(db, "SEA", "NE")
    delta, note = stat_ranking_adjustment(db, "SEA", 2026, 1)
    assert delta == 0.0
    assert note is None


def test_two_teams_with_data_still_rank_against_each_other(db):
    """A single game gives both its participants data (there's no way to
    get just one), so even the smallest possible league already supports a
    real, capped ranking between them."""
    _seed_teams(db, "SEA", "NE")
    _seed_blowout(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")

    sea_delta, sea_note = stat_ranking_adjustment(db, "SEA", 2026, 2)
    ne_delta, _ = stat_ranking_adjustment(db, "NE", 2026, 2)

    assert sea_delta == pytest.approx(settings.stat_rank_max_adjustment)
    assert ne_delta == pytest.approx(-settings.stat_rank_max_adjustment)
    assert "SEA ranks" in sea_note


def test_adjustment_never_looks_at_a_future_or_same_week_game(db):
    _seed_teams(db, "SEA", "NE", "KC", "DEN")
    _seed_blowout(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    _seed_blowout(db, 1, "KC", "DEN", 30, 3, "2026_01_DEN_KC")

    delta, note = stat_ranking_adjustment(db, "SEA", 2026, 1)

    assert delta == 0.0
    assert note is None


def test_top_team_gets_a_positive_capped_adjustment(db):
    """SEA and KC both crush their week-1 opponents, but KC crushes harder
    across every category -- SEA should still rank above the two losing
    teams, with a delta capped at stat_rank_max_adjustment."""
    _seed_teams(db, "SEA", "NE", "KC", "DEN")
    _seed_blowout(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    _seed_blowout(db, 1, "KC", "DEN", 45, 3, "2026_01_DEN_KC")

    sea_delta, sea_note = stat_ranking_adjustment(db, "SEA", 2026, 2)
    ne_delta, _ = stat_ranking_adjustment(db, "NE", 2026, 2)

    assert sea_delta > ne_delta
    assert abs(sea_delta) <= settings.stat_rank_max_adjustment + 1e-9
    assert "SEA ranks" in sea_note


def test_worst_team_gets_a_negative_adjustment(db):
    _seed_teams(db, "SEA", "NE", "KC", "DEN")
    _seed_blowout(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    _seed_blowout(db, 1, "KC", "DEN", 45, 3, "2026_01_DEN_KC")

    delta, note = stat_ranking_adjustment(db, "DEN", 2026, 2)

    assert delta < 0.0


def test_league_stat_averages_skips_games_with_no_ingested_plays(db):
    """A final game with a real score but no Play rows yet must not be
    silently treated as 0-for-everything."""
    _seed_teams(db, "SEA", "NE")
    db.add(
        Game(
            game_id="2026_01_NE_SEA", season=2026, week=1, game_type="REG", gameday="2026-09-09",
            home_team="SEA", away_team="NE", home_score=13, away_score=10, status="final",
            stadium_id="SEA01",
        )
    )
    db.commit()

    averages = league_stat_averages(db, 2026, 2)

    assert averages == {}


def test_league_stat_averages_handles_games_with_no_third_down_attempts(db):
    """A game with zero 3rd-down attempts gives team_stats a None
    third_down_pct -- must be excluded from that category's average, not
    treated as 0%."""
    _seed_teams(db, "SEA", "NE")
    db.add(
        Game(
            game_id="2026_01_NE_SEA", season=2026, week=1, game_type="REG", gameday="2026-09-09",
            home_team="SEA", away_team="NE", home_score=13, away_score=10, status="final",
            stadium_id="SEA01",
        )
    )
    db.commit()
    _play(db, "2026_01_NE_SEA", "SEA", "NE", "run", yards_gained=10, play_id=1)
    db.commit()

    averages = league_stat_averages(db, 2026, 2)

    assert "third_down_pct" not in averages["SEA"]
    assert averages["SEA"]["rushing_yards"] == 10
