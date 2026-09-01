import json
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.model.play_concepts import concept_label
from app.model.play_explainer import explain_scoring_play, explain_stop, is_notable_stop
from app.models import Play, SchemeFamily, TeamSeasonScheme
from app.schemas import (
    NotablePlayOut,
    PlayConceptOut,
    SchemeDetailOut,
    SchemeFamilyOut,
    TeamSeasonOut,
)

router = APIRouter()

TOP_CONCEPTS_LIMIT = 10
NOTABLE_PLAYS_LIMIT = 8


def _team_season_count(db: Session, family: SchemeFamily) -> int:
    col = TeamSeasonScheme.offense_scheme_id if family.side == "offense" else TeamSeasonScheme.defense_scheme_id
    return db.query(TeamSeasonScheme).filter(col == family.id).count()


def _to_family_out(db: Session, family: SchemeFamily) -> SchemeFamilyOut:
    return SchemeFamilyOut(
        id=family.id,
        side=family.side,
        name=family.name,
        era=family.era,
        description=family.description,
        core_concepts=json.loads(family.core_concepts),
        team_season_count=_team_season_count(db, family),
    )


@router.get("/systems", response_model=list[SchemeFamilyOut])
def list_systems(side: str | None = None, db: Session = Depends(get_db)) -> list[SchemeFamilyOut]:
    query = db.query(SchemeFamily)
    if side:
        query = query.filter(SchemeFamily.side == side)
    families = query.order_by(SchemeFamily.side, SchemeFamily.name).all()
    return [_to_family_out(db, f) for f in families]


@router.get("/systems/{scheme_id}", response_model=SchemeDetailOut)
def system_detail(scheme_id: str, db: Session = Depends(get_db)) -> SchemeDetailOut:
    family = db.get(SchemeFamily, scheme_id)
    if family is None:
        raise HTTPException(status_code=404, detail="scheme family not found")

    scheme_col = TeamSeasonScheme.offense_scheme_id if family.side == "offense" else TeamSeasonScheme.defense_scheme_id
    team_seasons = (
        db.query(TeamSeasonScheme)
        .filter(scheme_col == scheme_id)
        .order_by(TeamSeasonScheme.season.desc(), TeamSeasonScheme.team_abbr)
        .all()
    )

    play_col = Play.offense_scheme_id if family.side == "offense" else Play.defense_scheme_id
    plays = db.query(Play).filter(play_col == scheme_id).all()

    concept_counts: Counter[str] = Counter()
    for play in plays:
        concept_counts[concept_label(play)] += 1
    total_plays = sum(concept_counts.values())
    top_concepts = [
        PlayConceptOut(label=label, count=count, share=(count / total_plays if total_plays else 0.0))
        for label, count in concept_counts.most_common(TOP_CONCEPTS_LIMIT)
    ]

    notable_plays: list[NotablePlayOut] = []
    if family.side == "offense":
        scoring = sorted(
            (p for p in plays if p.scoring_play),
            key=lambda p: (p.epa if p.epa is not None else float("-inf")),
            reverse=True,
        )
        for play in scoring[:NOTABLE_PLAYS_LIMIT]:
            notable_plays.append(
                NotablePlayOut(
                    game_id=play.game_id,
                    season=play.season,
                    week=play.week,
                    posteam=play.posteam,
                    defteam=play.defteam,
                    desc=play.desc,
                    concept=concept_label(play),
                    explanation=explain_scoring_play(play),
                    epa=play.epa,
                    yards_gained=play.yards_gained,
                )
            )
    else:
        stops = sorted(
            (p for p in plays if is_notable_stop(p)),
            key=lambda p: (p.epa if p.epa is not None else float("inf")),
        )
        for play in stops[:NOTABLE_PLAYS_LIMIT]:
            notable_plays.append(
                NotablePlayOut(
                    game_id=play.game_id,
                    season=play.season,
                    week=play.week,
                    posteam=play.posteam,
                    defteam=play.defteam,
                    desc=play.desc,
                    concept=concept_label(play),
                    explanation=explain_stop(play),
                    epa=play.epa,
                    yards_gained=play.yards_gained,
                )
            )

    return SchemeDetailOut(
        id=family.id,
        side=family.side,
        name=family.name,
        era=family.era,
        description=family.description,
        core_concepts=json.loads(family.core_concepts),
        team_season_count=len(team_seasons),
        team_seasons=[
            TeamSeasonOut(team_abbr=ts.team_abbr, season=ts.season, head_coach=ts.head_coach) for ts in team_seasons
        ],
        top_concepts=top_concepts,
        notable_plays=notable_plays,
        sample_size=total_plays,
    )


@router.get("/teams/{team_abbr}/systems")
def team_systems(team_abbr: str, season: int, db: Session = Depends(get_db)) -> dict:
    ts = (
        db.query(TeamSeasonScheme)
        .filter(TeamSeasonScheme.team_abbr == team_abbr, TeamSeasonScheme.season == season)
        .first()
    )
    if ts is None:
        raise HTTPException(status_code=404, detail="no scheme data for this team-season")
    return {
        "team_abbr": ts.team_abbr,
        "season": ts.season,
        "head_coach": ts.head_coach,
        "offense_scheme_id": ts.offense_scheme_id,
        "defense_scheme_id": ts.defense_scheme_id,
    }
