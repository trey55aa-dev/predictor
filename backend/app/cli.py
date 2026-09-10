import datetime as dt

import typer

from app.db import SessionLocal, create_all
from app.ingestion.current_plays import ingest_current_season_plays
from app.ingestion.espn_injuries import ingest_espn_injuries
from app.ingestion.injuries import NoInjuryDataError, ingest_injuries
from app.ingestion.odds import ingest_odds
from app.ingestion.player_stats import ingest_player_stats
from app.ingestion.plays import ingest_plays
from app.ingestion.snap_counts import ingest_snap_counts
from app.ingestion.rosters import ingest_rosters
from app.ingestion.schedules import ingest_schedules, seed_reference_data
from app.ingestion.schemes import build_team_season_schemes, seed_scheme_families
from app.ingestion.weather import ingest_weather_for_games
from app.model.elo import build_ratings
from app.model.grade import grade_week, performance_summary
from app.model.parlays import grade_parlays, log_parlays, parlay_performance_summary
from app.model.player_projection import (
    grade_player_projections,
    player_projection_performance_summary,
    project_week,
)
from app.model.predict import predict_week
from app.model.recalibration import recalibrate
from app.model.schedule_context import current_or_next_week, current_season, should_run_dense_cadence
from app.models import Game

app = typer.Typer()

HISTORY_SEASONS = [2021, 2022, 2023, 2024, 2025]
# NOTE: intentionally does NOT include the current in-progress season.
# nflreadpy's load_pbp() rejects any season beyond its own notion of
# "current season" (a season needs to be complete, or nearly so, before
# full play-by-play is available) -- confirmed by a real ValueError running
# build-sim-params against 2026 mid-season. load_rosters() and
# load_snap_counts() DO support the in-progress season, which is why
# ingest-rosters/ingest-snap-counts were called with an explicit
# --seasons list including 2026 rather than relying on this default.

# Stop-condition thresholds. Small early samples can spuriously read 90%+;
# these guard against declaring victory on a lucky streak.
STOP_ACCURACY_THRESHOLD = 0.90
MIN_GRADED_PREDICTIONS_FOR_STOP = 50
MIN_GRADED_PARLAYS_FOR_STOP = 16


@app.command()
def init_db() -> None:
    """Create tables and seed stadiums/teams."""
    create_all()
    db = SessionLocal()
    try:
        seed_reference_data(db)
    finally:
        db.close()
    typer.echo("Database initialized and reference data seeded.")


@app.command()
def build_history(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Backfill historical schedules (with embedded closing lines) and build Elo ratings."""
    db = SessionLocal()
    try:
        seed_reference_data(db)
        n = ingest_schedules(db, seasons)
        typer.echo(f"Ingested {n} historical games for seasons {seasons}.")
        processed = build_ratings(db, seasons)
        typer.echo(f"Built Elo ratings over {processed} games.")
    finally:
        db.close()


@app.command()
def ingest_all(season: int, week: int) -> None:
    """Ingest schedule, odds, and weather for a given season/week."""
    db = SessionLocal()
    try:
        seed_reference_data(db)
        ingest_schedules(db, [season])
        typer.echo("Schedules ingested.")

        try:
            n_odds = ingest_odds(db)
            typer.echo(f"Ingested {n_odds} odds snapshots.")
        except RuntimeError as e:
            typer.echo(f"Skipping odds ingestion: {e}")

        games = (
            db.query(Game)
            .filter(Game.season == season, Game.week == week, Game.game_type == "REG")
            .all()
        )
        n_weather = ingest_weather_for_games(db, games)
        typer.echo(f"Ingested {n_weather} weather snapshots.")

        try:
            n_injuries = ingest_injuries(db, season, week)
            typer.echo(f"Ingested {n_injuries} injury reports.")
        except NoInjuryDataError as e:
            typer.echo(f"nflverse injury data unavailable ({e}); falling back to ESPN's live roster feed.")
            try:
                n_espn = ingest_espn_injuries(db, season, week)
                typer.echo(f"Ingested {n_espn} injury reports from ESPN.")
            except Exception as espn_e:  # noqa: BLE001 -- an external API hiccup shouldn't crash the pipeline
                typer.echo(f"ESPN injury fallback also failed: {espn_e}")
    finally:
        db.close()


@app.command(name="ingest-injuries")
def ingest_injuries_cmd(season: int, week: int) -> None:
    """Ingest one week's injury reports (ranked by starter status). Falls
    back to ESPN's live roster feed when nflverse has no data for this
    season (currently true for the whole 2026 season)."""
    db = SessionLocal()
    try:
        n = ingest_injuries(db, season, week)
        typer.echo(f"Ingested {n} injury reports for {season} week {week}.")
    except NoInjuryDataError as e:
        typer.echo(f"nflverse injury data unavailable ({e}); falling back to ESPN's live roster feed.")
        n_espn = ingest_espn_injuries(db, season, week)
        typer.echo(f"Ingested {n_espn} injury reports from ESPN for {season} week {week}.")
    finally:
        db.close()


@app.command(name="predict-week")
def predict_week_cmd(season: int, week: int) -> None:
    """Generate predictions for every game in a given season/week."""
    db = SessionLocal()
    try:
        predictions = predict_week(db, season, week)
        typer.echo(f"Generated {len(predictions)} predictions for {season} week {week}.")
    finally:
        db.close()


@app.command(name="grade-week")
def grade_week_cmd(season: int, week: int) -> None:
    """Grade predictions for a week whose games are final."""
    db = SessionLocal()
    try:
        n = grade_week(db, season, week)
        typer.echo(f"Graded {n} predictions.")
        summary = performance_summary(db, season=season, week=week)
        typer.echo(summary)
    finally:
        db.close()


@app.command(name="build-scheme-mapping")
def build_scheme_mapping_cmd(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Seed the scheme-family reference data and resolve each team-season's
    head coach to an offense/defense scheme family."""
    db = SessionLocal()
    try:
        seed_scheme_families(db)
        report = build_team_season_schemes(db, seasons)
        typer.echo(f"Mapped {report['team_seasons_mapped']} team-seasons.")
        if report["coaches_landed_in_independent_offense"]:
            typer.echo(
                "Coaches in Independent Offense (no specific tree matched): "
                f"{report['coaches_landed_in_independent_offense']}"
            )
        if report["coaches_landed_in_independent_defense"]:
            typer.echo(
                "Coaches in Independent Defense (no specific tree matched): "
                f"{report['coaches_landed_in_independent_defense']}"
            )
    finally:
        db.close()


@app.command(name="ingest-player-stats")
def ingest_player_stats_cmd(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Ingest weekly per-player rushing/receiving/passing stats (training
    signal + later grading ground truth for player projections)."""
    db = SessionLocal()
    try:
        n = ingest_player_stats(db, seasons)
        typer.echo(f"Ingested {n} player-game stat rows across seasons {seasons}.")
    finally:
        db.close()


@app.command(name="project-players")
def project_players_cmd(season: int, week: int) -> None:
    """Generate player TD/yardage projections for every game in a week."""
    db = SessionLocal()
    try:
        n = project_week(db, season, week)
        typer.echo(f"Generated {n} player projections for {season} week {week}.")
    finally:
        db.close()


@app.command(name="ingest-plays")
def ingest_plays_cmd(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Ingest run/pass plays (pbp + participation + FTN charting) tagged with
    each play's offense/defense scheme family. Requires build-scheme-mapping
    to have been run first for these seasons."""
    db = SessionLocal()
    try:
        n = ingest_plays(db, seasons)
        typer.echo(f"Ingested {n} plays across seasons {seasons}.")
    finally:
        db.close()


@app.command(name="ingest-current-season-plays")
def ingest_current_season_plays_cmd(season: int | None = None) -> None:
    """Ingest real play-by-play for the current, in-progress season's
    already-final games only -- powers the post-game 'keys to victory'
    breakdown. Narrower than ingest-plays (see ingestion/current_plays.py
    for why); run-routine already calls this automatically."""
    db = SessionLocal()
    try:
        target_season = season if season is not None else current_season()
        n = ingest_current_season_plays(db, target_season)
        typer.echo(f"Ingested {n} play-by-play rows for {target_season}'s final games.")
    finally:
        db.close()


@app.command(name="ingest-rosters")
def ingest_rosters_cmd(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Ingest real season+team roster membership -- gates the simulator's
    usage-share pools against who was actually on the roster that season."""
    db = SessionLocal()
    try:
        n = ingest_rosters(db, seasons)
        typer.echo(f"Ingested {n} roster-membership rows across seasons {seasons}.")
    finally:
        db.close()


@app.command(name="ingest-snap-counts")
def ingest_snap_counts_cmd(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Ingest real per-player-per-game snap shares -- the load-management
    signal for player projections and simulation-based props."""
    db = SessionLocal()
    try:
        n = ingest_snap_counts(db, seasons)
        typer.echo(f"Ingested {n} snap-count rows across seasons {seasons}.")
    finally:
        db.close()


@app.command()
def performance(season: int | None = None, week: int | None = None) -> None:
    """Show rolling model performance."""
    db = SessionLocal()
    try:
        typer.echo(performance_summary(db, season=season, week=week))
    finally:
        db.close()


@app.command(name="recalibrate")
def recalibrate_cmd() -> None:
    """Looks at accumulated grading evidence and nudges tunable model
    constants (market blend weight, confidence-range widths) toward what
    the evidence supports, when there's enough of it to trust. Logs every
    change made -- most runs find nothing worth changing."""
    db = SessionLocal()
    try:
        changes = recalibrate(db)
        if not changes:
            typer.echo("No calibration changes this pass.")
        for change in changes:
            typer.echo(
                f"Tuned {change['parameter']}: {change['old_value']} -> {change['new_value']} "
                f"({change['evidence']})"
            )
    finally:
        db.close()


@app.command(name="is-game-day")
def is_game_day_cmd() -> None:
    """Exits 0 on a 'dense' day (4x/day: unconditionally during the
    preseason countdown before the season's first game, or a game day/its
    eve once the season is under way), exits 1 only on a genuine off-day
    within an active season (3x/day, skips the late run). Reads whatever
    schedule data is already in the DB -- doesn't re-ingest, so it's fast
    and makes no network calls."""
    db = SessionLocal()
    try:
        season = current_season()
        dense = should_run_dense_cadence(db, season)
        typer.echo("dense" if dense else "quiet")
        raise typer.Exit(code=0 if dense else 1)
    finally:
        db.close()


@app.command(name="run-routine")
def run_routine_cmd() -> None:
    """Orchestrates one pass of the recurring routine: ingest current-week
    data, predict, grade anything newly final, log+grade parlays, and print
    a summary including whether the 90% stop condition is met. Designed to
    be safe to call repeatedly (idempotent) 3-4x/day."""
    db = SessionLocal()
    try:
        season = current_season()
        seed_reference_data(db)
        n_games = ingest_schedules(db, [season])
        typer.echo(f"Season {season}: ingested/refreshed {n_games} scheduled games.")

        week = current_or_next_week(db, season)
        if week is None:
            typer.echo(f"No schedule data ingested for season {season} yet -- nothing to do.")
            return

        earliest_gameday = min(
            dt.date.fromisoformat(g.gameday)
            for g in db.query(Game).filter(Game.season == season, Game.game_type == "REG").all()
        )
        if dt.date.today() < earliest_gameday:
            typer.echo(
                f"No games to predict yet: {season} Week 1 kicks off {earliest_gameday}. "
                "Preseason games aren't predictable through this pipeline -- nflverse's free "
                "schedule data doesn't include preseason games at all (see README)."
            )
        else:
            try:
                n_odds = ingest_odds(db)
                typer.echo(f"Ingested {n_odds} odds snapshots.")
            except RuntimeError as e:
                typer.echo(f"Skipping odds ingestion: {e}")

            week_games = (
                db.query(Game)
                .filter(Game.season == season, Game.week == week, Game.game_type == "REG")
                .all()
            )
            n_weather = ingest_weather_for_games(db, week_games)
            typer.echo(f"Ingested {n_weather} weather snapshots for week {week}.")

            try:
                n_injuries = ingest_injuries(db, season, week)
                typer.echo(f"Ingested {n_injuries} injury reports for week {week}.")
            except NoInjuryDataError as e:
                typer.echo(f"nflverse injury data unavailable ({e}); falling back to ESPN's live roster feed.")
                try:
                    n_espn = ingest_espn_injuries(db, season, week)
                    typer.echo(f"Ingested {n_espn} injury reports from ESPN for week {week}.")
                except Exception as espn_e:  # noqa: BLE001
                    typer.echo(f"ESPN injury fallback also failed: {espn_e}")

            predictions = predict_week(db, season, week)
            typer.echo(f"Generated {len(predictions)} predictions for week {week}.")

            n_player_projections = project_week(db, season, week)
            typer.echo(f"Generated {n_player_projections} player projections for week {week}.")

            log_parlays(db, season, week)

            # Refresh actual player stats (best-effort -- nflreadpy only has
            # data once a week's games are actually final) so this run's
            # grading pass below has real numbers to grade against.
            try:
                n_player_stats = ingest_player_stats(db, [season])
                typer.echo(f"Refreshed {n_player_stats} player-game stat rows for {season}.")
            except Exception as e:  # nflreadpy raises plain exceptions for unsupported seasons
                typer.echo(f"Skipping player-stats refresh: {e}")

            # Real play-by-play for this season's already-final games, so
            # the post-game "keys to victory" breakdown has real stats to
            # show as soon as a game goes final (see model/keys_to_victory.py).
            try:
                n_current_plays = ingest_current_season_plays(db, season)
                typer.echo(f"Ingested {n_current_plays} play-by-play rows for {season}'s final games.")
            except Exception as e:  # nflreadpy raises plain exceptions for unsupported seasons
                typer.echo(f"Skipping current-season play-by-play refresh: {e}")

            graded_this_run = 0
            for past_week in range(1, week + 1):
                graded_this_run += grade_week(db, season, past_week)
                grade_parlays(db, season, past_week)
                # Player projections grade off newly-ingested PlayerGameStat
                # rows, which come from a separate weekly refresh
                # (ingest-player-stats) -- best-effort here since that
                # week's actuals may not be ingested yet.
                grade_player_projections(db, season, past_week)
            typer.echo(f"Graded {graded_this_run} newly-final predictions across weeks 1-{week}.")

            calibration_changes = recalibrate(db)
            if calibration_changes:
                for change in calibration_changes:
                    typer.echo(
                        f"Recalibrated {change['parameter']}: {change['old_value']} -> "
                        f"{change['new_value']} ({change['evidence']})"
                    )
            else:
                typer.echo("Recalibration: no changes this pass.")

        perf = performance_summary(db)
        parlay_perf = parlay_performance_summary(db)
        player_perf = player_projection_performance_summary(db)
        winner_accuracy = perf.get("winner_accuracy")
        n_graded = perf.get("graded_predictions", 0)

        typer.echo(f"Rolling performance: {perf}")
        typer.echo(f"Rolling parlay performance: {parlay_perf}")
        typer.echo(f"Rolling player-projection performance: {player_perf}")
        if parlay_perf.get("best_money_move", {}).get("graded_parlays", 0) == 0:
            typer.echo(
                "Note: 'best_money_move' parlays require live market odds (ODDS_API_KEY in .env) -- "
                "without a key, this leg of the stop condition can never be satisfied."
            )

        stop_ready = (
            n_graded >= MIN_GRADED_PREDICTIONS_FOR_STOP
            and winner_accuracy is not None
            and winner_accuracy >= STOP_ACCURACY_THRESHOLD
            and all(
                parlay_perf.get(t, {}).get("graded_parlays", 0) >= MIN_GRADED_PARLAYS_FOR_STOP
                and (parlay_perf.get(t, {}).get("hit_rate") or 0) >= STOP_ACCURACY_THRESHOLD
                for t in ("safest", "best_money_move")
            )
        )
        typer.echo(f"STOP_CONDITION_MET={stop_ready}")
    finally:
        db.close()


@app.command(name="backfill-historical-odds")
def backfill_historical_odds_cmd(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Materialise OddsSnapshot rows from the closing lines already stored on
    historical games.

    `build-history` ingests each game's real closing moneyline/spread/total
    from nflverse, but nothing consumed them: `predict-week` reads market
    numbers only from OddsSnapshot, so backtested predictions had no market
    probability at all. Without this, the model-vs-market comparison and the
    self-recalibration grid search have no market evidence to work with on
    any game older than the live odds feed.

    Marked with its own source/bookmaker so a closing line is never mistaken
    for a live pre-game quote. Safe to re-run: it skips games that already
    have a snapshot from this source.
    """
    from app.models import OddsSnapshot

    db = SessionLocal()
    try:
        existing = {
            row.game_id
            for row in db.query(OddsSnapshot.game_id)
            .filter(OddsSnapshot.source == "nflverse-closing-line")
            .distinct()
        }
        games = (
            db.query(Game)
            .filter(Game.season.in_(seasons), Game.close_home_moneyline.isnot(None))
            .all()
        )
        created = 0
        for game in games:
            if game.game_id in existing:
                continue
            kickoff = game.gametime_utc or dt.datetime.utcnow()
            db.add(
                OddsSnapshot(
                    game_id=game.game_id,
                    fetched_at=kickoff,
                    bookmaker="closing-line",
                    home_moneyline=game.close_home_moneyline,
                    away_moneyline=game.close_away_moneyline,
                    spread_line=game.close_spread_line,
                    total_line=game.close_total_line,
                    source="nflverse-closing-line",
                )
            )
            created += 1
        db.commit()
        typer.echo(f"Created {created} closing-line odds snapshots (skipped {len(games) - created} existing).")
    finally:
        db.close()


@app.command(name="build-sim-params")
def build_sim_params_cmd(seasons: list[int] = HISTORY_SEASONS) -> None:
    """Measure the simulator's drive-structure inputs (fourth-down behaviour,
    field goals, punts, drive starts, clock burn) from real play-by-play."""
    from app.model.sim_params import build_sim_params

    db = SessionLocal()
    try:
        sizes = build_sim_params(db, seasons)
        for key, n in sizes.items():
            typer.echo(f"{key}: built from {n} plays/drives")
    finally:
        db.close()


@app.command(name="simulate-game")
def simulate_game_cmd(
    home_team: str,
    away_team: str,
    n_sims: int = 10000,
    seasons: list[int] = HISTORY_SEASONS,
    seed: int = 0,
) -> None:
    """Run the Monte Carlo simulator for one matchup and print the outcome
    distribution."""
    from app.model.simulation import simulate_matchup

    db = SessionLocal()
    try:
        summary = simulate_matchup(
            db, home_team, away_team, seasons, n_sims=n_sims, seed=seed or None
        )
        typer.echo(
            f"{summary['away_team']} @ {summary['home_team']}  ({summary['n_sims']} sims)"
        )
        typer.echo(
            f"  win prob : {summary['home_team']} {summary['home_win_prob']:.1%}"
        )
        typer.echo(
            f"  mean     : {summary['home_team']} {summary['mean_home_score']:.1f}"
            f" - {summary['away_team']} {summary['mean_away_score']:.1f}"
        )
        typer.echo(
            f"  total    : p10 {summary['total_p10']:.0f} | p50 {summary['total_p50']:.0f}"
            f" | p90 {summary['total_p90']:.0f}  (mean {summary['mean_total']:.1f})"
        )
        typer.echo(
            f"  margin   : p10 {summary['margin_p10']:+.0f} | p50 {summary['margin_p50']:+.0f}"
            f" | p90 {summary['margin_p90']:+.0f}"
        )
        typer.echo("  most likely scores:")
        for s in summary["most_likely_scores"]:
            typer.echo(
                f"    {summary['home_team']} {s['home_score']}-{s['away_score']}"
                f" {summary['away_team']}  ({s['probability']:.2%})"
            )
    finally:
        db.close()


@app.command(name="simulate-player-props")
def simulate_player_props_cmd(
    home_team: str,
    away_team: str,
    season: int,
    week: int,
    n_sims: int = 3000,
    seasons: list[int] = HISTORY_SEASONS,
    seed: int = 0,
) -> None:
    """Run the player-attributed simulator for one matchup and print each
    team's projected skill players."""
    from app.model.player_sim import PlayerPropsSimulator

    db = SessionLocal()
    try:
        sim = PlayerPropsSimulator(db, home_team, away_team, season, week, seasons, seed=seed or None)
        result = sim.simulate(n_sims)
        typer.echo(f"{away_team} @ {home_team} -- player props ({n_sims} sims)")
        for side_key, team in (("home", home_team), ("away", away_team)):
            typer.echo(f"\n{team}:")
            for p in result["players"][side_key][:6]:
                bits = []
                if p["rushing"]:
                    r = p["rushing"]
                    bits.append(f"rush {r['mean_yards']:.1f} yds (p10-p90 {r['yards_p10']:.0f}-{r['yards_p90']:.0f})")
                if p["receiving"]:
                    r = p["receiving"]
                    bits.append(f"rec {r['mean_yards']:.1f} yds (p10-p90 {r['yards_p10']:.0f}-{r['yards_p90']:.0f})")
                if p["passing"]:
                    r = p["passing"]
                    bits.append(f"pass {r['mean_yards']:.1f} yds (p10-p90 {r['yards_p10']:.0f}-{r['yards_p90']:.0f})")
                typer.echo(f"  {p['player_name']:<22} anytime-TD {p['anytime_td_probability']:.1%}  " + " | ".join(bits))
    finally:
        db.close()


@app.command(name="simulate-player-props-week")
def simulate_player_props_week_cmd(
    season: int,
    week: int,
    n_sims: int = 3000,
    seasons: list[int] = HISTORY_SEASONS,
) -> None:
    """Precompute and store simulation-based rushing/receiving player props
    for every game in a week. Passing is deliberately not included -- see
    SimPlayerProjection's docstring."""
    from app.model.player_sim import store_week_player_props

    db = SessionLocal()
    try:
        n = store_week_player_props(db, season, week, seasons, n_sims=n_sims)
        typer.echo(f"Stored {n} simulated player props for {season} week {week}.")
    finally:
        db.close()


@app.command(name="validate-simulator")
def validate_simulator_cmd(
    test_seasons: list[int] = [2024, 2025],
    n_sims: int = 400,
    limit_per_season: int = 0,
) -> None:
    """Backtest the simulator against real completed games, using only plays
    from seasons before each game (no lookahead)."""
    import json as _json

    from app.model.sim_validation import validate_simulator

    db = SessionLocal()
    try:
        report = validate_simulator(
            db,
            test_seasons=test_seasons,
            history_seasons=HISTORY_SEASONS,
            n_sims=n_sims,
            limit_per_season=limit_per_season or None,
        )
        typer.echo(_json.dumps(report, indent=2))
    finally:
        db.close()


@app.command(name="validate-player-props")
def validate_player_props_cmd(
    test_seasons: list[int] = [2024, 2025],
    n_sims: int = 500,
    limit_per_season: int = 20,
) -> None:
    """Backtest simulation-based player props against real box scores and
    against the existing trailing-average projection system, no lookahead."""
    import json as _json

    from app.model.player_sim_validation import validate_player_props

    db = SessionLocal()
    try:
        report = validate_player_props(
            db, test_seasons=test_seasons, history_seasons=HISTORY_SEASONS,
            n_sims=n_sims, limit_per_season=limit_per_season or None,
        )
        typer.echo(_json.dumps(report, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    app()
