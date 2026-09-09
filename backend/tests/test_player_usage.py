import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.player_usage import passer_shares, rushing_shares
from app.models import Base, Play, TeamRosterMembership


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _run_play(db, game_id, season, week, posteam, rusher_id, yards=4.0):
    db.add(
        Play(
            play_key=f"{game_id}_{rusher_id}_{week}",
            game_id=game_id,
            season=season,
            week=week,
            posteam=posteam,
            defteam="OPP",
            play_type="run",
            down=1,
            ydstogo=10,
            yardline_100=50,
            yards_gained=yards,
            touchdown=False,
            scoring_play=False,
            interception=False,
            fumble_lost=False,
            sack=False,
            rusher_player_id=rusher_id,
        )
    )


def _pass_play(db, game_id, season, week, posteam, passer_id, receiver_id, yards=8.0):
    db.add(
        Play(
            play_key=f"{game_id}_{passer_id}_{receiver_id}_{week}",
            game_id=game_id,
            season=season,
            week=week,
            posteam=posteam,
            defteam="OPP",
            play_type="pass",
            down=1,
            ydstogo=10,
            yardline_100=50,
            yards_gained=yards,
            touchdown=False,
            scoring_play=False,
            interception=False,
            fumble_lost=False,
            sack=False,
            passer_player_id=passer_id,
            receiver_player_id=receiver_id,
        )
    )


def _roster(db, season, team, player_id, position):
    db.add(TeamRosterMembership(season=season, team=team, player_id=player_id, player_name=player_id, position=position))


def test_rushing_shares_follows_a_player_after_a_team_change(db):
    """A free-agent signing's role should carry over to the new team, not
    reset to zero just because their historical plays are tagged with their
    old team -- the core fix for the Barkley/Henry/Jacobs class of bug."""
    # RB1 ran the ball for OLD_TEAM in the season before the trade.
    for week in range(1, 4):
        _run_play(db, f"2023_{week:02d}_OLD_TEAM_X", 2023, week, "OLD_TEAM", "RB1")
    # RB1 is now on NEW_TEAM's roster for 2024 -- no NEW_TEAM play history exists yet.
    _roster(db, 2024, "NEW_TEAM", "RB1", "RB")
    db.commit()

    shares = rushing_shares(db, "NEW_TEAM", before_season=2024, before_week=1)

    assert "RB1" in shares
    assert shares["RB1"] == pytest.approx(1.0)


def test_rushing_shares_excludes_a_departed_player(db):
    """A player who left the team should not keep soaking up simulated
    carries just because their old plays are still in the historical log."""
    for week in range(1, 4):
        _run_play(db, f"2023_{week:02d}_TEAM_X", 2023, week, "TEAM", "OLD_RB")
    # OLD_RB is NOT on TEAM's 2024 roster -- only NEW_RB is.
    _roster(db, 2024, "TEAM", "NEW_RB", "RB")
    db.commit()

    shares = rushing_shares(db, "TEAM", before_season=2024, before_week=1)

    assert "OLD_RB" not in shares


def test_passer_shares_restricted_to_roster_qb_position(db):
    """A non-QB who threw one career trick-play pass should never become a
    team's only passer candidate just because a real QB isn't on the roster
    this season (e.g. a backup traded elsewhere)."""
    _pass_play(db, "2023_01_TEAM_X", 2023, 1, "TEAM", "DEFENSIVE_LINEMAN", "WR1")
    # DEFENSIVE_LINEMAN is on the roster, but not as a QB.
    _roster(db, 2024, "TEAM", "DEFENSIVE_LINEMAN", "DE")
    db.commit()

    shares = passer_shares(db, "TEAM", before_season=2024, before_week=1)

    assert shares == {}


def test_passer_shares_accepts_a_real_qb_on_the_roster(db):
    for week in range(1, 4):
        _pass_play(db, f"2023_{week:02d}_TEAM_X", 2023, week, "TEAM", "QB1", "WR1")
    _roster(db, 2024, "TEAM", "QB1", "QB")
    db.commit()

    shares = passer_shares(db, "TEAM", before_season=2024, before_week=1)

    assert shares == {"QB1": pytest.approx(1.0)}


def test_shares_are_empty_without_any_roster_data(db):
    """No roster row for this team/season at all -- correct behaviour is an
    empty pool (no attribution), not a crash and not falling back to
    unfiltered historical plays."""
    _run_play(db, "2023_01_TEAM_X", 2023, 1, "TEAM", "RB1")
    db.commit()

    shares = rushing_shares(db, "TEAM", before_season=2024, before_week=1)

    assert shares == {}
