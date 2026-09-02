from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.grade import performance_summary
from app.models import CalibrationAdjustment
from app.schemas import PerformanceOut

router = APIRouter()


@router.get("/model/performance", response_model=PerformanceOut)
def model_performance(
    season: int | None = None, week: int | None = None, db: Session = Depends(get_db)
) -> PerformanceOut:
    summary = performance_summary(db, season=season, week=week)
    return PerformanceOut(**summary)


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
