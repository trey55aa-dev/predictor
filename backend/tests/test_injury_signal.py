import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.injury_signal import injury_adjustment
from app.models import Base, Injury


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _injury(db, team, gsis_id, name, status, is_starter, season=2026, week=2):
    db.add(
        Injury(
            season=season, week=week, team_abbr=team, gsis_id=gsis_id, player_name=name,
            position="RB", report_status=status, is_starter=is_starter, updated_at=dt.datetime.utcnow(),
        )
    )


def test_no_adjustment_when_no_injury_report_ingested_yet(db):
    delta, note = injury_adjustment(db, "SEA", 2026, 2)
    assert delta == 0.0
    assert note is None


def test_no_adjustment_but_explicit_note_when_report_exists_with_no_injured_starters(db):
    _injury(db, "SEA", "p1", "Bench Guy", "Questionable", is_starter=False)
    db.commit()

    delta, note = injury_adjustment(db, "SEA", 2026, 2)

    assert delta == 0.0
    assert note == "SEA has no starters listed as injured this week."


def test_one_out_starter_gives_half_the_capped_penalty(db):
    # burden = 1.0 (Out), cap = 2.0 -> net_fraction = -0.5
    _injury(db, "SEA", "p1", "Star RB", "Out", is_starter=True)
    db.commit()

    delta, note = injury_adjustment(db, "SEA", 2026, 2)

    assert delta == pytest.approx(-0.5 * settings.injury_max_adjustment)
    assert "Star RB (Out)" in note


def test_burden_at_or_above_cap_gives_the_full_penalty_not_more(db):
    _injury(db, "SEA", "p1", "RB1", "Out", is_starter=True)
    _injury(db, "SEA", "p2", "WR1", "Injured Reserve", is_starter=True)
    _injury(db, "SEA", "p3", "TE1", "Doubtful", is_starter=True)  # pushes burden past the 2.0 cap
    db.commit()

    delta, _note = injury_adjustment(db, "SEA", 2026, 2)

    assert delta == pytest.approx(-settings.injury_max_adjustment)


def test_non_starter_injuries_are_ignored(db):
    _injury(db, "SEA", "p1", "Bench Guy", "Out", is_starter=False)
    db.commit()

    delta, note = injury_adjustment(db, "SEA", 2026, 2)

    assert delta == 0.0
    assert "no starters" in note


def test_unrecognized_status_uses_the_default_weight(db):
    _injury(db, "SEA", "p1", "Star RB", "Day-To-Day", is_starter=True)
    db.commit()

    delta, _note = injury_adjustment(db, "SEA", 2026, 2)

    # DEFAULT_SEVERITY_WEIGHT (0.25) / cap (2.0) = 0.125 of the max adjustment
    assert delta == pytest.approx(-0.125 * settings.injury_max_adjustment)


def test_note_lists_worst_injuries_first_capped_at_three(db):
    _injury(db, "SEA", "p1", "Mild Guy", "Questionable", is_starter=True)
    _injury(db, "SEA", "p2", "Severe Guy", "Out", is_starter=True)
    _injury(db, "SEA", "p3", "Mid Guy", "Doubtful", is_starter=True)
    _injury(db, "SEA", "p4", "Another Mild Guy", "Probable", is_starter=True)
    db.commit()

    _delta, note = injury_adjustment(db, "SEA", 2026, 2)

    assert note.index("Severe Guy") < note.index("Mid Guy") < note.index("Mild Guy")
    assert "Another Mild Guy" not in note  # only the top 3 worst are named
    assert "4 starter(s)" in note


def test_different_week_does_not_leak_into_this_weeks_signal(db):
    _injury(db, "SEA", "p1", "Star RB", "Out", is_starter=True, week=1)
    db.commit()

    delta, note = injury_adjustment(db, "SEA", 2026, 2)

    assert delta == 0.0
    assert note is None
