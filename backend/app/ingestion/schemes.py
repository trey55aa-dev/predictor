"""Seeds the scheme-family reference dataset and resolves each team-season's
head coach (from already-ingested Game rows) to an offense/defense scheme family.
"""

import json
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Game, SchemeCoach, SchemeFamily, TeamSeasonScheme

SCHEME_FAMILIES_PATH = Path(__file__).resolve().parent.parent / "reference" / "scheme_families.json"

INDEPENDENT_OFFENSE_ID = "independent-offense"
INDEPENDENT_DEFENSE_ID = "independent-defense"


def seed_scheme_families(db: Session) -> None:
    families = json.loads(SCHEME_FAMILIES_PATH.read_text())

    db.query(SchemeCoach).delete()

    for family in families:
        existing = db.get(SchemeFamily, family["id"])
        if existing is None:
            existing = SchemeFamily(id=family["id"])
            db.add(existing)
        existing.side = family["side"]
        existing.name = family["name"]
        existing.era = family["era"]
        existing.description = family["description"]
        existing.core_concepts = json.dumps(family["core_concepts"])

        for coach_name in family["coaches"]:
            db.add(SchemeCoach(scheme_family_id=family["id"], coach_name=coach_name))

    db.commit()


def _coach_family_lookup(db: Session, side: str) -> dict[str, str]:
    rows = (
        db.query(SchemeCoach.coach_name, SchemeCoach.scheme_family_id)
        .join(SchemeFamily, SchemeCoach.scheme_family_id == SchemeFamily.id)
        .filter(SchemeFamily.side == side)
        .all()
    )
    return {coach_name: scheme_id for coach_name, scheme_id in rows}


def _primary_coach_per_team_season(db: Session, seasons: list[int]) -> dict[tuple[str, int], str]:
    """Resolves each team-season's primary head coach by counting games coached,
    not just taking whichever name appears first -- protects against in-season
    coaching changes (e.g. a fired HC replaced by an interim) picking the wrong name.
    """
    games = db.query(Game).filter(Game.season.in_(seasons)).all()
    counts: dict[tuple[str, int], Counter] = {}

    for game in games:
        pairs = [(game.home_team, game.home_coach), (game.away_team, game.away_coach)]
        for team, coach in pairs:
            if not coach:
                continue
            key = (team, game.season)
            counts.setdefault(key, Counter())[coach] += 1

    return {key: counter.most_common(1)[0][0] for key, counter in counts.items()}


def build_team_season_schemes(db: Session, seasons: list[int]) -> dict:
    offense_lookup = _coach_family_lookup(db, "offense")
    defense_lookup = _coach_family_lookup(db, "defense")
    primary_coaches = _primary_coach_per_team_season(db, seasons)

    db.query(TeamSeasonScheme).filter(TeamSeasonScheme.season.in_(seasons)).delete(synchronize_session=False)

    unmapped_offense: set[str] = set()
    unmapped_defense: set[str] = set()

    for (team, season), coach in primary_coaches.items():
        offense_id = offense_lookup.get(coach, INDEPENDENT_OFFENSE_ID)
        defense_id = defense_lookup.get(coach, INDEPENDENT_DEFENSE_ID)
        if offense_id == INDEPENDENT_OFFENSE_ID and coach not in offense_lookup:
            unmapped_offense.add(coach)
        if defense_id == INDEPENDENT_DEFENSE_ID and coach not in defense_lookup:
            unmapped_defense.add(coach)

        db.add(
            TeamSeasonScheme(
                team_abbr=team,
                season=season,
                head_coach=coach,
                offense_scheme_id=offense_id,
                defense_scheme_id=defense_id,
            )
        )

    db.commit()

    return {
        "team_seasons_mapped": len(primary_coaches),
        "coaches_landed_in_independent_offense": sorted(unmapped_offense),
        "coaches_landed_in_independent_defense": sorted(unmapped_defense),
    }
