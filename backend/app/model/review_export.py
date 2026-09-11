"""Exports a real, self-contained JSON snapshot of "what a reviewer needs to
know right now" -- rolling performance, recent recalibrations, and every
recently-final game's full post-game breakdown (see model/keys_to_victory.py)
plus the real injury reports that were live for each team that week.

Why this exists, specifically: the automated cloud reviewer that reads this
file runs in a sandbox whose network egress is restricted to a small
allowlist that does NOT include this project's own production API or any
external site -- confirmed live (a direct curl and a WebFetch to our own
Render URL both came back EGRESS_BLOCKED from that sandbox). That reviewer
DOES get a full git checkout of this repo, so writing the data it needs into
a file this same pipeline already commits back to the repo (see
cli.py's run-routine, which calls export_review_snapshot after grading)
sidesteps the network restriction entirely -- no egress needed to read it.
"""

import datetime as dt
import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.model.grade import performance_summary
from app.model.keys_to_victory import build_game_breakdown
from app.model.schedule_context import current_season
from app.models import CalibrationAdjustment, Game, Injury

RECENT_GAME_WINDOW_DAYS = 10
RECENT_CALIBRATION_LIMIT = 10


def _team_injuries(db: Session, team_abbr: str, season: int, week: int) -> list[dict]:
    rows = (
        db.query(Injury)
        .filter(Injury.season == season, Injury.week == week, Injury.team_abbr == team_abbr)
        .all()
    )
    return [
        {
            "player_name": r.player_name,
            "position": r.position,
            "report_status": r.report_status,
            "is_starter": r.is_starter,
        }
        for r in rows
    ]


def build_review_snapshot(db: Session) -> dict:
    season = current_season()
    cutoff = (dt.date.today() - dt.timedelta(days=RECENT_GAME_WINDOW_DAYS)).isoformat()

    recent_final_games = (
        db.query(Game)
        .filter(Game.season == season, Game.status == "final", Game.gameday >= cutoff)
        .order_by(Game.gameday.desc())
        .all()
    )

    games_out = []
    for game in recent_final_games:
        breakdown = build_game_breakdown(db, game)
        breakdown["home_injuries"] = _team_injuries(db, game.home_team, game.season, game.week)
        breakdown["away_injuries"] = _team_injuries(db, game.away_team, game.season, game.week)
        games_out.append(breakdown)

    recalibrations = (
        db.query(CalibrationAdjustment)
        .order_by(CalibrationAdjustment.created_at.desc())
        .limit(RECENT_CALIBRATION_LIMIT)
        .all()
    )

    return {
        "generated_at": dt.datetime.utcnow().isoformat(),
        "season": season,
        "rolling_performance": performance_summary(db),
        "recent_calibrations": [
            {
                "parameter_name": c.parameter_name,
                "old_value": c.old_value,
                "new_value": c.new_value,
                "evidence": c.evidence,
                "sample_size": c.sample_size,
                "created_at": c.created_at.isoformat(),
            }
            for c in recalibrations
        ],
        "recent_games": games_out,
    }


def export_review_snapshot(db: Session, out_path: Path) -> dict:
    """Builds the snapshot and writes it to `out_path` (creating parent dirs
    as needed). Returns the snapshot dict for callers that also want to log
    a summary."""
    snapshot = build_review_snapshot(db)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, indent=2, default=str))
    return snapshot
