import polars as pl
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.ingestion import current_plays
from app.models import Base, Game, Play, PlayAdvancedStat, Stadium, Team


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(db, game_id, status):
    db.add(
        Game(
            game_id=game_id, season=2026, week=1, game_type="REG", gameday="2026-09-09",
            home_team="SEA", away_team="NE", status=status,
            home_score=13 if status == "final" else None, away_score=10 if status == "final" else None,
        )
    )


def _fake_pbp(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def test_ingests_plays_only_for_final_games(db, monkeypatch):
    _game(db, "2026_01_NE_SEA", status="final")
    _game(db, "2026_01_SF_LA", status="scheduled")  # not yet final -- pbp for this could still change
    db.commit()

    rows = [
        {
            "game_id": "2026_01_NE_SEA", "play_id": 1.0, "week": 1, "posteam": "SEA", "defteam": "NE",
            "play_type": "run", "down": 1, "ydstogo": 10, "yardline_100": 75, "desc": "run play",
            "yards_gained": 8.0, "epa": 0.1, "success": True, "touchdown": False,
            "interception": False, "fumble_lost": False, "sack": False,
        },
        {
            # Belongs to a game that isn't final yet -- must not be ingested.
            "game_id": "2026_01_SF_LA", "play_id": 1.0, "week": 1, "posteam": "SF", "defteam": "LA",
            "play_type": "run", "down": 1, "ydstogo": 10, "yardline_100": 75, "desc": "run play",
            "yards_gained": 3.0, "epa": 0.0, "success": False, "touchdown": False,
            "interception": False, "fumble_lost": False, "sack": False,
        },
    ]
    monkeypatch.setattr(current_plays.nfl, "load_pbp", lambda seasons: _fake_pbp(rows))

    n = current_plays.ingest_current_season_plays(db, 2026)

    assert n == 1
    play = db.query(Play).filter(Play.game_id == "2026_01_NE_SEA").one()
    assert play.yards_gained == 8.0
    assert db.query(Play).filter(Play.game_id == "2026_01_SF_LA").count() == 0


def test_returns_zero_and_makes_no_network_call_when_no_games_are_final(db, monkeypatch):
    _game(db, "2026_01_SF_LA", status="scheduled")
    db.commit()

    def _boom(seasons):
        raise AssertionError("load_pbp should not be called when there's nothing final to ingest")

    monkeypatch.setattr(current_plays.nfl, "load_pbp", _boom)

    n = current_plays.ingest_current_season_plays(db, 2026)

    assert n == 0


def test_rerunning_replaces_rather_than_duplicates(db, monkeypatch):
    _game(db, "2026_01_NE_SEA", status="final")
    db.commit()

    row = {
        "game_id": "2026_01_NE_SEA", "play_id": 1.0, "week": 1, "posteam": "SEA", "defteam": "NE",
        "play_type": "run", "down": 1, "ydstogo": 10, "yardline_100": 75, "desc": "run play",
        "yards_gained": 8.0, "epa": 0.1, "success": True, "touchdown": False,
        "interception": False, "fumble_lost": False, "sack": False,
    }
    monkeypatch.setattr(current_plays.nfl, "load_pbp", lambda seasons: _fake_pbp([row]))

    current_plays.ingest_current_season_plays(db, 2026)
    current_plays.ingest_current_season_plays(db, 2026)

    assert db.query(Play).filter(Play.game_id == "2026_01_NE_SEA").count() == 1


def test_writes_cpoe_and_air_yards_to_advanced_stats_table(db, monkeypatch):
    _game(db, "2026_01_NE_SEA", status="final")
    db.commit()

    row = {
        "game_id": "2026_01_NE_SEA", "play_id": 1.0, "week": 1, "posteam": "SEA", "defteam": "NE",
        "play_type": "pass", "down": 1, "ydstogo": 10, "yardline_100": 75, "desc": "pass play",
        "yards_gained": 8.0, "epa": 0.1, "success": True, "touchdown": False,
        "interception": False, "fumble_lost": False, "sack": False,
        "cpoe": 3.5, "air_yards": 9.0,
    }
    monkeypatch.setattr(current_plays.nfl, "load_pbp", lambda seasons: _fake_pbp([row]))

    current_plays.ingest_current_season_plays(db, 2026)

    stat = db.query(PlayAdvancedStat).filter(PlayAdvancedStat.game_id == "2026_01_NE_SEA").one()
    assert stat.cpoe == pytest.approx(3.5)
    assert stat.air_yards == pytest.approx(9.0)
    assert stat.play_key == "2026_01_NE_SEA_1"


def test_rerunning_replaces_advanced_stats_without_a_foreign_key_violation(monkeypatch):
    """play_advanced_stats.play_key references plays.play_key with no
    DB-level cascade -- deleting Play rows before PlayAdvancedStat rows on
    a re-run would either orphan them or, with FK enforcement on, raise an
    IntegrityError. Runs against a fresh engine with SQLite's FK
    enforcement explicitly turned on (off by default here, unlike the real
    production Postgres DB), so a wrong delete order actually fails this
    test instead of silently passing the way the other tests in this file
    would."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_con, _con_record):
        dbapi_con.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        for abbr in ("SEA", "NE"):
            session.add(Stadium(stadium_id=f"{abbr}01", name=f"{abbr} stadium", roof_type="outdoor"))
            session.add(Team(team_abbr=abbr, name=abbr, stadium_id=f"{abbr}01"))
        session.commit()
        _game(session, "2026_01_NE_SEA", status="final")
        session.commit()

        row = {
            "game_id": "2026_01_NE_SEA", "play_id": 1.0, "week": 1, "posteam": "SEA", "defteam": "NE",
            "play_type": "pass", "down": 1, "ydstogo": 10, "yardline_100": 75, "desc": "pass play",
            "yards_gained": 8.0, "epa": 0.1, "success": True, "touchdown": False,
            "interception": False, "fumble_lost": False, "sack": False,
            "cpoe": 3.5, "air_yards": 9.0,
        }
        monkeypatch.setattr(current_plays.nfl, "load_pbp", lambda seasons: _fake_pbp([row]))

        current_plays.ingest_current_season_plays(session, 2026)
        current_plays.ingest_current_season_plays(session, 2026)  # must not raise IntegrityError

        assert session.query(PlayAdvancedStat).filter(PlayAdvancedStat.game_id == "2026_01_NE_SEA").count() == 1
    finally:
        session.close()
