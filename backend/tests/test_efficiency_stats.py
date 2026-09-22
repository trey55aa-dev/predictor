import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.efficiency_stats import (
    efficiency_adjustment,
    league_efficiency_averages,
    team_efficiency_stats,
)
from app.models import Base, Game, Play, PlayAdvancedStat, Stadium, Team


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


def _play(
    db, game_id, posteam, defteam, play_type, play_id, epa=None, sack=False,
    was_pressure=None, receiver_player_id=None,
):
    db.add(
        Play(
            play_key=f"{game_id}_{play_id}", game_id=game_id, season=2026, week=1,
            posteam=posteam, defteam=defteam, play_type=play_type,
            epa=epa, sack=sack, was_pressure=was_pressure, receiver_player_id=receiver_player_id,
        )
    )


def _seed_game(db, week, home, away, home_score, away_score, game_id):
    db.add(
        Game(
            game_id=game_id, season=2026, week=week, game_type="REG", gameday="2026-09-09",
            home_team=home, away_team=away, home_score=home_score, away_score=away_score,
            status="final", stadium_id=f"{home}01",
        )
    )
    db.commit()


def test_team_efficiency_stats_splits_dropbacks_rushes_and_targets():
    plays = [
        # a clean, completed dropback that's also a target
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", epa=1.5, was_pressure=False, receiver_player_id="00-1"),
        # a sacked dropback -- counts toward epa_per_dropback and pressure rate,
        # but has no receiver so it's excluded from epa_per_target
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", epa=-1.8, sack=True, was_pressure=True, receiver_player_id=None),
        # a rush attempt
        Play(play_key="p3", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", epa=0.2),
        # the opponent's own play -- must not leak into SEA's numbers
        Play(play_key="p4", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", epa=5.0, was_pressure=False, receiver_player_id="00-2"),
    ]

    stats = team_efficiency_stats(plays, "SEA")

    assert stats["epa_per_dropback"] == pytest.approx((1.5 + -1.8) / 2)
    assert stats["epa_per_rush"] == pytest.approx(0.2)
    assert stats["epa_per_target"] == pytest.approx(1.5)  # only the non-sack, targeted dropback
    assert stats["pressure_rate_allowed"] == pytest.approx(0.5)  # 1 of 2 dropbacks pressured
    assert stats["cpoe"] is None  # no `advanced` mapping passed in


def test_team_efficiency_stats_computes_cpoe_from_advanced_data():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", epa=1.0, receiver_player_id="00-1"),
        # a sacked dropback -- still counts toward cpoe if it has an advanced row
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", epa=-1.0, sack=True),
        # no matching advanced row at all -- excluded, not treated as 0
        Play(play_key="p3", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", epa=0.5, receiver_player_id="00-2"),
    ]
    advanced = {
        "p1": PlayAdvancedStat(play_key="p1", game_id="g", season=2026, cpoe=5.0),
        "p2": PlayAdvancedStat(play_key="p2", game_id="g", season=2026, cpoe=None),
    }

    stats = team_efficiency_stats(plays, "SEA", advanced)

    assert stats["cpoe"] == pytest.approx(5.0)  # only p1 has a non-null cpoe


def test_team_efficiency_stats_excludes_missing_participation_data_from_pressure_rate():
    """A dropback with was_pressure=None (participation coverage gap) must
    not be silently counted as a clean, unpressured dropback."""
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", epa=1.0, was_pressure=None, receiver_player_id="00-1"),
    ]

    stats = team_efficiency_stats(plays, "SEA")

    assert stats["pressure_rate_allowed"] is None


def test_scramble_excluded_from_epa_per_rush_but_included_in_box_score_rush_stats():
    """A scramble is recorded as play_type == 'run' in nflverse's own
    convention. epa_per_rush should exclude it (standard analytics
    convention: scrambles have a very different EPA distribution than
    designed runs), but rush_yards/rush_touchdowns -- real box-score
    counting stats -- must still include it, matching how the NFL's own
    box score credits a scramble as a rush attempt."""
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", epa=0.5, yards_gained=4),  # designed run
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", epa=3.0, yards_gained=25, rush_touchdown=True),  # scramble TD
    ]
    scrambles = {"p2"}

    stats = team_efficiency_stats(plays, "SEA", scrambles=scrambles)

    assert stats["epa_per_rush"] == pytest.approx(0.5)  # only the designed run
    assert stats["rush_yards"] == 29  # both plays
    assert stats["rush_touchdowns"] == 1  # the scramble TD still counts


def test_scramble_rate_uses_true_dropback_denominator():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", receiver_player_id="00-1"),  # real dropback
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run"),  # scramble
        Play(play_key="p3", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run"),  # designed run -- not a dropback at all
    ]
    scrambles = {"p2"}

    stats = team_efficiency_stats(plays, "SEA", scrambles=scrambles)

    # denominator is 1 dropback + 1 scramble = 2, NOT 3 (designed run excluded)
    assert stats["scramble_rate"] == pytest.approx(0.5)


def test_pass_touchdowns_interception_rate_and_average_depth_of_target():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", receiver_player_id="00-1", pass_touchdown=True),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", receiver_player_id="00-2", interception=True),
    ]
    advanced = {
        "p1": PlayAdvancedStat(play_key="p1", game_id="g", season=2026, air_yards=15.0),
        "p2": PlayAdvancedStat(play_key="p2", game_id="g", season=2026, air_yards=5.0),
    }

    stats = team_efficiency_stats(plays, "SEA", advanced=advanced)

    assert stats["pass_touchdowns"] == 1
    assert stats["interception_rate"] == pytest.approx(0.5)
    assert stats["average_depth_of_target"] == pytest.approx(10.0)


def test_epa_per_play_and_success_rate_span_dropbacks_and_all_runs():
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", receiver_player_id="00-1", epa=2.0, success=True),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="run", epa=-2.0, success=False),  # scramble, still a "play"
    ]
    scrambles = {"p2"}

    stats = team_efficiency_stats(plays, "SEA", scrambles=scrambles)

    assert stats["epa_per_play"] == pytest.approx(0.0)  # (2.0 + -2.0) / 2
    assert stats["total_epa"] == pytest.approx(0.0)
    assert stats["success_rate"] == pytest.approx(0.5)


def test_team_efficiency_stats_returns_none_for_categories_with_no_plays():
    stats = team_efficiency_stats([], "SEA")

    assert stats == {
        "epa_per_dropback": None,
        "epa_per_rush": None,
        "epa_per_target": None,
        "cpoe": None,
        "pressure_rate_allowed": None,
        "epa_per_play": None,
        "total_epa": None,
        "success_rate": None,
        "pass_yards": 0,
        "pass_touchdowns": 0,
        "rush_yards": 0,
        "rush_touchdowns": 0,
        "average_depth_of_target": None,
        "interception_rate": None,
        "scramble_rate": None,
    }


def test_no_adjustment_when_team_has_no_prior_game_this_season(db):
    _seed_teams(db, "SEA", "NE")
    delta, note = efficiency_adjustment(db, "SEA", 2026, 1)
    assert delta == 0.0
    assert note is None


def test_adjustment_never_looks_at_a_future_or_same_week_game(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    _play(db, "2026_01_NE_SEA", "SEA", "NE", "pass", 1, epa=2.0, was_pressure=False, receiver_player_id="00-1")
    _play(db, "2026_01_NE_SEA", "NE", "SEA", "pass", 2, epa=-1.0, was_pressure=True, receiver_player_id="00-2")
    db.commit()

    delta, note = efficiency_adjustment(db, "SEA", 2026, 1)

    assert delta == 0.0
    assert note is None


def test_more_efficient_team_gets_a_positive_capped_adjustment(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    # SEA: highly efficient, never pressured. NE: inefficient, pressured every dropback.
    _play(db, "2026_01_NE_SEA", "SEA", "NE", "pass", 1, epa=2.5, was_pressure=False, receiver_player_id="00-1")
    _play(db, "2026_01_NE_SEA", "SEA", "NE", "run", 2, epa=1.0)
    _play(db, "2026_01_NE_SEA", "NE", "SEA", "pass", 3, epa=-2.5, was_pressure=True, receiver_player_id="00-2")
    _play(db, "2026_01_NE_SEA", "NE", "SEA", "run", 4, epa=-1.0)
    db.commit()

    sea_delta, sea_note = efficiency_adjustment(db, "SEA", 2026, 2)
    ne_delta, _ = efficiency_adjustment(db, "NE", 2026, 2)

    assert sea_delta == pytest.approx(settings.efficiency_max_adjustment)
    assert ne_delta == pytest.approx(-settings.efficiency_max_adjustment)
    assert "SEA ranks" in sea_note


def test_league_efficiency_averages_skips_games_with_no_ingested_plays(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")

    averages = league_efficiency_averages(db, 2026, 2)

    assert averages == {}


def test_league_efficiency_averages_omits_categories_with_no_qualifying_plays(db):
    """A game with rushes but zero pass plays must not give epa_per_dropback
    a guessed value for either team."""
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    _play(db, "2026_01_NE_SEA", "SEA", "NE", "run", 1, epa=1.0)
    db.commit()

    averages = league_efficiency_averages(db, 2026, 2)

    assert "epa_per_dropback" not in averages["SEA"]
    assert averages["SEA"]["epa_per_rush"] == pytest.approx(1.0)


def test_league_efficiency_averages_picks_up_cpoe_from_advanced_stats_table(db):
    _seed_teams(db, "SEA", "NE")
    _seed_game(db, 1, "SEA", "NE", 13, 10, "2026_01_NE_SEA")
    _play(db, "2026_01_NE_SEA", "SEA", "NE", "pass", 1, epa=1.0, receiver_player_id="00-1")
    db.add(PlayAdvancedStat(play_key="2026_01_NE_SEA_1", game_id="2026_01_NE_SEA", season=2026, cpoe=4.0))
    db.commit()

    averages = league_efficiency_averages(db, 2026, 2)

    assert averages["SEA"]["cpoe"] == pytest.approx(4.0)
