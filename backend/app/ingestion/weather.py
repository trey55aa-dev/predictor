"""Pulls game-day weather forecasts from Open-Meteo for outdoor-stadium games."""

import datetime as dt

import httpx
from sqlalchemy.orm import Session

from app.models import Game, Stadium, WeatherSnapshot

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def ingest_weather_for_games(db: Session, games: list[Game]) -> int:
    """Fetch and store a weather snapshot for each outdoor-stadium game in `games`.

    Games at dome/retractable stadiums are skipped (weather_snapshots row marked
    applicable=False) since conditions inside are not weather-dependent.
    """
    fetched_at = dt.datetime.utcnow()
    count = 0

    for game in games:
        stadium = db.get(Stadium, game.stadium_id) if game.stadium_id else None

        if stadium is None or stadium.roof_type != "outdoor":
            db.add(
                WeatherSnapshot(
                    game_id=game.game_id,
                    fetched_at=fetched_at,
                    applicable=False,
                )
            )
            count += 1
            continue

        if game.gametime_utc is None:
            continue

        response = httpx.get(
            OPEN_METEO_URL,
            params={
                "latitude": stadium.lat,
                "longitude": stadium.lon,
                "hourly": "temperature_2m,wind_speed_10m,precipitation",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "precipitation_unit": "mm",
                "forecast_days": 16,
                "timezone": "UTC",
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            continue

        target = game.gametime_utc
        best_idx = min(
            range(len(times)),
            key=lambda i: abs((dt.datetime.fromisoformat(times[i]) - target).total_seconds()),
        )

        db.add(
            WeatherSnapshot(
                game_id=game.game_id,
                fetched_at=fetched_at,
                forecast_for_utc=dt.datetime.fromisoformat(times[best_idx]),
                temp_f=hourly.get("temperature_2m", [None] * len(times))[best_idx],
                wind_mph=hourly.get("wind_speed_10m", [None] * len(times))[best_idx],
                precip_mm=hourly.get("precipitation", [None] * len(times))[best_idx],
                applicable=True,
            )
        )
        count += 1

    db.commit()
    return count
