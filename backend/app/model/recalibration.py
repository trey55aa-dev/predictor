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
from app.models import CalibrationAdjustment, Prediction

MIN_SAMPLE_FOR_CALIBRATION = 30

# market_blend_weight: grid search + gradual nudge (a search, not a formula --
# move toward the best candidate, don't jump straight to it off one evaluation).
BLEND_WEIGHT_CANDIDATES = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
BLEND_WEIGHT_MAX_STEP = 0.05
BLEND_WEIGHT_MIN_BRIER_IMPROVEMENT = 0.001

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
        _recalibrate_std_dev(db, "margin_std_default", "margin_error", settings.margin_std_default),
        _recalibrate_std_dev(db, "total_std_default", "total_error", settings.total_std_default),
    ):
        if result is not None:
            changes.append(result)

    db.commit()
    return changes
