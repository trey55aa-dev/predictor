"""Elo power-rating engine built from historical game results.

Follows the well-documented public approach popularized by FiveThirtyEight's
NFL Elo model: a standard logistic Elo update, a home-field offset, a
margin-of-victory multiplier so blowouts move ratings more than close games,
and partial regression to the mean between seasons.
"""

import math

from sqlalchemy.orm import Session

from app.config import settings
from app.models import Game, TeamRating


def expected_win_prob(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** (-(rating_a - rating_b) / 400.0))


def _mov_multiplier(margin: float, elo_diff_winner: float) -> float:
    """Margin-of-victory multiplier (538-style): dampens blowout ratings
    inflation when the favorite was already heavily favored."""
    return math.log(abs(margin) + 1) * (2.2 / (0.001 * abs(elo_diff_winner) + 2.2))


def build_ratings(db: Session, seasons: list[int]) -> int:
    """Recompute Elo ratings from scratch over the given seasons (chronological),
    writing a snapshot row per team per week. Returns the number of games processed.
    """
    db.query(TeamRating).filter(TeamRating.season.in_(seasons)).delete(synchronize_session=False)

    ratings: dict[str, float] = {}
    games = (
        db.query(Game)
        .filter(
            Game.season.in_(seasons),
            Game.game_type == "REG",
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
        )
        .order_by(Game.season, Game.week)
        .all()
    )

    current_season: int | None = None
    processed = 0

    for game in games:
        if current_season is not None and game.season != current_season:
            for team in ratings:
                ratings[team] = (
                    settings.elo_start_rating * settings.elo_season_regression
                    + ratings[team] * (1 - settings.elo_season_regression)
                )
        current_season = game.season

        home_elo = ratings.setdefault(game.home_team, settings.elo_start_rating)
        away_elo = ratings.setdefault(game.away_team, settings.elo_start_rating)

        home_elo_adj = home_elo + settings.elo_home_field_advantage
        expected_home = expected_win_prob(home_elo_adj, away_elo)

        margin = game.home_score - game.away_score
        if margin > 0:
            actual_home = 1.0
        elif margin < 0:
            actual_home = 0.0
        else:
            actual_home = 0.5

        elo_diff_winner = (home_elo_adj - away_elo) if margin >= 0 else (away_elo - home_elo_adj)
        mult = _mov_multiplier(margin if margin != 0 else 1, elo_diff_winner)

        delta = settings.elo_k_factor * mult * (actual_home - expected_home)
        ratings[game.home_team] = home_elo + delta
        ratings[game.away_team] = away_elo - delta

        db.add(TeamRating(team_abbr=game.home_team, season=game.season, week=game.week, elo=ratings[game.home_team]))
        db.add(TeamRating(team_abbr=game.away_team, season=game.season, week=game.week, elo=ratings[game.away_team]))
        processed += 1

    db.commit()
    return processed


def latest_rating(db: Session, team_abbr: str, before_season: int, before_week: int) -> float:
    """Most recent Elo rating for a team strictly before the given season/week."""
    row = (
        db.query(TeamRating)
        .filter(
            TeamRating.team_abbr == team_abbr,
            (TeamRating.season < before_season)
            | ((TeamRating.season == before_season) & (TeamRating.week < before_week)),
        )
        .order_by(TeamRating.season.desc(), TeamRating.week.desc())
        .first()
    )
    return row.elo if row else settings.elo_start_rating
