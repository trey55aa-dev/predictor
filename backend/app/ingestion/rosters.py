"""Ingests real season+team roster membership (nflverse weekly rosters,
deduped) -- see TeamRosterMembership for why the simulator's usage-share
pools need this."""

from sqlalchemy.orm import Session

from app.models import TeamRosterMembership


def ingest_rosters(db: Session, seasons: list[int]) -> int:
    import nflreadpy as nfl

    total = 0
    for season in seasons:
        rosters = (
            nfl.load_rosters(seasons=[season])
            .select(["season", "team", "gsis_id", "full_name", "position"])
            .drop_nulls(subset=["gsis_id", "team"])
            .unique(subset=["team", "gsis_id"])
        )
        db.query(TeamRosterMembership).filter(TeamRosterMembership.season == season).delete(synchronize_session=False)

        rows_to_insert = [
            {
                "season": season,
                "team": row["team"],
                "player_id": row["gsis_id"],
                "player_name": row["full_name"],
                "position": row.get("position"),
            }
            for row in rosters.iter_rows(named=True)
        ]
        db.bulk_insert_mappings(TeamRosterMembership, rows_to_insert)
        db.commit()
        total += len(rows_to_insert)

    return total
