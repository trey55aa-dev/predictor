import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.recalibration import (
    MIN_SAMPLE_FOR_CALIBRATION,
    get_tuned_value,
    recalibrate,
)
from app.models import Base, CalibrationAdjustment, Game, OddsSnapshot, Prediction


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _prediction(
    db,
    game_id,
    elo_win_prob,
    market_win_prob,
    home_won,
    margin_error=None,
    total_error=None,
):
    db.add(
        Prediction(
            game_id=game_id,
            model_version="test",
            created_at=dt.datetime.utcnow(),
            home_elo=1500,
            away_elo=1500,
            home_win_prob=0.5 * elo_win_prob + 0.5 * market_win_prob,
            elo_win_prob=elo_win_prob,
            market_win_prob=market_win_prob,
            predicted_home_score=24,
            predicted_away_score=20,
            predicted_margin=4,
            predicted_total=44,
            margin_range_low=-9.5,
            margin_range_high=17.5,
            total_range_low=34,
            total_range_high=54,
            actual_home_score=27 if home_won else 17,
            actual_away_score=20 if home_won else 24,
            margin_error=margin_error,
            total_error=total_error,
        )
    )


def _graded_game_with_odds(
    db, game_id, home_team, away_team, week,
    spread_line=None, total_line=None,
    actual_home_score=27, actual_away_score=17,
    home_elo=1500, away_elo=1500,
):
    """A graded Prediction plus the real Game and OddsSnapshot it was
    predicted against -- what _margin_samples/_total_samples reconstruct
    elo/market components from (no new columns, see recalibration.py)."""
    db.add(
        Game(
            game_id=game_id, season=2026, week=week, game_type="REG", gameday="2026-09-09",
            home_team=home_team, away_team=away_team,
            home_score=actual_home_score, away_score=actual_away_score, status="final",
        )
    )
    odds = OddsSnapshot(
        game_id=game_id, fetched_at=dt.datetime.utcnow(), bookmaker="test-book",
        spread_line=spread_line, total_line=total_line,
    )
    db.add(odds)
    db.commit()

    db.add(
        Prediction(
            game_id=game_id, model_version="test", created_at=dt.datetime.utcnow(),
            odds_snapshot_id=odds.id,
            home_elo=home_elo, away_elo=away_elo, home_win_prob=0.5,
            predicted_home_score=(actual_home_score + actual_away_score) / 2,
            predicted_away_score=(actual_home_score + actual_away_score) / 2,
            predicted_margin=0, predicted_total=44,
            margin_range_low=-10, margin_range_high=10, total_range_low=34, total_range_high=54,
            actual_home_score=actual_home_score, actual_away_score=actual_away_score,
        )
    )


def test_recalibrate_margin_blend_weight_prefers_market_when_market_is_more_accurate(db):
    # Elo margin is a constant 2.4 on every game (equal ratings, +60
    # home-field bonus, /25); the market's spread nails the real margin (10)
    # exactly. A grid search should shift weight toward the market (down
    # from the 0.4 default).
    for i in range(MIN_SAMPLE_FOR_CALIBRATION + 5):
        _graded_game_with_odds(
            db, f"m{i}", "SEA", "NE", week=1,
            spread_line=-10.0,  # home favored by 10 -> market_margin = 10
            actual_home_score=30, actual_away_score=20,  # actual margin = 10
        )
    db.commit()

    changes = recalibrate(db)
    change = next((c for c in changes if c["parameter"] == "margin_blend_weight"), None)
    assert change is not None
    assert change["new_value"] < change["old_value"]
    assert abs(change["new_value"] - change["old_value"]) <= 0.05 + 1e-9


def test_recalibrate_total_blend_weight_prefers_market_when_market_is_more_accurate(db):
    # Every game uses a brand-new team pair, so team_scoring_averages has no
    # prior history for either team and scoring_expected_total falls back to
    # a constant 44 (2x league average). The market's total nails the real,
    # much higher total (60) exactly, so a grid search should shift weight
    # toward the market.
    for i in range(MIN_SAMPLE_FOR_CALIBRATION + 5):
        _graded_game_with_odds(
            db, f"t{i}", f"T{i}A", f"T{i}B", week=1,
            total_line=60.0,
            actual_home_score=33, actual_away_score=27,  # actual total = 60
        )
    db.commit()

    changes = recalibrate(db)
    change = next((c for c in changes if c["parameter"] == "total_blend_weight"), None)
    assert change is not None
    assert change["new_value"] < change["old_value"]
    assert abs(change["new_value"] - change["old_value"]) <= 0.05 + 1e-9


def test_recalibrate_margin_and_total_blend_weight_do_nothing_below_min_sample(db):
    for i in range(MIN_SAMPLE_FOR_CALIBRATION - 5):
        _graded_game_with_odds(
            db, f"m{i}", "SEA", "NE", week=1, spread_line=-10.0, total_line=60.0,
            actual_home_score=30, actual_away_score=20,
        )
    db.commit()

    changes = recalibrate(db)
    assert not any(c["parameter"] in ("margin_blend_weight", "total_blend_weight") for c in changes)


def test_margin_blend_weight_unaffected_when_no_market_line_exists(db):
    """No spread on any game -- market_margin is always None, so blend_line
    always falls back to the Elo-only value regardless of weight. Error is
    identical at every candidate, so nothing should be nudged toward a
    phantom improvement."""
    for i in range(MIN_SAMPLE_FOR_CALIBRATION + 5):
        _graded_game_with_odds(
            db, f"m{i}", "SEA", "NE", week=1, spread_line=None,
            actual_home_score=30, actual_away_score=20,
        )
    db.commit()

    changes = recalibrate(db)
    assert not any(c["parameter"] == "margin_blend_weight" for c in changes)


def test_total_blend_weight_unaffected_when_no_market_line_exists(db):
    for i in range(MIN_SAMPLE_FOR_CALIBRATION + 5):
        _graded_game_with_odds(
            db, f"t{i}", f"T{i}A", f"T{i}B", week=1, total_line=None,
            actual_home_score=33, actual_away_score=27,
        )
    db.commit()

    changes = recalibrate(db)
    assert not any(c["parameter"] == "total_blend_weight" for c in changes)


def test_get_tuned_value_falls_back_to_default_when_never_tuned(db):
    assert get_tuned_value(db, "market_blend_weight", 0.4) == 0.4


def test_get_tuned_value_returns_latest_adjustment(db):
    db.add(
        CalibrationAdjustment(
            parameter_name="market_blend_weight",
            old_value=0.4,
            new_value=0.45,
            evidence="test",
            sample_size=50,
            created_at=dt.datetime.utcnow(),
        )
    )
    db.commit()
    assert get_tuned_value(db, "market_blend_weight", 0.4) == 0.45


def test_recalibrate_does_nothing_below_min_sample(db):
    for i in range(MIN_SAMPLE_FOR_CALIBRATION - 5):
        _prediction(db, f"g{i}", elo_win_prob=0.9, market_win_prob=0.5, home_won=True)
    db.commit()
    changes = recalibrate(db)
    assert changes == []


def test_recalibrate_prefers_market_when_market_is_more_accurate(db):
    # Elo is consistently overconfident and wrong; market is spot-on.
    # A grid search should favor leaning further toward the market than the
    # current 0.4 default, moving the weight down (at most by the step cap).
    for i in range(MIN_SAMPLE_FOR_CALIBRATION + 20):
        home_won = i % 2 == 0
        _prediction(
            db, f"g{i}",
            elo_win_prob=0.95 if home_won else 0.05,  # elo overconfident but directionally right here
            market_win_prob=0.99 if home_won else 0.01,  # market even more accurate
            home_won=home_won,
        )
    # Make Elo actually WRONG on a chunk of games so the market clearly outperforms it.
    for i in range(15):
        _prediction(
            db, f"wrong{i}",
            elo_win_prob=0.9,  # confidently wrong
            market_win_prob=0.15,  # market correctly leans away
            home_won=False,
        )
    db.commit()

    changes = recalibrate(db)
    blend_change = next((c for c in changes if c["parameter"] == "market_blend_weight"), None)
    assert blend_change is not None
    assert blend_change["new_value"] < blend_change["old_value"]  # shifted toward the market


def test_recalibrate_blend_weight_step_is_capped(db):
    for i in range(MIN_SAMPLE_FOR_CALIBRATION + 15):
        home_won = True
        _prediction(db, f"g{i}", elo_win_prob=0.05, market_win_prob=0.95, home_won=home_won)
    db.commit()

    changes = recalibrate(db)
    blend_change = next((c for c in changes if c["parameter"] == "market_blend_weight"), None)
    assert blend_change is not None
    assert abs(blend_change["new_value"] - blend_change["old_value"]) <= 0.05 + 1e-9


def test_recalibrate_std_dev_sets_observed_value(db):
    # Errors with a known, computable std-dev.
    errors = [10.0, -10.0, 15.0, -15.0, 5.0, -5.0] * 6  # 36 samples, > MIN_SAMPLE_FOR_CALIBRATION
    for i, err in enumerate(errors):
        _prediction(
            db, f"g{i}", elo_win_prob=0.6, market_win_prob=0.6, home_won=True,
            margin_error=err, total_error=err,
        )
    db.commit()

    changes = recalibrate(db)
    margin_change = next((c for c in changes if c["parameter"] == "margin_std_default"), None)
    assert margin_change is not None
    # population stdev of that repeating pattern should differ meaningfully from the 13.5 default
    assert margin_change["new_value"] != margin_change["old_value"]


def test_recalibrate_is_idempotent_with_no_new_data(db):
    for i in range(MIN_SAMPLE_FOR_CALIBRATION + 10):
        _prediction(db, f"g{i}", elo_win_prob=0.05, market_win_prob=0.95, home_won=True)
    db.commit()

    first_changes = recalibrate(db)
    assert len(first_changes) > 0

    second_changes = recalibrate(db)
    # Same data, already-tuned value -- either no further change, or a much
    # smaller one as it converges; it must not oscillate wildly.
    for change in second_changes:
        assert abs(change["new_value"] - change["old_value"]) <= 0.05 + 1e-9
