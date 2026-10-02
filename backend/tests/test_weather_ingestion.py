import datetime as dt

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingestion import weather as weather_module
from app.ingestion.weather import ingest_weather_for_games
from app.models import Base, Game, Stadium, Team, WeatherSnapshot


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_outdoor_game(db, game_id: str, stadium_id: str) -> Game:
    stadium = Stadium(stadium_id=stadium_id, name=stadium_id, lat=39.0, lon=-94.0, roof_type="outdoor")
    db.add(stadium)
    team = Team(team_abbr=game_id[-3:], name=game_id, stadium_id=stadium_id)
    db.add(team)
    game = Game(
        game_id=game_id,
        season=2026,
        week=3,
        game_type="REG",
        gameday="2026-09-21",
        gametime_utc=dt.datetime(2026, 9, 21, 17, 0),
        home_team=team.team_abbr,
        away_team=team.team_abbr,
        stadium_id=stadium_id,
    )
    db.add(game)
    db.commit()
    return game


def test_ingest_weather_skips_a_game_whose_fetch_times_out_instead_of_crashing(db, monkeypatch):
    """The real bug this guards against: a single slow/unreachable Open-Meteo
    request raised httpx.ConnectTimeout with nothing catching it, which
    propagated out of ingest_weather_for_games, out of run_routine_cmd (which
    wraps every other best-effort ingestion step in its own try/except but
    not this one), and crashed the entire scheduled routine -- skipping
    predictions, injury ingestion, grading, and the review snapshot for
    every other game in the week too. Confirmed live in GitHub Actions run
    36928696334: 'ConnectTimeout: _ssl.c:999: The handshake operation timed
    out' inside ingest_weather_for_games, exit code 1, nothing downstream ran."""
    game = _make_outdoor_game(db, "2026_03_KC_MIA", "stadium-1")

    def _raise(*args, **kwargs):
        raise httpx.ConnectTimeout("The handshake operation timed out")

    monkeypatch.setattr(weather_module.httpx, "get", _raise)

    count = ingest_weather_for_games(db, [game])

    assert count == 0
    assert db.query(WeatherSnapshot).filter(WeatherSnapshot.game_id == game.game_id).count() == 0


def test_ingest_weather_keeps_processing_later_games_after_one_fetch_fails(db, monkeypatch):
    failing_game = _make_outdoor_game(db, "2026_03_PHI_CHI", "stadium-2")
    ok_game = _make_outdoor_game(db, "2026_03_BAL_DAL", "stadium-3")

    calls = {"n": 0}

    def _side_effect(url, params, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectTimeout("The handshake operation timed out")
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-09-21T17:00"],
                    "temperature_2m": [72.0],
                    "wind_speed_10m": [5.0],
                    "precipitation": [0.0],
                }
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(weather_module.httpx, "get", _side_effect)

    count = ingest_weather_for_games(db, [failing_game, ok_game])

    assert count == 1
    assert db.query(WeatherSnapshot).filter(WeatherSnapshot.game_id == failing_game.game_id).count() == 0
    stored = db.query(WeatherSnapshot).filter(WeatherSnapshot.game_id == ok_game.game_id).one()
    assert stored.applicable is True
    assert stored.temp_f == 72.0
