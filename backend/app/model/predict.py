"""Orchestrates the Elo + market + weather pipeline into a stored Prediction row."""

import datetime as dt

from sqlalchemy.orm import Session

from app.config import settings
from app.model.elo import expected_win_prob, latest_rating
from app.model.market import blend_line, blend_win_prob, market_home_win_prob
from app.model.recalibration import get_tuned_value
from app.model.scoring import matchup_expected_total
from app.model.weather_adjust import total_points_adjustment
from app.models import Game, OddsSnapshot, Prediction, WeatherSnapshot

ELO_POINTS_PER_ELO = 25.0  # rough conversion: 25 Elo points ~= 1 point of expected margin


def _latest_odds(db: Session, game_id: str) -> OddsSnapshot | None:
    return (
        db.query(OddsSnapshot)
        .filter(OddsSnapshot.game_id == game_id)
        .order_by(OddsSnapshot.fetched_at.desc())
        .first()
    )


def _latest_weather(db: Session, game_id: str) -> WeatherSnapshot | None:
    return (
        db.query(WeatherSnapshot)
        .filter(WeatherSnapshot.game_id == game_id)
        .order_by(WeatherSnapshot.fetched_at.desc())
        .first()
    )


def predict_game(db: Session, game: Game) -> Prediction:
    home_elo = latest_rating(db, game.home_team, game.season, game.week)
    away_elo = latest_rating(db, game.away_team, game.season, game.week)

    home_elo_adj = home_elo + settings.elo_home_field_advantage
    elo_home_prob = expected_win_prob(home_elo_adj, away_elo)
    elo_margin = (home_elo_adj - away_elo) / ELO_POINTS_PER_ELO

    odds = _latest_odds(db, game.game_id)
    weather = _latest_weather(db, game.game_id)

    market_prob = None
    market_spread = None  # home spread, negative = home favored
    market_total = None
    if odds is not None and odds.home_moneyline is not None and odds.away_moneyline is not None:
        market_prob = market_home_win_prob(odds.home_moneyline, odds.away_moneyline)
        market_spread = odds.spread_line
        market_total = odds.total_line

    tuned_blend_weight = get_tuned_value(db, "market_blend_weight", settings.market_blend_weight)
    home_win_prob = blend_win_prob(elo_home_prob, market_prob, weight=tuned_blend_weight)

    market_margin = -market_spread if market_spread is not None else None  # spread is from home's perspective (negative = favored)
    predicted_margin = blend_line(elo_margin, market_margin, weight=tuned_blend_weight)

    scoring_expected_total = matchup_expected_total(db, game.home_team, game.away_team, game.season, game.week)
    predicted_total = blend_line(scoring_expected_total, market_total, weight=tuned_blend_weight)

    weather_adjustment, weather_note = total_points_adjustment(weather)
    predicted_total = max(predicted_total - weather_adjustment, 20.0)

    tuned_margin_std = get_tuned_value(db, "margin_std_default", settings.margin_std_default)
    tuned_total_std = get_tuned_value(db, "total_std_default", settings.total_std_default)

    predicted_home_score = (predicted_total + predicted_margin) / 2
    predicted_away_score = (predicted_total - predicted_margin) / 2

    prediction = Prediction(
        game_id=game.game_id,
        model_version=settings.model_version,
        created_at=dt.datetime.utcnow(),
        odds_snapshot_id=odds.id if odds else None,
        weather_snapshot_id=weather.id if weather else None,
        home_elo=home_elo,
        away_elo=away_elo,
        home_win_prob=home_win_prob,
        elo_win_prob=elo_home_prob,
        market_win_prob=market_prob,
        predicted_home_score=predicted_home_score,
        predicted_away_score=predicted_away_score,
        predicted_margin=predicted_margin,
        predicted_total=predicted_total,
        margin_range_low=predicted_margin - tuned_margin_std,
        margin_range_high=predicted_margin + tuned_margin_std,
        total_range_low=predicted_total - tuned_total_std,
        total_range_high=predicted_total + tuned_total_std,
        weather_note=weather_note,
    )
    db.add(prediction)
    return prediction


def predict_week(db: Session, season: int, week: int) -> list[Prediction]:
    games = (
        db.query(Game)
        .filter(Game.season == season, Game.week == week, Game.game_type == "REG")
        .all()
    )
    predictions = [predict_game(db, game) for game in games]
    db.commit()
    return predictions
