from app.model.play_explainer import explain_scoring_play, explain_stop, is_notable_stop
from app.models import Play


def _play(**kwargs) -> Play:
    defaults = dict(play_key="k", game_id="g", season=2024, week=1, touchdown=False, scoring_play=False)
    defaults.update(kwargs)
    return Play(**defaults)


def test_explain_scoring_run_light_box():
    p = _play(play_type="run", personnel_group="11", defenders_in_box=5, yards_gained=8)
    text = explain_scoring_play(p)
    assert "light box" in text
    assert "5 defenders" in text


def test_explain_scoring_run_stacked_box_explosive():
    p = _play(play_type="run", personnel_group="12", defenders_in_box=8, yards_gained=22)
    text = explain_scoring_play(p)
    assert "stacked box" in text
    assert "explosive" in text.lower()


def test_explain_scoring_pass_play_action_clean_pocket_man_beat():
    p = _play(
        play_type="pass",
        is_play_action=True,
        was_pressure=False,
        man_zone="MAN_COVERAGE",
        pass_length="deep",
    )
    text = explain_scoring_play(p)
    assert "play-action" in text.lower()
    assert "clean pocket" in text.lower()
    assert "man coverage" in text.lower()
    assert "shot down the field" in text.lower()


def test_explain_scoring_pass_zone_window():
    p = _play(play_type="pass", man_zone="ZONE_COVERAGE", coverage_type="COVER_3")
    text = explain_scoring_play(p)
    assert "cover 3" in text.lower()


def test_explain_scoring_falls_back_when_no_charted_data():
    p = _play(play_type="pass", yards_gained=12, epa=1.5)
    text = explain_scoring_play(p)
    assert "detailed charting unavailable" in text.lower()
    assert "12" in text


def test_explain_stop_sack_with_blitz():
    p = _play(play_type="pass", sack=True, n_pass_rushers=5, n_blitzers=2)
    text = explain_stop(p)
    assert "pass rush got home" in text.lower()
    assert "5" in text
    assert "2 blitzer" in text.lower()


def test_explain_stop_interception_zone():
    p = _play(play_type="pass", interception=True, man_zone="ZONE_COVERAGE", coverage_type="COVER_4")
    text = explain_stop(p)
    assert "cover 4" in text.lower()
    assert "undercut" in text.lower()


def test_explain_stop_run_stuffed_numbers():
    p = _play(play_type="run", yards_gained=-2, personnel_group="11", defenders_in_box=7)
    text = explain_stop(p)
    assert "won numbers" in text.lower()


def test_explain_stop_fallback():
    p = _play(play_type="pass", yards_gained=0, epa=-0.8, down=3, success=False)
    text = explain_stop(p)
    assert "detailed charting unavailable" in text.lower() or "held the offense" in text.lower()


def test_is_notable_stop_sack():
    assert is_notable_stop(_play(sack=True))


def test_is_notable_stop_third_down_failure():
    assert is_notable_stop(_play(down=3, success=False))


def test_is_notable_stop_false_on_successful_play():
    assert not is_notable_stop(_play(play_type="pass", down=1, success=True, yards_gained=6))
