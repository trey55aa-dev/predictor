from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Game

router = APIRouter()


@router.get("/games/week/{season}/{week}")
def games_for_week(season: int, week: int, db: Session = Depends(get_db)) -> list[dict]:
    games = (
        db.query(Game)
        .filter(Game.season == season, Game.week == week)
        .order_by(Game.gametime_utc)
        .all()
    )
    return [
        {
            "game_id": g.game_id,
            "home_team": g.home_team,
            "away_team": g.away_team,
            "gameday": g.gameday,
            "status": g.status,
            "home_score": g.home_score,
            "away_score": g.away_score,
        }
        for g in games
    ]
