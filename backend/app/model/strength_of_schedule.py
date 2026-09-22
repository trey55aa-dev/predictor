"""Strength of schedule: the average Elo rating of the opponents a team has
already played this season, and of the opponents left on its schedule --
nfelo publishes the same idea as its "Strength of Schedule" tool, built
here from this project's own Elo ratings (model/elo.py) and schedule
(already ingested) rather than a live feed.

Deliberately informational only, not wired into predict.py as another
capped adjustment: Elo already accounts for opponent quality through its
own update rule at the time each game is actually played (beating a good
team moves a rating up more than beating a bad one). A separate SOS
signal here would either double-count that or contradict it -- this is
context for a human reading the breakdown, not a new independent source
of predictive information the way EPA or red-zone execution are.
"""

from sqlalchemy.orm import Session

from app.model.elo import latest_rating
from app.models import Game


def _team_games(db: Session, team: str, season: int) -> list[Game]:
    return (
        db.query(Game)
        .filter(
            Game.season == season,
            Game.game_type == "REG",
            (Game.home_team == team) | (Game.away_team == team),
        )
        .order_by(Game.week)
        .all()
    )


def strength_of_schedule(db: Session, team: str, season: int, before_week: int) -> dict:
    """{opponents_played, avg_opponent_elo_played, opponents_remaining,
    avg_opponent_elo_remaining} for `team` this season. "Played" uses each
    opponent's own Elo rating AT THE TIME that game was actually played (no
    hindsight leak, same convention as predict.py); "remaining" uses each
    opponent's most current known rating, since a future opponent's actual
    rating on gameday isn't knowable yet. Either average is None (never a
    guessed 1500) when there are no games in that bucket."""
    games = _team_games(db, team, season)

    played_ratings: list[float] = []
    remaining_ratings: list[float] = []

    for game in games:
        opponent = game.away_team if game.home_team == team else game.home_team
        already_played = game.week < before_week and game.home_score is not None and game.away_score is not None
        if already_played:
            played_ratings.append(latest_rating(db, opponent, game.season, game.week))
        else:
            remaining_ratings.append(latest_rating(db, opponent, season, before_week))

    return {
        "opponents_played": len(played_ratings),
        "avg_opponent_elo_played": (sum(played_ratings) / len(played_ratings)) if played_ratings else None,
        "opponents_remaining": len(remaining_ratings),
        "avg_opponent_elo_remaining": (
            (sum(remaining_ratings) / len(remaining_ratings)) if remaining_ratings else None
        ),
    }
