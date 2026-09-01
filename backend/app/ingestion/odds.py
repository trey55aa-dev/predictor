"""Pulls live NFL odds from The Odds API and stores them as odds_snapshots."""

import datetime as dt
import statistics

import httpx
from sqlalchemy.orm import Session

from app.config import settings
from app.ingestion.team_name_map import to_abbr
from app.models import Game, OddsSnapshot

ODDS_API_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"


def _find_matching_game(db: Session, home_abbr: str, away_abbr: str, commence_time: dt.datetime) -> Game | None:
    candidates = (
        db.query(Game)
        .filter(Game.home_team == home_abbr, Game.away_team == away_abbr)
        .all()
    )
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    # Multiple historical meetings (e.g. different seasons): pick the one whose
    # gameday is closest to the odds event's commence date.
    target_date = commence_time.date()

    def _distance(game: Game) -> int:
        game_date = dt.date.fromisoformat(game.gameday)
        return abs((game_date - target_date).days)

    return min(candidates, key=_distance)


def _american_to_implied_prob(price: float) -> float:
    if price > 0:
        return 100 / (price + 100)
    return -price / (-price + 100)


def ingest_odds(db: Session, api_key: str | None = None) -> int:
    """Fetch current NFL odds and store one consensus snapshot per matched game."""
    key = api_key or settings.odds_api_key
    if not key:
        raise RuntimeError("ODDS_API_KEY is not set (see backend/.env.example)")

    response = httpx.get(
        ODDS_API_URL,
        params={
            "regions": "us",
            "markets": "h2h,spreads,totals",
            "oddsFormat": "american",
            "apiKey": key,
        },
        timeout=30,
    )
    response.raise_for_status()
    events = response.json()

    fetched_at = dt.datetime.utcnow()
    count = 0

    for event in events:
        home_name = event.get("home_team")
        away_name = event.get("away_team")
        home_abbr = to_abbr(home_name) if home_name else None
        away_abbr = to_abbr(away_name) if away_name else None
        if not home_abbr or not away_abbr:
            continue

        commence_time = dt.datetime.fromisoformat(event["commence_time"].replace("Z", "+00:00"))
        game = _find_matching_game(db, home_abbr, away_abbr, commence_time)
        if game is None:
            continue

        home_moneylines: list[float] = []
        away_moneylines: list[float] = []
        home_spreads: list[float] = []
        totals: list[float] = []

        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                outcomes = market.get("outcomes", [])
                if market["key"] == "h2h":
                    for outcome in outcomes:
                        if outcome["name"] == home_name:
                            home_moneylines.append(outcome["price"])
                        elif outcome["name"] == away_name:
                            away_moneylines.append(outcome["price"])
                elif market["key"] == "spreads":
                    for outcome in outcomes:
                        if outcome["name"] == home_name:
                            home_spreads.append(outcome["point"])
                elif market["key"] == "totals":
                    for outcome in outcomes:
                        if outcome["name"] == "Over":
                            totals.append(outcome["point"])

        if not (home_moneylines and away_moneylines):
            continue

        snapshot = OddsSnapshot(
            game_id=game.game_id,
            fetched_at=fetched_at,
            bookmaker="consensus",
            home_moneyline=statistics.mean(home_moneylines),
            away_moneyline=statistics.mean(away_moneylines),
            spread_line=statistics.mean(home_spreads) if home_spreads else None,
            total_line=statistics.mean(totals) if totals else None,
        )
        db.add(snapshot)
        count += 1

    db.commit()
    return count
