"""Buckets individual plays into a 'concept' label for frequency counting.

Honesty note: nflverse's free data gives us run direction (location + gap)
and pass depth/location/flags, but not actual playbook terminology or
blocking-scheme identity (e.g. we can't tell outside zone from power/duo
just from "right tackle"). Labels are built only from what's actually in
the data -- direction, depth, and charted flags -- not guessed scheme names.
"""

from app.models import Play

_LOCATION_LABELS = {"left": "Left", "right": "Right", "middle": "Middle"}
_GAP_LABELS = {"end": "End", "tackle": "Tackle", "guard": "Guard"}
_LENGTH_LABELS = {"short": "Short", "deep": "Deep"}


def _personnel_suffix(play: Play) -> str:
    return f" ({play.personnel_group} personnel)" if play.personnel_group else ""


def _run_concept(play: Play) -> str:
    location = _LOCATION_LABELS.get(play.run_location or "", None)
    gap = _GAP_LABELS.get(play.run_gap or "", None)
    if location and gap and location != "Middle":
        base = f"{location} {gap} Run"
    elif location:
        base = f"{location} Run"
    elif gap:
        base = f"{gap} Run"
    else:
        base = "Run (direction unlogged)"
    return base + _personnel_suffix(play)


def _pass_concept(play: Play) -> str:
    length = _LENGTH_LABELS.get(play.pass_length or "", "")
    location = _LOCATION_LABELS.get(play.pass_location or "", "")
    depth_dir = " ".join(part for part in [length, location] if part)

    if play.is_screen_pass:
        prefix = "Screen"
    elif play.is_rpo:
        prefix = "RPO"
    elif play.is_play_action:
        prefix = "Play-Action"
    else:
        prefix = "Dropback"

    base = f"{prefix} {depth_dir}".strip() if depth_dir else prefix
    return base + _personnel_suffix(play)


def concept_label(play: Play) -> str:
    if play.play_type == "run":
        return _run_concept(play)
    if play.play_type == "pass":
        return _pass_concept(play)
    return play.play_type or "Unknown"
