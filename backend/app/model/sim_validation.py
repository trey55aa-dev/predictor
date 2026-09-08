"""Backtests the game simulator against real completed games.

The point of this module is to be able to say whether the simulator is any
good *before* anything on the site leans on it. Three things are measured:

1. Accuracy of the central estimate (mean absolute error on final margin and
   total).
2. Whether its win probability is competitive with Elo, the market and the
   served blend, scored by Brier on the same games.
3. Whether its *intervals* are honest -- if 80% of real outcomes don't land
   inside the p10-p90 band, the distribution is the wrong width, which
   matters more than the mean for anything the distribution is used for.

Lookahead is avoided deliberately: a game in season S is simulated using only
plays from seasons strictly before S. Pooling all seasons would let the
simulator sample plays from games that hadn't happened yet, which quietly
flatters every number here.
"""

from sqlalchemy.orm import Session

from app.model.sim_params import load_sim_params
from app.model.simulation import GameSimulator, PlayLibrary
from app.models import Game, Prediction


def _percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    return float(s[min(len(s) - 1, int(p * len(s)))])


def validate_simulator(
    db: Session,
    test_seasons: list[int],
    history_seasons: list[int],
    n_sims: int = 500,
    limit_per_season: int | None = None,
    seed: int = 0,
) -> dict:
    params = load_sim_params(db)
    if not params:
        raise RuntimeError("Run `build-sim-params` first.")

    records: list[dict] = []

    for season in test_seasons:
        prior = [s for s in history_seasons if s < season]
        if not prior:
            continue
        library = PlayLibrary(db, prior)

        games = (
            db.query(Game)
            .filter(
                Game.season == season,
                Game.game_type == "REG",
                Game.home_score.isnot(None),
                Game.away_score.isnot(None),
            )
            .order_by(Game.gameday)
            .all()
        )
        if limit_per_season:
            games = games[:limit_per_season]

        for game in games:
            # Pools are keyed by (team, is_home), so check the exact setting
            # each team will actually be simulated in.
            if (game.home_team, True) not in library.by_offense:
                continue
            if (game.away_team, False) not in library.by_offense:
                continue
            sim = GameSimulator(
                db,
                game.home_team,
                game.away_team,
                prior,
                seed=seed + hash(game.game_id) % 100000,
                library=library,
                params=params,
            )
            result = sim.simulate(n_sims)
            summary = result.summary()

            actual_margin = game.home_score - game.away_score
            actual_total = game.home_score + game.away_score
            home_won = 1.0 if actual_margin > 0 else (0.5 if actual_margin == 0 else 0.0)

            prediction = (
                db.query(Prediction)
                .filter(Prediction.game_id == game.game_id)
                .order_by(Prediction.created_at.desc())
                .first()
            )

            records.append(
                {
                    "game_id": game.game_id,
                    "sim_win_prob": summary["home_win_prob"],
                    "sim_margin": summary["mean_margin"],
                    "sim_total": summary["mean_total"],
                    "margin_in_band": summary["margin_p10"] <= actual_margin <= summary["margin_p90"],
                    "total_in_band": summary["total_p10"] <= actual_total <= summary["total_p90"],
                    # Percentile offsets around the simulator's own mean. These
                    # are the *shape* of the distribution, independent of where
                    # it is centred -- what gets reused when the band is
                    # re-anchored to the model's central estimate.
                    "margin_off_lo": summary["margin_p10"] - summary["mean_margin"],
                    "margin_off_hi": summary["margin_p90"] - summary["mean_margin"],
                    "total_off_lo": summary["total_p10"] - summary["mean_total"],
                    "total_off_hi": summary["total_p90"] - summary["mean_total"],
                    "actual_margin": actual_margin,
                    "actual_total": actual_total,
                    "home_won": home_won,
                    "elo_win_prob": prediction.elo_win_prob if prediction else None,
                    "market_win_prob": prediction.market_win_prob if prediction else None,
                    "blend_win_prob": prediction.home_win_prob if prediction else None,
                    "model_margin": prediction.predicted_margin if prediction else None,
                    "model_total": prediction.predicted_total if prediction else None,
                }
            )

    if not records:
        return {"games": 0}

    n = len(records)

    def brier(field: str) -> float | None:
        pairs = [(r[field], r["home_won"]) for r in records if r.get(field) is not None]
        if not pairs:
            return None
        return sum((p - o) ** 2 for p, o in pairs) / len(pairs)

    def mae(pred_field: str, actual_field: str) -> float | None:
        pairs = [
            (r[pred_field], r[actual_field]) for r in records if r.get(pred_field) is not None
        ]
        if not pairs:
            return None
        return sum(abs(p - a) for p, a in pairs) / len(pairs)

    return {
        "games": n,
        "test_seasons": test_seasons,
        "n_sims_per_game": n_sims,
        "brier": {
            "simulation": brier("sim_win_prob"),
            "elo": brier("elo_win_prob"),
            "market": brier("market_win_prob"),
            "blend": brier("blend_win_prob"),
        },
        "margin_mae": {"simulation": mae("sim_margin", "actual_margin"), "model": mae("model_margin", "actual_margin")},
        "total_mae": {"simulation": mae("sim_total", "actual_total"), "model": mae("model_total", "actual_total")},
        # A well-calibrated p10-p90 band should contain the real result ~80%
        # of the time. Materially below means the simulator is overconfident;
        # materially above means it is hedging too wide to be useful.
        "interval_coverage_p10_p90": {
            "margin": sum(1 for r in records if r["margin_in_band"]) / n,
            "total": sum(1 for r in records if r["total_in_band"]) / n,
            "target": 0.80,
        },
        "mean_bias": {
            "sim_total_minus_actual": sum(r["sim_total"] - r["actual_total"] for r in records) / n,
            "sim_margin_minus_actual": sum(r["sim_margin"] - r["actual_margin"] for r in records) / n,
        },
        # The simulator's spread re-centred on the model's central estimate --
        # the arrangement the app actually uses, since the model is the more
        # accurate centre and the simulator is the honest width. Worth checking
        # separately: a band can be correctly *shaped* and still miss because
        # it is centred in the wrong place.
        "anchored_interval_coverage": _anchored_coverage(records),
    }


def _anchored_coverage(records: list[dict]) -> dict:
    margin_hits = margin_n = total_hits = total_n = 0

    for r in records:
        if r.get("model_margin") is not None:
            lo = r["model_margin"] + r["margin_off_lo"]
            hi = r["model_margin"] + r["margin_off_hi"]
            margin_n += 1
            margin_hits += 1 if lo <= r["actual_margin"] <= hi else 0
        if r.get("model_total") is not None:
            lo = r["model_total"] + r["total_off_lo"]
            hi = r["model_total"] + r["total_off_hi"]
            total_n += 1
            total_hits += 1 if lo <= r["actual_total"] <= hi else 0

    return {
        "margin": (margin_hits / margin_n) if margin_n else None,
        "total": (total_hits / total_n) if total_n else None,
        "games_with_model_estimate": max(margin_n, total_n),
        "target": 0.80,
    }
