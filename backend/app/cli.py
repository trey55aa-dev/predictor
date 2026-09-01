import datetime as dt

import typer

from app.db import SessionLocal, create_all
from app.ingestion.injuries import NoInjuryDataError, ingest_injuries
from app.ingestion.odds import ingest_odds
from app.ingestion.player_stats import ingest_player_stats
from app.ingestion.plays import ingest_plays
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
from app.model.schedule_context import current_or_next_week, current_season, should_run_dense_cadence
from app.models import Game

app = typer.Typer()

HISTORY_SEASONS = [2021, 2022, 2023, 2024, 2025]

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
            typer.echo(f"Skipping injury ingestion: {e}")
    finally:
        db.close()


@app.command(name="ingest-injuries")
def ingest_injuries_cmd(season: int, week: int) -> None:
    """Ingest one week's injury reports (ranked by starter status)."""
    db = SessionLocal()
    try:
        n = ingest_injuries(db, season, week)
        typer.echo(f"Ingested {n} injury reports for {season} week {week}.")
    except NoInjuryDataError as e:
        typer.echo(f"No injury data available: {e}")
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


@app.command()
def performance(season: int | None = None, week: int | None = None) -> None:
    """Show rolling model performance."""
    db = SessionLocal()
    try:
        typer.echo(performance_summary(db, season=season, week=week))
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
                typer.echo(f"Skipping injury ingestion: {e}")

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


if __name__ == "__main__":
    app()
