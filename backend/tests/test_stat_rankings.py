import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.stat_rankings import stat_rank_adjustment
from app.models import Base, Game, Play, Stadium, Team


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_teams(db, teams):
    db.add(Stadium(stadium_id="ST01", name="Test Stadium", roof_type="outdoor"))
    for abbr in teams:
        db.add(Team(team_abbr=abbr, name=abbr, stadium_id="ST01"))
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


def _final_game(db, game_id, week, home, away, home_score, away_score):
    db.add(
        Game(
            game_id=game_id, season=2026, week=week, game_type="REG", gameday="2026-09-10",
            home_team=home, away_team=away, home_score=home_score, away_score=away_score,
            status="final", stadium_id="ST01",
        )
    )
    db.commit()


def _seed_dominant_win(db, game_id, week, winner, loser):
    """`winner` sweeps every core-four key against `loser`."""
    _final_game(db, game_id, week, winner, loser, 30, 3)
    _play(db, game_id, winner, loser, "run", yards_gained=150, play_id=1)
    _play(db, game_id, loser, winner, "run", yards_gained=20, play_id=2)
    _play(db, game_id, winner, loser, "pass", yards_gained=250, play_id=3)
    _play(db, game_id, loser, winner, "pass", yards_gained=60, play_id=4)
    _play(db, game_id, loser, winner, "pass", yards_gained=0, interception=True, play_id=5)
    _play(db, game_id, winner, loser, "run", yards_gained=5, down=3, ydstogo=3, play_id=6)
    _play(db, game_id, loser, winner, "run", yards_gained=0, down=3, ydstogo=3, play_id=7)
    db.commit()


def test_no_adjustment_when_league_has_too_few_ranked_teams(db):
    _seed_teams(db, ["AAA", "BBB"])
    _seed_dominant_win(db, "2026_01_AAA_BBB", 1, "AAA", "BBB")

    delta, note = stat_rank_adjustment(db, "AAA", "BBB", 2026, 2)

    assert delta == 0.0
    assert note is None


def test_no_adjustment_when_a_team_has_no_games_yet_this_season(db):
    _seed_teams(db, ["AAA", "BBB", "CCC", "DDD", "EEE"])
    _seed_dominant_win(db, "2026_01_AAA_BBB", 1, "AAA", "BBB")
    _seed_dominant_win(db, "2026_01_CCC_DDD", 1, "CCC", "DDD")

    delta, note = stat_rank_adjustment(db, "AAA", "EEE", 2026, 2)

    assert delta == 0.0
    assert note is None


def test_team_with_dominant_season_gets_positive_adjustment(db):
    _seed_teams(db, ["AAA", "BBB", "CCC", "DDD"])
    # AAA dominates every core-four key; BBB is worst in every one. CCC/DDD
    # split an even game in between, on all four keys, so the league has a
    # full 4 ranked teams and no stat key gets skipped for too few values.
    _seed_dominant_win(db, "2026_01_AAA_BBB", 1, "AAA", "BBB")
    _final_game(db, "2026_01_CCC_DDD", 1, "CCC", "DDD", 20, 20)
    _play(db, "2026_01_CCC_DDD", "CCC", "DDD", "run", yards_gained=80, play_id=1)
    _play(db, "2026_01_CCC_DDD", "DDD", "CCC", "run", yards_gained=80, play_id=2)
    _play(db, "2026_01_CCC_DDD", "CCC", "DDD", "pass", yards_gained=100, play_id=3)
    _play(db, "2026_01_CCC_DDD", "DDD", "CCC", "pass", yards_gained=100, play_id=4)
    _play(db, "2026_01_CCC_DDD", "CCC", "DDD", "run", yards_gained=10, down=3, ydstogo=5, play_id=5)
    _play(db, "2026_01_CCC_DDD", "CCC", "DDD", "run", yards_gained=2, down=3, ydstogo=5, play_id=6)
    _play(db, "2026_01_CCC_DDD", "DDD", "CCC", "run", yards_gained=10, down=3, ydstogo=5, play_id=7)
    _play(db, "2026_01_CCC_DDD", "DDD", "CCC", "run", yards_gained=2, down=3, ydstogo=5, play_id=8)
    db.commit()

    delta, note = stat_rank_adjustment(db, "AAA", "BBB", 2026, 2)

    # With 4 ranked teams, a team's own value always counts as "at or below
    # itself," so the worst-ranked team's percentile floor is 1/4 (0.25), not
    # 0.0 -- the ±cap is a mathematical ceiling on the gap, never a value a
    # real (non-2-team) league can literally reach. AAA is top percentile
    # (1.0) on all four keys here and BBB is bottom (0.25) on all four, so
    # the gap is exactly 0.75 of the cap.
    assert delta == pytest.approx(0.75 * settings.stat_rank_max_adjustment)
    assert "AAA" in note and "BBB" in note


def test_adjustment_never_looks_at_a_future_or_same_week_game(db):
    _seed_teams(db, ["AAA", "BBB", "CCC", "DDD"])
    _seed_dominant_win(db, "2026_01_AAA_BBB", 1, "AAA", "BBB")
    _final_game(db, "2026_01_CCC_DDD", 1, "CCC", "DDD", 20, 20)
    _play(db, "2026_01_CCC_DDD", "CCC", "DDD", "run", yards_gained=80, play_id=1)
    _play(db, "2026_01_CCC_DDD", "DDD", "CCC", "run", yards_gained=80, play_id=2)
    db.commit()

    delta, note = stat_rank_adjustment(db, "AAA", "BBB", 2026, 1)

    assert delta == 0.0
    assert note is None
