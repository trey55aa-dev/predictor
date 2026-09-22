"""Season-to-date league-wide stat rankings, converted into a small, bounded
win-probability nudge from a team's overall standing against the rest of
the league so far this season.

Distinct from recent_form.py's signal, which only looks at a team's single
most recent game against one opponent. This looks at a team's whole body of
work this season across the same "core four" categories keys_to_victory.py
already established as predictive (turnover margin, rushing yards, passing
yards, 3rd-down%), plus points scored/allowed, ranked against every other
team that has played -- the same idea sites like StatMuse's team-rankings
pages surface, computed here from play-by-play already in this database
rather than scraped from an external site.

MVP heuristic in the same spirit as recent_form.py and weather_adjust.py:
capped small and always paired with a human-readable note, since it isn't
backtested yet.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.model.keys_to_victory import team_stats
from app.model.percentile import percentile
from app.models import Game, Play

# higher is better for every category except points_allowed.
HIGHER_IS_BETTER = {
    "rushing_yards": True,
    "passing_yards": True,
    "turnover_margin": True,
    "third_down_pct": True,
    "points_scored": True,
    "points_allowed": False,
}
RANKING_CATEGORIES = tuple(HIGHER_IS_BETTER)


def _final_games_before(db: Session, season: int, before_week: int) -> list[Game]:
    # Strictly before `before_week`, same anti-leakage convention as
    # recent_form.py's _most_recent_final_game: a team's own upcoming game
    # must never contribute to its own pregame ranking.
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


def league_stat_averages(db: Session, season: int, before_week: int) -> dict[str, dict[str, float]]:
    """{team_abbr: {category: season-to-date per-game average}} using only
    games strictly before `before_week` this season whose play-by-play has
    already been ingested. A team with zero such games is omitted entirely
    -- never given a guessed average. third_down_pct is averaged only over
    the games that actually had a 3rd-down attempt (team_stats returns None
    otherwise), so a game with none doesn't drag a team's average toward 0."""
    games = _final_games_before(db, season, before_week)

    sums: dict[str, dict[str, float]] = {}
    counts: dict[str, dict[str, int]] = {}

    def _add(team: str, category: str, value: float | None) -> None:
        if value is None:
            return
        sums.setdefault(team, {c: 0.0 for c in RANKING_CATEGORIES})
        counts.setdefault(team, {c: 0 for c in RANKING_CATEGORIES})
        sums[team][category] += value
        counts[team][category] += 1

    for game in games:
        plays = db.query(Play).filter(Play.game_id == game.game_id).all()
        if not plays:
            continue  # play-by-play not ingested yet for this game -- skip, don't guess

        home_stats = team_stats(plays, game.home_team)
        away_stats = team_stats(plays, game.away_team)

        for team, own, opp, own_score, opp_score in (
            (game.home_team, home_stats, away_stats, game.home_score, game.away_score),
            (game.away_team, away_stats, home_stats, game.away_score, game.home_score),
        ):
            _add(team, "rushing_yards", own["rushing_yards"])
            _add(team, "passing_yards", own["passing_yards"])
            _add(team, "turnover_margin", opp["turnovers"] - own["turnovers"])
            _add(team, "third_down_pct", own["third_down_pct"])
            _add(team, "points_scored", own_score)
            _add(team, "points_allowed", opp_score)

    return {
        team: {
            category: (sums[team][category] / counts[team][category])
            for category in RANKING_CATEGORIES
            if counts[team][category] > 0
        }
        for team in sums
    }


def stat_ranking_adjustment(db: Session, team: str, season: int, before_week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team` based on its season-to-date
    rank across tracked stat categories against every other team that's
    played this season. delta is 0.0 (with note=None) when the team has no
    graded, play-by-play-ingested game yet this season, or fewer than 2
    teams league-wide do -- there's no meaningful ranking with less than
    that, and this signal never guesses one."""
    averages = league_stat_averages(db, season, before_week)
    if team not in averages or len(averages) < 2:
        return 0.0, None

    percentiles = []
    for category in RANKING_CATEGORIES:
        values = [stats[category] for stats in averages.values() if category in stats]
        if team not in [t for t in averages if category in averages[t]] or len(values) < 2:
            continue
        percentiles.append(percentile(averages[team][category], values, HIGHER_IS_BETTER[category]))

    if not percentiles:
        return 0.0, None

    composite = sum(percentiles) / len(percentiles)  # 0.0 worst .. 1.0 best, 0.5 = league average
    net_fraction = (composite - 0.5) * 2  # -1.0 .. +1.0
    delta = net_fraction * settings.stat_rank_max_adjustment

    percentile_rank = round((1 - composite) * len(averages)) + 1  # 1 = best team in the league
    note = f"{team} ranks about {percentile_rank}/{len(averages)} in the league across tracked stat categories this season."
    return delta, note
