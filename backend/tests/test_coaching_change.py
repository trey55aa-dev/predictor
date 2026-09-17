import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.model.coaching_change import coaching_change_uncertainty, recent_coaching_changes
from app.models import Base, CoachingChange, Team


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _change(db, team, season, effective_week, role, person, **kwargs):
    if db.get(Team, team) is None:
        db.add(Team(team_abbr=team, name=team))
    db.add(
        CoachingChange(
            team_abbr=team, season=season, effective_week=effective_week, role=role,
            person_name=person, logged_at=dt.datetime.utcnow(), **kwargs,
        )
    )


def test_no_widening_when_nothing_logged(db):
    delta, note = coaching_change_uncertainty(db, "DEN", 2026, 2)
    assert delta == 0.0
    assert note is None


def test_one_change_widens_by_the_per_change_amount(db):
    _change(db, "DEN", 2026, 2, "offensive_play_caller", "Davis Webb", previous_person_name="Sean Payton")
    db.commit()

    delta, note = coaching_change_uncertainty(db, "DEN", 2026, 2)

    assert delta == pytest.approx(settings.coaching_change_uncertainty_per_change)
    assert "DEN" in note
    assert "Davis Webb" in note


def test_multiple_changes_stack_but_are_capped(db):
    _change(db, "NYG", 2026, 1, "head_coach", "New Coach")
    _change(db, "NYG", 2026, 1, "offensive_play_caller", "New OC")
    _change(db, "NYG", 2026, 1, "defensive_play_caller", "New DC")
    db.commit()

    delta, _note = coaching_change_uncertainty(db, "NYG", 2026, 1)

    assert delta == pytest.approx(settings.coaching_change_uncertainty_cap)


def test_change_outside_recency_window_no_longer_applies(db):
    _change(db, "DAL", 2026, 1, "defensive_play_caller", "New DC")
    db.commit()

    # RECENCY_WINDOW_WEEKS is 4 -- week 1 + 4 = week 5 is outside it.
    delta, note = coaching_change_uncertainty(db, "DAL", 2026, 5)

    assert delta == 0.0
    assert note is None


def test_change_effective_in_the_future_does_not_apply_yet(db):
    _change(db, "DAL", 2026, 5, "defensive_play_caller", "New DC")
    db.commit()

    delta, note = coaching_change_uncertainty(db, "DAL", 2026, 2)

    assert delta == 0.0
    assert note is None


def test_a_different_teams_change_does_not_leak_over(db):
    _change(db, "DAL", 2026, 1, "defensive_play_caller", "New DC")
    db.commit()

    delta, note = coaching_change_uncertainty(db, "NYG", 2026, 2)

    assert delta == 0.0
    assert note is None


def test_recent_coaching_changes_orders_most_recent_first(db):
    _change(db, "NYG", 2026, 1, "head_coach", "New Coach")
    _change(db, "NYG", 2026, 3, "offensive_play_caller", "New OC")
    db.commit()

    changes = recent_coaching_changes(db, "NYG", 2026, 4)

    assert [c.role for c in changes] == ["offensive_play_caller", "head_coach"]
