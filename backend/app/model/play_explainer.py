"""Rules-based 'why it worked' explanations, grounded only in real charted
fields (participation data + FTN charting) -- not a learned model. Falls back
to a plainer EPA/yardage-based explanation when charted detail is missing
(mostly pre-2022 plays, before FTN charting existed).

This is intentionally simple/heuristic for v1, same spirit as the predictor's
weather-adjustment heuristics -- a documented starting point meant to be
refined as the grading loop accumulates more evidence, not a claim of
film-level scouting precision.
"""

from app.models import Play

_COVERAGE_LABELS = {
    "COVER_0": "Cover 0 (no deep help)",
    "COVER_1": "Cover 1 (single-high man)",
    "COVER_2": "Cover 2",
    "2_MAN": "2-Man",
    "COVER_3": "Cover 3",
    "COVER_4": "Cover 4 (quarters)",
    "COVER_6": "Cover 6",
    "COVER_9": "Cover 9",
    "COMBO": "combo coverage",
    "BLOWN": "a blown coverage",
}


def _estimate_blockers(play: Play) -> int | None:
    """Rough blocker estimate: 5 offensive linemen + the TE count from the
    personnel grouping (e.g. '12' personnel -> 5 + 2 TEs = 7). A heuristic,
    not a snap-by-snap blocking assignment -- doesn't know pass-protecting
    RBs or motion-in extra blockers."""
    if not play.personnel_group or len(play.personnel_group) != 2:
        return None
    try:
        te_count = int(play.personnel_group[1])
    except ValueError:
        return None
    return 5 + te_count


def _coverage_phrase(play: Play) -> str | None:
    if play.coverage_type and play.coverage_type in _COVERAGE_LABELS:
        return _COVERAGE_LABELS[play.coverage_type]
    if play.man_zone == "MAN_COVERAGE":
        return "man coverage"
    if play.man_zone == "ZONE_COVERAGE":
        return "zone coverage"
    return None


def explain_scoring_play(play: Play) -> str:
    """Why an offensive scoring play worked."""
    clauses: list[str] = []

    if play.play_type == "run":
        blockers = _estimate_blockers(play)
        if play.defenders_in_box is not None and blockers is not None:
            box = play.defenders_in_box
            if box < blockers:
                clauses.append(f"attacked a light box ({box:.0f} defenders vs. ~{blockers} blockers)")
            elif box > blockers:
                clauses.append(f"broke through despite a stacked box ({box:.0f} defenders vs. ~{blockers} blockers)")
        if play.yards_gained is not None and play.yards_gained >= 15:
            clauses.append("then turned it into an explosive gain in the open field")

    elif play.play_type == "pass":
        if play.is_play_action:
            clauses.append("play-action held the defense's eyes in the backfield")
        elif play.is_rpo:
            clauses.append("an RPO put a defender in run/pass conflict")
        if play.was_pressure is False:
            clauses.append("thrown from a clean pocket")
        elif play.was_pressure is True:
            clauses.append("delivered even with pressure in his face")
        coverage = _coverage_phrase(play)
        if coverage:
            if play.man_zone == "MAN_COVERAGE":
                clauses.append(f"won {coverage} downfield")
            else:
                clauses.append(f"found the window in {coverage}")
        if play.pass_length == "deep":
            clauses.append("connecting on a shot down the field")

    if not clauses:
        # Charted detail unavailable (mostly pre-2022) -- fall back to EPA/yardage.
        yards = play.yards_gained if play.yards_gained is not None else 0
        epa_note = f", EPA {play.epa:+.2f}" if play.epa is not None else ""
        clauses.append(f"a {yards:.0f}-yard scoring play{epa_note} (detailed charting unavailable for this season)")

    return "; ".join(clauses).capitalize() + "."


def explain_stop(play: Play) -> str:
    """Why a defensive 'stop' worked (sack, TFL, turnover, or a failed-success
    play on a passing down)."""
    clauses: list[str] = []

    if play.sack:
        rush_note = f" rushing {play.n_pass_rushers:.0f}" if play.n_pass_rushers is not None else ""
        blitz_note = f", including {play.n_blitzers:.0f} blitzer(s)" if play.n_blitzers else ""
        clauses.append(f"the pass rush got home{rush_note}{blitz_note}")
    elif play.interception:
        coverage = _coverage_phrase(play)
        if coverage:
            clauses.append(f"{coverage} sat on the route and undercut the throw")
        else:
            clauses.append("the defender read the throw and undercut the route")
    elif play.fumble_lost:
        clauses.append("a physical tackle forced the ball out")
    elif play.play_type == "run" and (play.yards_gained or 0) <= 0:
        blockers = _estimate_blockers(play)
        if play.defenders_in_box is not None and blockers is not None:
            box = play.defenders_in_box
            if box >= blockers:
                clauses.append(f"won numbers at the point of attack ({box:.0f} defenders vs. ~{blockers} blockers)")
            else:
                clauses.append("won the individual matchup at the line despite being outnumbered")
        else:
            clauses.append("the front won at the line of scrimmage")
    elif play.play_type == "pass":
        coverage = _coverage_phrase(play)
        if coverage:
            clauses.append(f"{coverage} took away the throwing window")
        elif play.was_pressure:
            clauses.append("pressure forced an incompletion before the route developed")

    if not clauses:
        epa_note = f" (EPA {play.epa:+.2f})" if play.epa is not None else ""
        clauses.append(f"held the offense below a successful play{epa_note} (detailed charting unavailable for this season)")

    return "; ".join(clauses).capitalize() + "."


def is_notable_stop(play: Play) -> bool:
    """What counts as a defensive 'stop' worth explaining."""
    if play.sack or play.interception or play.fumble_lost:
        return True
    if play.play_type == "run" and (play.yards_gained or 0) <= 0:
        return True
    if play.down in (3, 4) and play.success is False:
        return True
    return False
