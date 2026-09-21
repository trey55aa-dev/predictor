"""Season-to-date EPA/pressure efficiency splits -- EPA per dropback, EPA
per rush attempt, EPA per target, and pressure rate allowed per dropback --
converted into a small, bounded win-probability nudge. Same MVP-signal
shape as stat_rankings.py and recent_form.py, and deliberately similar in
structure: a team's percentile rank across these categories against every
other team that's played this season so far.

"Dropback" follows this codebase's existing convention (keys_to_victory.py's
team_stats): every Play row with play_type == "pass", which under
nflverse's own convention includes sacks (a sacked dropback is still
recorded as a pass play) but not pure QB scrambles (recorded as
play_type == "run", so out of scope for this simplified split). "Target"
further narrows to dropbacks with a recorded receiver_player_id, which
excludes sacks (no receiver) and any throwaway nflverse didn't attribute to
a receiver.

No new ingestion and no new columns: epa, was_pressure, play_type, and
receiver_player_id are all already on the `plays` table (was_pressure comes
from nflverse's participation data, ingested since plays.py first shipped).
Computed live from Play rows already ingested rather than persisted to a
new column or table -- the same choice stat_rankings.py made, for the same
reason: this project has no migration tooling (schema is created via
Base.metadata.create_all, which never alters an existing table), so adding
a column to the already-populated production `plays` table would risk
crashing the live app the moment it deploys.

MVP heuristic, not yet backtested: capped small and always paired with a
human-readable note.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.model.stat_rankings import percentile
from app.models import Game, Play

# pressure_rate_allowed: lower is better for the offense being pressured.
# Every other category: higher is better.
HIGHER_IS_BETTER = {
    "epa_per_dropback": True,
    "epa_per_rush": True,
    "epa_per_target": True,
    "pressure_rate_allowed": False,
}
EFFICIENCY_CATEGORIES = tuple(HIGHER_IS_BETTER)


def _final_games_before(db: Session, season: int, before_week: int) -> list[Game]:
    # Strictly before `before_week`, same anti-leakage convention as
    # stat_rankings.py's _final_games_before.
    return (
        db.query(Game)
        .filter(
            Game.season == season,
            Game.week < before_week,
            Game.game_type == "REG",
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
        )
        .all()
    )


def team_efficiency_stats(plays: list[Play], team: str) -> dict:
    """Per-game efficiency splits for `team`'s own offensive plays.
    pressure_rate_allowed is scoped to `team`'s own dropbacks (was it
    `team` getting pressured), and only over dropbacks where participation
    data actually reported a pressure reading -- a dropback with
    was_pressure=None (participation coverage is partial before 2023) is
    excluded from that rate rather than silently counted as clean."""
    dropbacks = [p for p in plays if p.posteam == team and p.play_type == "pass"]
    rushes = [p for p in plays if p.posteam == team and p.play_type == "run"]
    targets = [p for p in dropbacks if p.receiver_player_id is not None]
    dropbacks_with_pressure_data = [p for p in dropbacks if p.was_pressure is not None]
    pressured = [p for p in dropbacks_with_pressure_data if p.was_pressure]

    def _avg_epa(rows: list[Play]) -> float | None:
        values = [p.epa for p in rows if p.epa is not None]
        return (sum(values) / len(values)) if values else None

    return {
        "epa_per_dropback": _avg_epa(dropbacks),
        "epa_per_rush": _avg_epa(rushes),
        "epa_per_target": _avg_epa(targets),
        "pressure_rate_allowed": (
            (len(pressured) / len(dropbacks_with_pressure_data)) if dropbacks_with_pressure_data else None
        ),
    }


def league_efficiency_averages(db: Session, season: int, before_week: int) -> dict[str, dict[str, float]]:
    """{team_abbr: {category: season-to-date per-game average}} using only
    games strictly before `before_week` this season whose play-by-play has
    already been ingested. A team with zero such games, or a category with
    zero qualifying plays across all of them, is omitted entirely -- never
    given a guessed average."""
    games = _final_games_before(db, season, before_week)

    sums: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}

    def _add(team: str, category: str, value: float | None) -> None:
        if value is None:
            return
        sums.setdefault(team, {c: 0.0 for c in EFFICIENCY_CATEGORIES})
        counts.setdefault(team, {c: 0 for c in EFFICIENCY_CATEGORIES})
        sums[team][category] += value
        counts[team][category] += 1

    for game in games:
        plays = db.query(Play).filter(Play.game_id == game.game_id).all()
        if not plays:
            continue  # play-by-play not ingested yet for this game -- skip, don't guess

        for team in (game.home_team, game.away_team):
            stats = team_efficiency_stats(plays, team)
            for category in EFFICIENCY_CATEGORIES:
                _add(team, category, stats[category])

    return {
        team: {
            category: (sums[team][category] / counts[team][category])
            for category in EFFICIENCY_CATEGORIES
            if counts[team][category] > 0
        }
        for team in sums
    }


def efficiency_adjustment(db: Session, team: str, season: int, before_week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team` based on its season-to-date
    percentile rank across EPA/pressure efficiency categories against every
    other team that's played this season. delta is 0.0 (with note=None)
    when the team has no graded, play-by-play-ingested game yet this
    season, or fewer than 2 teams league-wide do."""
    averages = league_efficiency_averages(db, season, before_week)
    if team not in averages or len(averages) < 2:
        return 0.0, None

    percentiles = []
    for category in EFFICIENCY_CATEGORIES:
        values = [stats[category] for stats in averages.values() if category in stats]
        if category not in averages[team] or len(values) < 2:
            continue
        percentiles.append(percentile(averages[team][category], values, HIGHER_IS_BETTER[category]))

    if not percentiles:
        return 0.0, None

    composite = sum(percentiles) / len(percentiles)  # 0.0 worst .. 1.0 best, 0.5 = league average
    net_fraction = (composite - 0.5) * 2  # -1.0 .. +1.0
    delta = net_fraction * settings.efficiency_max_adjustment

    percentile_rank = round((1 - composite) * len(averages)) + 1  # 1 = best team in the league
    note = f"{team} ranks about {percentile_rank}/{len(averages)} in the league on EPA/pressure efficiency this season."
    return delta, note
