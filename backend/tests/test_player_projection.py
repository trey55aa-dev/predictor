import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.player_projection import (
    _td_prob,
    opponent_allowed_by_position,
    player_scoring_rates,
    project_game_players,
    project_player,
)
from app.models import Base, Game, PlayerGameStat, TeamRosterMembership


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _stat(db, player_id, name, position, team, season, week, **kwargs):
    defaults = dict(
        carries=0, rushing_yards=0, rushing_tds=0, targets=0, receptions=0,
        receiving_yards=0, receiving_tds=0, pass_attempts=0, passing_yards=0, passing_tds=0,
        game_id=f"{season}_{week}_{team}",
    )
    defaults.update(kwargs)
    db.add(
        PlayerGameStat(
            player_id=player_id, player_name=name, position=position, team=team,
            season=season, week=week, **defaults,
        )
    )


def _game(db, game_id, home, away, season=2025, week=5, home_score=None, away_score=None):
    db.add(
        Game(
            game_id=game_id, season=season, week=week, game_type="REG", gameday="2025-10-01",
            home_team=home, away_team=away, home_score=home_score, away_score=away_score,
            status="final" if home_score is not None else "scheduled",
        )
    )


def test_td_prob_poisson_approximation():
    assert _td_prob(0.0) == 0.0
    assert 0 < _td_prob(1.0) < 1
    # higher rate -> higher probability, monotonic
    assert _td_prob(2.0) > _td_prob(0.5)


def test_player_scoring_rates_no_history(db):
    result = player_scoring_rates(db, "00-9999999", 2025, 1)
    assert result["sample_size"] == 0
    assert result["rushing_yards"] == 0.0


def test_player_scoring_rates_computes_average(db):
    _stat(db, "p1", "Test RB", "RB", "KC", 2025, 1, rushing_yards=80, rushing_tds=1)
    _stat(db, "p1", "Test RB", "RB", "KC", 2025, 2, rushing_yards=120, rushing_tds=2)
    db.commit()

    result = player_scoring_rates(db, "p1", 2025, 3)
    assert result["sample_size"] == 2
    assert abs(result["rushing_yards"] - 100) < 1e-9
    assert abs(result["rushing_td_rate"] - 1.5) < 1e-9


def test_project_player_returns_none_below_min_games(db):
    _stat(db, "p1", "Rookie", "WR", "KC", 2025, 1, receiving_yards=50)
    db.commit()
    result = project_player(db, "p1", "Rookie", "WR", "KC", "DEN", 2025, 2)
    assert result is None


def test_project_player_returns_projection_with_enough_history(db):
    _stat(db, "p1", "Vet WR", "WR", "KC", 2025, 1, receiving_yards=80, receiving_tds=1, targets=8)
    _stat(db, "p1", "Vet WR", "WR", "KC", 2025, 2, receiving_yards=60, receiving_tds=0, targets=7)
    db.commit()
    result = project_player(db, "p1", "Vet WR", "WR", "KC", "DEN", 2025, 3)
    assert result is not None
    assert result["projected_receiving_yards"] > 0
    assert 0 < result["anytime_td_prob"] < 1


def test_project_player_yardage_never_negative(db):
    # QB receiving yards is near-zero noise on both sides of the blend --
    # confirm the projection floors at 0 rather than showing e.g. -0.6.
    _stat(db, "qb1", "Test QB", "QB", "KC", 2025, 1, receiving_yards=-1, passing_yards=250)
    _stat(db, "qb1", "Test QB", "QB", "KC", 2025, 2, receiving_yards=0, passing_yards=230)
    db.commit()
    result = project_player(db, "qb1", "Test QB", "QB", "KC", "DEN", 2025, 3)
    assert result["projected_receiving_yards"] >= 0.0


def test_opponent_allowed_by_position_uses_recent_games_faced(db):
    _game(db, "g1", "DEN", "LAC", week=1, home_score=20, away_score=17)
    _stat(db, "opp_wr", "Opp WR", "WR", "LAC", 2025, 1, receiving_yards=100, game_id="g1")
    db.commit()

    result = opponent_allowed_by_position(db, "DEN", "WR", 2025, 2)
    assert result["sample_size"] == 1
    assert abs(result["receiving_yards"] - 100) < 1e-9


def test_project_game_players_splits_home_and_away(db):
    _game(db, "g1", "KC", "DEN", season=2025, week=5)
    _stat(db, "kc_rb", "KC Back", "RB", "KC", 2025, 3, rushing_yards=90, rushing_tds=1, carries=18)
    _stat(db, "kc_rb", "KC Back", "RB", "KC", 2025, 4, rushing_yards=70, rushing_tds=0, carries=15)
    _stat(db, "den_wr", "Den Wideout", "WR", "DEN", 2025, 3, receiving_yards=75, targets=9)
    _stat(db, "den_wr", "Den Wideout", "WR", "DEN", 2025, 4, receiving_yards=55, targets=7)
    # Roster membership is now the source of truth for "who's on this team",
    # not each player's last stat row -- see _current_roster's docstring.
    db.add(TeamRosterMembership(season=2025, team="KC", player_id="kc_rb", player_name="KC Back", position="RB"))
    db.add(TeamRosterMembership(season=2025, team="DEN", player_id="den_wr", player_name="Den Wideout", position="WR"))
    db.commit()

    game = db.query(Game).filter(Game.game_id == "g1").first()
    result = project_game_players(db, game)

    assert any(p["player_id"] == "kc_rb" for p in result["home"])
    assert any(p["player_id"] == "den_wr" for p in result["away"])
    assert not any(p["player_id"] == "den_wr" for p in result["home"])


def test_project_game_players_uses_real_roster_not_last_stat_row(db):
    """The bug this guards against: a player traded to a new team kept
    showing up under their OLD team here, because 'current team' was
    inferred from their last PlayerGameStat row, which only updates once
    they've actually played (and had stats ingested) for the new team.
    Confirmed live with Kenneth Walker III, traded to KC for 2026 but still
    shown as a Seahawk since his most recent stat row was a 2025 SEA game.
    """
    _game(db, "g1", "KC", "DEN", season=2026, week=1)
    # All of this player's real stat history is with SEA -- exactly the
    # traded-player shape.
    _stat(db, "rb1", "Traded Back", "RB", "SEA", 2025, 3, rushing_yards=90, rushing_tds=1, carries=18)
    _stat(db, "rb1", "Traded Back", "RB", "SEA", 2025, 4, rushing_yards=70, rushing_tds=0, carries=15)
    # But the real roster says they're on KC now.
    db.add(TeamRosterMembership(season=2026, team="KC", player_id="rb1", player_name="Traded Back", position="RB"))
    db.commit()

    game = db.query(Game).filter(Game.game_id == "g1").first()
    result = project_game_players(db, game)

    assert any(p["player_id"] == "rb1" for p in result["home"])  # KC is home
    assert not any(p["player_id"] == "rb1" for p in result["away"])
