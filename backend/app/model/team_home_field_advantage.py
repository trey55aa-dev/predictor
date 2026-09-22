"""Per-team home-field advantage: how much better (or worse) a specific
team does at home, beyond the league-average home boost the model already
grants every team -- nfelo publishes the same idea as its "HFA Tracker."

Deliberately NOT built by tuning Elo's own home_field_advantage constant:
recalibration.py's own docstring is explicit that Elo internals (K-factor,
home-field advantage, season regression) can't be live-tuned the way
market_blend_weight etc. are, because every historical TeamRating snapshot
already baked in the constant at the time it was computed -- changing it
live without a full `build-history` rebuild would make old and new
ratings inconsistent with each other. So this is a separate, small capped
win-prob nudge layered on top of predictions instead (same shape as every
other MVP signal in predict.py), not a change to Elo's own update rule --
model/elo.py and build_ratings() are untouched.

Uses the team's own game history across ALL seasons on record, not just
the current one: a real home-field edge (crowd noise, travel, altitude,
stadium quirks) is a comparatively stable property of a team/stadium
across years, unlike form-based signals like recent_form.py that are
deliberately season-scoped. Still anti-leakage: only games strictly
before the season/week being predicted.
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.model.elo import expected_win_prob, latest_rating
from app.model.venue import is_true_home_game
from app.models import Game


def _true_home_game_results(db: Session, team: str, before_season: int, before_week: int) -> list[tuple[float, float, float]]:
    """(home_elo_pregame, away_elo_pregame, actual_result) for every one of
    `team`'s own true (non-neutral-site) home games with a final score,
    strictly before the given season/week. Pregame ratings use
    latest_rating the same way predict.py does, so this reflects exactly
    what the model knew walking into each of those games, not hindsight."""
    games = (
        db.query(Game)
        .filter(
            Game.home_team == team,
            Game.game_type == "REG",
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
            (Game.season < before_season) | ((Game.season == before_season) & (Game.week < before_week)),
        )
        .all()
    )

    samples = []
    for game in games:
        if not is_true_home_game(db, game):
            continue
        home_elo = latest_rating(db, team, game.season, game.week)
        away_elo = latest_rating(db, game.away_team, game.season, game.week)
        if game.home_score > game.away_score:
            actual = 1.0
        elif game.home_score < game.away_score:
            actual = 0.0
        else:
            actual = 0.5
        samples.append((home_elo, away_elo, actual))
    return samples


def team_hfa_excess(db: Session, team: str, before_season: int, before_week: int) -> tuple[float | None, int]:
    """(average excess win rate, sample size). "Excess" is how much better
    `team` actually performed at home than the model -- already including
    the league-average home boost (settings.elo_home_field_advantage) --
    expected. None (never guessed) with fewer than
    settings.team_hfa_min_home_games true home games on record."""
    samples = _true_home_game_results(db, team, before_season, before_week)
    n = len(samples)
    if n < settings.team_hfa_min_home_games:
        return None, n

    excesses = []
    for home_elo, away_elo, actual in samples:
        expected_with_league_average_hfa = expected_win_prob(
            home_elo + settings.elo_home_field_advantage, away_elo
        )
        excesses.append(actual - expected_with_league_average_hfa)

    return sum(excesses) / n, n


def team_hfa_adjustment(db: Session, team: str, season: int, before_week: int) -> tuple[float, str | None]:
    """Returns (win_prob_delta, note) for `team`'s upcoming home game --
    0.0 (note=None) for an away game context, or with too little home-game
    history to trust a team-specific deviation from the league average."""
    excess, n = team_hfa_excess(db, team, season, before_week)
    if excess is None:
        return 0.0, None

    delta = max(-settings.team_hfa_max_adjustment, min(settings.team_hfa_max_adjustment, excess))
    direction = "outperforms" if delta > 0 else "underperforms"
    note = (
        f"{team} historically {direction} the league-average home-field boost by "
        f"about {abs(delta) * 100:.1f} points of win probability, across {n} true home games."
    )
    return delta, note
