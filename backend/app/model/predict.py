"""Orchestrates the Elo + market + weather pipeline into a stored Prediction row."""

import datetime as dt

from sqlalchemy.orm import Session

from app.config import settings
from app.model.elo import expected_win_prob, latest_rating
from app.model.market import blend_line, blend_win_prob, market_home_win_prob
from app.model.recalibration import get_tuned_value
from app.model.recent_form import recent_form_adjustment
from app.model.scoring import matchup_expected_total
from app.model.stat_rankings import stat_rank_adjustment
from app.model.venue import is_true_home_game
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

    home_field_bonus = settings.elo_home_field_advantage if is_true_home_game(db, game) else 0.0
    home_elo_adj = home_elo + home_field_bonus
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

    # Small, capped nudge from each team's most recent game's keys-to-victory
    # record (turnover margin, rushing/passing yards, 3rd-down%) -- a signal
    # distinct from Elo's own margin-of-victory update (which only sees who
    # won and by how much, not how). See model/recent_form.py.
    home_form_delta, _home_form_note = recent_form_adjustment(db, game.home_team, game.season, game.week)
    away_form_delta, _away_form_note = recent_form_adjustment(db, game.away_team, game.season, game.week)

    # Separate, season-long counterpart to the single-game nudge above -- see
    # model/stat_rankings.py. Distinct signal from Elo (which reflects wins,
    # not box-score volume) and from the last-game-only form nudge.
    stat_rank_delta, _stat_rank_note = stat_rank_adjustment(db, game.home_team, game.away_team, game.season, game.week)

    home_win_prob = min(max(home_win_prob + home_form_delta - away_form_delta + stat_rank_delta, 0.02), 0.98)

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
    # Excludes games already final: a week stays "current" (see
    # schedule_context.current_or_next_week) from its first kickoff until its
    # last game finishes, so run-routine calls this repeatedly across the
    # whole week. Re-predicting an already-decided game here would use Elo
    # ratings that build_ratings() already rebuilt from that exact game's own
    # result (a later run-routine step), silently overwriting its one real,
    # locked-in pre-game prediction with a hindsight-leaked one.
    games = (
        db.query(Game)
        .filter(Game.season == season, Game.week == week, Game.game_type == "REG", Game.status != "final")
        .all()
    )
    predictions = [predict_game(db, game) for game in games]
    db.commit()
    return predictions
