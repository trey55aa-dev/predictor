"""Derives the drive-structure and special-teams inputs the game simulator
needs, measured from real play-by-play rather than assumed.

The `plays` table deliberately holds only run/pass plays -- it powers the
scheme catalog, where including punts and kicks would distort every
per-formation and per-personnel split. A drive simulation, though, has to
know how drives actually end. Everything here is a low-dimensional aggregate
(a few hundred numbers total), so it is computed once and stored as JSON in
`simulation_params` instead of ingesting hundreds of thousands more play rows.

Every value is an observed frequency or distribution from nflverse pbp. None
of it is hand-tuned -- if a number here looks wrong, the fix is to look at
what the data says, not to nudge a constant.
"""

import datetime as dt
import json
from collections import defaultdict

import nflreadpy as nfl
import polars as pl
from sqlalchemy.orm import Session

from app.models import SimulationParam

# Field-position buckets, in yards from the opponent's goal line (yardline_100).
FIELD_BUCKETS = [(1, 10), (11, 20), (21, 40), (41, 60), (61, 80), (81, 99)]
# Yards-to-go buckets on fourth down.
TOGO_BUCKETS = [(1, 1), (2, 3), (4, 6), (7, 10), (11, 99)]


def field_bucket(yardline_100: float) -> str:
    """Bucket a field position, clamping to the nearest real bucket.

    Clamping matters at the low end: a stray 0 means "on the goal line", so
    falling through to the *last* bucket would file a goal-line snap as a
    play from 81+ yards out -- the opposite end of the field.
    """
    value = max(FIELD_BUCKETS[0][0], min(FIELD_BUCKETS[-1][1], yardline_100))
    for low, high in FIELD_BUCKETS:
        if low <= value <= high:
            return f"{low}-{high}"
    return f"{FIELD_BUCKETS[-1][0]}-{FIELD_BUCKETS[-1][1]}"


def togo_bucket(ydstogo: float) -> str:
    """Bucket a distance-to-go, clamping so that 0 or a negative distance
    reads as short yardage rather than as long."""
    value = max(TOGO_BUCKETS[0][0], min(TOGO_BUCKETS[-1][1], ydstogo))
    for low, high in TOGO_BUCKETS:
        if low <= value <= high:
            return f"{low}-{high}"
    return f"{TOGO_BUCKETS[-1][0]}-{TOGO_BUCKETS[-1][1]}"


def _load(seasons: list[int]) -> pl.DataFrame:
    cols = [
        "game_id", "play_id", "posteam", "defteam", "play_type", "down", "ydstogo",
        "yardline_100", "qtr", "game_seconds_remaining", "fixed_drive",
        "fixed_drive_result", "drive_start_yard_line", "field_goal_attempt",
        "field_goal_result", "kick_distance", "punt_attempt", "extra_point_attempt",
        "extra_point_result", "two_point_attempt", "touchdown", "return_touchdown",
        "safety", "interception", "fumble_lost", "yards_gained",
    ]
    pbp = nfl.load_pbp(seasons=seasons)
    return pbp.select([c for c in cols if c in pbp.columns])


def _fourth_down_decisions(df: pl.DataFrame) -> tuple[dict, int]:
    """What offenses actually do on fourth down, by distance and field position.

    Measured, not assumed: coaches go for it far more often near midfield on
    short yardage than the old conventional wisdom suggests, and a simulator
    that punts on every fourth down scores materially too few points.
    """
    fourth = df.filter(pl.col("down") == 4)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"go": 0, "fg": 0, "punt": 0})

    for row in fourth.iter_rows(named=True):
        yl, togo = row.get("yardline_100"), row.get("ydstogo")
        if yl is None or togo is None:
            continue
        key = f"{togo_bucket(togo)}|{field_bucket(yl)}"
        if row.get("field_goal_attempt") == 1:
            counts[key]["fg"] += 1
        elif row.get("punt_attempt") == 1:
            counts[key]["punt"] += 1
        elif row.get("play_type") in ("run", "pass"):
            counts[key]["go"] += 1

    rates = {}
    total = 0
    for key, c in counts.items():
        n = c["go"] + c["fg"] + c["punt"]
        if n < 10:  # too thin to trust -- the simulator falls back to a neighbour
            continue
        total += n
        rates[key] = {"go": c["go"] / n, "fg": c["fg"] / n, "punt": c["punt"] / n, "n": n}
    return rates, total


def _field_goals(df: pl.DataFrame) -> tuple[dict, int]:
    """Field-goal make rate by attempt distance, in 5-yard buckets."""
    fg = df.filter(pl.col("field_goal_attempt") == 1)
    buckets: dict[str, list[int]] = defaultdict(list)
    for row in fg.iter_rows(named=True):
        dist = row.get("kick_distance")
        if dist is None:
            continue
        b = f"{int(dist // 5) * 5}"
        buckets[b].append(1 if row.get("field_goal_result") == "made" else 0)

    made = {b: {"make_rate": sum(v) / len(v), "n": len(v)} for b, v in buckets.items() if len(v) >= 15}
    return made, sum(len(v) for v in buckets.values())


def _punts(df: pl.DataFrame) -> tuple[dict, int]:
    """Net punt result: where the receiving team actually starts, given where
    the punt was kicked from."""
    punts = df.filter(pl.col("punt_attempt") == 1)
    by_bucket: dict[str, list[float]] = defaultdict(list)
    n = 0
    for row in punts.iter_rows(named=True):
        yl = row.get("yardline_100")
        dist = row.get("kick_distance")
        if yl is None or dist is None:
            continue
        # Receiving team's own yardline_100 after a net-of-return punt, before
        # touchback handling; clamped to a legal spot.
        resulting = max(1.0, min(99.0, 100.0 - (yl - dist)))
        by_bucket[field_bucket(yl)].append(resulting)
        n += 1

    out = {}
    for b, vals in by_bucket.items():
        if len(vals) < 20:
            continue
        vals_sorted = sorted(vals)
        out[b] = {
            "mean": sum(vals) / len(vals),
            "p25": vals_sorted[len(vals) // 4],
            "p50": vals_sorted[len(vals) // 2],
            "p75": vals_sorted[3 * len(vals) // 4],
            "n": len(vals),
        }
    return out, n


def _drive_starts(df: pl.DataFrame) -> tuple[dict, int]:
    """Where drives actually start, split by how the previous drive ended.

    Chaining drive starts to the previous outcome is what makes field position
    behave: a punt hands over deep territory, a turnover usually does not.
    """
    # Use nflverse's own drive_start_yard_line ("KC 25") rather than the first
    # row's yardline_100. Two traps that avoids: group_by does not guarantee
    # row order, and a drive's first row is often the kickoff/punt itself,
    # whose yardline_100 is from the *kicking* team's perspective -- which
    # silently turns "receiving team starts at its own 25" (75) into 35.
    drives = (
        df.filter(pl.col("fixed_drive").is_not_null() & pl.col("drive_start_yard_line").is_not_null())
        .group_by(["game_id", "fixed_drive"], maintain_order=True)
        .agg(
            pl.col("posteam").drop_nulls().first().alias("posteam"),
            pl.col("fixed_drive_result").drop_nulls().first().alias("result"),
            pl.col("drive_start_yard_line").drop_nulls().first().alias("start_text"),
            pl.col("play_id").min().alias("first_play"),
        )
        .sort(["game_id", "first_play"])
    )

    def parse_start(text: str | None, posteam: str | None) -> float | None:
        """'KC 25' -> yards from the opponent's goal line for the team with the
        ball. Own territory counts up from 100, opponent territory counts down."""
        if not text or not posteam:
            return None
        parts = text.split()
        if len(parts) != 2 or not parts[1].isdigit():
            return None
        side, yard = parts[0], float(parts[1])
        if yard == 50:
            return 50.0
        return 100.0 - yard if side == posteam else yard

    by_prev: dict[str, list[float]] = defaultdict(list)
    prev_result = None
    prev_game = None
    n = 0
    for row in drives.iter_rows(named=True):
        if row["game_id"] != prev_game:
            prev_result, prev_game = None, row["game_id"]
        start = parse_start(row.get("start_text"), row.get("posteam"))
        if start is not None:
            key = (prev_result or "KICKOFF").upper().replace(" ", "_")
            by_prev[key].append(float(start))
            n += 1
        prev_result = row["result"]

    out = {}
    for key, vals in by_prev.items():
        if len(vals) < 25:
            continue
        s = sorted(vals)
        out[key] = {
            "mean": sum(vals) / len(vals),
            "p25": s[len(s) // 4],
            "p50": s[len(s) // 2],
            "p75": s[3 * len(s) // 4],
            "n": len(vals),
        }
    return out, n


def _clock_and_scoring(df: pl.DataFrame) -> tuple[dict, int]:
    """Seconds burned per play type, plus the scoring conversions the
    simulator needs (extra points, defensive/return touchdowns, safeties)."""
    times: dict[str, list[float]] = defaultdict(list)
    ordered = df.sort(["game_id", "play_id"])
    prev_game, prev_secs = None, None
    for row in ordered.iter_rows(named=True):
        secs = row.get("game_seconds_remaining")
        ptype = row.get("play_type")
        if row["game_id"] != prev_game:
            prev_game, prev_secs = row["game_id"], secs
            continue
        if secs is not None and prev_secs is not None and ptype in ("run", "pass", "punt", "field_goal"):
            elapsed = prev_secs - secs
            if 0 <= elapsed <= 60:
                times[ptype].append(float(elapsed))
        prev_secs = secs

    seconds_per_play = {k: sum(v) / len(v) for k, v in times.items() if len(v) >= 100}

    xp = df.filter(pl.col("extra_point_attempt") == 1)
    xp_made = xp.filter(pl.col("extra_point_result") == "good").height
    xp_total = xp.height

    turnovers = df.filter((pl.col("interception") == 1) | (pl.col("fumble_lost") == 1))
    return_tds = turnovers.filter(pl.col("return_touchdown") == 1).height

    drives = df.filter(pl.col("fixed_drive").is_not_null()).select(["game_id", "posteam", "fixed_drive"]).unique()
    games = df.select("game_id").unique().height
    drives_per_team = (drives.height / games / 2) if games else 11.0

    return (
        {
            "seconds_per_play": seconds_per_play,
            "extra_point_make_rate": (xp_made / xp_total) if xp_total else 0.94,
            "turnover_return_td_rate": (return_tds / turnovers.height) if turnovers.height else 0.0,
            "drives_per_team_per_game": drives_per_team,
        },
        xp_total,
    )


BUILDERS = {
    "fourth_down_decisions": _fourth_down_decisions,
    "field_goal_make_rate": _field_goals,
    "punt_result": _punts,
    "drive_starts": _drive_starts,
    "clock_and_scoring": _clock_and_scoring,
}


def build_sim_params(db: Session, seasons: list[int]) -> dict[str, int]:
    """Computes every simulator input from real pbp and upserts it. Returns
    the sample size behind each, so a thin one is visible rather than silently
    trusted."""
    df = _load(seasons)
    now = dt.datetime.utcnow()
    seasons_label = ",".join(str(s) for s in seasons)
    sizes: dict[str, int] = {}

    for key, builder in BUILDERS.items():
        value, n = builder(df)
        existing = db.get(SimulationParam, key)
        if existing is None:
            existing = SimulationParam(key=key)
            db.add(existing)
        existing.value_json = json.dumps(value)
        existing.seasons = seasons_label
        existing.sample_size = n
        existing.created_at = now
        sizes[key] = n

    db.commit()
    return sizes


def load_sim_params(db: Session) -> dict:
    return {p.key: json.loads(p.value_json) for p in db.query(SimulationParam).all()}
