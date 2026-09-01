from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.player_projection import project_game_players
from app.models import Game

router = APIRouter()


@router.get("/predictions/game/{game_id}/players")
def player_projections(game_id: str, db: Session = Depends(get_db)) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    return project_game_players(db, game)
