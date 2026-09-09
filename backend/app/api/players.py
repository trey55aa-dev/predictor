from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.player_projection import project_game_players
from app.models import Game, SimPlayerProjection

router = APIRouter()


@router.get("/predictions/game/{game_id}/players")
def player_projections(game_id: str, db: Session = Depends(get_db)) -> dict:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    return project_game_players(db, game)


@router.get("/predictions/game/{game_id}/sim-player-props")
def sim_player_props(game_id: str, db: Session = Depends(get_db)) -> dict:
    """Simulation-based rushing/receiving prop distributions -- precomputed
    (see app.cli simulate-player-props-week), not run live: a Monte Carlo
    pass takes several seconds to tens of seconds per game. Rushing and
    receiving only; passing is intentionally not served here, see
    SimPlayerProjection's docstring for why."""
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")

    rows = (
        db.query(SimPlayerProjection)
        .filter(SimPlayerProjection.game_id == game_id)
        .order_by(SimPlayerProjection.anytime_td_probability.desc())
        .all()
    )

    def to_out(r: SimPlayerProjection) -> dict:
        return {
            "player_id": r.player_id,
            "player_name": r.player_name,
            "team": r.team,
            "rushing": (
                {"mean_yards": r.rushing_mean_yards, "p10": r.rushing_p10, "p90": r.rushing_p90, "td_probability": r.rushing_td_prob}
                if r.rushing_mean_yards is not None
                else None
            ),
            "receiving": (
                {"mean_yards": r.receiving_mean_yards, "p10": r.receiving_p10, "p90": r.receiving_p90, "td_probability": r.receiving_td_prob}
                if r.receiving_mean_yards is not None
                else None
            ),
            "anytime_td_probability": r.anytime_td_probability,
        }

    return {
        "game_id": game_id,
        "n_sims": rows[0].n_sims if rows else None,
        "home": [to_out(r) for r in rows if r.team == game.home_team],
        "away": [to_out(r) for r in rows if r.team == game.away_team],
    }
