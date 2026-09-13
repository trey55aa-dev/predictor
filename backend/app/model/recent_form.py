"""Small, bounded win-probability nudge from a team's most recent game's
performance on the "core four" keys to victory -- turnover margin, rushing
yards, passing yards, and 3rd-down% (see keys_to_victory.py, where these
were established as the keys that show real separation between winners and
losers, unlike 4th-down%, which is close to a coin flip and excluded here).

MVP heuristic in the same spirit as weather_adjust.py: capped small and
always paired with a human-readable note, since it isn't backtested yet --
there's only a couple of games of the 2026 season to validate it against so
far. Elo's own margin-of-victory update already reacts to *who won and by
how much*; this adds a distinct signal (*how* they won, play-by-play) on
top of that, not a duplicate of it.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.model.keys_to_victory import SIGNAL_KEYS, build_keys, team_stats
from app.models import Game, Play


def _most_recent_final_game(db: Session, team: str, season: int, before_week: int) -> Game | None:
    return (
        db.query(Game)
        .filter(
            Game.season == season,
            Game.week < before_week,
            Game.game_type == "REG",
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
            (Game.home_team == team) | (Game.away_team == team),
        )
        .order_by(Game.week.desc())
        .first()
    )


def recent_form_adjustment(db: Session, team: str, season: int, before_week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team` based on its most recent
    graded game's keys-to-victory record. delta is 0.0 (with note=None) when
    there's no prior game this season yet, or its play-by-play hasn't been
    ingested yet -- never a guessed value."""
    game = _most_recent_final_game(db, team, season, before_week)
    if game is None:
        return 0.0, None

    plays = db.query(Play).filter(Play.game_id == game.game_id).all()
    if not plays:
        return 0.0, None

    home_stats = team_stats(plays, game.home_team)
    away_stats = team_stats(plays, game.away_team)
    keys = build_keys(home_stats, away_stats)

    side = "home" if team == game.home_team else "away"
    signal_keys = [k for k in keys if k["key"] in SIGNAL_KEYS and k["winner"] is not None]
    if not signal_keys:
        return 0.0, None

    won = sum(1 for k in signal_keys if k["winner"] == side)
    net_fraction = (2 * won - len(signal_keys)) / len(signal_keys)  # -1.0 .. +1.0
    delta = net_fraction * settings.recent_form_max_adjustment

    opponent = game.away_team if side == "home" else game.home_team
    note = f"{team} won {won}/{len(signal_keys)} tracked keys vs {opponent} last week."
    return delta, note
