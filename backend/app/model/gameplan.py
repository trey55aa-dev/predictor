"""Synthesizes everything known about one game into a single 'game plan'
bundle: prediction, weather, injuries, scheme/play-style matchup, over/under
lean, and an upset-alert flag. Pure aggregation over data other modules
already produce -- no new inputs of its own.
"""

from sqlalchemy.orm import Session

from app.model.matchup import defensive_tendencies, scheme_matchup_history, top_offensive_concepts
from app.models import Game, Injury, OddsSnapshot, Prediction, TeamSeasonScheme, WeatherSnapshot

UPSET_ALERT_THRESHOLD = 0.40
_SEVERITY_RANK = {"Out": 0, "Doubtful": 1, "Questionable": 2}


def _latest_prediction(db: Session, game_id: str) -> Prediction | None:
    return (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(Prediction.created_at.desc())
        .first()
    )


def upset_alert(home_win_prob: float | None, home_team: str, away_team: str) -> dict:
    if home_win_prob is None:
        return {"is_upset_alert": False, "note": None}
    underdog, underdog_prob = (away_team, 1 - home_win_prob) if home_win_prob >= 0.5 else (home_team, home_win_prob)
    is_alert = underdog_prob >= UPSET_ALERT_THRESHOLD
    note = (
        f"{underdog} given a {underdog_prob:.0%} chance despite being the underdog -- a real upset risk."
        if is_alert
        else None
    )
    return {"is_upset_alert": is_alert, "note": note}


def over_under(predicted_total: float | None, market_total_line: float | None) -> dict:
    if predicted_total is None or market_total_line is None:
        return {"lean": None, "edge": None, "note": "No market total available yet for this game."}
    edge = predicted_total - market_total_line
    if abs(edge) < 0.5:
        return {"lean": "push", "edge": edge, "note": "Model total is essentially in line with the market."}
    lean = "over" if edge > 0 else "under"
    return {
        "lean": lean,
        "edge": edge,
        "note": f"Model total ({predicted_total:.1f}) is {abs(edge):.1f} pts {'above' if edge > 0 else 'below'} the market line ({market_total_line:.1f}).",
    }


def _team_injuries(db: Session, team_abbr: str, season: int, week: int) -> list[dict]:
    rows = db.query(Injury).filter(Injury.team_abbr == team_abbr, Injury.season == season, Injury.week == week).all()
    rows.sort(key=lambda r: (not r.is_starter, _SEVERITY_RANK.get(r.report_status, 9)))
    return [
        {
            "player_name": r.player_name,
            "position": r.position,
            "report_status": r.report_status,
            "primary_injury": r.primary_injury,
            "is_starter": r.is_starter,
        }
        for r in rows
    ]


def _injury_data_available(db: Session, season: int) -> bool:
    """Whether the injury data SOURCE has any rows at all for this season --
    distinct from a team's own list being empty.

    Real gap this closes: nflverse's injury feed has zero rows for a season
    until partway into it (confirmed live -- `nfl.load_injuries` raises
    "Season must be between 2009 and 2025" for the 2026 season entirely, not
    yet available at all), so every team's injury list reads as empty right
    now. Without this flag, "empty list" is indistinguishable from "checked,
    genuinely clean" -- which is a real, misleading honesty gap: the panel
    would say "No notable injuries reported" with exactly the same words
    whether that's actually true or we simply have no data source yet.
    """
    return db.query(Injury.id).filter(Injury.season == season).first() is not None


def _team_scheme(db: Session, team_abbr: str, season: int) -> TeamSeasonScheme | None:
    return (
        db.query(TeamSeasonScheme)
        .filter(TeamSeasonScheme.team_abbr == team_abbr, TeamSeasonScheme.season == season)
        .first()
    )


def build_gameplan(db: Session, game: Game) -> dict:
    prediction = _latest_prediction(db, game.game_id)
    odds = db.get(OddsSnapshot, prediction.odds_snapshot_id) if prediction and prediction.odds_snapshot_id else None
    weather = (
        db.get(WeatherSnapshot, prediction.weather_snapshot_id) if prediction and prediction.weather_snapshot_id else None
    )

    home_scheme = _team_scheme(db, game.home_team, game.season)
    away_scheme = _team_scheme(db, game.away_team, game.season)

    home_off_id = home_scheme.offense_scheme_id if home_scheme else None
    home_def_id = home_scheme.defense_scheme_id if home_scheme else None
    away_off_id = away_scheme.offense_scheme_id if away_scheme else None
    away_def_id = away_scheme.defense_scheme_id if away_scheme else None

    return {
        "game_id": game.game_id,
        "season": game.season,
        "week": game.week,
        "home_team": game.home_team,
        "away_team": game.away_team,
        "prediction": {
            "home_win_prob": prediction.home_win_prob if prediction else None,
            "predicted_home_score": prediction.predicted_home_score if prediction else None,
            "predicted_away_score": prediction.predicted_away_score if prediction else None,
        }
        if prediction
        else None,
        "upset_alert": upset_alert(
            prediction.home_win_prob if prediction else None, game.home_team, game.away_team
        ),
        "over_under": over_under(
            prediction.predicted_total if prediction else None, odds.total_line if odds else None
        ),
        "weather_note": prediction.weather_note if prediction else None,
        "injuries": {
            "home": _team_injuries(db, game.home_team, game.season, game.week),
            "away": _team_injuries(db, game.away_team, game.season, game.week),
            "data_available": _injury_data_available(db, game.season),
        },
        "play_styles": {
            "home_offense": top_offensive_concepts(db, game.home_team, game.season),
            "away_offense": top_offensive_concepts(db, game.away_team, game.season),
            "home_defense": defensive_tendencies(db, game.home_team, game.season),
            "away_defense": defensive_tendencies(db, game.away_team, game.season),
        },
        "scheme_matchups": {
            "home_offense_vs_away_defense": scheme_matchup_history(db, home_off_id, away_def_id),
            "away_offense_vs_home_defense": scheme_matchup_history(db, away_off_id, home_def_id),
        },
        "schemes": {
            "home_offense_scheme_id": home_off_id,
            "home_defense_scheme_id": home_def_id,
            "away_offense_scheme_id": away_off_id,
            "away_defense_scheme_id": away_def_id,
        },
    }
