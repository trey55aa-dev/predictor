import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.ingestion import espn_injuries
from app.models import Base, Injury, SnapCount, Team


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_teams_payload(teams: list[tuple[str, str]]):
    return {
        "sports": [
            {
                "leagues": [
                    {"teams": [{"team": {"abbreviation": abbr, "id": tid}} for abbr, tid in teams]}
                ]
            }
        ]
    }


def _fake_roster_payload(players: list[dict]):
    return {"athletes": [{"items": players}]}


def _snap(db, player_id, season, week, offense_pct, id_mapped=True):
    db.add(
        SnapCount(
            game_id=f"g_{season}_{week}", season=season, week=week, player_id=player_id,
            id_mapped=id_mapped, player_name=player_id, position="RB", team="TEAM",
            offense_snaps=40, offense_pct=offense_pct,
        )
    )


def test_espn_team_ids_maps_was_to_wsh(db, monkeypatch):
    db.add(Team(team_abbr="WAS", name="Washington"))
    db.add(Team(team_abbr="SEA", name="Seattle"))
    db.commit()

    monkeypatch.setattr(
        espn_injuries.httpx,
        "get",
        lambda *a, **k: FakeResponse(_fake_teams_payload([("WSH", "28"), ("SEA", "26"), ("KC", "12")])),
    )

    result = espn_injuries._espn_team_ids(db)

    assert result == {"WAS": "28", "SEA": "26"}  # KC excluded -- not one of our teams


def test_espn_team_ids_prefers_the_real_team_code_over_an_unused_duplicate(db, monkeypatch):
    """The bug this guards against: our Team table has both "LA" (used by
    every real game) and a stale duplicate "LAR" row for the same
    franchise. "LAR" also happens to be ESPN's own abbreviation, so without
    an explicit exclusion it silently wins the string match and the Rams'
    real injury data lands under a team code nothing else in the app
    queries -- confirmed live, on a day the Rams were actually playing."""
    db.add(Team(team_abbr="LA", name="Los Angeles Rams"))
    db.add(Team(team_abbr="LAR", name="Los Angeles Rams"))
    db.commit()

    monkeypatch.setattr(
        espn_injuries.httpx, "get", lambda *a, **k: FakeResponse(_fake_teams_payload([("LAR", "14")]))
    )

    result = espn_injuries._espn_team_ids(db)

    assert result == {"LA": "14"}


def test_starter_ids_uses_most_recent_snap_share_only(db):
    # Player crossed the starter threshold long ago, but their most recent
    # game was a token appearance -- the most recent game should decide.
    _snap(db, "declining_player", 2024, 1, offense_pct=0.9)
    _snap(db, "declining_player", 2025, 5, offense_pct=0.1)
    _snap(db, "rising_player", 2024, 1, offense_pct=0.1)
    _snap(db, "rising_player", 2025, 5, offense_pct=0.8)
    # Unmapped rows (id_mapped=False) shouldn't be trusted for this signal.
    _snap(db, "unmapped_player", 2025, 5, offense_pct=0.9, id_mapped=False)
    db.commit()

    starters = espn_injuries._starter_ids(db)

    assert "declining_player" not in starters
    assert "rising_player" in starters
    assert "unmapped_player" not in starters


def test_ingest_espn_injuries_end_to_end(db, monkeypatch):
    db.add(Team(team_abbr="NE", name="New England"))
    db.commit()

    monkeypatch.setattr(espn_injuries, "_espn_team_ids", lambda db: {"NE": "17"})
    monkeypatch.setattr(espn_injuries, "_espn_to_gsis_map", lambda: {"4432710": "00-0040734", "999": "00-9999999"})

    roster = _fake_roster_payload(
        [
            {
                "id": "4432710",
                "fullName": "TreVeyon Henderson",
                "position": {"abbreviation": "RB"},
                "injuries": [{"status": "Out", "date": "2026-09-08T21:05Z"}],
            },
            {
                "id": "1",
                "fullName": "Healthy Player",
                "position": {"abbreviation": "WR"},
                "injuries": [],
            },
            {
                # No GSIS crosswalk match -- should be skipped, not crash.
                "id": "unmapped_espn_id",
                "fullName": "Unmapped Guy",
                "position": {"abbreviation": "TE"},
                "injuries": [{"status": "Questionable", "date": "2026-09-08T21:05Z"}],
            },
        ]
    )
    monkeypatch.setattr(espn_injuries.httpx, "get", lambda *a, **k: FakeResponse(roster))

    n = espn_injuries.ingest_espn_injuries(db, season=2026, week=1)

    assert n == 1
    row = db.query(Injury).filter(Injury.gsis_id == "00-0040734").first()
    assert row.report_status == "Out"
    assert row.team_abbr == "NE"
    assert row.player_name == "TreVeyon Henderson"


def test_ingest_espn_injuries_marks_starters_from_real_snap_data(db, monkeypatch):
    db.add(Team(team_abbr="NE", name="New England"))
    _snap(db, "00-0040734", 2025, 10, offense_pct=0.75)
    db.commit()

    monkeypatch.setattr(espn_injuries, "_espn_team_ids", lambda db: {"NE": "17"})
    monkeypatch.setattr(espn_injuries, "_espn_to_gsis_map", lambda: {"4432710": "00-0040734"})
    roster = _fake_roster_payload(
        [
            {
                "id": "4432710", "fullName": "TreVeyon Henderson", "position": {"abbreviation": "RB"},
                "injuries": [{"status": "Out", "date": "2026-09-08T21:05Z"}],
            }
        ]
    )
    monkeypatch.setattr(espn_injuries.httpx, "get", lambda *a, **k: FakeResponse(roster))

    espn_injuries.ingest_espn_injuries(db, season=2026, week=1)

    row = db.query(Injury).filter(Injury.gsis_id == "00-0040734").first()
    assert row.is_starter is True


def test_ingest_espn_injuries_replaces_stale_rows_for_the_same_week(db, monkeypatch):
    """Re-running mid-week as a status changes (Wednesday Questionable ->
    Sunday Out) should reflect the latest report, not pile up duplicates."""
    db.add(Team(team_abbr="NE", name="New England"))
    db.add(
        Injury(
            season=2026, week=1, team_abbr="NE", gsis_id="00-0040734", player_name="TreVeyon Henderson",
            position="RB", report_status="Questionable", is_starter=True, updated_at=dt.datetime.utcnow(),
        )
    )
    db.commit()

    monkeypatch.setattr(espn_injuries, "_espn_team_ids", lambda db: {"NE": "17"})
    monkeypatch.setattr(espn_injuries, "_espn_to_gsis_map", lambda: {"4432710": "00-0040734"})
    roster = _fake_roster_payload(
        [
            {
                "id": "4432710", "fullName": "TreVeyon Henderson", "position": {"abbreviation": "RB"},
                "injuries": [{"status": "Out", "date": "2026-09-08T21:05Z"}],
            }
        ]
    )
    monkeypatch.setattr(espn_injuries.httpx, "get", lambda *a, **k: FakeResponse(roster))

    espn_injuries.ingest_espn_injuries(db, season=2026, week=1)

    rows = db.query(Injury).filter(Injury.season == 2026, Injury.week == 1, Injury.gsis_id == "00-0040734").all()
    assert len(rows) == 1
    assert rows[0].report_status == "Out"
