from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.gameplan import build_gameplan
from app.models import Game

router = APIRouter()


@router.get("/predictions/game/{game_id}/gameplan")
def game_plan(game_id: str, db: Session = Depends(get_db)) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    return build_gameplan(db, game)
