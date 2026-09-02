# Football Predictor

Weekly NFL game predictions blending an Elo-style power rating (built from
real historical results) with Vegas market odds and a weather adjustment.

This is the first vertical slice of a larger planned system (fantasy
projections, player grading, scouting notes, live in-game projections come
later).

## Stack

- **Backend**: Python (FastAPI + SQLAlchemy + SQLite), data from
  [`nflreadpy`](https://pypi.org/project/nflreadpy/) (nflverse),
  [The Odds API](https://the-odds-api.com/), and
  [Open-Meteo](https://open-meteo.com/).
- **Frontend**: React + TypeScript (Vite).

## Setup

### Backend

Requires Python 3.10+. If your system Python is older, install
[`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`),
which will fetch a pinned Python version automatically.

```bash
cd backend
uv sync
cp .env.example .env
# edit .env and add your free API key from https://the-odds-api.com/
```

Run the API:

```bash
uv run uvicorn app.main:app --reload
```

Run the ingestion + prediction pipeline:

```bash
uv run python -m app.cli ingest-all --season 2026 --week 1
uv run python -m app.cli predict-week --season 2026 --week 1
```

Grade a completed week against actual results:

```bash
uv run python -m app.cli grade-week --season 2026 --week 1
```

Build the offensive/defensive systems & coaching-trees catalog (run once,
re-run after adding a new season to `HISTORY_SEASONS` in `app/cli.py`):

```bash
uv run python -m app.cli build-scheme-mapping
uv run python -m app.cli ingest-plays
```

Injury reports are pulled automatically as part of `ingest-all` (best-effort
-- nflreadpy only has data for seasons that have actually started publishing
practice reports). To backfill a specific past week on its own:

```bash
uv run python -m app.cli ingest-injuries --season 2024 --week 10
```

Each game's prediction card also exposes a "View game plan" panel (injuries,
play-style/scheme matchup, over/under lean, upset alerts) via
`GET /api/predictions/game/{game_id}/gameplan`, and a **Parlays** tab
suggests a safest and a best-money-move parlay via
`GET /api/parlays/week/{season}/{week}`.

Player-level touchdown/yardage projections (rushing, receiving, passing,
anytime-TD probability) live in the same Game Plan panel, backed by
`GET /api/predictions/game/{game_id}/players`. Build/refresh them with:

```bash
uv run python -m app.cli ingest-player-stats --seasons 2021 2022 2023 2024 2025
uv run python -m app.cli project-players --season 2026 --week 1
```

## Self-recalibration

Team strength (Elo) has always updated automatically from results. Everything
else -- the Elo/market blend weight, the confidence-range widths -- used to
be a fixed constant in `config.py` that never moved, even though grading
already computed the evidence needed to correct it. `app/model/recalibration.py`
closes that gap: it grid-searches the blend weight against real Brier scores
and nudges it (capped step, gradual) toward whatever the evidence supports,
and sets the confidence-range widths directly to the observed error std-dev.
Every change is logged (`CalibrationAdjustment`, `GET /api/model/calibration-history`,
visible on the dashboard under "self-correction history") with the before/
after numbers -- an auditable trail, not a black box. Gated by a 30-graded-
prediction minimum so a small early streak can't swing it. Elo's own
internals (K-factor, home-field advantage, season regression) are
deliberately NOT auto-tuned -- they're baked into every historical Elo
snapshot, so changing them needs a full `build-history` rebuild, not a live
nudge.

```bash
uv run python -m app.cli recalibrate
```

Also runs automatically as part of `run-routine`, after grading, every time
it fires.

Anytime-TD legs are also eligible for the **Safest** parlay (tagged
`anytime_td` vs `game_winner` in the API and UI) -- at most one leg per game
is ever selected, since a team-win leg and that team's own player-TD leg are
correlated outcomes, not independent, and the combined-probability math
assumes independence. Player props are model-probability-only for now (no
market player-prop odds ingested -- see the plan notes for why), so they're
only eligible for Safest, not Best Money Move, which needs a real market
price to compute edge against.

Run tests:

```bash
uv run pytest
```

### Frontend

Requires Node 20+.

```bash
cd frontend
npm install
npm run dev
```

The dashboard expects the backend running at `http://localhost:8000`
(configurable via `frontend/.env` → `VITE_API_BASE_URL`).

## Autonomous routine (3-4x/day)

A local `launchd` job runs the ingest → predict → grade → parlay-track
pipeline several times a day, has Claude write a short qualitative read on
the week's most notable games, and self-disables once winner accuracy AND
parlay hit-rate both clear 90% on a real sample (see
`backend/app/cli.py:run_routine_cmd` for the exact thresholds --
realistically this will just run indefinitely, since ~65-70% is roughly the
ceiling for NFL prediction; that's expected, not a bug).

**One-time setup** (the auth step has to be done by you -- it's an
interactive login tied to your account):

```bash
# 1. Install the native CLI (avoids needing sudo for the global npm dir):
npm config set prefix "$HOME/.npm-global"
npm install -g @anthropic-ai/claude-code
export PATH="$HOME/.npm-global/bin:$PATH"   # add this line to your ~/.zshrc too

# 2. Authenticate it (interactive -- opens a login flow):
claude setup-token

# 3. Verify:
claude -p "reply with OK" # should print OK, not an auth error

# 4. Load the scheduled job:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.footballpredictor.routine.plist

# Check it's loaded:
launchctl list | grep footballpredictor
```

Fires at 8:00, 13:00, 18:00, and 23:00 local time (edit the `Hour`/`Minute`
values in the plist to change). Each run appends to
`backend/routine_log/YYYY-MM-DD.md` (the human-readable summary) and
`backend/routine_log/launchd-runs.log` (raw stdout/stderr, for debugging the
job itself). The routine's own permission scope lives in
`.claude/settings.local.json` (gitignored) -- it can run the app's own
scripts/tests, edit files in the repo, and self-disable via `launchctl
bootout`, but cannot push to git, commit, or run destructive commands.

**Cost-aware by design**: `run_routine_headless.sh` runs pytest and the data
pipeline directly in plain bash on every fire (free, no LLM involved) and
only invokes `claude -p` when there's something worth its judgment: a test
failure, the first check of the day that has real predictions to analyze
(subsequent same-day fires still refresh data but skip re-analyzing --
`backend/routine_log/.analyzed-YYYY-MM-DD` is the sentinel), or the stop
condition firing. This isn't optional polish -- the first version called
Claude unconditionally on every fire and burned a full week's usage limit
in two runs during a quiet preseason stretch where there was nothing to do.
If you ever see `You've hit your weekly limit` in `launchd-runs.log`, that's
the account-level usage cap talking, not this script; it'll resume once the
limit resets (the message tells you when).

**Cadence also adapts to game proximity**: the launchd schedule itself stays
static (4 fixed daily fires -- the routine is deliberately not permitted to
touch its own plist, for the same safety reasons it can't `git push`), but
the 11pm fire is a no-op (skipped before pytest/the pipeline even run) on
any day that isn't a game day or the day before one, per
`app/cli.py:is-game-day` (`app/model/schedule_context.py:is_game_day_or_eve`,
checked against whatever schedule data is already in the DB -- no network
call). Net effect: 3x/day (8am/1pm/6pm) on quiet days, 4x/day around actual
games.

To stop it manually at any time:
```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.footballpredictor.routine.plist
```

**Known limitation**: `nflreadpy` has zero preseason game data (verified --
`game_type` only ever contains REG/WC/DIV/CON/SB, for every season). The
routine can't predict preseason games; it just monitors until Week 1 enters
range each year. Also: the "best money move" parlay leg of the stop
condition needs live market odds (`ODDS_API_KEY` in `backend/.env`) --
without one, that half of the 90% target can never be satisfied.
