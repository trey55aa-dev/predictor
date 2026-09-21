import polars as pl
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.ingestion import plays
from app.models import Base, Game, Play, PlayAdvancedStat, Stadium, Team

# FTN_MIN_SEASON is 2022 -- using an earlier season here keeps this test
# from also needing to fake nfl.load_ftn_charting.
SEASON = 2021


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _game(db, game_id):
    db.add(
        Game(
            game_id=game_id, season=SEASON, week=1, game_type="REG", gameday="2021-09-09",
            home_team="SEA", away_team="NE", home_score=13, away_score=10, status="final",
        )
    )


def _fake_pbp(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def _fake_participation(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def _patch_nflverse(monkeypatch, pbp_rows, participation_rows):
    monkeypatch.setattr(plays.nfl, "load_pbp", lambda seasons: _fake_pbp(pbp_rows))
    monkeypatch.setattr(plays.nfl, "load_participation", lambda seasons: _fake_participation(participation_rows))


def _pbp_row(game_id="2021_01_NE_SEA", play_id=1.0, cpoe=3.5, air_yards=9.0):
    return {
        "game_id": game_id, "play_id": play_id, "season": SEASON, "week": 1,
        "posteam": "SEA", "defteam": "NE", "play_type": "pass",
        "yards_gained": 8.0, "epa": 0.1, "success": True, "touchdown": False,
        "interception": False, "fumble_lost": False, "sack": False,
        "cpoe": cpoe, "air_yards": air_yards,
    }


def _participation_row(game_id="2021_01_NE_SEA", play_id=1.0):
    return {
        "nflverse_game_id": game_id, "play_id": play_id,
        "offense_formation": None, "offense_personnel": None, "defenders_in_box": None,
        "was_pressure": False, "defense_man_zone_type": None, "defense_coverage_type": None,
        "time_to_throw": None,
    }


def test_writes_cpoe_and_air_yards_to_advanced_stats_table(db, monkeypatch):
    _game(db, "2021_01_NE_SEA")
    db.commit()
    _patch_nflverse(monkeypatch, [_pbp_row()], [_participation_row()])

    plays.ingest_plays(db, [SEASON])

    stat = db.query(PlayAdvancedStat).filter(PlayAdvancedStat.game_id == "2021_01_NE_SEA").one()
    assert stat.cpoe == pytest.approx(3.5)
    assert stat.air_yards == pytest.approx(9.0)
    assert stat.play_key == "2021_01_NE_SEA_1"


def test_rerunning_replaces_rather_than_duplicates(db, monkeypatch):
    _game(db, "2021_01_NE_SEA")
    db.commit()
    _patch_nflverse(monkeypatch, [_pbp_row()], [_participation_row()])

    plays.ingest_plays(db, [SEASON])
    plays.ingest_plays(db, [SEASON])

    assert db.query(Play).filter(Play.season == SEASON).count() == 1
    assert db.query(PlayAdvancedStat).filter(PlayAdvancedStat.season == SEASON).count() == 1


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
        _game(session, "2021_01_NE_SEA")
        session.commit()
        _patch_nflverse(monkeypatch, [_pbp_row()], [_participation_row()])

        plays.ingest_plays(session, [SEASON])
        plays.ingest_plays(session, [SEASON])  # must not raise IntegrityError

        assert session.query(PlayAdvancedStat).filter(PlayAdvancedStat.season == SEASON).count() == 1
    finally:
        session.close()
