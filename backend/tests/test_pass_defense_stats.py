import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.pass_defense_stats import (
    league_pass_defense_averages,
    pass_defense_adjustment,
    team_pass_defense_stats,
)
from app.models import Base, Game, Play, PlayAdvancedStat, PlayCoverageStat, Stadium, Team


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


def test_team_pass_defense_stats_basic_counts_and_rates():
    # SEA's defense (defteam=SEA) faces 3 targets from NE: 2 receptions, 1 incompletion.
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=15.0, receiver_player_id="00-1"),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=0.0, receiver_player_id="00-2"),  # incomplete
        Play(play_key="p3", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=35.0, receiver_player_id="00-3", pass_touchdown=True),
        # a sack -- must not count as a target
        Play(play_key="p4", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", sack=True),
        # SEA's own offensive play against NE's defense -- must not leak into SEA's allowed numbers
        Play(play_key="p5", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", yards_gained=50.0, receiver_player_id="00-4"),
    ]
    coverage = {
        "p1": PlayCoverageStat(play_key="p1", game_id="g", season=2026, complete_pass=True, yards_after_catch=5.0),
        "p2": PlayCoverageStat(play_key="p2", game_id="g", season=2026, complete_pass=False,
                                pass_defense_1_player_id="00-9"),
        "p3": PlayCoverageStat(play_key="p3", game_id="g", season=2026, complete_pass=True, yards_after_catch=20.0),
    }

    stats = team_pass_defense_stats(plays, "SEA", coverage=coverage)

    assert stats["targets_allowed"] == 3
    assert stats["receptions_allowed"] == 2
    assert stats["yards_allowed"] == pytest.approx(50.0)  # 15 + 35
    assert stats["touchdowns_allowed"] == 1
    assert stats["pass_break_ups"] == 1
    assert stats["catch_rate_allowed"] == pytest.approx(2 / 3)
    assert stats["td_rate_allowed"] == pytest.approx(1 / 3)
    assert stats["yards_per_target_allowed"] == pytest.approx(50.0 / 3)
    assert stats["yards_per_reception_allowed"] == pytest.approx(25.0)
    assert stats["yards_after_catch_allowed"] == pytest.approx(25.0)  # 5 + 20
    assert stats["yards_after_catch_allowed_per_reception"] == pytest.approx(12.5)


def test_explosive_reception_tiers():
    plays = [
        Play(play_key=f"p{n}", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=float(yards), receiver_player_id=f"00-{n}")
        for n, yards in enumerate([5, 12, 32, 41, 55], start=1)
    ]
    coverage = {p.play_key: PlayCoverageStat(play_key=p.play_key, game_id="g", season=2026, complete_pass=True)
                for p in plays}

    stats = team_pass_defense_stats(plays, "SEA", coverage=coverage)

    assert stats["receptions_allowed_10plus_yards"] == 4  # 12, 32, 41, 55
    assert stats["receptions_allowed_30plus_yards"] == 3  # 32, 41, 55
    assert stats["receptions_allowed_40plus_yards"] == 2  # 41, 55
    assert stats["receptions_allowed_50plus_yards"] == 1  # 55
    assert stats["receptions_allowed_10plus_yards_rate"] == pytest.approx(4 / 5)
    assert stats["explosive_receptions_allowed"] == 3  # standard 20+ threshold: 32, 41, 55
    assert stats["explosive_reception_rate_allowed"] == pytest.approx(3 / 5)


def test_passer_rating_allowed_matches_standard_formula():
    # 2/3 for 50 yards, 1 TD, 0 INT -- hand-computed against the real NFL formula.
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=20.0, receiver_player_id="00-1"),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=30.0, receiver_player_id="00-2", pass_touchdown=True),
        Play(play_key="p3", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=0.0, receiver_player_id="00-3"),
    ]
    coverage = {
        "p1": PlayCoverageStat(play_key="p1", game_id="g", season=2026, complete_pass=True),
        "p2": PlayCoverageStat(play_key="p2", game_id="g", season=2026, complete_pass=True),
        "p3": PlayCoverageStat(play_key="p3", game_id="g", season=2026, complete_pass=False),
    }

    stats = team_pass_defense_stats(plays, "SEA", coverage=coverage)

    comp, att, yds, td, intc = 2, 3, 50, 1, 0
    a = max(0.0, min(2.375, ((comp / att) - 0.3) * 5))
    b = max(0.0, min(2.375, ((yds / att) - 3) * 0.25))
    c = max(0.0, min(2.375, (td / att) * 20))
    d = max(0.0, min(2.375, 2.375 - (intc / att * 25)))
    expected = ((a + b + c + d) / 6) * 100

    assert stats["passer_rating_allowed"] == pytest.approx(expected)


def test_rates_are_none_not_zero_when_no_targets():
    stats = team_pass_defense_stats([], "SEA")
    assert stats["targets_allowed"] == 0
    assert stats["catch_rate_allowed"] is None
    assert stats["td_rate_allowed"] is None
    assert stats["yards_per_target_allowed"] is None
    assert stats["passer_rating_allowed"] is None
    assert stats["average_depth_of_target_allowed"] is None


def test_average_depth_of_target_allowed_from_advanced_stats():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=10.0, receiver_player_id="00-1"),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", yards_gained=5.0, receiver_player_id="00-2"),
    ]
    advanced = {
        "p1": PlayAdvancedStat(play_key="p1", game_id="g", season=2026, air_yards=8.0),
        "p2": PlayAdvancedStat(play_key="p2", game_id="g", season=2026, air_yards=2.0),
    }

    stats = team_pass_defense_stats(plays, "SEA", advanced=advanced)

    assert stats["average_depth_of_target_allowed"] == pytest.approx(5.0)


def test_no_adjustment_when_team_has_no_prior_game_this_season(db):
    _seed_teams(db, "SEA", "NE")
    delta, note = pass_defense_adjustment(db, "SEA", 2026, 1)
    assert delta == 0.0
    assert note is None


def test_better_pass_defense_gets_a_positive_capped_adjustment(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    # SEA's defense (facing NE's 2 targets): 1 short completion, 1 incompletion,
    # no TD -- strictly better than NE's defense below on every category.
    db.add(Play(play_key="2026_01_NE_SEA_1", game_id="2026_01_NE_SEA", season=2026, week=1,
                posteam="NE", defteam="SEA", play_type="pass", yards_gained=3.0, receiver_player_id="00-1"))
    db.add(PlayCoverageStat(play_key="2026_01_NE_SEA_1", game_id="2026_01_NE_SEA", season=2026, complete_pass=True))
    db.add(Play(play_key="2026_01_NE_SEA_2", game_id="2026_01_NE_SEA", season=2026, week=1,
                posteam="NE", defteam="SEA", play_type="pass", yards_gained=0.0, receiver_player_id="00-2"))
    db.add(PlayCoverageStat(play_key="2026_01_NE_SEA_2", game_id="2026_01_NE_SEA", season=2026, complete_pass=False))
    # NE's defense (facing SEA's 2 targets): both caught, one a 40-yard TD.
    db.add(Play(play_key="2026_01_NE_SEA_3", game_id="2026_01_NE_SEA", season=2026, week=1,
                posteam="SEA", defteam="NE", play_type="pass", yards_gained=40.0,
                receiver_player_id="00-3", pass_touchdown=True))
    db.add(PlayCoverageStat(play_key="2026_01_NE_SEA_3", game_id="2026_01_NE_SEA", season=2026, complete_pass=True))
    db.add(Play(play_key="2026_01_NE_SEA_4", game_id="2026_01_NE_SEA", season=2026, week=1,
                posteam="SEA", defteam="NE", play_type="pass", yards_gained=20.0, receiver_player_id="00-4"))
    db.add(PlayCoverageStat(play_key="2026_01_NE_SEA_4", game_id="2026_01_NE_SEA", season=2026, complete_pass=True))
    db.commit()

    sea_delta, sea_note = pass_defense_adjustment(db, "SEA", 2026, 2)
    ne_delta, _ = pass_defense_adjustment(db, "NE", 2026, 2)

    assert sea_delta == pytest.approx(settings.pass_defense_max_adjustment)
    assert ne_delta == pytest.approx(-settings.pass_defense_max_adjustment)
    assert "SEA" in sea_note


def test_league_pass_defense_averages_skips_games_with_no_ingested_plays(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")

    averages = league_pass_defense_averages(db, 2026, 2)

    assert averages == {}
