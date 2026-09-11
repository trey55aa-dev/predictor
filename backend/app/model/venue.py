"""Whether a game was actually played at the home team's own stadium.

The NFL schedules a handful of games a year (London, Melbourne, Sao Paulo,
Frankfurt...) where one team is still designated "home" for record-keeping
and the game log, but the game is played at a neutral international site.
Neither the "home" team's crowd advantage nor its cancelled travel apply
there, so Elo's home-field-advantage offset must not be applied either --
that offset is exactly the terms Elo doesn't otherwise capture: sleeping in
your own bed, the home crowd, your opponent's travel fatigue.
"""

from sqlalchemy.orm import Session

from app.models import Game, Team


def is_true_home_game(db: Session, game: Game) -> bool:
    """False only when we can positively confirm a neutral site: the game
    has a recorded stadium and it isn't the home team's own. Missing data
    (either side) defaults to True -- the overwhelming majority of games are
    normal home games, and we should only withhold the advantage when the
    data actually says otherwise."""
    if game.stadium_id is None:
        return True
    home_team = db.get(Team, game.home_team)
    if home_team is None or home_team.stadium_id is None:
        return True
    return game.stadium_id == home_team.stadium_id
