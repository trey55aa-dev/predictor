"""Figures out 'what week is it' from the already-ingested schedule, rather
than trusting nflreadpy's own current-week helper (which lags behind the
actual calendar until nflverse's data catches up -- confirmed empirically
earlier in this project).
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.models import Game


def current_season(today: dt.date | None = None) -> int:
    """NFL seasons are named for the year they kick off in (September) and
    run into January/February of the following year. Jan/Feb belong to the
    PREVIOUS season's playoffs; March onward belongs to the upcoming season
    even before its schedule is released."""
    today = today or dt.date.today()
    return today.year if today.month >= 3 else today.year - 1


def is_game_day_or_eve(db: Session, season: int, today: dt.date | None = None) -> bool:
    """True if today or tomorrow has a scheduled REG-season game."""
    today = today or dt.date.today()
    tomorrow = today + dt.timedelta(days=1)
    check_dates = {today.isoformat(), tomorrow.isoformat()}

    exists = (
        db.query(Game)
        .filter(Game.season == season, Game.game_type == "REG", Game.gameday.in_(check_dates))
        .first()
    )
    return exists is not None


def should_run_dense_cadence(db: Session, season: int, today: dt.date | None = None) -> bool:
    """The routine's actual cadence decision: dense (4x/day) unconditionally
    during the preseason countdown (before the season's first game -- there's
    no 'quiet day' concept yet when nothing has started), OR once the season
    is under way, dense on a game day/its eve specifically. Quiet (3x/day,
    skip the late run) only kicks in on an off-day *within* an active season.
    """
    today = today or dt.date.today()

    games = db.query(Game).filter(Game.season == season, Game.game_type == "REG").all()
    if not games:
        return True  # no schedule ingested yet -- default dense rather than guess

    earliest_gameday = min(dt.date.fromisoformat(g.gameday) for g in games)
    if today < earliest_gameday:
        return True  # preseason countdown: always dense until kickoff

    return is_game_day_or_eve(db, season, today)


def current_or_next_week(db: Session, season: int, today: dt.date | None = None) -> int | None:
    """Returns the week containing `today`, or the next upcoming week if
    `today` falls between weeks. Returns the season's final week if `today`
    is after every game. Returns None if the season has no ingested games.
    """
    today = today or dt.date.today()

    games = db.query(Game).filter(Game.season == season, Game.game_type == "REG").all()
    if not games:
        return None

    weeks: dict[int, list[dt.date]] = {}
    for game in games:
        game_date = dt.date.fromisoformat(game.gameday)
        weeks.setdefault(game.week, []).append(game_date)

    for week in sorted(weeks):
        if max(weeks[week]) >= today:
            return week

    return max(weeks)
