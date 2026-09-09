"""Evaluates a user's own bet slip -- legs they picked themselves, not ones
the app generated -- against the model's real probability for each one.

Reuses exactly the same underlying data (Prediction, PlayerProjection,
SimPlayerProjection) the app's own auto-generated parlays use, so a leg
checked here gets the same number the site would show if it had picked
that leg itself -- not a second, inconsistent opinion.

Player-yardage legs are the one approximation here, and it's disclosed in
the response, not hidden: SimPlayerProjection stores only a mean and a
10th-90th percentile band (the full simulated distribution isn't persisted
-- storing every simulated game's yardage for every player would be a lot
of storage for a benefit that shows up only in this one feature). A normal
distribution is fit from that mean and band to estimate P(over/under a
line), which is an approximation of the real (mildly right-skewed)
simulated distribution, not the distribution itself.
"""

import math

from sqlalchemy.orm import Session

from app.model.market import market_home_win_prob
from app.model.odds_math import american_to_decimal, combined_decimal_payout, combined_probability
from app.models import Game, OddsSnapshot, PlayerProjection, Prediction, SimPlayerProjection

# z-score for the 90th percentile of a standard normal distribution --
# converts a stored p10-p90 band into an implied standard deviation.
Z_90 = 1.2816


class LegEvaluationError(ValueError):
    """A leg couldn't be evaluated -- missing game/player/data. Carries a
    plain-English reason so the API can report exactly why, rather than a
    generic failure."""


def _latest_prediction(db: Session, game_id: str) -> Prediction | None:
    return (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(Prediction.created_at.desc())
        .first()
    )


def _normal_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def evaluate_game_winner_leg(db: Session, game_id: str, team: str) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise LegEvaluationError(f"Game {game_id} not found.")
    if team not in (game.home_team, game.away_team):
        raise LegEvaluationError(f"{team} isn't playing in {game.away_team} @ {game.home_team}.")

    prediction = _latest_prediction(db, game_id)
    if prediction is None or prediction.home_win_prob is None:
        raise LegEvaluationError("No prediction available for this game yet.")

    picked_home = team == game.home_team
    opponent = game.away_team if picked_home else game.home_team
    model_prob = prediction.home_win_prob if picked_home else 1 - prediction.home_win_prob
    elo_prob = None
    if prediction.elo_win_prob is not None:
        elo_prob = prediction.elo_win_prob if picked_home else 1 - prediction.elo_win_prob

    market_prob = None
    odds = db.get(OddsSnapshot, prediction.odds_snapshot_id) if prediction.odds_snapshot_id else None
    if odds and odds.home_moneyline is not None and odds.away_moneyline is not None:
        home_fair = market_home_win_prob(odds.home_moneyline, odds.away_moneyline)
        market_prob = home_fair if picked_home else 1 - home_fair

    return {
        "leg_type": "game_winner",
        "description": f"{team} to beat {opponent}",
        "model_prob": model_prob,
        "elo_prob": elo_prob,
        "market_prob": market_prob,
        "approximated": False,
    }


def evaluate_anytime_td_leg(db: Session, game_id: str, player_id: str) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise LegEvaluationError(f"Game {game_id} not found.")

    proj = (
        db.query(PlayerProjection)
        .filter(PlayerProjection.game_id == game_id, PlayerProjection.player_id == player_id)
        .order_by(PlayerProjection.created_at.desc())
        .first()
    )
    if proj is None:
        raise LegEvaluationError("No projection available for this player in this game.")

    return {
        "leg_type": "anytime_td",
        "description": f"{proj.player_name} anytime TD",
        "model_prob": proj.anytime_td_prob,
        "elo_prob": None,
        "market_prob": None,
        "approximated": False,
    }


def evaluate_player_yards_leg(
    db: Session, game_id: str, player_id: str, stat: str, side: str, line: float
) -> dict:
    if stat not in ("rushing", "receiving"):
        raise LegEvaluationError(f"Unknown stat '{stat}' -- expected 'rushing' or 'receiving'.")
    if side not in ("over", "under"):
        raise LegEvaluationError(f"Unknown side '{side}' -- expected 'over' or 'under'.")

    row = (
        db.query(SimPlayerProjection)
        .filter(SimPlayerProjection.game_id == game_id, SimPlayerProjection.player_id == player_id)
        .order_by(SimPlayerProjection.created_at.desc())
        .first()
    )
    if row is None:
        raise LegEvaluationError("No simulated projection available for this player in this game.")

    mean = row.rushing_mean_yards if stat == "rushing" else row.receiving_mean_yards
    p10 = row.rushing_p10 if stat == "rushing" else row.receiving_p10
    p90 = row.rushing_p90 if stat == "rushing" else row.receiving_p90
    if mean is None or p10 is None or p90 is None:
        raise LegEvaluationError(f"No {stat} projection for {row.player_name} in this game.")

    std = max((p90 - p10) / (2 * Z_90), 1e-6)
    z = (line - mean) / std
    prob_over = 1 - _normal_cdf(z)
    model_prob = prob_over if side == "over" else 1 - prob_over

    return {
        "leg_type": "player_yards",
        "description": f"{row.player_name} {side} {line:g} {stat} yards",
        "model_prob": max(0.0, min(1.0, model_prob)),
        "elo_prob": None,
        "market_prob": None,
        "approximated": True,
        "sim_mean": mean,
    }


EVALUATORS = {
    "game_winner": lambda db, leg: evaluate_game_winner_leg(db, leg["game_id"], leg["team"]),
    "anytime_td": lambda db, leg: evaluate_anytime_td_leg(db, leg["game_id"], leg["player_id"]),
    "player_yards": lambda db, leg: evaluate_player_yards_leg(
        db, leg["game_id"], leg["player_id"], leg["stat"], leg["side"], float(leg["line"])
    ),
}


def evaluate_slip(db: Session, legs: list[dict]) -> dict:
    """Evaluates every leg in a user-submitted slip. A leg that fails to
    evaluate (bad id, no data yet) is reported inline with its error rather
    than failing the whole request -- the rest of the slip still gets
    evaluated."""
    results = []
    for leg in legs:
        leg_type = leg.get("leg_type")
        evaluator = EVALUATORS.get(leg_type)
        american_odds = leg.get("american_odds")
        try:
            if evaluator is None:
                raise LegEvaluationError(f"Unknown leg_type '{leg_type}'.")
            result = evaluator(db, leg)
            result["american_odds"] = american_odds
            result["decimal_odds"] = american_to_decimal(american_odds) if american_odds else None
            result["error"] = None
        except LegEvaluationError as e:
            result = {
                "leg_type": leg_type,
                "description": None,
                "model_prob": None,
                "elo_prob": None,
                "market_prob": None,
                "approximated": None,
                "american_odds": american_odds,
                "decimal_odds": None,
                "error": str(e),
            }
        results.append(result)

    valid = [r for r in results if r["error"] is None]
    combined_prob = combined_probability([r["model_prob"] for r in valid]) if valid else None
    priced = [r for r in valid if r["decimal_odds"] is not None]
    combined_payout = (
        combined_decimal_payout([r["decimal_odds"] for r in priced])
        if priced and len(priced) == len(valid) and valid
        else None
    )

    return {
        "legs": results,
        "combined_probability": combined_prob,
        "combined_decimal_payout": combined_payout,
        "caveat": (
            f"Model-based estimate, not a guarantee -- these are {len(valid)} independent legs; "
            "parlays compound risk even when each leg looks favorable on its own. Player-yardage "
            "legs are approximated from a normal distribution fit to the simulator's mean and "
            "10th-90th percentile range, not the real simulated distribution itself."
            if any(r.get("approximated") for r in valid)
            else f"Model-based estimate, not a guarantee -- these are {len(valid)} independent legs; "
            "parlays compound risk even when each leg looks favorable on its own."
        ),
    }
