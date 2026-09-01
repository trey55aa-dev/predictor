from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.gameplan import over_under as compute_over_under
from app.model.gameplan import upset_alert as compute_upset_alert
from app.models import Game, OddsSnapshot, Prediction, WeatherSnapshot
from app.schemas import GamePredictionOut, WeatherOut

router = APIRouter()


def _latest_prediction(db: Session, game_id: str) -> Prediction | None:
    return (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(Prediction.created_at.desc())
        .first()
    )


def _to_out(db: Session, game: Game) -> GamePredictionOut:
    prediction = _latest_prediction(db, game.game_id)

    market_spread_line = None
    market_total_line = None
    weather_out = None

    if prediction is not None:
        if prediction.odds_snapshot_id is not None:
            odds = db.get(OddsSnapshot, prediction.odds_snapshot_id)
            if odds is not None:
                market_spread_line = odds.spread_line
                market_total_line = odds.total_line
        if prediction.weather_snapshot_id is not None:
            weather = db.get(WeatherSnapshot, prediction.weather_snapshot_id)
            if weather is not None:
                weather_out = WeatherOut.model_validate(weather)

    upset = compute_upset_alert(
        prediction.home_win_prob if prediction else None, game.home_team, game.away_team
    )
    ou = compute_over_under(
        prediction.predicted_total if prediction else None, market_total_line
    )

    return GamePredictionOut(
        game_id=game.game_id,
        season=game.season,
        week=game.week,
        gameday=game.gameday,
        gametime_utc=game.gametime_utc,
        home_team=game.home_team,
        away_team=game.away_team,
        status=game.status,
        home_score=game.home_score,
        away_score=game.away_score,
        home_win_prob=prediction.home_win_prob if prediction else None,
        predicted_home_score=prediction.predicted_home_score if prediction else None,
        predicted_away_score=prediction.predicted_away_score if prediction else None,
        predicted_margin=prediction.predicted_margin if prediction else None,
        predicted_total=prediction.predicted_total if prediction else None,
        margin_range_low=prediction.margin_range_low if prediction else None,
        margin_range_high=prediction.margin_range_high if prediction else None,
        total_range_low=prediction.total_range_low if prediction else None,
        total_range_high=prediction.total_range_high if prediction else None,
        weather_note=prediction.weather_note if prediction else None,
        market_spread_line=market_spread_line,
        market_total_line=market_total_line,
        weather=weather_out,
        correct_winner=prediction.correct_winner if prediction else None,
        model_version=prediction.model_version if prediction else None,
        is_upset_alert=upset["is_upset_alert"],
        upset_note=upset["note"],
        over_under_lean=ou["lean"],
        over_under_note=ou["note"],
    )


@router.get("/predictions/week/{season}/{week}", response_model=list[GamePredictionOut])
def predictions_for_week(season: int, week: int, db: Session = Depends(get_db)) -> list[GamePredictionOut]:
    games = (
        db.query(Game)
        .filter(Game.season == season, Game.week == week, Game.game_type == "REG")
        .order_by(Game.gametime_utc)
        .all()
    )
    return [_to_out(db, g) for g in games]


@router.get("/predictions/game/{game_id}", response_model=GamePredictionOut)
def prediction_for_game(game_id: str, db: Session = Depends(get_db)) -> GamePredictionOut:
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")
    return _to_out(db, game)
