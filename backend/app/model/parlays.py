"""Builds two parlay suggestions for a week's games: the safest (highest
combined hit probability) and the best money move (largest positive edge
between the model's probability and the de-vigged market probability).

Framing note, surfaced in the response, not just this docstring: these are
the model's own probability estimates, not guarantees, and combining legs
compounds risk even when each leg individually looks favorable.
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.model.market import market_home_win_prob
from app.model.odds_math import american_to_decimal, combined_decimal_payout, combined_probability
from app.models import Game, OddsSnapshot, ParlayPick, ParlayPickLeg, PlayerGameStat, PlayerProjection, Prediction

MAX_TD_LEG_CANDIDATES_PER_GAME = 3  # top-N most-involved players per game, to avoid flooding the pool


def _latest_prediction(db: Session, game_id: str) -> Prediction | None:
    return (
        db.query(Prediction)
        .filter(Prediction.game_id == game_id)
        .order_by(Prediction.created_at.desc())
        .first()
    )


def _leg_candidate(db: Session, game: Game) -> dict | None:
    prediction = _latest_prediction(db, game.game_id)
    if prediction is None or prediction.home_win_prob is None:
        return None

    picked_home = prediction.home_win_prob >= 0.5
    team = game.home_team if picked_home else game.away_team
    opponent = game.away_team if picked_home else game.home_team
    model_prob = prediction.home_win_prob if picked_home else 1 - prediction.home_win_prob

    # The data-only model's own call, before the market blend, shown alongside
    # the blended number so the disagreement is visible rather than implied.
    elo_prob = None
    if prediction.elo_win_prob is not None:
        elo_prob = prediction.elo_win_prob if picked_home else 1 - prediction.elo_win_prob

    market_prob = None
    decimal_odds = None
    american_price = None

    odds = db.get(OddsSnapshot, prediction.odds_snapshot_id) if prediction.odds_snapshot_id else None
    if odds and odds.home_moneyline is not None and odds.away_moneyline is not None:
        home_fair = market_home_win_prob(odds.home_moneyline, odds.away_moneyline)
        market_prob = home_fair if picked_home else 1 - home_fair
        american_price = odds.home_moneyline if picked_home else odds.away_moneyline
        # Payout math uses the unrounded consensus price; the displayed price is
        # rounded because odds.home_moneyline/away_moneyline are a mean across
        # sportsbooks (see ingestion/odds.py), and a mean of integers lands on
        # values like -539.333 that no book would ever actually post.
        decimal_odds = american_to_decimal(american_price)
        american_price = round(american_price)

    edge = (model_prob - market_prob) if market_prob is not None else None

    return {
        "leg_type": "game_winner",
        "game_id": game.game_id,
        "team": team,
        "opponent": opponent,
        "player_id": None,
        "player_name": None,
        "model_prob": model_prob,
        "elo_prob": elo_prob,
        "market_prob": market_prob,
        "american_price": american_price,
        "decimal_odds": decimal_odds,
        "edge": edge,
    }


def _anytime_td_candidates(db: Session, game: Game) -> list[dict]:
    """Anytime-TD leg candidates from this week's player projections. No
    market player-prop odds are ingested (see model/player_projection.py
    docstring) -- these are model-probability-only, so market_prob/edge/
    decimal_odds stay None, same as a game leg with no odds snapshot yet.
    """
    top_players = (
        db.query(PlayerProjection)
        .filter(PlayerProjection.game_id == game.game_id)
        .order_by(PlayerProjection.anytime_td_prob.desc())
        .limit(MAX_TD_LEG_CANDIDATES_PER_GAME)
        .all()
    )
    return [
        {
            "leg_type": "anytime_td",
            "game_id": game.game_id,
            "team": p.team,
            "opponent": p.opponent,
            "player_id": p.player_id,
            "player_name": p.player_name,
            "model_prob": p.anytime_td_prob,
            # Player projections have no Elo/market split to separate -- the
            # projection is already data-only.
            "elo_prob": None,
            "market_prob": None,
            "american_price": None,
            "decimal_odds": None,
            "edge": None,
        }
        for p in top_players
    ]


def _summarize(legs: list[dict]) -> dict:
    model_probs = [leg["model_prob"] for leg in legs]
    combined_prob = combined_probability(model_probs)

    decimal_odds = [leg["decimal_odds"] for leg in legs]
    combined_payout = combined_decimal_payout(decimal_odds) if all(d is not None for d in decimal_odds) else None

    return {
        "legs": legs,
        "combined_probability": combined_prob,
        "combined_decimal_payout": combined_payout,
        "caveat": (
            f"Model-based estimate, not a guarantee -- these are {len(legs)} independent legs with "
            f"roughly a {combined_prob:.0%} chance of all hitting. Parlays compound risk even when "
            "each leg looks favorable on its own."
        ),
    }


def build_parlays(db: Session, season: int, week: int, legs: int = 3) -> dict:
    games = db.query(Game).filter(Game.season == season, Game.week == week, Game.game_type == "REG").all()
    game_winner_candidates = [c for c in (_leg_candidate(db, g) for g in games) if c is not None]
    td_candidates = [c for g in games for c in _anytime_td_candidates(db, g)]

    # Safest draws from both leg types (whatever's most likely to hit); best
    # money move needs a market price to compute edge against, which player
    # props don't have yet, so it stays game-winner-only (see docstring).
    # At most one leg per game: a team-win leg and that team's own player-TD
    # leg are correlated outcomes, not independent, so the combined-
    # probability math (a product of independent probabilities) would
    # overstate the parlay's real hit chance if both were allowed in.
    all_candidates = sorted(game_winner_candidates + td_candidates, key=lambda c: c["model_prob"], reverse=True)
    safest_legs = []
    used_games = set()
    for candidate in all_candidates:
        if candidate["game_id"] in used_games:
            continue
        safest_legs.append(candidate)
        used_games.add(candidate["game_id"])
        if len(safest_legs) == legs:
            break

    value_candidates = [c for c in game_winner_candidates if c["edge"] is not None and c["edge"] > 0]
    value_candidates.sort(key=lambda c: c["edge"], reverse=True)
    best_money_move_legs = value_candidates[:legs]

    result = {
        "season": season,
        "week": week,
        "requested_legs": legs,
        "safest": _summarize(safest_legs) if safest_legs else None,
        "best_money_move": _summarize(best_money_move_legs) if best_money_move_legs else None,
    }

    if not value_candidates:
        result["best_money_move_note"] = (
            "No positive-edge legs available -- market odds haven't been ingested for this week yet, "
            "or the model doesn't currently disagree with the market on any game."
        )
    elif len(best_money_move_legs) < legs:
        result["best_money_move_note"] = (
            f"Only {len(best_money_move_legs)} positive-edge leg(s) available this week "
            f"(requested {legs})."
        )

    return result


def log_parlays(db: Session, season: int, week: int, legs: int = 3) -> int:
    """Persists this week's safest/best-money-move suggestions so they can be
    graded later. Re-running mid-week (as odds/injuries update) replaces any
    not-yet-graded pick of the same type for that week -- one tracked pick
    per week per type, not a growing pile of duplicates. Already-graded picks
    are never touched, preserving the historical accuracy record.
    """
    result = build_parlays(db, season, week, legs=legs)
    now = dt.datetime.utcnow()
    logged = 0

    for parlay_type in ("safest", "best_money_move"):
        summary = result.get(parlay_type)
        if not summary:
            continue

        # Bulk .delete() bypasses the ORM relationship cascade entirely, and
        # the FK has no ON DELETE CASCADE at the DB level -- deleting a
        # not-yet-graded ParlayPick that already has legs raised a real
        # ForeignKeyViolation in production the first time this ran twice
        # in the same still-ungraded week (confirmed live: a Wednesday
        # routine run's parlay pick still had no graded_at by the time a
        # later same-day run replaced it). Delete the children first.
        stale_pick_ids = [
            pid
            for (pid,) in db.query(ParlayPick.id)
            .filter(
                ParlayPick.season == season,
                ParlayPick.week == week,
                ParlayPick.parlay_type == parlay_type,
                ParlayPick.graded_at.is_(None),
            )
            .all()
        ]
        if stale_pick_ids:
            db.query(ParlayPickLeg).filter(ParlayPickLeg.parlay_pick_id.in_(stale_pick_ids)).delete(
                synchronize_session=False
            )
            db.query(ParlayPick).filter(ParlayPick.id.in_(stale_pick_ids)).delete(synchronize_session=False)

        pick = ParlayPick(
            season=season,
            week=week,
            parlay_type=parlay_type,
            created_at=now,
            combined_probability=summary["combined_probability"],
            combined_decimal_payout=summary["combined_decimal_payout"],
        )
        db.add(pick)
        db.flush()

        for leg in summary["legs"]:
            db.add(
                ParlayPickLeg(
                    parlay_pick_id=pick.id,
                    game_id=leg["game_id"],
                    leg_type=leg["leg_type"],
                    team=leg["team"],
                    player_id=leg["player_id"],
                    player_name=leg["player_name"],
                    model_prob=leg["model_prob"],
                    market_prob=leg["market_prob"],
                    american_price=leg["american_price"],
                )
            )
        logged += 1

    db.commit()
    return logged


def _leg_hit(leg: ParlayPickLeg, game: Game, db: Session) -> bool:
    if leg.leg_type == "anytime_td":
        stat = (
            db.query(PlayerGameStat)
            .filter(
                PlayerGameStat.player_id == leg.player_id,
                PlayerGameStat.season == game.season,
                PlayerGameStat.week == game.week,
            )
            .first()
        )
        if stat is None:
            return False  # didn't play / no stat line -- counts as a miss, same as a real anytime-TD prop
        tds = (stat.rushing_tds or 0) + (stat.receiving_tds or 0) + (stat.passing_tds or 0)
        return tds > 0

    winner = game.home_team if game.home_score > game.away_score else game.away_team
    return leg.team == winner


def grade_parlays(db: Session, season: int, week: int) -> int:
    """Grades any ungraded parlay picks for this week whose games are all final."""
    picks = (
        db.query(ParlayPick)
        .filter(ParlayPick.season == season, ParlayPick.week == week, ParlayPick.graded_at.is_(None))
        .all()
    )

    graded = 0
    for pick in picks:
        legs = db.query(ParlayPickLeg).filter(ParlayPickLeg.parlay_pick_id == pick.id).all()
        if not legs:
            continue

        games = {leg.game_id: db.get(Game, leg.game_id) for leg in legs}
        if any(g is None or g.home_score is None or g.away_score is None for g in games.values()):
            continue  # not all games final yet

        all_hit = all(_leg_hit(leg, games[leg.game_id], db) for leg in legs)

        pick.all_legs_hit = all_hit
        pick.graded_at = dt.datetime.utcnow()
        graded += 1

    db.commit()
    return graded


def parlay_performance_summary(db: Session) -> dict:
    graded = db.query(ParlayPick).filter(ParlayPick.graded_at.isnot(None)).all()
    by_type: dict[str, list[ParlayPick]] = {"safest": [], "best_money_move": []}
    for pick in graded:
        by_type.setdefault(pick.parlay_type, []).append(pick)

    summary = {}
    for parlay_type, picks in by_type.items():
        n = len(picks)
        summary[parlay_type] = {
            "graded_parlays": n,
            "hit_rate": (sum(1 for p in picks if p.all_legs_hit) / n) if n else None,
        }
    return summary
