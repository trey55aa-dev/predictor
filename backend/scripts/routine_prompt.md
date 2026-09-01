You are running as an unattended, scheduled pass for the Football Predictor
project (a local FastAPI + SQLite NFL prediction app). This runs a few times
a day via launchd with nobody watching, so be conservative: no force-push,
no deleting data, no schema changes unless you're highly confident and you
clearly log why. Work only inside this repo. You are only invoked when
there's something worth your judgment (a test failure, real predictions to
analyze, or the stop condition) -- the wrapper script already ran pytest and
the data pipeline and appended their raw output below this prompt; do NOT
re-run them yourself.

## 1. Bug check
Look at the pytest output already provided below. If it failed: investigate,
and fix it yourself ONLY if the fix is small, obviously safe, and clearly
scoped to the failing test (e.g. a typo, an off-by-one, a stale assertion).
If the fix is not obviously safe, or you're not confident, do NOT change
production code speculatively -- log exactly what's failing and why you're
leaving it, so a human can look at it. If pytest passed, just note that.

## 2. Qualitative analysis ("ask Claude about chances and predictors")
If the run-routine output below shows real predictions were generated for
an upcoming week, pick the 1-2 most notable games (closest win probability
to 50/50, an upset-alert flag, or a significant injury) and write a short
paragraph of real analysis for each -- not a restatement of the numbers.
Pull from `GET /api/predictions/game/{game_id}/gameplan` (start the API
with `uv run uvicorn app.main:app --port 8000 &` if it's not already
running -- check with `lsof -i :8000` first so you don't stack duplicate
servers) -- reason about what the injury, scheme matchup, and weather data
actually imply, the way a knowledgeable analyst would. A few sentences per
game is enough.

## 3. Check the stop condition
The run-routine output ends with `STOP_CONDITION_MET=True` or `False`. If
(and only if) it says `True`:
- Log clearly that the accuracy goal (90% winner accuracy AND 90% parlay
  hit-rate, both on a real sample) has been reached.
- Disable this routine so it stops running:
  `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.footballpredictor.routine.plist`
  Log the exact command you ran and its output.

## 4. Write the log entry
Append one entry to `backend/routine_log/YYYY-MM-DD.md` (create the file if
today's doesn't exist yet):

```
## Run at <local timestamp>

**Bug check:** <pass / what you found and fixed / what you left and why>

**Analysis:** <your qualitative paragraph(s), or "no upcoming games to analyze this run">

**Stop condition:** <met and disabled routine / not met, current numbers>
```

Keep it tight -- this is an operational log a human skims occasionally, not
a report. You do not need to repeat the full pytest/pipeline output in the
log; a summary sentence is enough.
