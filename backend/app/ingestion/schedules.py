"""Seed reference data (stadiums/teams) and ingest games from nflverse via nflreadpy."""

import datetime as dt
import json
from pathlib import Path

import nflreadpy as nfl
from sqlalchemy.orm import Session

from app.models import Game, Stadium, Team

STADIUMS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "stadiums.json"


def seed_reference_data(db: Session) -> None:
    """Seed stadiums + teams. Safe to re-run (upserts by primary key)."""
    stadiums = json.loads(STADIUMS_PATH.read_text())

    for stadium_id, info in stadiums.items():
        existing = db.get(Stadium, stadium_id)
        if existing is None:
            existing = Stadium(stadium_id=stadium_id)
            db.add(existing)
        existing.name = info["name"]
        existing.lat = info["lat"]
        existing.lon = info["lon"]
        existing.roof_type = info["roof_type"]

    team_to_stadium = {
        team_abbr: stadium_id
        for stadium_id, info in stadiums.items()
        for team_abbr in info["teams"]
    }

    for row in nfl.load_teams().iter_rows(named=True):
        team_abbr = row["team_abbr"]
        existing_team = db.get(Team, team_abbr)
        if existing_team is None:
            existing_team = Team(team_abbr=team_abbr)
            db.add(existing_team)
        existing_team.name = row["team_name"]
        existing_team.conference = row["team_conf"]
        existing_team.division = row["team_division"]
        existing_team.stadium_id = team_to_stadium.get(team_abbr)

    db.commit()


def _parse_gametime_utc(gameday: str | None, gametime: str | None) -> dt.datetime | None:
    if not gameday or not gametime:
        return None
    try:
        # nflverse gametime is local kickoff time (US Eastern-ish, already offset
        # in the source data is inconsistent); store as naive local-ish datetime,
        # good enough for "nearest hour" weather lookups and display ordering.
        return dt.datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # filter NaN


def _safe_int(value: object) -> int | None:
    f = _safe_float(value)
    return None if f is None else int(f)


def ingest_schedules(db: Session, seasons: list[int]) -> int:
    """Upsert games (and any missing teams) for the given seasons. Returns row count."""
    rows = nfl.load_schedules(seasons=seasons).iter_rows(named=True)

    known_teams = {t.team_abbr for t in db.query(Team).all()}

    count = 0
    for row in rows:
        for team_abbr in (row["home_team"], row["away_team"]):
            if team_abbr not in known_teams:
                db.add(Team(team_abbr=team_abbr, name=team_abbr))
                known_teams.add(team_abbr)

        game = db.get(Game, row["game_id"])
        if game is None:
            game = Game(game_id=row["game_id"])
            db.add(game)

        home_score = _safe_int(row.get("home_score"))
        away_score = _safe_int(row.get("away_score"))

        game.season = row["season"]
        game.week = row["week"]
        game.game_type = row["game_type"]
        game.gameday = row["gameday"]
        game.gametime_utc = _parse_gametime_utc(row["gameday"], row.get("gametime"))
        game.home_team = row["home_team"]
        game.away_team = row["away_team"]
        game.home_score = home_score
        game.away_score = away_score
        game.stadium_id = row.get("stadium_id") if isinstance(row.get("stadium_id"), str) else None
        game.status = "final" if home_score is not None and away_score is not None else "scheduled"
        game.home_coach = row.get("home_coach")
        game.away_coach = row.get("away_coach")

        game.close_spread_line = _safe_float(row.get("spread_line"))
        game.close_total_line = _safe_float(row.get("total_line"))
        game.close_home_moneyline = _safe_float(row.get("home_moneyline"))
        game.close_away_moneyline = _safe_float(row.get("away_moneyline"))

        count += 1

    db.commit()
    return count
