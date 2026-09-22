"""QB-adjusted Elo: a small, capped win-prob nudge for when a team's
CURRENT starting quarterback isn't the same one who's taken most of its
pass attempts this season -- an injury, a benching, an in-season change.
Inspired by nfelo's well-known QB-adjusted Elo model, but built from data
already in this project rather than nfelo's own (nfelo publishes outputs,
not a data feed, and its own site says it's "powered by the nflfastR
dataset" -- the same open-source data this project already ingests via
nflreadpy).

Plain team Elo (model/elo.py) has no notion of who's under center -- a
backup playing is invisible to it until enough results accumulate to move
the rating, which can take weeks. This looks at real, current-week
starter information (player_usage.py's passer_shares, the same "who
actually threw the ball last game" signal already used for the
simulator) against each QB's own career EPA/dropback (their skill
travels with them, same "player follows the role" philosophy as
rushing_shares/target_shares), and nudges win probability by the gap
between the current starter and the team's normal starter -- only when
there's a real, checkable roster change, and only when both QBs have
enough career attempts to trust their own rating.

MVP heuristic, not yet backtested: capped small and always paired with a
human-readable note, same shape as every other signal in predict.py.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.model.player_usage import passer_shares
from app.models import Play


def _qb_epa_rating(db: Session, player_id: str, before_season: int, before_week: int) -> tuple[float | None, int]:
    """(avg EPA/dropback, career pass attempts) for `player_id` across every
    team they've played for, strictly before the given season/week. None
    (with n) when there's no data at all; callers separately gate on a
    minimum attempt count before trusting the rating."""
    rows = (
        db.query(Play.epa)
        .filter(
            Play.passer_player_id == player_id,
            Play.play_type == "pass",
            Play.epa.isnot(None),
            (Play.season < before_season) | ((Play.season == before_season) & (Play.week < before_week)),
        )
        .all()
    )
    n = len(rows)
    if n == 0:
        return None, 0
    return sum(epa for (epa,) in rows) / n, n


def _primary_qb(db: Session, team: str, season: int, before_week: int) -> str | None:
    """The player_id with the plurality of `team`'s pass attempts THIS
    season strictly before `before_week` -- who's actually been the
    starter, not just who's playing right now. None with no attempts yet
    this season (too early to know)."""
    row = (
        db.query(Play.passer_player_id, func.count().label("attempts"))
        .filter(
            Play.posteam == team,
            Play.play_type == "pass",
            Play.season == season,
            Play.week < before_week,
            Play.passer_player_id.isnot(None),
        )
        .group_by(Play.passer_player_id)
        .order_by(func.count().desc())
        .first()
    )
    return row.passer_player_id if row else None


def qb_elo_adjustment(db: Session, team: str, season: int, before_week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team`. delta is 0.0 (note=None)
    whenever there isn't a real, checkable QB change to explain: no season
    data yet, the current starter IS the season's primary starter, or
    either QB's own career sample is too thin (settings.qb_elo_min_attempts)
    to trust their EPA rating -- never guessed from a handful of plays."""
    current_shares = passer_shares(db, team, season, before_week)
    if not current_shares:
        return 0.0, None
    current_starter = max(current_shares, key=current_shares.get)

    primary_qb = _primary_qb(db, team, season, before_week)
    if primary_qb is None or primary_qb == current_starter:
        return 0.0, None

    current_rating, current_n = _qb_epa_rating(db, current_starter, season, before_week)
    primary_rating, primary_n = _qb_epa_rating(db, primary_qb, season, before_week)
    if current_rating is None or primary_rating is None:
        return 0.0, None
    if current_n < settings.qb_elo_min_attempts or primary_n < settings.qb_elo_min_attempts:
        return 0.0, None

    gap = current_rating - primary_rating
    raw_delta = (gap / settings.qb_elo_epa_scale) * settings.qb_elo_max_adjustment
    delta = max(-settings.qb_elo_max_adjustment, min(settings.qb_elo_max_adjustment, raw_delta))

    direction = "an upgrade over" if delta > 0 else "a downgrade from"
    note = (
        f"{team} is starting a QB other than its season-long starter this week -- "
        f"{direction} its usual starter by EPA/dropback over {current_n} vs {primary_n} career attempts."
    )
    return delta, note
