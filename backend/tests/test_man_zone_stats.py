import pytest

from app.model.man_zone_stats import team_man_zone_stats
from app.models import Play


def test_computes_rate_faced_and_played_separately():
    plays = [
        # SEA's offense (posteam) faces 2 MAN, 1 ZONE from NE's defense.
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", man_zone="MAN_COVERAGE"),
        Play(play_key="p2", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", man_zone="MAN_COVERAGE"),
        Play(play_key="p3", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", man_zone="ZONE_COVERAGE"),
        # SEA's own defense (defteam) plays 1 ZONE against NE's offense.
        Play(play_key="p4", game_id="g", season=2026, week=1, posteam="NE", defteam="SEA",
             play_type="pass", man_zone="ZONE_COVERAGE"),
    ]

    stats = team_man_zone_stats(plays, "SEA")

    assert stats["man_rate_faced"] == pytest.approx(2 / 3)
    assert stats["zone_rate_faced"] == pytest.approx(1 / 3)
    assert stats["man_rate_played"] == pytest.approx(0.0)
    assert stats["zone_rate_played"] == pytest.approx(1.0)


def test_returns_none_when_no_coverage_data_at_all():
    """This is the expected state for every in-progress-season game right
    now -- current_plays.py doesn't ingest participation data, so man_zone
    is always None. Must read as "no data", not a fabricated 0/0 rate."""
    plays = [
        Play(play_key="p1", game_id="g", season=2026, week=1, posteam="SEA", defteam="NE",
             play_type="pass", man_zone=None),
    ]

    stats = team_man_zone_stats(plays, "SEA")

    assert stats == {
        "man_rate_faced": None,
        "zone_rate_faced": None,
        "man_rate_played": None,
        "zone_rate_played": None,
    }
