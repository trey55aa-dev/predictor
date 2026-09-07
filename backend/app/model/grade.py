"""Compares stored predictions to actual final scores once games are final,
and aggregates rolling accuracy/calibration metrics.

This is the "self-improvement" plumbing: every prediction is logged
immutably at creation time, and this module is the only thing that ever
fills in the actual-result columns after the fact. It doesn't auto-retrain
the model yet, but the log -> compare -> measure loop is fully wired so a
later phase can feed these metrics back into model tuning.
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.models import Game, Prediction


def grade_week(db: Session, season: int, week: int) -> int:
    """Fills in actual results for any final games with an ungraded prediction.
    Returns the number of predictions graded."""
    predictions = (
        db.query(Prediction)
        .join(Game, Prediction.game_id == Game.game_id)
        .filter(Game.season == season, Game.week == week, Prediction.graded_at.is_(None))
        .all()
    )

    graded = 0
    for prediction in predictions:
        game = db.get(Game, prediction.game_id)
        if game is None or game.home_score is None or game.away_score is None:
            continue

        actual_margin = game.home_score - game.away_score
        actual_total = game.home_score + game.away_score
        actual_home_won = actual_margin > 0

        prediction.actual_home_score = game.home_score
        prediction.actual_away_score = game.away_score
        prediction.correct_winner = (prediction.home_win_prob >= 0.5) == actual_home_won
        prediction.margin_error = prediction.predicted_margin - actual_margin
        prediction.total_error = prediction.predicted_total - actual_total
        # Brier score component: (forecast_prob - outcome)^2, outcome=1 if home won.
        outcome = 1.0 if actual_home_won else 0.0
        prediction.brier_component = (prediction.home_win_prob - outcome) ** 2
        prediction.graded_at = dt.datetime.utcnow()
        graded += 1

    db.commit()
    return graded


def performance_summary(db: Session, season: int | None = None, week: int | None = None) -> dict:
    query = db.query(Prediction).filter(Prediction.graded_at.isnot(None))
    if season is not None or week is not None:
        query = query.join(Game, Prediction.game_id == Game.game_id)
        if season is not None:
            query = query.filter(Game.season == season)
        if week is not None:
            query = query.filter(Game.week == week)

    graded = query.all()
    n = len(graded)
    if n == 0:
        return {"graded_predictions": 0}

    correct = sum(1 for p in graded if p.correct_winner)
    avg_brier = sum(p.brier_component for p in graded) / n
    avg_margin_error = sum(abs(p.margin_error) for p in graded) / n
    avg_total_error = sum(abs(p.total_error) for p in graded) / n

    return {
        "graded_predictions": n,
        "winner_accuracy": correct / n,
        "avg_brier_score": avg_brier,
        "avg_abs_margin_error": avg_margin_error,
        "avg_abs_total_error": avg_total_error,
    }


def model_vs_market_comparison(db: Session) -> dict:
    """Scores the three probability sources against each other on the exact
    same graded games: the data-only Elo model, the market's de-vigged price,
    and the blend that's actually served on the site.

    This exists to keep the parlay page's "edge" numbers honest. An edge
    measured against the market only means money if the model is the more
    accurate of the two -- that is a claim to be measured, not assumed, and
    on real NFL data the market usually wins. Whoever has the lower Brier
    score here is the one worth believing when they disagree.
    """
    rows = (
        db.query(Prediction, Game)
        .join(Game, Prediction.game_id == Game.game_id)
        .filter(
            Prediction.graded_at.isnot(None),
            Prediction.elo_win_prob.isnot(None),
            Prediction.market_win_prob.isnot(None),
            Game.home_score.isnot(None),
            Game.away_score.isnot(None),
        )
        .all()
    )

    n = len(rows)
    if n == 0:
        return {"sample_size": 0, "sources": []}

    outcomes = [1.0 if g.home_score > g.away_score else 0.0 for _, g in rows]

    def score(probs: list[float], label: str, description: str) -> dict:
        brier = sum((p - o) ** 2 for p, o in zip(probs, outcomes)) / n
        accuracy = sum(1 for p, o in zip(probs, outcomes) if (p >= 0.5) == (o == 1.0)) / n
        return {
            "key": label,
            "description": description,
            "brier_score": brier,
            "winner_accuracy": accuracy,
        }

    sources = [
        score(
            [p.elo_win_prob for p, _ in rows],
            "elo_only",
            "Data only -- Elo power ratings plus weather, no market input.",
        ),
        score(
            [p.market_win_prob for p, _ in rows],
            "market_only",
            "The sportsbooks' own de-vigged probability.",
        ),
        score(
            [p.home_win_prob for p, _ in rows],
            "blend",
            "The blend actually served on this site.",
        ),
    ]

    best = min(sources, key=lambda s: s["brier_score"])
    return {"sample_size": n, "sources": sources, "most_accurate": best["key"]}
