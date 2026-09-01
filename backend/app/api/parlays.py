from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.parlays import build_parlays

router = APIRouter()


@router.get("/parlays/week/{season}/{week}")
def parlays_for_week(season: int, week: int, legs: int = 3, db: Session = Depends(get_db)) -> dict:
    return build_parlays(db, season, week, legs=legs)
