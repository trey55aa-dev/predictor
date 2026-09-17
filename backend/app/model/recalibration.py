"""The actual self-correction step: looks at accumulated grading evidence
and, when there's enough of it to trust, nudges the model's own tunable
constants toward what the evidence supports -- not just measuring accuracy,
acting on it. Every change is logged (CalibrationAdjustment) with the
evidence that justified it, so it's auditable, not a black box.

Scope boundary: Elo's own internals (K-factor, home-field advantage, season
regression) are NOT tuned here -- they're baked into every historical
TeamRating snapshot, so changing them needs a full `build-history` rebuild,
not a live nudge. Only prediction-time-only constants are safe to tune live.
"""

import datetime as dt
import statistics

from sqlalchemy.orm import Session

from app.config import settings
from app.model.market import blend_line
from app.model.scoring import ELO_POINTS_PER_ELO, matchup_expected_total
from app.model.venue import is_true_home_game
from app.model.weather_adjust import total_points_adjustment
from app.models import CalibrationAdjustment, Game, OddsSnapshot, Prediction, WeatherSnapshot

MIN_SAMPLE_FOR_CALIBRATION = 30

# market_blend_weight: grid search + gradual nudge (a search, not a formula --
# move toward the best candidate, don't jump straight to it off one evaluation).
BLEND_WEIGHT_CANDIDATES = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
BLEND_WEIGHT_MAX_STEP = 0.05
BLEND_WEIGHT_MIN_BRIER_IMPROVEMENT = 0.001

# margin_blend_weight / total_blend_weight: same grid-search shape as
# market_blend_weight above, but optimized directly against point error
# (avg_abs_margin_error / avg_abs_total_error) instead of Brier score --
# market_blend_weight is tuned purely for win-probability calibration, which
# isn't necessarily the weight that best predicts the actual final score.
BLEND_WEIGHT_MIN_ERROR_IMPROVEMENT = 0.1  # points

# margin_std_default / total_std_default: these have a real right answer (the
# observed error std-dev), so we set them directly rather than searching.
STD_DEV_MIN_CHANGE = 1.0  # points


def get_tuned_value(db: Session, parameter_name: str, default: float) -> float:
    """Current value of a tunable constant: the latest adjustment on record,
    or the config.py default if it's never been tuned."""
    latest = (
        db.query(CalibrationAdjustment)
        .filter(CalibrationAdjustment.parameter_name == parameter_name)
        .order_by(CalibrationAdjustment.created_at.desc())
        .first()
    )
    return latest.new_value if latest else default


def _graded_predictions_with_market(db: Session) -> list[Prediction]:
    return (
        db.query(Prediction)
        .filter(
            Prediction.elo_win_prob.isnot(None),
            Prediction.market_win_prob.isnot(None),
            Prediction.actual_home_score.isnot(None),
            Prediction.actual_away_score.isnot(None),
        )
        .all()
    )


def _avg_brier_at_weight(predictions: list[Prediction], weight: float) -> float:
    total = 0.0
    for p in predictions:
        blended = weight * p.elo_win_prob + (1 - weight) * p.market_win_prob
        outcome = 1.0 if p.actual_home_score > p.actual_away_score else 0.0
        total += (blended - outcome) ** 2
    return total / len(predictions)


def _recalibrate_blend_weight(db: Session) -> dict | None:
    predictions = _graded_predictions_with_market(db)
    n = len(predictions)
    if n < MIN_SAMPLE_FOR_CALIBRATION:
        return None

    current = get_tuned_value(db, "market_blend_weight", settings.market_blend_weight)
    current_brier = _avg_brier_at_weight(predictions, current)

    best_weight = min(BLEND_WEIGHT_CANDIDATES, key=lambda w: _avg_brier_at_weight(predictions, w))
    best_brier = _avg_brier_at_weight(predictions, best_weight)

    if current_brier - best_brier < BLEND_WEIGHT_MIN_BRIER_IMPROVEMENT:
        return None  # not a meaningful improvement -- leave it alone

    step = max(-BLEND_WEIGHT_MAX_STEP, min(BLEND_WEIGHT_MAX_STEP, best_weight - current))
    new_value = round(current + step, 4)
    if new_value == current:
        return None

    adjustment = CalibrationAdjustment(
        parameter_name="market_blend_weight",
        old_value=current,
        new_value=new_value,
        evidence=(
            f"Grid search over {n} graded predictions: best candidate w={best_weight} "
            f"(avg Brier {best_brier:.4f}) vs. current w={current} (avg Brier {current_brier:.4f})."
        ),
        sample_size=n,
        created_at=dt.datetime.utcnow(),
    )
    db.add(adjustment)
    return {
        "parameter": "market_blend_weight",
        "old_value": current,
        "new_value": new_value,
        "evidence": adjustment.evidence,
    }


def _graded_predictions_with_game(db: Session) -> list[tuple[Prediction, Game]]:
    return (
        db.query(Prediction, Game)
        .join(Game, Prediction.game_id == Game.game_id)
        .filter(Prediction.actual_home_score.isnot(None), Prediction.actual_away_score.isnot(None))
        .all()
    )


def _margin_samples(db: Session, rows: list[tuple[Prediction, Game]]) -> list[tuple[float, float | None, float]]:
    """(elo_margin, market_margin, actual_margin) per graded prediction,
    recomputed from data every row already carries rather than a new stored
    column: home_elo/away_elo are on the Prediction itself, the market
    spread comes from the OddsSnapshot it was actually predicted against
    (odds_snapshot_id), and neither changes after the fact -- so this
    reproduces exactly what predict_game saw at prediction time."""
    samples = []
    for prediction, game in rows:
        home_field_bonus = settings.elo_home_field_advantage if is_true_home_game(db, game) else 0.0
        elo_margin = (prediction.home_elo + home_field_bonus - prediction.away_elo) / ELO_POINTS_PER_ELO

        market_margin = None
        if prediction.odds_snapshot_id is not None:
            odds = db.get(OddsSnapshot, prediction.odds_snapshot_id)
            if odds is not None and odds.spread_line is not None:
                market_margin = -odds.spread_line  # spread is home's perspective, negative = favored

        actual_margin = prediction.actual_home_score - prediction.actual_away_score
        samples.append((elo_margin, market_margin, actual_margin))
    return samples


def _total_samples(
    db: Session, rows: list[tuple[Prediction, Game]]
) -> list[tuple[float, float | None, float, float]]:
    """(scoring_expected_total, market_total, weather_adjustment, actual_total)
    per graded prediction -- same reproduce-from-existing-data approach as
    _margin_samples. scoring_expected_total only ever looks at games
    strictly before the graded game's own week, so recomputing it now gives
    the same value predict_game saw (past results don't change)."""
    samples = []
    for prediction, game in rows:
        scoring_expected_total = matchup_expected_total(db, game.home_team, game.away_team, game.season, game.week)

        market_total = None
        if prediction.odds_snapshot_id is not None:
            odds = db.get(OddsSnapshot, prediction.odds_snapshot_id)
            if odds is not None and odds.total_line is not None:
                market_total = odds.total_line

        weather = db.get(WeatherSnapshot, prediction.weather_snapshot_id) if prediction.weather_snapshot_id else None
        weather_adjustment, _ = total_points_adjustment(weather)

        actual_total = prediction.actual_home_score + prediction.actual_away_score
        samples.append((scoring_expected_total, market_total, weather_adjustment, actual_total))
    return samples


def _avg_abs_margin_error_at_weight(samples: list[tuple[float, float | None, float]], weight: float) -> float:
    total = 0.0
    for elo_margin, market_margin, actual_margin in samples:
        predicted_margin = blend_line(elo_margin, market_margin, weight=weight)
        total += abs(predicted_margin - actual_margin)
    return total / len(samples)


def _avg_abs_total_error_at_weight(samples: list[tuple[float, float | None, float, float]], weight: float) -> float:
    total = 0.0
    for scoring_expected_total, market_total, weather_adjustment, actual_total in samples:
        blended = blend_line(scoring_expected_total, market_total, weight=weight)
        predicted_total = max(blended - weather_adjustment, 20.0)  # matches predict_game's own floor
        total += abs(predicted_total - actual_total)
    return total / len(samples)


def _recalibrate_margin_blend_weight(db: Session) -> dict | None:
    rows = _graded_predictions_with_game(db)
    n = len(rows)
    if n < MIN_SAMPLE_FOR_CALIBRATION:
        return None

    samples = _margin_samples(db, rows)
    current = get_tuned_value(db, "margin_blend_weight", settings.margin_blend_weight)
    current_error = _avg_abs_margin_error_at_weight(samples, current)

    best_weight = min(BLEND_WEIGHT_CANDIDATES, key=lambda w: _avg_abs_margin_error_at_weight(samples, w))
    best_error = _avg_abs_margin_error_at_weight(samples, best_weight)

    if current_error - best_error < BLEND_WEIGHT_MIN_ERROR_IMPROVEMENT:
        return None  # not a meaningful improvement -- leave it alone

    step = max(-BLEND_WEIGHT_MAX_STEP, min(BLEND_WEIGHT_MAX_STEP, best_weight - current))
    new_value = round(current + step, 4)
    if new_value == current:
        return None

    adjustment = CalibrationAdjustment(
        parameter_name="margin_blend_weight",
        old_value=current,
        new_value=new_value,
        evidence=(
            f"Grid search over {n} graded predictions: best candidate w={best_weight} "
            f"(avg margin error {best_error:.2f}) vs. current w={current} (avg margin error {current_error:.2f})."
        ),
        sample_size=n,
        created_at=dt.datetime.utcnow(),
    )
    db.add(adjustment)
    return {
        "parameter": "margin_blend_weight",
        "old_value": current,
        "new_value": new_value,
        "evidence": adjustment.evidence,
    }


def _recalibrate_total_blend_weight(db: Session) -> dict | None:
    rows = _graded_predictions_with_game(db)
    n = len(rows)
    if n < MIN_SAMPLE_FOR_CALIBRATION:
        return None

    samples = _total_samples(db, rows)
    current = get_tuned_value(db, "total_blend_weight", settings.total_blend_weight)
    current_error = _avg_abs_total_error_at_weight(samples, current)

    best_weight = min(BLEND_WEIGHT_CANDIDATES, key=lambda w: _avg_abs_total_error_at_weight(samples, w))
    best_error = _avg_abs_total_error_at_weight(samples, best_weight)

    if current_error - best_error < BLEND_WEIGHT_MIN_ERROR_IMPROVEMENT:
        return None

    step = max(-BLEND_WEIGHT_MAX_STEP, min(BLEND_WEIGHT_MAX_STEP, best_weight - current))
    new_value = round(current + step, 4)
    if new_value == current:
        return None

    adjustment = CalibrationAdjustment(
        parameter_name="total_blend_weight",
        old_value=current,
        new_value=new_value,
        evidence=(
            f"Grid search over {n} graded predictions: best candidate w={best_weight} "
            f"(avg total error {best_error:.2f}) vs. current w={current} (avg total error {current_error:.2f})."
        ),
        sample_size=n,
        created_at=dt.datetime.utcnow(),
    )
    db.add(adjustment)
    return {
        "parameter": "total_blend_weight",
        "old_value": current,
        "new_value": new_value,
        "evidence": adjustment.evidence,
    }


def _recalibrate_std_dev(db: Session, parameter_name: str, error_attr: str, default: float) -> dict | None:
    predictions = (
        db.query(Prediction).filter(getattr(Prediction, error_attr).isnot(None)).all()
    )
    n = len(predictions)
    if n < MIN_SAMPLE_FOR_CALIBRATION:
        return None

    errors = [getattr(p, error_attr) for p in predictions]
    observed_std = statistics.pstdev(errors)
    current = get_tuned_value(db, parameter_name, default)

    if abs(observed_std - current) < STD_DEV_MIN_CHANGE:
        return None

    new_value = round(observed_std, 2)
    adjustment = CalibrationAdjustment(
        parameter_name=parameter_name,
        old_value=current,
        new_value=new_value,
        evidence=(
            f"Observed std-dev of {error_attr} across {n} graded predictions is {observed_std:.2f} "
            f"(current confidence range uses {current:.2f})."
        ),
        sample_size=n,
        created_at=dt.datetime.utcnow(),
    )
    db.add(adjustment)
    return {
        "parameter": parameter_name,
        "old_value": current,
        "new_value": new_value,
        "evidence": adjustment.evidence,
    }


def recalibrate(db: Session) -> list[dict]:
    """Runs every tunable check; returns what actually changed this pass
    (usually nothing -- most passes find the current values already fine)."""
    changes = []
    for result in (
        _recalibrate_blend_weight(db),
        _recalibrate_margin_blend_weight(db),
        _recalibrate_total_blend_weight(db),
        _recalibrate_std_dev(db, "margin_std_default", "margin_error", settings.margin_std_default),
        _recalibrate_std_dev(db, "total_std_default", "total_error", settings.total_std_default),
    ):
        if result is not None:
            changes.append(result)

    db.commit()
    return changes
