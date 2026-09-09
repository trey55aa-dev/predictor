"""Real, current usage shares for a team's skill players: what fraction of
the team's rush attempts / pass targets each player gets right now.

This is deliberately separate from player_projection.py's trailing-average
yardage model. That model answers "how many yards is this player likely to
get" from the player's own rate blended with what the opponent allows.
Usage share answers a different question -- "if this offense gains yards on
a run/pass, who is it likely to be" -- which is what the simulator needs to
turn a team-level yardage total into a specific player's distribution (see
model/player_sim.py).

Load management, done honestly: nflverse's play-by-play has no "this player
is being rested" flag, so that can't be detected directly (same limitation
as injuries -- nothing to fabricate here). What IS real and measurable is a
player's own recent SNAP share moving before their box-score average catches
up -- a receiver's role can visibly shrink on the field for two or three
weeks before it shows up in a yards-per-game trailing average. Recency-
weighting the usage shares, with the two most recent games weighted several
times heavier than the rest, is what lets a real recent role change show up
in the projection quickly rather than being smoothed away by a whole
season's worth of history.
"""

from collections import defaultdict

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Play, SnapCount, TeamRosterMembership

# Exponential decay by true calendar distance (in games, treating each season
# as 18 weeks), not by ordinal position within a player's own appearance
# list. That distinction matters: an earlier ordinal-position version gave a
# player's single most-recent appearance the same top weight regardless of
# whether it happened last week or over a year ago, which meant a backup's
# stale injury-replacement starts from a year prior could end up weighted
# almost equally to the actual current starter's own recent games -- found in
# validation as Joe Burrow (CIN's undisputed 2024 starter) coming out at a
# virtual 48/52 coin flip against Jake Browning, who started games for CIN
# during Burrow's 2023 injury and hadn't played a snap since.
#
# HALF_LIFE_GAMES=6 means a game 6 games stale carries half the weight of a
# game from right before the cutoff; by ~30 games stale (over a full season
# old) a game contributes well under 5% of a current one's weight.
LOOKBACK_GAMES = 6  # used only by recent_snap_share_trend's simple "last N games" query
HALF_LIFE_GAMES = 6.0
GAMES_PER_SEASON = 18  # for converting a season+week gap into a single distance
DECAY_PER_GAME = 0.5 ** (1.0 / HALF_LIFE_GAMES)
# Games older than this contribute negligibly (0.5^(60/6) < 0.001) -- capping
# the query window avoids scanning a player's entire career for a
# vanishingly small contribution.
MAX_GAMES_AGO = 60



def _roster(db: Session, team: str, season: int, position: str | None = None) -> list[str]:
    """Player ids on this team's roster this season, optionally restricted to
    one roster position (used for the passer role -- see passer_shares)."""
    query = db.query(TeamRosterMembership.player_id).filter(
        TeamRosterMembership.season == season, TeamRosterMembership.team == team
    )
    if position is not None:
        query = query.filter(TeamRosterMembership.position == position)
    return [row.player_id for row in query.all()]


def _roster_shares(db: Session, team: str, before_season: int, before_week: int, id_column, roster_position: str | None) -> dict[str, float]:
    """Each rostered player's recency-weighted average per-game count (rush
    attempts, or targets), normalized into shares. Each player's own last
    LOOKBACK_GAMES games are used wherever they actually played, not gated to
    this team -- the piece that makes a free-agent signing or a trade carry
    the player's role with them, instead of resetting to zero the moment
    their team-of-record changes (exactly what player_projection.py's
    trailing-average baseline already does for yardage; this applies the
    same idea to usage share).

    One batched query for the whole roster, not one round trip per player --
    the per-player version of this was correct but made the simulator too
    slow to run at any real scale (an offensive roster is ~20-30 skill
    players, each needing its own two-query lookup).
    """
    roster = _roster(db, team, before_season, position=roster_position)
    if not roster:
        return {}

    target_index = before_season * GAMES_PER_SEASON + before_week
    min_season = before_season - (MAX_GAMES_AGO // GAMES_PER_SEASON) - 1

    rows = (
        db.query(Play.season, Play.week, id_column.label("player_id"), func.count().label("n"))
        .filter(
            id_column.in_(roster),
            Play.season >= min_season,
            (Play.season < before_season) | ((Play.season == before_season) & (Play.week < before_week)),
        )
        .group_by(Play.season, Play.week, id_column)
        .all()
    )

    weighted_totals: dict[str, float] = defaultdict(float)
    for season, week, player_id, count in rows:
        games_ago = target_index - (season * GAMES_PER_SEASON + week)
        if games_ago < 0 or games_ago > MAX_GAMES_AGO:
            continue
        weighted_totals[player_id] += count * (DECAY_PER_GAME**games_ago)

    total = sum(weighted_totals.values())
    if total <= 0:
        return {}
    return {pid: v / total for pid, v in weighted_totals.items() if v > 0}


def rushing_shares(db: Session, team: str, before_season: int, before_week: int) -> dict[str, float]:
    """{gsis_id: share of team rush attempts}, recency-weighted, computed from
    each currently-rostered player's OWN recent rushing role wherever they
    last played -- see _player_weighted_avg for why that matters."""
    return _roster_shares(db, team, before_season, before_week, Play.rusher_player_id, roster_position=None)


def target_shares(db: Session, team: str, before_season: int, before_week: int) -> dict[str, float]:
    """{gsis_id: share of team pass targets}, recency-weighted, same
    player-follows-the-role approach as rushing_shares."""
    return _roster_shares(db, team, before_season, before_week, Play.receiver_player_id, roster_position=None)


def passer_shares(db: Session, team: str, before_season: int, before_week: int) -> dict[str, float]:
    """{gsis_id: share of team pass attempts}.

    Deliberately NOT a multi-game decayed blend like rushing_shares/
    target_shares -- a team's snap-taking QB is winner-take-all within any
    one real game, unlike RB carries or WR targets, which genuinely split
    across several teammates every week. Blending across several games (even
    with decay) meant a real starter's share landed well under 100% whenever
    a backup had thrown meaningful volume in ANY recent game (mop-up relief,
    a spot start during a past injury, a timeshare a season ago) -- found in
    validation as CIN's Joe Burrow, the undisputed 2024 starter, sharing
    passer credit 65/35 with Jake Browning, who hadn't played a snap since
    2023. This instead looks at only the single most recent team game with
    passing attempts and uses that game's own attempt split -- correctly
    ~100% to a healthy starter, and correctly split only when a real
    in-progress QB change means the most recent game itself had two passers.
    """
    roster = _roster(db, team, before_season, position="QB")
    if not roster:
        return {}

    most_recent = (
        db.query(Play.season, Play.week)
        .filter(
            Play.passer_player_id.in_(roster),
            Play.posteam == team,
            (Play.season < before_season) | ((Play.season == before_season) & (Play.week < before_week)),
        )
        .order_by(Play.season.desc(), Play.week.desc())
        .first()
    )
    if most_recent is None:
        return {}
    season, week = most_recent

    rows = (
        db.query(Play.passer_player_id, func.count())
        .filter(
            Play.passer_player_id.in_(roster),
            Play.posteam == team,
            Play.season == season,
            Play.week == week,
        )
        .group_by(Play.passer_player_id)
        .all()
    )
    total = sum(count for _, count in rows)
    if total <= 0:
        return {}
    return {pid: count / total for pid, count in rows}


def player_names(db: Session, player_ids: list[str]) -> dict[str, str]:
    """Most recent known display name for each given player_id, looked up
    from their own plays at ANY team -- not the target team's game log. A
    traded/free-agent player (Barkley, Henry, Jacobs, ...) has no play
    history at their new team yet, so resolving names the old way (team's own
    game log only) would show their raw player_id instead of a name for
    exactly the players this module was fixed to include."""
    if not player_ids:
        return {}
    names: dict[str, str] = {}
    for id_col, name_col in (
        (Play.rusher_player_id, Play.rusher_player_name),
        (Play.receiver_player_id, Play.receiver_player_name),
        (Play.passer_player_id, Play.passer_player_name),
    ):
        for game_id, pid, name in (
            db.query(Play.game_id, id_col, name_col).filter(id_col.in_(player_ids)).all()
        ):
            if pid and name and (pid not in names or game_id > names.get(f"__game__{pid}", "")):
                names[pid] = name
                names[f"__game__{pid}"] = game_id
    return {pid: name for pid, name in names.items() if not pid.startswith("__game__")}


def recent_snap_share_trend(db: Session, player_id: str, before_season: int, before_week: int) -> dict:
    """A player's own snap-share trend over their last few games -- the most
    direct real signal available for 'is this player's role shrinking or
    growing right now', independent of how many yards they've produced."""
    rows = (
        db.query(SnapCount.season, SnapCount.week, SnapCount.offense_pct)
        .filter(
            SnapCount.player_id == player_id,
            SnapCount.id_mapped.is_(True),
            (SnapCount.season < before_season) | ((SnapCount.season == before_season) & (SnapCount.week < before_week)),
        )
        .order_by(SnapCount.season.desc(), SnapCount.week.desc())
        .limit(LOOKBACK_GAMES)
        .all()
    )
    pcts = [r.offense_pct for r in rows if r.offense_pct is not None]
    if not pcts:
        return {"sample_size": 0, "most_recent_pct": None, "trailing_avg_pct": None}
    return {
        "sample_size": len(pcts),
        "most_recent_pct": pcts[0],
        "trailing_avg_pct": sum(pcts) / len(pcts),
    }
