from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.grade import model_vs_market_comparison, over_under_summary, performance_summary
from app.model.league_context import league_pass_rate_by_week
from app.model.strength_of_schedule import strength_of_schedule
from app.model.team_home_field_advantage import team_hfa_excess
from app.models import CalibrationAdjustment
from app.schemas import PerformanceOut

router = APIRouter()


@router.get("/model/performance", response_model=PerformanceOut)
def model_performance(
    season: int | None = None, week: int | None = None, db: Session = Depends(get_db)
) -> PerformanceOut:
    summary = performance_summary(db, season=season, week=week)
    return PerformanceOut(**summary)


@router.get("/model/over-under-performance")
def over_under_performance(
    season: int | None = None, week: int | None = None, db: Session = Depends(get_db)
) -> dict:
    """Rolling over/under hit rate -- how often the predicted total landed
    on the right side of the real betting line, not just how close it was.
    See model/grade.py:over_under_summary."""
    return over_under_summary(db, season=season, week=week)


@router.get("/model/accuracy-comparison")
def accuracy_comparison(db: Session = Depends(get_db)) -> dict:
    """How the data-only Elo model, the market, and the served blend actually
    scored against each other on the same graded games. Backs the honesty
    note on the parlay page -- see model/grade.py:model_vs_market_comparison."""
    return model_vs_market_comparison(db)


@router.get("/model/league-pass-rate")
def league_pass_rate(season: int, db: Session = Depends(get_db)) -> dict:
    """League-wide pass rate per week for `season`, across every team's
    ingested run/pass plays combined. See model/league_context.py."""
    return league_pass_rate_by_week(db, season)


@router.get("/model/strength-of-schedule")
def team_strength_of_schedule(
    team: str, season: int, week: int, db: Session = Depends(get_db)
) -> dict:
    """Average Elo rating of `team`'s opponents already played this season
    and of those still remaining, as of `week`. See
    model/strength_of_schedule.py."""
    return strength_of_schedule(db, team, season, week)


@router.get("/model/home-field-advantage")
def team_home_field_advantage(
    team: str, season: int, week: int, db: Session = Depends(get_db)
) -> dict:
    """`team`'s own historical deviation from the league-average home-field
    boost, as of `week` (anti-leakage: only games strictly before it). See
    model/team_home_field_advantage.py."""
    excess, sample_size = team_hfa_excess(db, team, season, week)
    return {"team": team, "excess_win_prob": excess, "sample_size": sample_size}


@router.get("/model/calibration-history")
def calibration_history(db: Session = Depends(get_db)) -> list[dict]:
    """Every time a model constant was auto-tuned, most-recent first --
    real evidence of the model correcting itself, not just tracking accuracy."""
    adjustments = db.query(CalibrationAdjustment).order_by(CalibrationAdjustment.created_at.desc()).all()
    return [
        {
            "parameter_name": a.parameter_name,
            "old_value": a.old_value,
            "new_value": a.new_value,
            "evidence": a.evidence,
            "sample_size": a.sample_size,
            "created_at": a.created_at.isoformat(),
        }
        for a in adjustments
    ]
