from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.grade import performance_summary
from app.schemas import PerformanceOut

router = APIRouter()


@router.get("/model/performance", response_model=PerformanceOut)
def model_performance(
    season: int | None = None, week: int | None = None, db: Session = Depends(get_db)
) -> PerformanceOut:
    summary = performance_summary(db, season=season, week=week)
    return PerformanceOut(**summary)
