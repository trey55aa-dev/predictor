"""Season-to-date team stat rankings, a small nudge to predict_game()
distinct from Elo (whose rating already reflects *who won*, not the
underlying box-score volume) and from recent_form.py's last-game-only
signal. Ranks every team that has at least one final, play-ingested game
this season across the same "core four" keys already validated in
keys_to_victory.py (turnover margin, rushing yards, passing yards,
3rd-down%), then nudges win probability by the percentile gap between the
two teams' season averages.

Real per-game aggregates computed from real Play rows only -- never
estimated or backfilled. A team with no ingested games yet this season, or
a league with fewer than 4 ranked teams to compare against, contributes no
signal (delta 0.0) rather than a guessed rank.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Game, Play

STAT_KEYS = ("turnover_margin_per_game", "rushing_yards_per_game", "passing_yards_per_game", "third_down_pct")

MIN_RANKED_TEAMS = 4


def _teams_with_final_games(db: Session, season: int, before_week: int) -> set[str]:
    games = (
        db.query(Game)
        .filter(Game.season == season, Game.week < before_week, Game.game_type == "REG", Game.status == "final")
        .all()
    )
    teams: set[str] = set()
    for game in games:
        teams.add(game.home_team)
        teams.add(game.away_team)
    return teams


def _season_stats(db: Session, team: str, season: int, before_week: int) -> dict | None:
    games_played = (
        db.query(Game)
        .filter(
            Game.season == season,
            Game.week < before_week,
            Game.game_type == "REG",
            Game.status == "final",
            (Game.home_team == team) | (Game.away_team == team),
        )
        .all()
    )
    if not games_played:
        return None

    game_ids = [game.game_id for game in games_played]
    plays = db.query(Play).filter(Play.game_id.in_(game_ids)).all()
    if not plays:
        return None

    n_games = len(games_played)
    rushing_yards = sum(p.yards_gained or 0 for p in plays if p.posteam == team and p.play_type == "run")
    passing_yards = sum(
        p.yards_gained or 0 for p in plays if p.posteam == team and p.play_type == "pass" and not p.sack
    )
    # defteam already identifies the defense on each play, so takeaways for
    # `team` can be counted directly from plays across any number of games --
    # no need to pair up each game's two sides by hand.
    giveaways = sum(1 for p in plays if p.posteam == team and (p.interception or p.fumble_lost))
    takeaways = sum(1 for p in plays if p.defteam == team and (p.interception or p.fumble_lost))
    third_downs = [p for p in plays if p.posteam == team and p.down == 3]
    third_conversions = sum(1 for p in third_downs if (p.yards_gained or 0) >= (p.ydstogo or 999))

    return {
        "rushing_yards_per_game": rushing_yards / n_games,
        "passing_yards_per_game": passing_yards / n_games,
        "turnover_margin_per_game": (takeaways - giveaways) / n_games,
        "third_down_pct": (third_conversions / len(third_downs)) if third_downs else None,
    }


def _percentile(value: float, all_values: list[float]) -> float:
    """1.0 = best (highest) in the league, 0.0 = worst."""
    at_or_below = sum(1 for v in all_values if v <= value)
    return at_or_below / len(all_values)


def _composite_percentile(team: str, league_stats: dict[str, dict]) -> float:
    percentiles = []
    for key in STAT_KEYS:
        values = [s[key] for s in league_stats.values() if s[key] is not None]
        team_value = league_stats[team][key]
        if team_value is None or len(values) < MIN_RANKED_TEAMS:
            continue
        percentiles.append(_percentile(team_value, values))
    return sum(percentiles) / len(percentiles) if percentiles else 0.5


def stat_rank_adjustment(
    db: Session, home_team: str, away_team: str, season: int, before_week: int
) -> tuple[float, str | None]:
    """Returns (home_win_prob_delta, note). delta is 0.0 with note=None
    whenever either team has no ingested games yet this season, or fewer
    than MIN_RANKED_TEAMS teams league-wide can be ranked -- never a
    guessed rank."""
    league_teams = _teams_with_final_games(db, season, before_week)
    if len(league_teams) < MIN_RANKED_TEAMS:
        return 0.0, None

    league_stats = {}
    for team in league_teams:
        stats = _season_stats(db, team, season, before_week)
        if stats is not None:
            league_stats[team] = stats

    if home_team not in league_stats or away_team not in league_stats:
        return 0.0, None

    home_pct = _composite_percentile(home_team, league_stats)
    away_pct = _composite_percentile(away_team, league_stats)

    delta = (home_pct - away_pct) * settings.stat_rank_max_adjustment
    note = (
        f"{home_team} ranks in the {home_pct:.0%} percentile of the league's core-four stats "
        f"this season vs {away_team}'s {away_pct:.0%}."
    )
    return delta, note
