"""Backtests simulation-based player props against real box scores, and
against the existing trailing-average projection system (player_projection.py)
on the exact same games -- so "is this actually better" is measured, not
assumed just because it's a fancier method.

Same no-lookahead discipline as sim_validation.py: a game in season S is
projected using only play data from seasons strictly before S (usage shares
are computed the same way inside player_usage.py, filtered to games before
the target season/week).
"""

from sqlalchemy.orm import Session

from app.model.player_projection import project_player
from app.model.player_sim import PlayerPropsSimulator
from app.models import Game, PlayerGameStat


def _actual_stats(db: Session, game_id: str) -> dict[str, PlayerGameStat]:
    return {row.player_id: row for row in db.query(PlayerGameStat).filter(PlayerGameStat.game_id == game_id).all()}


def validate_player_props(
    db: Session,
    test_seasons: list[int],
    history_seasons: list[int],
    n_sims: int = 800,
    limit_per_season: int | None = None,
    min_actual_yards: float = 20.0,
) -> dict:
    """`min_actual_yards` excludes players who barely touched the ball in the
    real game (a 3-yard actual outcome makes every method's absolute error
    look artificially tiny and dilutes the comparison)."""
    records = []

    for season in test_seasons:
        prior = [s for s in history_seasons if s < season]
        if not prior:
            continue

        games = (
            db.query(Game)
            .filter(Game.season == season, Game.game_type == "REG", Game.home_score.isnot(None))
            .order_by(Game.gameday)
            .all()
        )
        if limit_per_season and limit_per_season < len(games):
            # Evenly spaced across the season, not the first N games by date --
            # taking the first N biases hard toward early weeks, which is
            # exactly when a free-agent-signed starter's real volume hasn't
            # accumulated yet and the model is at its weakest. An even spread
            # is a fair read of a full season's performance.
            step = len(games) / limit_per_season
            games = [games[int(i * step)] for i in range(limit_per_season)]

        for game in games:
            actuals = _actual_stats(db, game.game_id)
            if not actuals:
                continue

            try:
                # Real season+week cutoff, not a hardcoded week 1: usage shares
                # (rushing_shares/target_shares/passer_shares) filter Play rows
                # by season+week directly, independent of the whole-season-only
                # `prior` list used for yardage sampling below -- so this lets a
                # mid-season game correctly see that season's own earlier weeks
                # (e.g. a free-agent-signed starter's real volume through week 4
                # is visible when testing week 5), without introducing any
                # lookahead into the separately-scoped play-outcome sampling.
                sim = PlayerPropsSimulator(db, game.home_team, game.away_team, game.season, game.week, prior, seed=hash(game.game_id) % 100000)
            except RuntimeError:
                continue
            result = sim.simulate(n_sims)

            sim_by_player: dict[str, dict] = {}
            for side in ("home", "away"):
                for p in result["players"][side]:
                    sim_by_player[p["player_id"]] = p

            for player_id, actual in actuals.items():
                actual_rush = actual.rushing_yards or 0.0
                actual_rec = actual.receiving_yards or 0.0
                actual_pass = actual.passing_yards or 0.0
                primary_actual = max(actual_rush, actual_rec, actual_pass)
                if primary_actual < min_actual_yards:
                    continue

                sim_player = sim_by_player.get(player_id)
                sim_rush = sim_player["rushing"]["mean_yards"] if sim_player and sim_player["rushing"] else None
                sim_rec = sim_player["receiving"]["mean_yards"] if sim_player and sim_player["receiving"] else None
                sim_pass = sim_player["passing"]["mean_yards"] if sim_player and sim_player["passing"] else None
                sim_band = None
                if sim_player:
                    stat_key = "rushing" if actual_rush >= actual_rec and actual_rush >= actual_pass else (
                        "receiving" if actual_rec >= actual_pass else "passing"
                    )
                    if sim_player.get(stat_key):
                        sim_band = (sim_player[stat_key]["yards_p10"], sim_player[stat_key]["yards_p90"])

                baseline = project_player(
                    db, player_id, actual.player_name, actual.position, actual.team,
                    game.away_team if actual.team == game.home_team else game.home_team,
                    game.season, game.week,
                )

                records.append(
                    {
                        "player_id": player_id,
                        "position": actual.position,
                        "actual_rush": actual_rush,
                        "actual_rec": actual_rec,
                        "actual_pass": actual_pass,
                        "sim_rush": sim_rush,
                        "sim_rec": sim_rec,
                        "sim_pass": sim_pass,
                        "sim_band": sim_band,
                        "baseline_rush": baseline["projected_rushing_yards"] if baseline else None,
                        "baseline_rec": baseline["projected_receiving_yards"] if baseline else None,
                        "baseline_pass": baseline["projected_passing_yards"] if baseline else None,
                    }
                )

    if not records:
        return {"players": 0}

    def mae(sim_key: str, actual_key: str) -> float | None:
        pairs = [(r[sim_key], r[actual_key]) for r in records if r[sim_key] is not None]
        return sum(abs(s - a) for s, a in pairs) / len(pairs) if pairs else None

    n = len(records)
    band_hits = sum(1 for r in records if r["sim_band"] is not None and r["sim_band"][0] <= max(r["actual_rush"], r["actual_rec"], r["actual_pass"]) <= r["sim_band"][1])
    band_n = sum(1 for r in records if r["sim_band"] is not None)

    return {
        "players": n,
        "test_seasons": test_seasons,
        "mae": {
            "rushing": {"simulation": mae("sim_rush", "actual_rush"), "baseline": mae("baseline_rush", "actual_rush")},
            "receiving": {"simulation": mae("sim_rec", "actual_rec"), "baseline": mae("baseline_rec", "actual_rec")},
            "passing": {"simulation": mae("sim_pass", "actual_pass"), "baseline": mae("baseline_pass", "actual_pass")},
        },
        "interval_coverage_p10_p90": {
            "hit_rate": (band_hits / band_n) if band_n else None,
            "n": band_n,
            "target": 0.80,
        },
    }
