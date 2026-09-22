"""Red-zone (inside the opponent's 20-yard line) trip and touchdown-rate
stats, both a team's own offense and what its defense allowed. A "trip" is
a possession, not a play: 4 plays inside the 20 on one drive is one trip,
not four -- grouping needs PlayDriveContext's drive number (see that
model's docstring for why it's a separate table from `plays`). Available
for the in-progress season too, since `drive` comes from the base pbp
feed, not participation (unlike man_zone_stats.py's data).

Same MVP-signal shape as efficiency_stats.py/pass_defense_stats.py for the
predictive piece: a small, capped, percentile-ranked nudge, not yet
backtested. red_zone_td_rate (offense, higher is better) and
red_zone_td_rate_allowed (defense, lower is better) both feed it.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.model.percentile import percentile
from app.model.play_lookups import drive_by_play_key
from app.models import Game, Play

RED_ZONE_YARDLINE_100 = 20

ADJUSTMENT_CATEGORIES = ("red_zone_td_rate", "red_zone_td_rate_allowed")
HIGHER_IS_BETTER = {"red_zone_td_rate": True, "red_zone_td_rate_allowed": False}


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


def _red_zone_trips(plays: list[Play], team: str, side: str, drives: dict[str, int]) -> dict[int, list[Play]]:
    """Groups `team`'s plays on `side` ('offense'=posteam, 'defense'=defteam)
    by drive, keeping only drives that actually reached the red zone."""
    by_drive: dict[int, list[Play]] = {}
    for p in plays:
        team_field = p.posteam if side == "offense" else p.defteam
        if team_field != team:
            continue
        drive = drives.get(p.play_key)
        if drive is None:
            continue
        by_drive.setdefault(drive, []).append(p)

    return {
        drive: drive_plays
        for drive, drive_plays in by_drive.items()
        if any((p.yardline_100 or 999) <= RED_ZONE_YARDLINE_100 for p in drive_plays)
    }


def team_red_zone_stats(plays: list[Play], team: str, drives: dict[str, int] | None = None) -> dict:
    """Real red-zone trip/TD stats for `team`: red_zone_trips/tds/td_rate
    for its own offense, and the _allowed versions for its defense. Every
    rate is None (never 0 or guessed) when there were no red zone trips.
    `drives` defaults to empty, which just means no trips are found at all
    -- the same "no data, no guess" behavior as every other category here."""
    drives = drives or {}

    def _summarize(side: str) -> dict:
        trip_groups = _red_zone_trips(plays, team, side, drives)
        n_trips = len(trip_groups)
        n_tds = sum(1 for drive_plays in trip_groups.values() if any(p.touchdown for p in drive_plays))
        return {"trips": n_trips, "tds": n_tds, "td_rate": (n_tds / n_trips) if n_trips else None}

    offense = _summarize("offense")
    defense = _summarize("defense")
    return {
        "red_zone_trips": offense["trips"],
        "red_zone_tds": offense["tds"],
        "red_zone_td_rate": offense["td_rate"],
        "red_zone_trips_allowed": defense["trips"],
        "red_zone_tds_allowed": defense["tds"],
        "red_zone_td_rate_allowed": defense["td_rate"],
    }


def league_red_zone_averages(db: Session, season: int, before_week: int) -> dict[str, dict[str, float]]:
    """{team_abbr: {category: season-to-date per-game average}} for
    red_zone_td_rate/red_zone_td_rate_allowed. Same anti-leakage and
    no-guessing rules as league_efficiency_averages."""
    games = _final_games_before(db, season, before_week)

    sums: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}

    def _add(team: str, category: str, value: float | None) -> None:
        if value is None:
            return
        sums.setdefault(team, {c: 0.0 for c in ADJUSTMENT_CATEGORIES})
        counts.setdefault(team, {c: 0 for c in ADJUSTMENT_CATEGORIES})
        sums[team][category] += value
        counts[team][category] += 1

    for game in games:
        plays = db.query(Play).filter(Play.game_id == game.game_id).all()
        if not plays:
            continue  # play-by-play not ingested yet for this game -- skip, don't guess

        drives = drive_by_play_key(db, game.game_id)
        for team in (game.home_team, game.away_team):
            stats = team_red_zone_stats(plays, team, drives)
            for category in ADJUSTMENT_CATEGORIES:
                _add(team, category, stats[category])

    return {
        team: {
            category: (sums[team][category] / counts[team][category])
            for category in ADJUSTMENT_CATEGORIES
            if counts[team][category] > 0
        }
        for team in sums
    }


def red_zone_adjustment(db: Session, team: str, season: int, before_week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team` based on its season-to-date
    percentile rank on red-zone TD rate (offense) and red-zone TD rate
    allowed (defense), against every other team that's played this season.
    delta is 0.0 (note=None) with no qualifying data."""
    averages = league_red_zone_averages(db, season, before_week)
    if team not in averages or len(averages) < 2:
        return 0.0, None

    percentiles = []
    for category in ADJUSTMENT_CATEGORIES:
        values = [stats[category] for stats in averages.values() if category in stats]
        if category not in averages[team] or len(values) < 2:
            continue
        percentiles.append(percentile(averages[team][category], values, HIGHER_IS_BETTER[category]))

    if not percentiles:
        return 0.0, None

    composite = sum(percentiles) / len(percentiles)  # 0.0 worst .. 1.0 best, 0.5 = league average
    net_fraction = (composite - 0.5) * 2  # -1.0 .. +1.0
    delta = net_fraction * settings.red_zone_max_adjustment

    percentile_rank = round((1 - composite) * len(averages)) + 1  # 1 = best red-zone team in the league
    note = f"{team} ranks about {percentile_rank}/{len(averages)} in the league on red-zone execution this season."
    return delta, note
