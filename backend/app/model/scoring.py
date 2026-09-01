"""Team-specific scoring baselines, used to project a matchup's total points
without needing a market total line. Without this, the model has nothing
team-specific to anchor a total to and falls back to a flat league-average
constant for every game -- exactly the "every total lands near 44" bug this
module fixes.
"""

from sqlalchemy.orm import Session

from app.models import Game

LOOKBACK_GAMES = 17  # roughly one season
LEAGUE_AVERAGE_POINTS = 22.0  # fallback for a team with no completed-game history at all


def team_scoring_averages(db: Session, team_abbr: str, before_season: int, before_week: int) -> dict:
    """Average points scored/allowed over the team's last LOOKBACK_GAMES
    completed games strictly before the given season/week (crosses season
    boundaries so early-season weeks still have a real sample)."""
    games = (
        db.query(Game)
        .filter(
            (Game.home_team == team_abbr) | (Game.away_team == team_abbr),
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
            (Game.season < before_season) | ((Game.season == before_season) & (Game.week < before_week)),
        )
        .order_by(Game.season.desc(), Game.week.desc())
        .limit(LOOKBACK_GAMES)
        .all()
    )

    if not games:
        return {"avg_scored": LEAGUE_AVERAGE_POINTS, "avg_allowed": LEAGUE_AVERAGE_POINTS, "sample_size": 0}

    scored = []
    allowed = []
    for game in games:
        if game.home_team == team_abbr:
            scored.append(game.home_score)
            allowed.append(game.away_score)
        else:
            scored.append(game.away_score)
            allowed.append(game.home_score)

    return {
        "avg_scored": sum(scored) / len(scored),
        "avg_allowed": sum(allowed) / len(allowed),
        "sample_size": len(games),
    }


def matchup_expected_total(db: Session, home_team: str, away_team: str, season: int, week: int) -> float:
    """Blends each team's own scoring rate with what their opponent
    typically allows -- the standard way to project a total from two teams'
    independent scoring tendencies rather than a single league constant."""
    home = team_scoring_averages(db, home_team, season, week)
    away = team_scoring_averages(db, away_team, season, week)

    home_expected = (home["avg_scored"] + away["avg_allowed"]) / 2
    away_expected = (away["avg_scored"] + home["avg_allowed"]) / 2
    return home_expected + away_expected
