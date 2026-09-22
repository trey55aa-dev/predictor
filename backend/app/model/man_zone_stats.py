"""Man vs. zone coverage rate, both what a team's OFFENSE faced and what
its DEFENSE played -- from nflverse's participation data, already sitting
on Play.man_zone (see ingestion/plays.py).

Historical seasons only. Participation coverage is 2021+ partial, 2023+
near-complete -- and NOT available for the in-progress season at all:
ingestion/current_plays.py doesn't ingest participation (confirmed live:
load_participation(seasons=[2026]) errors, see that module's own
docstring), so every man_zone value is None for this season's games right
now. That's the same real gap already flagged for
efficiency_stats.py's pressure_rate_allowed.

Deliberately informational only (surfaced in the post-game breakdown),
not wired into predict.py: a signal that's guaranteed 0.0 for every game
this season until an external feed catches up is dead weight in the
prediction pipeline, not a "small unbacktested nudge" the way the other
MVP signals are -- those at least have real data for the season being
predicted.
"""

from app.models import Play

MAN_COVERAGE = "MAN_COVERAGE"
ZONE_COVERAGE = "ZONE_COVERAGE"


def team_man_zone_stats(plays: list[Play], team: str) -> dict:
    """man/zone rate `team`'s OFFENSE faced (posteam == team) and the rate
    `team`'s DEFENSE played (defteam == team) in this game. None (never
    guessed) when no coverage data is available for any of `team`'s plays."""
    faced = [p.man_zone for p in plays if p.posteam == team and p.man_zone in (MAN_COVERAGE, ZONE_COVERAGE)]
    played = [p.man_zone for p in plays if p.defteam == team and p.man_zone in (MAN_COVERAGE, ZONE_COVERAGE)]

    def _man_rate(values: list[str]) -> float | None:
        return (sum(1 for v in values if v == MAN_COVERAGE) / len(values)) if values else None

    man_rate_faced = _man_rate(faced)
    man_rate_played = _man_rate(played)

    return {
        "man_rate_faced": man_rate_faced,
        "zone_rate_faced": (1 - man_rate_faced) if man_rate_faced is not None else None,
        "man_rate_played": man_rate_played,
        "zone_rate_played": (1 - man_rate_played) if man_rate_played is not None else None,
    }
