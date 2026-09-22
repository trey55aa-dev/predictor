import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.red_zone_stats import (
    league_red_zone_averages,
    red_zone_adjustment,
    team_red_zone_stats,
)
from app.models import Base, Game, Play, PlayDriveContext, Stadium, Team


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


def _seed_game(db, week, home, away, home_score, away_score, game_id):
    db.add(
        Game(
            game_id=game_id, season=2026, week=week, game_type="REG", gameday="2026-09-09",
            home_team=home, away_team=away, home_score=home_score, away_score=away_score,
            status="final", stadium_id=f"{home}01",
        )
    )
    db.commit()


def test_multiple_plays_on_one_drive_count_as_a_single_trip():
    # SEA runs 3 plays on drive 1, all inside the red zone, scoring on the 3rd.
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", yardline_100=15, touchdown=False),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", yardline_100=8, touchdown=False),
        Play(play_key="p3", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", yardline_100=2, touchdown=True),
    ]
    drives = {"p1": 1, "p2": 1, "p3": 1}

    stats = team_red_zone_stats(plays, "SEA", drives)

    assert stats["red_zone_trips"] == 1  # not 3
    assert stats["red_zone_tds"] == 1
    assert stats["red_zone_td_rate"] == pytest.approx(1.0)


def test_two_separate_drives_count_as_two_trips_one_scoring():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", yardline_100=10, touchdown=True),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", yardline_100=15, touchdown=False),
    ]
    drives = {"p1": 1, "p2": 2}

    stats = team_red_zone_stats(plays, "SEA", drives)

    assert stats["red_zone_trips"] == 2
    assert stats["red_zone_tds"] == 1
    assert stats["red_zone_td_rate"] == pytest.approx(0.5)


def test_a_drive_that_never_enters_the_red_zone_is_not_a_trip():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", yardline_100=45, touchdown=False),
    ]
    drives = {"p1": 1}

    stats = team_red_zone_stats(plays, "SEA", drives)

    assert stats["red_zone_trips"] == 0
    assert stats["red_zone_td_rate"] is None


def test_plays_with_no_known_drive_are_excluded_not_guessed():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", yardline_100=10, touchdown=True),
    ]
    stats = team_red_zone_stats(plays, "SEA", drives={})  # no drive data at all

    assert stats["red_zone_trips"] == 0
    assert stats["red_zone_td_rate"] is None


def test_defense_allowed_side_scoped_to_defteam():
    # SEA's defense faces one NE red-zone drive that scores.
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="run", yardline_100=5, touchdown=True),
    ]
    drives = {"p1": 1}

    stats = team_red_zone_stats(plays, "SEA", drives)

    assert stats["red_zone_trips"] == 0  # SEA's own offense had no trips
    assert stats["red_zone_trips_allowed"] == 1
    assert stats["red_zone_tds_allowed"] == 1
    assert stats["red_zone_td_rate_allowed"] == pytest.approx(1.0)


def test_no_adjustment_when_team_has_no_prior_game_this_season(db):
    _seed_teams(db, "SEA", "NE")
    delta, note = red_zone_adjustment(db, "SEA", 2026, 1)
    assert delta == 0.0
    assert note is None


def test_league_red_zone_averages_skips_games_with_no_ingested_plays(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")

    averages = league_red_zone_averages(db, 2026, 2)

    assert averages == {}


def test_better_red_zone_team_gets_a_positive_capped_adjustment(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    # SEA: one red zone trip, scores. NE: one red zone trip, doesn't score.
    db.add(Play(play_key="2026_01_NE_SEA_1", game_id="2026_01_NE_SEA", season=2026, week=1,
                posteam="SEA", defteam="NE", play_type="run", yardline_100=5, touchdown=True))
    db.add(PlayDriveContext(play_key="2026_01_NE_SEA_1", game_id="2026_01_NE_SEA", season=2026, drive=1))
    db.add(Play(play_key="2026_01_NE_SEA_2", game_id="2026_01_NE_SEA", season=2026, week=1,
                posteam="NE", defteam="SEA", play_type="run", yardline_100=8, touchdown=False))
    db.add(PlayDriveContext(play_key="2026_01_NE_SEA_2", game_id="2026_01_NE_SEA", season=2026, drive=2))
    db.commit()

    sea_delta, sea_note = red_zone_adjustment(db, "SEA", 2026, 2)
    ne_delta, _ = red_zone_adjustment(db, "NE", 2026, 2)

    assert sea_delta == pytest.approx(settings.red_zone_max_adjustment)
    assert ne_delta == pytest.approx(-settings.red_zone_max_adjustment)
    assert "SEA" in sea_note
