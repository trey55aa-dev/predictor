import datetime as dt
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model import review_export
from app.model.review_export import build_review_snapshot, export_review_snapshot
from app.models import Base, CalibrationAdjustment, Game, Injury, Play, Prediction


@pytest.fixture()
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    monkeypatch.setattr(review_export, "current_season", lambda: 2026)
    yield session
    session.close()


def _game(db, game_id, home, away, gameday, home_score=13, away_score=10, status="final"):
    db.add(
        Game(
            game_id=game_id, season=2026, week=1, game_type="REG", gameday=gameday,
            home_team=home, away_team=away, home_score=home_score, away_score=away_score, status=status,
        )
    )


def _prediction(db, game_id, home_win_prob=0.6):
    db.add(
        Prediction(
            game_id=game_id, model_version="test", created_at=dt.datetime.utcnow(),
            home_elo=1500, away_elo=1500, home_win_prob=home_win_prob,
            predicted_home_score=24, predicted_away_score=20, predicted_margin=4, predicted_total=44,
            margin_range_low=-9.5, margin_range_high=17.5, total_range_low=34, total_range_high=54,
        )
    )


def _play(db, game_id, posteam, defteam, yards_gained=10, play_id=1):
    db.add(
        Play(
            play_key=f"{game_id}_{play_id}", game_id=game_id, season=2026, week=1,
            posteam=posteam, defteam=defteam, play_type="run", yards_gained=yards_gained,
        )
    )


def _today_str(offset_days=0):
    return (dt.date.today() + dt.timedelta(days=offset_days)).isoformat()


def test_only_includes_games_within_the_recent_window(db):
    _game(db, "g_recent", "SEA", "NE", gameday=_today_str(-1))
    _prediction(db, "g_recent")
    _play(db, "g_recent", "SEA", "NE")

    stale_day = _today_str(-(review_export.RECENT_GAME_WINDOW_DAYS + 5))
    _game(db, "g_stale", "KC", "DEN", gameday=stale_day)
    _prediction(db, "g_stale")
    _play(db, "g_stale", "KC", "DEN")
    db.commit()

    snapshot = build_review_snapshot(db)

    game_ids = [g["game_id"] for g in snapshot["recent_games"]]
    assert "g_recent" in game_ids
    assert "g_stale" not in game_ids


def test_excludes_games_not_yet_final(db):
    _game(db, "g_live", "SEA", "NE", gameday=_today_str(), status="scheduled", home_score=None, away_score=None)
    db.commit()

    snapshot = build_review_snapshot(db)

    assert snapshot["recent_games"] == []


def test_includes_real_injuries_for_both_teams(db):
    _game(db, "g1", "SEA", "NE", gameday=_today_str())
    _prediction(db, "g1")
    _play(db, "g1", "SEA", "NE")
    db.add(
        Injury(
            season=2026, week=1, team_abbr="NE", gsis_id="00-1", player_name="Star Player",
            position="RB", report_status="Out", is_starter=True, updated_at=dt.datetime.utcnow(),
        )
    )
    db.commit()

    snapshot = build_review_snapshot(db)

    game = snapshot["recent_games"][0]
    assert game["away_injuries"][0]["player_name"] == "Star Player"
    assert game["home_injuries"] == []


def test_includes_recent_calibration_history(db):
    db.add(
        CalibrationAdjustment(
            parameter_name="market_blend_weight", old_value=0.3, new_value=0.25,
            evidence="test evidence", sample_size=100, created_at=dt.datetime.utcnow(),
        )
    )
    db.commit()

    snapshot = build_review_snapshot(db)

    assert snapshot["recent_calibrations"][0]["parameter_name"] == "market_blend_weight"
    assert snapshot["recent_calibrations"][0]["new_value"] == 0.25


def test_export_writes_real_json_to_disk(db, tmp_path):
    _game(db, "g1", "SEA", "NE", gameday=_today_str())
    _prediction(db, "g1")
    _play(db, "g1", "SEA", "NE")
    db.commit()

    out_path = tmp_path / "review" / "model_state.json"
    snapshot = export_review_snapshot(db, out_path)

    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())
    assert on_disk["recent_games"][0]["game_id"] == "g1"
    assert on_disk == json.loads(json.dumps(snapshot, default=str))
