from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.slip_checker import evaluate_slip

router = APIRouter()


class SlipRequest(BaseModel):
    legs: list[dict[str, Any]]


@router.post("/slip/evaluate")
def evaluate_user_slip(request: SlipRequest, db: Session = Depends(get_db)) -> dict:
    """Evaluates a user-built bet slip against the model's own data -- the
    same underlying Prediction/PlayerProjection/SimPlayerProjection rows the
    app's own auto-generated parlays use. Each leg is a dict shaped as one
    of:
      {"leg_type": "game_winner", "game_id": "...", "team": "KC"}
      {"leg_type": "anytime_td", "game_id": "...", "player_id": "..."}
      {"leg_type": "player_yards", "game_id": "...", "player_id": "...",
       "stat": "rushing"|"receiving", "side": "over"|"under", "line": 45.5}
    Any leg may also carry "american_odds" (the price the user's own
    sportsbook is offering) to get payout math alongside the model's
    probability.
    """
    return evaluate_slip(db, request.legs)
