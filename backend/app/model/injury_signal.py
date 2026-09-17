"""Small, bounded win-probability nudge from a team's own current injury
report -- the structural gap flagged and left open earlier (live
predictions previously never queried the Injury table at all; injuries only
reached the pipeline indirectly through the market line, and directly only
in the post-hoc narrative/what-if tools, never the prediction itself).

Reuses gameplan.py's own severity vocabulary (_SEVERITY_RANK) rather than
inventing a second one, and only counts starters (is_starter, itself a real
measured-snap-share signal -- see ingestion/espn_injuries.py) toward the
burden: a banged-up bench is noise, a banged-up starter is signal, which
matches how every post-game miss investigated so far already reasoned about
injuries informally (e.g. "starting RB on IR" vs "one questionable WR").

MVP heuristic in the same spirit as recent_form.py and stat_rankings.py:
capped small and always paired with a human-readable note, since it isn't
backtested yet.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Injury

# Higher = more severe. Mirrors gameplan.py's _SEVERITY_RANK vocabulary
# (same Injury.report_status values), converted from an ordinal sort key
# into a numeric burden weight.
SEVERITY_WEIGHT = {
    "Injured Reserve": 1.0,
    "Out": 1.0,
    "Doubtful": 0.75,
    "Questionable": 0.25,
    "Probable": 0.1,
}
DEFAULT_SEVERITY_WEIGHT = 0.25  # an unrecognized status string -- treat like "Questionable", never ignore or overweight

# Total starter burden at/above this gets the full capped adjustment (e.g.
# two Out-level starters, or one IR starter plus a Doubtful one) -- more
# than that doesn't add further signal, so one team fielding a dozen
# dinged-up backups can't swamp the comparison.
INJURY_BURDEN_CAP = 2.0


def injury_adjustment(db: Session, team: str, season: int, week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team` based on its own current
    starters' injury report -- never the opponent's (that's handled by the
    caller combining both teams' deltas). delta is 0.0 (with note=None)
    when no injury report has been ingested yet for this team/week -- never
    a guessed value. A team with a report but no injured starters also gets
    delta=0.0, with a note that says so explicitly rather than looking the
    same as missing data."""
    team_rows = (
        db.query(Injury).filter(Injury.team_abbr == team, Injury.season == season, Injury.week == week).all()
    )
    if not team_rows:
        return 0.0, None

    starter_rows = [r for r in team_rows if r.is_starter]
    if not starter_rows:
        return 0.0, f"{team} has no starters listed as injured this week."

    burden = sum(SEVERITY_WEIGHT.get(r.report_status, DEFAULT_SEVERITY_WEIGHT) for r in starter_rows)
    net_fraction = -min(burden / INJURY_BURDEN_CAP, 1.0)  # -1.0 (max burden) .. 0.0 (no burden)
    delta = net_fraction * settings.injury_max_adjustment

    worst_first = sorted(starter_rows, key=lambda r: SEVERITY_WEIGHT.get(r.report_status, DEFAULT_SEVERITY_WEIGHT), reverse=True)
    names = ", ".join(f"{r.player_name} ({r.report_status})" for r in worst_first[:3])
    note = f"{team} has {len(starter_rows)} starter(s) banged up: {names}."
    return delta, note
