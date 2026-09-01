"""Team play-style summaries and scheme-vs-scheme historical matchup stats,
built entirely from the already-ingested `plays` fact table."""

from collections import Counter

from sqlalchemy.orm import Session

from app.model.play_concepts import concept_label
from app.models import Play

MIN_MATCHUP_SAMPLE = 10


def top_offensive_concepts(db: Session, team_abbr: str, season: int, limit: int = 3) -> list[dict]:
    plays = db.query(Play).filter(Play.posteam == team_abbr, Play.season == season).all()
    counts: Counter[str] = Counter()
    for play in plays:
        counts[concept_label(play)] += 1
    total = sum(counts.values())
    return [
        {"label": label, "count": count, "share": count / total if total else 0.0}
        for label, count in counts.most_common(limit)
    ]


def defensive_tendencies(db: Session, team_abbr: str, season: int) -> dict:
    plays = db.query(Play).filter(Play.defteam == team_abbr, Play.season == season).all()
    n = len(plays)
    if n == 0:
        return {"sample_size": 0}

    man_calls = sum(1 for p in plays if p.man_zone == "MAN_COVERAGE")
    zone_calls = sum(1 for p in plays if p.man_zone == "ZONE_COVERAGE")
    coverage_calls = man_calls + zone_calls
    blitzes = sum(1 for p in plays if (p.n_blitzers or 0) > 0)
    blitz_eligible = sum(1 for p in plays if p.n_blitzers is not None)
    pressures = sum(1 for p in plays if p.was_pressure)
    pressure_eligible = sum(1 for p in plays if p.was_pressure is not None)
    boxes = [p.defenders_in_box for p in plays if p.defenders_in_box is not None]

    return {
        "sample_size": n,
        "man_rate": man_calls / coverage_calls if coverage_calls else None,
        "zone_rate": zone_calls / coverage_calls if coverage_calls else None,
        "blitz_rate": blitzes / blitz_eligible if blitz_eligible else None,
        "pressure_rate": pressures / pressure_eligible if pressure_eligible else None,
        "avg_box_count": sum(boxes) / len(boxes) if boxes else None,
    }


def scheme_matchup_history(db: Session, offense_scheme_id: str | None, defense_scheme_id: str | None) -> dict:
    if not offense_scheme_id or not defense_scheme_id:
        return {"sample_size": 0}

    plays = (
        db.query(Play)
        .filter(Play.offense_scheme_id == offense_scheme_id, Play.defense_scheme_id == defense_scheme_id)
        .all()
    )
    n = len(plays)
    if n < MIN_MATCHUP_SAMPLE:
        return {"sample_size": n}

    epas = [p.epa for p in plays if p.epa is not None]
    successes = [p.success for p in plays if p.success is not None]

    return {
        "sample_size": n,
        "avg_epa": sum(epas) / len(epas) if epas else None,
        "success_rate": sum(1 for s in successes if s) / len(successes) if successes else None,
    }
