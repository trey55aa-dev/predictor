"""League-wide (not team-specific) weekly context stats, computed live from
plays already ingested. Distinct from stat_rankings.py/efficiency_stats.py,
which rank one team against the league -- this describes the league itself,
e.g. "how pass-heavy was week 4 across every game."
"""

from sqlalchemy.orm import Session

from app.models import Play


def league_pass_rate_by_week(db: Session, season: int) -> dict[int, float]:
    """{week: pass_rate} across every ingested run/pass play in `season`,
    every team combined. A week with zero ingested run/pass plays is
    omitted entirely -- never given a guessed rate."""
    rows = (
        db.query(Play.week, Play.play_type)
        .filter(Play.season == season, Play.play_type.in_(["run", "pass"]))
        .all()
    )

    pass_counts: dict[int, int] = {}
    total_counts: dict[int, int] = {}
    for week, play_type in rows:
        total_counts[week] = total_counts.get(week, 0) + 1
        if play_type == "pass":
            pass_counts[week] = pass_counts.get(week, 0) + 1

    return {
        week: pass_counts.get(week, 0) / total_counts[week]
        for week in total_counts
    }
