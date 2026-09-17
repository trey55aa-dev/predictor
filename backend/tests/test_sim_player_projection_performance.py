import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.player_sim import sim_player_projection_performance_summary
from app.models import Base, PlayerGameStat, SimPlayerProjection


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _sim(db, player_id, season=2026, week=1, game_id="2026_01_NE_SEA", team="SEA", **kwargs):
    db.add(
        SimPlayerProjection(
            game_id=game_id, player_id=player_id, player_name=player_id, team=team,
            season=season, week=week, model_version="player-sim-v1", n_sims=3000,
            created_at=dt.datetime.utcnow(), anytime_td_probability=kwargs.pop("anytime_td_probability", 0.5),
            **kwargs,
        )
    )


def _stat(db, player_id, season=2026, week=1, **kwargs):
    db.add(
        PlayerGameStat(
            player_id=player_id, player_name=player_id, position="RB", team="SEA",
            season=season, week=week, game_id=kwargs.pop("game_id", "2026_01_NE_SEA"),
            **kwargs,
        )
    )


def test_no_data_returns_zero_sample(db):
    assert sim_player_projection_performance_summary(db) == {"graded_projections": 0}


def test_projection_without_matching_stat_line_is_excluded(db):
    """The box score for that week hasn't been ingested yet -- not a miss,
    just not gradeable yet."""
    _sim(db, "p1", rushing_mean_yards=80, rushing_p10=50, rushing_p90=110, rushing_td_prob=0.6)
    db.commit()

    assert sim_player_projection_performance_summary(db) == {"graded_projections": 0}


def test_mae_coverage_and_brier_computed_correctly(db):
    # Player A: rushing only. Predicted mean 80 (band 50-110, td_prob 0.6);
    # actual 90 rushing yards with a TD -- inside the band, TD prob was directionally right.
    _sim(
        db, "pA", rushing_mean_yards=80, rushing_p10=50, rushing_p90=110, rushing_td_prob=0.6,
        anytime_td_probability=0.6,
    )
    _stat(db, "pA", rushing_yards=90, rushing_tds=1, receiving_yards=0, receiving_tds=0)

    # Player B: receiving only. Predicted mean 40 (band 20-60, td_prob 0.2);
    # actual only 15 receiving yards, no TD -- outside the band on the low side.
    _sim(
        db, "pB", receiving_mean_yards=40, receiving_p10=20, receiving_p90=60, receiving_td_prob=0.2,
        anytime_td_probability=0.2,
    )
    _stat(db, "pB", rushing_yards=0, rushing_tds=0, receiving_yards=15, receiving_tds=0)
    db.commit()

    summary = sim_player_projection_performance_summary(db)

    assert summary["graded_projections"] == 2
    assert summary["mae"]["rushing"] == pytest.approx(10.0)  # |80-90|
    assert summary["mae"]["receiving"] == pytest.approx(25.0)  # |40-15|
    assert summary["interval_coverage_p10_p90"]["rushing"] == {"hit_rate": 1.0, "n": 1, "target": 0.80}
    assert summary["interval_coverage_p10_p90"]["receiving"] == {"hit_rate": 0.0, "n": 1, "target": 0.80}
    assert summary["td_brier_score"]["rushing"] == pytest.approx((0.6 - 1.0) ** 2)
    assert summary["td_brier_score"]["receiving"] == pytest.approx((0.2 - 0.0) ** 2)
    # anytime_td_probability set to the rushing/receiving td_prob for each player in _sim below
    assert summary["anytime_td_brier_score"] == pytest.approx(((0.6 - 1.0) ** 2 + (0.2 - 0.0) ** 2) / 2)


def test_mae_and_coverage_are_none_for_a_stat_the_player_had_no_projection_for(db):
    """A rushing-only player's SimPlayerProjection leaves receiving_mean_yards
    etc. as None -- must not crash, and must not silently count as a miss."""
    _sim(db, "pA", rushing_mean_yards=50, rushing_p10=30, rushing_p90=70, rushing_td_prob=0.4)
    _stat(db, "pA", rushing_yards=55, rushing_tds=0, receiving_yards=0, receiving_tds=0)
    db.commit()

    summary = sim_player_projection_performance_summary(db)

    assert summary["mae"]["receiving"] is None
    assert summary["td_brier_score"]["receiving"] is None
    assert summary["interval_coverage_p10_p90"]["receiving"] == {"hit_rate": None, "n": 0, "target": 0.80}


def test_filters_by_season_and_week(db):
    _sim(db, "pA", season=2026, week=1, game_id="g1", rushing_mean_yards=50, rushing_p10=30, rushing_p90=70)
    _stat(db, "pA", season=2026, week=1, game_id="g1", rushing_yards=55, rushing_tds=0)

    _sim(db, "pB", season=2026, week=2, game_id="g2", rushing_mean_yards=50, rushing_p10=30, rushing_p90=70)
    _stat(db, "pB", season=2026, week=2, game_id="g2", rushing_yards=55, rushing_tds=0)
    db.commit()

    summary = sim_player_projection_performance_summary(db, season=2026, week=1)

    assert summary["graded_projections"] == 1
