"""Widens prediction uncertainty for a team with a recently-logged coaching
or play-calling change -- deliberately never moves home_win_prob in either
direction.

There's no automated source for "who actually called plays this game" --
it isn't in any ingested feed -- so changes are logged manually as they're
learned (see app.cli log-coaching-change), real ground truth rather than a
guess. But *how* a change should move a prediction has no honest prior
either: the real examples logged against this model so far cut both ways
(Denver's first game with a new play-caller and Dallas's new defensive
coordinator both coincided with worse performance; the Giants' entirely
rebuilt, physical-identity staff coincided with a win). Asserting a
direction from that would be fabricating a signal the evidence doesn't
support -- exactly what this project's other signals are careful never to
do. Widening the confidence range instead is the statistically honest
response to "we have less information about how this staff performs than
usual," without pretending to know if that's good or bad news.

Once real games are graded under the new staff, recent_form_adjustment and
stat_ranking_adjustment pick up its actual on-field performance on their
own -- this signal is only meant to cover the blind window before that
happens.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.models import CoachingChange

# A change logged more than this many weeks before the target week is
# treated as already absorbed -- by then recent_form/stat_rankings have
# real graded games under the new staff to go on instead.
RECENCY_WINDOW_WEEKS = 4


def recent_coaching_changes(db: Session, team: str, season: int, week: int) -> list[CoachingChange]:
    """Changes logged for `team` this season, effective at or before `week`
    and within RECENCY_WINDOW_WEEKS of it."""
    return (
        db.query(CoachingChange)
        .filter(
            CoachingChange.team_abbr == team,
            CoachingChange.season == season,
            CoachingChange.effective_week <= week,
            CoachingChange.effective_week > week - RECENCY_WINDOW_WEEKS,
        )
        .order_by(CoachingChange.effective_week.desc())
        .all()
    )


def coaching_change_uncertainty(db: Session, team: str, season: int, week: int) -> tuple[float, str | None]:
    """Returns (extra_range_width_points, note). 0.0/None when nothing's
    been logged for this team within the recency window."""
    changes = recent_coaching_changes(db, team, season, week)
    if not changes:
        return 0.0, None

    widening = min(
        len(changes) * settings.coaching_change_uncertainty_per_change,
        settings.coaching_change_uncertainty_cap,
    )
    roles = ", ".join(f"{c.role.replace('_', ' ')}: {c.person_name}" for c in changes)
    note = f"{team} has a recent coaching change ({roles}) -- wider than usual uncertainty until it's established."
    return widening, note
