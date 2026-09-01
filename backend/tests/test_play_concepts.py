from app.model.play_concepts import concept_label
from app.models import Play


def _play(**kwargs) -> Play:
    defaults = dict(play_key="k", game_id="g", season=2024, week=1)
    defaults.update(kwargs)
    return Play(**defaults)


def test_run_concept_with_location_and_gap():
    p = _play(play_type="run", run_location="right", run_gap="tackle", personnel_group="11")
    assert concept_label(p) == "Right Tackle Run (11 personnel)"


def test_run_concept_middle():
    p = _play(play_type="run", run_location="middle", run_gap="guard")
    assert concept_label(p) == "Middle Run"


def test_run_concept_missing_fields():
    p = _play(play_type="run", run_location=None, run_gap=None)
    assert concept_label(p) == "Run (direction unlogged)"


def test_pass_concept_play_action_deep():
    p = _play(play_type="pass", pass_length="deep", pass_location="right", is_play_action=True)
    assert concept_label(p) == "Play-Action Deep Right"


def test_pass_concept_screen():
    p = _play(play_type="pass", pass_length="short", pass_location="left", is_screen_pass=True)
    assert concept_label(p) == "Screen Short Left"


def test_pass_concept_rpo_overrides_play_action():
    p = _play(play_type="pass", pass_length="short", pass_location="middle", is_rpo=True, is_play_action=True)
    assert concept_label(p) == "RPO Short Middle"


def test_pass_concept_plain_dropback():
    p = _play(play_type="pass", pass_length="short", pass_location="right")
    assert concept_label(p) == "Dropback Short Right"


def test_unknown_play_type_falls_back():
    p = _play(play_type="field_goal")
    assert concept_label(p) == "field_goal"
