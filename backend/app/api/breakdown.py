from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.keys_to_victory import build_game_breakdown
from app.models import Game

router = APIRouter()


@router.get("/predictions/game/{game_id}/breakdown")
def game_breakdown(game_id: str, db: Session = Depends(get_db)) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    return build_game_breakdown(db, game)
