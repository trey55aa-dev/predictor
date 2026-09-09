"""Player-level yardage and touchdown projections. Mirrors model/scoring.py's
approach exactly: a player's own trailing rate blended with what the
opponent has recently allowed to that position, same season-boundary-
crossing logic as team_scoring_averages.
"""

import datetime as dt
import math

from sqlalchemy.orm import Session

from app.models import Game, PlayerGameStat, PlayerProjection, TeamRosterMembership

LOOKBACK_GAMES = 10
MIN_GAMES_TO_PROJECT = 2  # need at least a couple of real games to trust a rate
YARDAGE_STD_FRACTION = 0.55  # rough spread heuristic: +/-55% of the projected mean
SKILL_POSITIONS = ["QB", "RB", "WR", "TE", "FB"]
TOP_N_PER_TEAM = 8
MODEL_VERSION = "player-projection-v1"


def player_scoring_rates(db: Session, player_id: str, before_season: int, before_week: int) -> dict:
    games = (
        db.query(PlayerGameStat)
        .filter(
            PlayerGameStat.player_id == player_id,
            (PlayerGameStat.season < before_season)
            | ((PlayerGameStat.season == before_season) & (PlayerGameStat.week < before_week)),
        )
        .order_by(PlayerGameStat.season.desc(), PlayerGameStat.week.desc())
        .limit(LOOKBACK_GAMES)
        .all()
    )

    n = len(games)
    if n == 0:
        return {
            "sample_size": 0,
            "rushing_yards": 0.0,
            "receiving_yards": 0.0,
            "passing_yards": 0.0,
            "rushing_td_rate": 0.0,
            "receiving_td_rate": 0.0,
            "passing_td_rate": 0.0,
        }

    def avg(attr: str) -> float:
        return sum(getattr(g, attr) or 0.0 for g in games) / n

    return {
        "sample_size": n,
        "rushing_yards": avg("rushing_yards"),
        "receiving_yards": avg("receiving_yards"),
        "passing_yards": avg("passing_yards"),
        "rushing_td_rate": avg("rushing_tds"),
        "receiving_td_rate": avg("receiving_tds"),
        "passing_td_rate": avg("passing_tds"),
    }


def opponent_allowed_by_position(
    db: Session, opponent_team: str, position: str, before_season: int, before_week: int
) -> dict:
    """Average yards/TDs the opponent's defense has allowed to this position
    group over its own recent games (proxy: sum of that position's output by
    all opposing skill players who faced this defense, per game faced)."""
    # Find which games the opponent's defense played first, then pull only
    # that position's stat rows from those specific games -- NOT every
    # historical row for the position across all seasons (that unbounded
    # scan was the original, much slower version of this query).
    defense_games = (
        db.query(Game)
        .filter(
            (Game.home_team == opponent_team) | (Game.away_team == opponent_team),
            Game.home_score.isnot(None),
            (Game.season < before_season) | ((Game.season == before_season) & (Game.week < before_week)),
        )
        .order_by(Game.season.desc(), Game.week.desc())
        .limit(LOOKBACK_GAMES)
        .all()
    )
    game_ids = [g.game_id for g in defense_games]
    if not game_ids:
        return {"sample_size": 0, "rushing_yards": 0.0, "receiving_yards": 0.0, "passing_yards": 0.0}

    relevant = (
        db.query(PlayerGameStat)
        .filter(
            PlayerGameStat.position == position,
            PlayerGameStat.game_id.in_(game_ids),
            PlayerGameStat.team != opponent_team,
        )
        .all()
    )
    n_games = len(game_ids)

    def total(attr: str) -> float:
        return sum(getattr(g, attr) or 0.0 for g in relevant) / n_games

    return {
        "sample_size": n_games,
        "rushing_yards": total("rushing_yards"),
        "receiving_yards": total("receiving_yards"),
        "passing_yards": total("passing_yards"),
    }


def _blend(own: float, allowed: float, weight_own: float = 0.6) -> float:
    return weight_own * own + (1 - weight_own) * allowed


def _td_prob(rate: float) -> float:
    """Poisson approximation: turns a per-game TD rate into P(at least one)."""
    return 1 - math.exp(-max(rate, 0.0))


def project_player(db: Session, player_id: str, player_name: str, position: str, team: str, opponent: str, season: int, week: int) -> dict | None:
    own = player_scoring_rates(db, player_id, season, week)
    if own["sample_size"] < MIN_GAMES_TO_PROJECT:
        return None

    allowed = opponent_allowed_by_position(db, opponent, position, season, week)

    def project_yards(own_key: str) -> float:
        if allowed["sample_size"] == 0:
            raw = own[own_key]
        else:
            raw = _blend(own[own_key], allowed[own_key])
        return max(raw, 0.0)  # e.g. QB receiving yards is near-zero noise that can dip negative in the blend

    projected_rushing = project_yards("rushing_yards")
    projected_receiving = project_yards("receiving_yards")
    projected_passing = project_yards("passing_yards")

    rushing_td_prob = _td_prob(own["rushing_td_rate"])
    receiving_td_prob = _td_prob(own["receiving_td_rate"])
    passing_td_prob = _td_prob(own["passing_td_rate"])
    # Anytime TD = scored rushing OR receiving (independent-ish approximation).
    anytime_td_prob = 1 - (1 - rushing_td_prob) * (1 - receiving_td_prob)

    involvement = projected_rushing + projected_receiving + projected_passing * 0.3

    return {
        "player_id": player_id,
        "player_name": player_name,
        "position": position,
        "team": team,
        "opponent": opponent,
        "projected_rushing_yards": projected_rushing,
        "projected_receiving_yards": projected_receiving,
        "projected_passing_yards": projected_passing,
        "rushing_td_prob": rushing_td_prob,
        "receiving_td_prob": receiving_td_prob,
        "passing_td_prob": passing_td_prob,
        "anytime_td_prob": anytime_td_prob,
        "involvement": involvement,
        "sample_size": own["sample_size"],
    }


def _current_roster(db: Session, team: str, season: int) -> list[TeamRosterMembership]:
    """Real, current roster membership for this team+season -- NOT inferred
    from each player's last stat row.

    That was the original approach here, and it has a real blind spot: a
    player's "most recent team" by stat row only updates once they've
    actually played (and had stats ingested) for their new team, so a
    trade or a free-agent signing left them listed under their OLD team for
    the entire gap in between -- confirmed live: Kenneth Walker III, traded
    to KC for 2026, was still shown as a Seahawk here because his most
    recent PlayerGameStat row was still a 2025 Seattle game. Real roster
    data (TeamRosterMembership, the same table model/player_usage.py's
    simulator was already fixed to use for exactly this reason) is the
    source of truth for "who's on this team now" instead.
    """
    return (
        db.query(TeamRosterMembership)
        .filter(TeamRosterMembership.team == team, TeamRosterMembership.season == season)
        .filter(TeamRosterMembership.position.in_(SKILL_POSITIONS))
        .all()
    )


def project_game_players(db: Session, game: Game) -> dict:
    """Returns {'home': [...], 'away': [...]}, each a list of the team's
    most-involved skill players' projections for this matchup."""
    result = {}
    for side_team, opponent in ((game.home_team, game.away_team), (game.away_team, game.home_team)):
        roster = _current_roster(db, side_team, game.season)
        projections = []
        for player in roster:
            proj = project_player(
                db, player.player_id, player.player_name, player.position, side_team, opponent, game.season, game.week
            )
            if proj is not None:
                projections.append(proj)
        projections.sort(key=lambda p: p["involvement"], reverse=True)
        key = "home" if side_team == game.home_team else "away"
        result[key] = projections[:TOP_N_PER_TEAM]
    return result


def save_projections(db: Session, game: Game) -> int:
    """Computes and persists player projections for one game. Re-running for
    the same game replaces any not-yet-graded projections for it (same
    pattern as log_parlays) so re-projecting mid-week with fresher usage
    data doesn't pile up duplicates."""
    projections = project_game_players(db, game)
    now = dt.datetime.utcnow()

    db.query(PlayerProjection).filter(
        PlayerProjection.game_id == game.game_id, PlayerProjection.graded_at.is_(None)
    ).delete(synchronize_session=False)

    count = 0
    for side_projections in projections.values():
        for p in side_projections:
            db.add(
                PlayerProjection(
                    game_id=game.game_id,
                    player_id=p["player_id"],
                    player_name=p["player_name"],
                    position=p["position"],
                    team=p["team"],
                    opponent=p["opponent"],
                    season=game.season,
                    week=game.week,
                    model_version=MODEL_VERSION,
                    created_at=now,
                    projected_rushing_yards=p["projected_rushing_yards"],
                    projected_receiving_yards=p["projected_receiving_yards"],
                    projected_passing_yards=p["projected_passing_yards"],
                    rushing_td_prob=p["rushing_td_prob"],
                    receiving_td_prob=p["receiving_td_prob"],
                    passing_td_prob=p["passing_td_prob"],
                    anytime_td_prob=p["anytime_td_prob"],
                )
            )
            count += 1

    db.commit()
    return count


def project_week(db: Session, season: int, week: int) -> int:
    games = db.query(Game).filter(Game.season == season, Game.week == week, Game.game_type == "REG").all()
    total = 0
    for game in games:
        total += save_projections(db, game)
    return total


def grade_player_projections(db: Session, season: int, week: int) -> int:
    """Fills in actuals for ungraded projections once that week's real
    PlayerGameStat rows exist (ingest_player_stats must have run for this
    week already)."""
    projections = (
        db.query(PlayerProjection)
        .filter(PlayerProjection.season == season, PlayerProjection.week == week, PlayerProjection.graded_at.is_(None))
        .all()
    )

    graded = 0
    for proj in projections:
        actual = (
            db.query(PlayerGameStat)
            .filter(
                PlayerGameStat.player_id == proj.player_id,
                PlayerGameStat.season == season,
                PlayerGameStat.week == week,
            )
            .first()
        )
        if actual is None:
            continue  # player didn't record a stat line this week (DNP, etc.) -- can't grade yet

        proj.actual_rushing_yards = actual.rushing_yards
        proj.actual_receiving_yards = actual.receiving_yards
        proj.actual_passing_yards = actual.passing_yards
        proj.actual_tds = int((actual.rushing_tds or 0) + (actual.receiving_tds or 0) + (actual.passing_tds or 0))
        proj.graded_at = dt.datetime.utcnow()
        graded += 1

    db.commit()
    return graded


def player_projection_performance_summary(db: Session) -> dict:
    graded = db.query(PlayerProjection).filter(PlayerProjection.graded_at.isnot(None)).all()
    n = len(graded)
    if n == 0:
        return {"graded_projections": 0}

    yard_errors = []
    for p in graded:
        projected_total = p.projected_rushing_yards + p.projected_receiving_yards + p.projected_passing_yards
        actual_total = (p.actual_rushing_yards or 0) + (p.actual_receiving_yards or 0) + (p.actual_passing_yards or 0)
        yard_errors.append(abs(projected_total - actual_total))

    any_td_hits = sum(1 for p in graded if (p.actual_tds or 0) > 0)
    # Brier-style calibration: how close was anytime_td_prob to the actual 0/1 outcome.
    brier = sum((p.anytime_td_prob - (1.0 if (p.actual_tds or 0) > 0 else 0.0)) ** 2 for p in graded) / n

    return {
        "graded_projections": n,
        "avg_abs_yardage_error": sum(yard_errors) / n,
        "actual_anytime_td_rate": any_td_hits / n,
        "anytime_td_brier_score": brier,
    }
