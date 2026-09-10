"""Ingests real, current injury/inactive status from ESPN's public roster
API -- a real, live, free, no-auth-required source, unlike nflverse's own
injuries feed, which has zero rows for the 2026 season at all (confirmed:
`nflreadpy.load_injuries(seasons=[2026])` raises "Season must be between
2009 and 2025" -- a known, acknowledged gap in nflverse's own automation,
not a freshness lag).

Found and verified live while investigating a user report (TreVeyon
Henderson shown as day-to-day when he was actually ruled out for a game
already under way): `site.api.espn.com/.../teams/{id}/roster` embeds each
player's current injury status directly on their roster entry --
`{"injuries": [{"status": "Out", "date": "..."}]}`, present only for
players with an active designation, absent for healthy ones. One request
per team (32 total), no pagination, no per-player follow-up calls needed
-- ESPN's separate, deeper `sports.core.api.espn.com/.../injuries` feed
was tried first and works too, but needs a $ref chase per item (a team can
carry 50+ historical entries) for the same information this endpoint gives
directly.

This is unofficial and undocumented (no published rate limits or uptime
guarantee) -- the existing nflverse-based pipeline stays the primary
source for everything else; this only fills the specific gap nflverse
currently has no data for at all.
"""

import datetime as dt

import httpx
from sqlalchemy.orm import Session

from app.models import Injury, SnapCount, Team

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ESPN_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"

# ESPN's abbreviation differs from ours for exactly one current franchise
# (confirmed by diffing the full 32-team list against our own Team table --
# every other code matches).
OUR_ABBR_TO_ESPN = {"WAS": "WSH", "LA": "LAR"}

# Our own Team table carries a real duplicate for exactly one franchise --
# "LA" and "LAR" are both "Los Angeles Rams" rows, a leftover from an
# earlier schema/ingestion pass. "LA" is the one actually used everywhere
# else in this app (112 real games reference it; zero reference "LAR").
# Left unexcluded, "LAR" independently string-matches ESPN's own
# abbreviation (which is literally "LAR"), silently soaking up the Rams'
# real injury data under a team code nothing else in the app ever queries
# -- confirmed live: every Rams injury landed under "LAR" while "LA" (what
# today's actual schedule/game-plan lookups use) showed zero, on a day the
# Rams were playing.
UNUSED_DUPLICATE_TEAM_ABBRS = {"LAR"}

# A player's most recent real snap share is used as the "is this a starter"
# signal (matching the field gameplan.py's severity sort already uses) --
# real measured usage, not a guess. 50% of offensive snaps is a reasonable
# real-world cutoff for "regular contributor."
STARTER_SNAP_THRESHOLD = 0.5

REQUEST_TIMEOUT = 20.0


def _espn_team_ids(db: Session) -> dict[str, str]:
    """{our team_abbr: espn team id}, current 32 franchises only -- skips
    historical/relocated codes in our own Team table (OAK/SD/STL) that
    ESPN's live team list naturally doesn't carry, and the one confirmed
    unused duplicate (see UNUSED_DUPLICATE_TEAM_ABBRS) that would otherwise
    silently collide with a real team's data."""
    our_teams = {t.team_abbr for t in db.query(Team).all()} - UNUSED_DUPLICATE_TEAM_ABBRS
    resp = httpx.get(ESPN_TEAMS_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    espn_teams = resp.json()["sports"][0]["leagues"][0]["teams"]
    espn_by_abbr = {t["team"]["abbreviation"]: str(t["team"]["id"]) for t in espn_teams}

    result = {}
    for our_abbr in our_teams:
        espn_abbr = OUR_ABBR_TO_ESPN.get(our_abbr, our_abbr)
        if espn_abbr in espn_by_abbr:
            result[our_abbr] = espn_by_abbr[espn_abbr]
    return result


def _espn_to_gsis_map() -> dict[str, str]:
    import nflreadpy as nfl

    ids = nfl.load_ff_playerids().select(["gsis_id", "espn_id"]).drop_nulls()
    return {str(int(row["espn_id"])): row["gsis_id"] for row in ids.iter_rows(named=True)}


def _starter_ids(db: Session) -> set[str]:
    """gsis_ids of players whose most recent real snap share cleared
    STARTER_SNAP_THRESHOLD -- see the module docstring's reasoning."""
    rows = (
        db.query(SnapCount.player_id, SnapCount.offense_pct)
        .filter(SnapCount.id_mapped.is_(True), SnapCount.offense_pct.isnot(None))
        .order_by(SnapCount.season.desc(), SnapCount.week.desc())
        .all()
    )
    seen: set[str] = set()
    starters: set[str] = set()
    for player_id, pct in rows:
        if player_id in seen:
            continue
        seen.add(player_id)
        if pct >= STARTER_SNAP_THRESHOLD:
            starters.add(player_id)
    return starters


def ingest_espn_injuries(db: Session, season: int, week: int) -> int:
    """Fetches every team's current roster, pulls out players with an active
    injury designation, and upserts them into the same Injury table
    nflverse-sourced rows use -- same schema, tagged with the given
    season/week (this source has no week concept of its own; it's simply
    "current status as of right now," so the caller supplies which week that
    current status applies to)."""
    espn_ids = _espn_team_ids(db)
    espn_to_gsis = _espn_to_gsis_map()
    starters = _starter_ids(db)
    now = dt.datetime.utcnow()

    rows_to_upsert: list[dict] = []
    unmapped = 0

    for team_abbr, espn_team_id in espn_ids.items():
        resp = httpx.get(ESPN_ROSTER_URL.format(team_id=espn_team_id), timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        for group in data.get("athletes", []):
            for athlete in group.get("items", []):
                injuries = athlete.get("injuries") or []
                if not injuries:
                    continue
                status = injuries[0].get("status")
                if not status:
                    continue

                espn_id = str(athlete.get("id"))
                gsis_id = espn_to_gsis.get(espn_id)
                if gsis_id is None:
                    unmapped += 1
                    continue

                rows_to_upsert.append(
                    {
                        "season": season,
                        "week": week,
                        "team_abbr": team_abbr,
                        "gsis_id": gsis_id,
                        "player_name": athlete.get("fullName", ""),
                        "position": (athlete.get("position") or {}).get("abbreviation"),
                        "report_status": status,
                        "practice_status": None,  # not carried by this endpoint
                        "primary_injury": None,  # not carried by this endpoint
                        "is_starter": gsis_id in starters,
                        "updated_at": now,
                    }
                )

    # Replace this week's ESPN-sourced rows atomically -- re-running mid-week
    # as statuses change (a Wednesday "Questionable" becoming Sunday's "Out")
    # should reflect the latest report, not accumulate stale duplicates.
    db.query(Injury).filter(Injury.season == season, Injury.week == week).delete(synchronize_session=False)
    for row in rows_to_upsert:
        db.add(Injury(**row))
    db.commit()

    if unmapped:
        print(f"  {unmapped} injured players had no GSIS id match and were skipped")

    return len(rows_to_upsert)
