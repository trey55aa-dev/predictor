import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.slip_checker import (
    LegEvaluationError,
    evaluate_anytime_td_leg,
    evaluate_game_winner_leg,
    evaluate_player_yards_leg,
    evaluate_slip,
)
from app.models import Base, Game, OddsSnapshot, PlayerProjection, Prediction, SimPlayerProjection


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(db, game_id="2024_01_KC_BUF", home="BUF", away="KC"):
    g = Game(
        game_id=game_id, season=2024, week=1, game_type="REG", gameday="2024-09-08",
        home_team=home, away_team=away, status="scheduled",
    )
    db.add(g)
    return g


def _prediction(db, game_id, home_win_prob, elo_win_prob=None, odds_snapshot_id=None):
    p = Prediction(
        game_id=game_id, model_version="v1", created_at=dt.datetime.utcnow(),
        home_elo=1550, away_elo=1500, home_win_prob=home_win_prob, elo_win_prob=elo_win_prob,
        predicted_home_score=24, predicted_away_score=20, predicted_margin=4, predicted_total=44,
        margin_range_low=-6, margin_range_high=14, total_range_low=34, total_range_high=54,
        odds_snapshot_id=odds_snapshot_id,
    )
    db.add(p)
    return p


def test_game_winner_leg_for_the_home_team(db):
    _game(db)
    _prediction(db, "2024_01_KC_BUF", home_win_prob=0.65)
    db.commit()

    result = evaluate_game_winner_leg(db, "2024_01_KC_BUF", "BUF")

    assert result["model_prob"] == pytest.approx(0.65)
    assert result["leg_type"] == "game_winner"


def test_game_winner_leg_for_the_away_team_flips_the_probability(db):
    _game(db)
    _prediction(db, "2024_01_KC_BUF", home_win_prob=0.65)
    db.commit()

    result = evaluate_game_winner_leg(db, "2024_01_KC_BUF", "KC")

    assert result["model_prob"] == pytest.approx(0.35)


def test_game_winner_leg_rejects_a_team_not_in_the_game(db):
    _game(db)
    _prediction(db, "2024_01_KC_BUF", home_win_prob=0.65)
    db.commit()

    with pytest.raises(LegEvaluationError, match="isn't playing"):
        evaluate_game_winner_leg(db, "2024_01_KC_BUF", "DAL")


def test_game_winner_leg_reports_a_missing_game(db):
    with pytest.raises(LegEvaluationError, match="not found"):
        evaluate_game_winner_leg(db, "nonexistent_game", "BUF")


def test_game_winner_leg_uses_market_prob_when_odds_exist(db):
    _game(db)
    odds = OddsSnapshot(
        game_id="2024_01_KC_BUF", fetched_at=dt.datetime.utcnow(), bookmaker="consensus",
        home_moneyline=-150, away_moneyline=130,
    )
    db.add(odds)
    db.flush()
    _prediction(db, "2024_01_KC_BUF", home_win_prob=0.65, odds_snapshot_id=odds.id)
    db.commit()

    result = evaluate_game_winner_leg(db, "2024_01_KC_BUF", "BUF")

    assert result["market_prob"] is not None
    assert 0.5 < result["market_prob"] < 0.7  # -150 implies a bit under 60%, de-vigged


def test_anytime_td_leg_reads_the_stored_projection(db):
    _game(db)
    db.add(
        PlayerProjection(
            game_id="2024_01_KC_BUF", player_id="P1", player_name="Test Back", position="RB",
            team="BUF", opponent="KC", season=2024, week=1, model_version="v1",
            created_at=dt.datetime.utcnow(), projected_rushing_yards=60, projected_receiving_yards=10,
            projected_passing_yards=0, rushing_td_prob=0.3, receiving_td_prob=0.05, passing_td_prob=0,
            anytime_td_prob=0.34,
        )
    )
    db.commit()

    result = evaluate_anytime_td_leg(db, "2024_01_KC_BUF", "P1")

    assert result["model_prob"] == pytest.approx(0.34)
    assert "Test Back" in result["description"]


def test_anytime_td_leg_reports_a_missing_player(db):
    _game(db)
    db.commit()
    with pytest.raises(LegEvaluationError, match="No projection"):
        evaluate_anytime_td_leg(db, "2024_01_KC_BUF", "nobody")


def _sim_projection(db, **overrides):
    defaults = dict(
        game_id="2024_01_KC_BUF", player_id="P1", player_name="Test Back", team="BUF",
        season=2024, week=1, model_version="player-sim-v1", n_sims=1000, created_at=dt.datetime.utcnow(),
        rushing_mean_yards=60.0, rushing_p10=30.0, rushing_p90=90.0, rushing_td_prob=0.3,
        receiving_mean_yards=None, receiving_p10=None, receiving_p90=None, receiving_td_prob=None,
        anytime_td_probability=0.3,
    )
    defaults.update(overrides)
    db.add(SimPlayerProjection(**defaults))


def test_player_yards_leg_at_the_mean_is_close_to_a_coin_flip(db):
    _game(db)
    _sim_projection(db)
    db.commit()

    over = evaluate_player_yards_leg(db, "2024_01_KC_BUF", "P1", "rushing", "over", 60.0)

    assert over["model_prob"] == pytest.approx(0.5, abs=0.01)
    assert over["approximated"] is True


def test_player_yards_leg_over_and_under_sum_to_one(db):
    _game(db)
    _sim_projection(db)
    db.commit()

    over = evaluate_player_yards_leg(db, "2024_01_KC_BUF", "P1", "rushing", "over", 75.0)
    under = evaluate_player_yards_leg(db, "2024_01_KC_BUF", "P1", "rushing", "under", 75.0)

    assert over["model_prob"] + under["model_prob"] == pytest.approx(1.0)


def test_player_yards_leg_far_below_mean_is_a_near_certain_over(db):
    _game(db)
    _sim_projection(db)  # mean 60, p10 30, p90 90
    db.commit()

    result = evaluate_player_yards_leg(db, "2024_01_KC_BUF", "P1", "rushing", "over", 5.0)

    assert result["model_prob"] > 0.95


def test_player_yards_leg_rejects_unknown_stat_and_side(db):
    _game(db)
    _sim_projection(db)
    db.commit()

    with pytest.raises(LegEvaluationError, match="Unknown stat"):
        evaluate_player_yards_leg(db, "2024_01_KC_BUF", "P1", "kicking", "over", 10)
    with pytest.raises(LegEvaluationError, match="Unknown side"):
        evaluate_player_yards_leg(db, "2024_01_KC_BUF", "P1", "rushing", "sideways", 10)


def test_player_yards_leg_reports_missing_stat_for_this_player(db):
    _game(db)
    _sim_projection(db)  # only rushing populated
    db.commit()

    with pytest.raises(LegEvaluationError, match="No receiving projection"):
        evaluate_player_yards_leg(db, "2024_01_KC_BUF", "P1", "receiving", "over", 10)


def test_evaluate_slip_combines_independent_leg_probabilities(db):
    _game(db)
    _prediction(db, "2024_01_KC_BUF", home_win_prob=0.5)
    _sim_projection(db)
    db.commit()

    result = evaluate_slip(
        db,
        [
            {"leg_type": "game_winner", "game_id": "2024_01_KC_BUF", "team": "BUF"},
            {"leg_type": "player_yards", "game_id": "2024_01_KC_BUF", "player_id": "P1",
             "stat": "rushing", "side": "over", "line": 60.0},
        ],
    )

    assert result["combined_probability"] == pytest.approx(0.5 * 0.5, abs=0.01)


def test_evaluate_slip_keeps_a_bad_leg_from_failing_the_whole_request(db):
    _game(db)
    _prediction(db, "2024_01_KC_BUF", home_win_prob=0.5)
    db.commit()

    result = evaluate_slip(
        db,
        [
            {"leg_type": "game_winner", "game_id": "2024_01_KC_BUF", "team": "BUF"},
            {"leg_type": "game_winner", "game_id": "does_not_exist", "team": "XYZ"},
        ],
    )

    assert result["legs"][0]["error"] is None
    assert result["legs"][1]["error"] is not None
    # Combined probability should reflect only the one valid leg.
    assert result["combined_probability"] == pytest.approx(0.5)


def test_evaluate_slip_computes_payout_only_when_every_leg_has_odds(db):
    _game(db)
    _prediction(db, "2024_01_KC_BUF", home_win_prob=0.5)
    db.commit()

    with_odds = evaluate_slip(
        db, [{"leg_type": "game_winner", "game_id": "2024_01_KC_BUF", "team": "BUF", "american_odds": -110}]
    )
    without_odds = evaluate_slip(
        db, [{"leg_type": "game_winner", "game_id": "2024_01_KC_BUF", "team": "BUF"}]
    )

    assert with_odds["combined_decimal_payout"] is not None
    assert without_odds["combined_decimal_payout"] is None
