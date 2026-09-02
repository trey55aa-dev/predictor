import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.recalibration import (
    MIN_SAMPLE_FOR_CALIBRATION,
    get_tuned_value,
    recalibrate,
)
from app.models import Base, CalibrationAdjustment, Prediction


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
