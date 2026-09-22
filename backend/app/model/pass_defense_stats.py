"""Team-level pass-defense "Allowed" stats -- what a defense gave up
through the air, computed from real plays. Explicitly team-level, not
per-defender: nflverse has no public per-play primary-coverage-defender
attribution (that's proprietary charting data, e.g. PFF), so anything
implying a specific cornerback's numbers (alignment splits, cushion,
routes defended, fantasy points per cover snap) isn't buildable here and
isn't attempted. What *is* real and attributable at the team level:
receptions/targets/yards/TDs allowed, catch rate, YAC, average depth of
target, explosive-reception tiers, a computed passer rating allowed, and
pass break-ups (nflverse's pass_defense_1_player_id is real per-player
attribution, rolled up to a team total here).

"Target" here matches this codebase's existing convention (see
efficiency_stats.py): a pass play (not a sack) with a recorded
receiver_player_id. "Attempt" (used only for passer_rating_allowed, which
has its own real-NFL denominator) is broader -- every non-sack pass play
against the defense, targeted or not, matching how the real passer-rating
stat is defined.

Depends on PlayCoverageStat (complete_pass, yards_after_catch, pass-defense
attribution) and PlayAdvancedStat (air_yards) -- see those models'
docstrings for why they're separate tables from `plays`.

Same MVP-signal shape as efficiency_stats.py/stat_rankings.py for the
predictive piece: a small, capped, percentile-ranked nudge over a handful
of headline categories with an unambiguous direction (lower allowed is
better defense), not yet backtested. The full stat set below is also
exposed as informational per-game data (see keys_to_victory.py's
build_game_breakdown), independent of which categories feed the nudge.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.model.percentile import percentile
from app.model.play_lookups import advanced_stats_by_play_key, coverage_stats_by_play_key
from app.models import Game, Play, PlayAdvancedStat, PlayCoverageStat

EXPLOSIVE_RECEPTION_THRESHOLDS = (10, 30, 40, 50)
EXPLOSIVE_RECEPTION_STANDARD_THRESHOLD = 20  # the single "explosive play" cutoff, separate from the tiers above

# Every one of these is "lower allowed = better defense", so ranking is
# inverted relative to efficiency_stats.py's offensive categories.
ADJUSTMENT_CATEGORIES = ("catch_rate_allowed", "yards_per_target_allowed", "td_rate_allowed", "passer_rating_allowed")
HIGHER_IS_BETTER = dict.fromkeys(ADJUSTMENT_CATEGORIES, False)


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


def _passer_rating(completions: int, attempts: int, yards: float, touchdowns: int, interceptions: int) -> float | None:
    """Standard NFL passer rating formula. None (never guessed) if there
    were no attempts to rate."""
    if attempts == 0:
        return None

    def _clamp(x: float) -> float:
        return max(0.0, min(2.375, x))

    a = _clamp(((completions / attempts) - 0.3) * 5)
    b = _clamp(((yards / attempts) - 3) * 0.25)
    c = _clamp((touchdowns / attempts) * 20)
    d = _clamp(2.375 - (interceptions / attempts * 25))
    return ((a + b + c + d) / 6) * 100


def team_pass_defense_stats(
    plays: list[Play],
    team: str,
    advanced: dict[str, PlayAdvancedStat] | None = None,
    coverage: dict[str, PlayCoverageStat] | None = None,
) -> dict:
    """Real, computable pass-defense-allowed stats for `team`'s defense in
    one game. Every rate is None (never 0 or guessed) when its denominator
    is zero. advanced/coverage default to empty, which just means every
    field that needs them (aDOT, YAC, catch rate, passer rating, etc.)
    comes back None -- the same "no data, no guess" behavior as every
    other category here."""
    advanced = advanced or {}
    coverage = coverage or {}

    pass_attempts = [p for p in plays if p.defteam == team and p.play_type == "pass" and not p.sack]
    targets = [p for p in pass_attempts if p.receiver_player_id is not None]
    receptions = [p for p in targets if coverage.get(p.play_key) and coverage[p.play_key].complete_pass]
    touchdowns = [p for p in pass_attempts if p.pass_touchdown]
    interceptions = [p for p in pass_attempts if p.interception]
    pass_break_ups = [p for p in pass_attempts if coverage.get(p.play_key) and coverage[p.play_key].pass_defense_1_player_id]

    yards_allowed = sum(p.yards_gained or 0 for p in receptions)
    yac_values = [
        coverage[p.play_key].yards_after_catch
        for p in receptions
        if p.play_key in coverage and coverage[p.play_key].yards_after_catch is not None
    ]
    air_yards_values = [
        advanced[p.play_key].air_yards for p in targets if p.play_key in advanced and advanced[p.play_key].air_yards is not None
    ]

    n_targets = len(targets)
    n_receptions = len(receptions)

    explosive_tiers = {}
    for threshold in EXPLOSIVE_RECEPTION_THRESHOLDS:
        count = sum(1 for p in receptions if (p.yards_gained or 0) >= threshold)
        explosive_tiers[f"receptions_allowed_{threshold}plus_yards"] = count
        explosive_tiers[f"receptions_allowed_{threshold}plus_yards_rate"] = (
            (count / n_receptions) if n_receptions else None
        )

    explosive_count = sum(1 for p in receptions if (p.yards_gained or 0) >= EXPLOSIVE_RECEPTION_STANDARD_THRESHOLD)

    return {
        "targets_allowed": n_targets,
        "receptions_allowed": n_receptions,
        "yards_allowed": yards_allowed,
        "touchdowns_allowed": len(touchdowns),
        "interceptions": len(interceptions),
        "pass_break_ups": len(pass_break_ups),
        "catch_rate_allowed": (n_receptions / n_targets) if n_targets else None,
        "target_rate_allowed": (n_targets / len(pass_attempts)) if pass_attempts else None,
        "td_rate_allowed": (len(touchdowns) / n_targets) if n_targets else None,
        "yards_per_target_allowed": (yards_allowed / n_targets) if n_targets else None,
        "yards_per_reception_allowed": (yards_allowed / n_receptions) if n_receptions else None,
        "yards_after_catch_allowed": sum(yac_values) if yac_values else None,
        "yards_after_catch_allowed_per_reception": (sum(yac_values) / len(yac_values)) if yac_values else None,
        "average_depth_of_target_allowed": (sum(air_yards_values) / len(air_yards_values)) if air_yards_values else None,
        "explosive_receptions_allowed": explosive_count,
        "explosive_reception_rate_allowed": (explosive_count / n_receptions) if n_receptions else None,
        "passer_rating_allowed": _passer_rating(
            n_receptions, len(pass_attempts), yards_allowed, len(touchdowns), len(interceptions)
        ),
        **explosive_tiers,
    }


def league_pass_defense_averages(db: Session, season: int, before_week: int) -> dict[str, dict[str, float]]:
    """{team_abbr: {category: season-to-date per-game average}} for the
    categories in ADJUSTMENT_CATEGORIES only (the full per-game stat dict
    has too many derived/overlapping fields to average meaningfully across
    games -- rate categories average cleanly, so only those are ranked).
    Same anti-leakage and no-guessing rules as league_efficiency_averages."""
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

        advanced = advanced_stats_by_play_key(db, game.game_id)
        coverage = coverage_stats_by_play_key(db, game.game_id)
        for team in (game.home_team, game.away_team):
            stats = team_pass_defense_stats(plays, team, advanced, coverage)
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


def pass_defense_adjustment(db: Session, team: str, season: int, before_week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team` based on its own defense's
    season-to-date percentile rank on catch rate/yards-per-target/TD-rate/
    passer-rating allowed, against every other team that's played this
    season. Lower allowed is better, so a team giving up less than the
    league gets a positive delta. delta is 0.0 (note=None) with no
    qualifying data, same as efficiency_stats.py's equivalent."""
    averages = league_pass_defense_averages(db, season, before_week)
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
    delta = net_fraction * settings.pass_defense_max_adjustment

    percentile_rank = round((1 - composite) * len(averages)) + 1  # 1 = best pass defense in the league
    note = f"{team}'s pass defense ranks about {percentile_rank}/{len(averages)} in the league this season."
    return delta, note
