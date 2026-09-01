import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.model.matchup import defensive_tendencies, scheme_matchup_history, top_offensive_concepts
from app.models import Base, Play


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _play(key, **kwargs):
    defaults = dict(play_key=key, game_id="g", season=2024, week=1, touchdown=False, scoring_play=False)
    defaults.update(kwargs)
    return Play(**defaults)


def test_top_offensive_concepts_ranks_by_frequency(db):
    db.add_all(
        [
            _play("1", posteam="KC", play_type="run", run_location="right", run_gap="tackle"),
            _play("2", posteam="KC", play_type="run", run_location="right", run_gap="tackle"),
            _play("3", posteam="KC", play_type="pass", pass_length="short", pass_location="left"),
        ]
    )
    db.commit()
    result = top_offensive_concepts(db, "KC", 2024)
    assert result[0]["label"] == "Right Tackle Run"
    assert result[0]["count"] == 2
    assert abs(result[0]["share"] - 2 / 3) < 1e-9


def test_defensive_tendencies_computes_rates(db):
    db.add_all(
        [
            _play("1", defteam="SF", man_zone="MAN_COVERAGE", n_blitzers=1, was_pressure=True, defenders_in_box=6),
            _play("2", defteam="SF", man_zone="ZONE_COVERAGE", n_blitzers=0, was_pressure=False, defenders_in_box=8),
        ]
    )
    db.commit()
    result = defensive_tendencies(db, "SF", 2024)
    assert result["sample_size"] == 2
    assert abs(result["man_rate"] - 0.5) < 1e-9
    assert abs(result["blitz_rate"] - 0.5) < 1e-9
    assert abs(result["pressure_rate"] - 0.5) < 1e-9
    assert abs(result["avg_box_count"] - 7.0) < 1e-9


def test_defensive_tendencies_empty(db):
    result = defensive_tendencies(db, "SF", 2024)
    assert result == {"sample_size": 0}


def test_scheme_matchup_history_below_min_sample(db):
    db.add_all([_play(str(i), offense_scheme_id="a", defense_scheme_id="b", epa=1.0, success=True) for i in range(3)])
    db.commit()
    result = scheme_matchup_history(db, "a", "b")
    assert result == {"sample_size": 3}


def test_scheme_matchup_history_computes_stats_above_min_sample(db):
    plays = []
    for i in range(12):
        epa = 1.0 if i < 6 else -1.0
        success = i < 6
        plays.append(_play(str(i), offense_scheme_id="a", defense_scheme_id="b", epa=epa, success=success))
    db.add_all(plays)
    db.commit()
    result = scheme_matchup_history(db, "a", "b")
    assert result["sample_size"] == 12
    assert abs(result["avg_epa"] - 0.0) < 1e-9
    assert abs(result["success_rate"] - 0.5) < 1e-9


def test_scheme_matchup_history_missing_scheme_ids(db):
    result = scheme_matchup_history(db, None, "b")
    assert result == {"sample_size": 0}
