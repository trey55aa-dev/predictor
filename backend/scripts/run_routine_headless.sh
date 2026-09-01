#!/bin/bash
# Headless entry point for the 3-4x/day launchd routine.
#
# Cost-aware by design: pytest and the data pipeline are plain deterministic
# commands, so they run directly here in bash -- no LLM needed to run tests
# or notice "still preseason." Claude is only invoked when there's something
# worth its judgment: a test failure, real predictions to analyze, or the
# 90% stop condition. This matters in practice, not just in theory -- the
# first version of this script called `claude -p` unconditionally on every
# fire and burned a full week's usage limit in two runs during a preseason
# stretch where there was nothing to do.
#
# Flags verified against `claude --help` on this machine (CLI v2.1.246).

set -uo pipefail

PROJECT_ROOT="/Users/treyalsbrooks/Football"
BACKEND_DIR="$PROJECT_ROOT/backend"
PROMPT_FILE="$BACKEND_DIR/scripts/routine_prompt.md"
LOG_DIR="$BACKEND_DIR/routine_log"
RUN_LOG="$LOG_DIR/launchd-runs.log"
TODAY_LOG="$LOG_DIR/$(date '+%Y-%m-%d').md"
TOKEN_FILE="$HOME/.config/football-predictor/claude-token"

mkdir -p "$LOG_DIR"

# Make sure uv and the Claude Code CLI are on PATH -- launchd jobs get a
# minimal environment, not your interactive shell's PATH.
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

cd "$BACKEND_DIR" || exit 1

# Cadence: 3x/day (8am/1pm/6pm) every day; the 4th, late-night (11pm) slot
# only runs on a game day or the day before one (checked against whatever
# schedule data is already in the DB -- fast, no network call). Keeps the
# OS-level launchd schedule static (still fires 4x/day; the routine is
# deliberately not allowed to touch its own plist) while the actual work
# done varies with what's happening.
CURRENT_HOUR="$(date '+%H')"
if [ "$CURRENT_HOUR" = "23" ]; then
  if ! uv run python -m app.cli is-game-day > /dev/null 2>&1; then
    {
      echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') — routine run skipped (quiet day, late-night slot only runs near a game) ====="
    } >> "$RUN_LOG" 2>&1
    exit 0
  fi
fi

{
  TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo "===== $TIMESTAMP — routine run starting ====="

  TEST_OUTPUT="$(uv run pytest -q 2>&1)"
  TEST_EXIT=$?
  echo "--- pytest (exit $TEST_EXIT) ---"
  echo "$TEST_OUTPUT" | tail -10

  PIPELINE_OUTPUT="$(uv run python -m app.cli run-routine 2>&1)"
  PIPELINE_EXIT=$?
  echo "--- run-routine (exit $PIPELINE_EXIT) ---"
  echo "$PIPELINE_OUTPUT"

  # run-routine regenerates predictions on every call by design (to pick up
  # fresh odds/injuries), so "predictions exist" is true on nearly every
  # in-season run -- it can't be the trigger for Claude's analysis step, or
  # we're right back to a Claude call every firing. Analysis is a once-a-day
  # thing regardless of how many times data refreshes; a same-day sentinel
  # file gates it so 3-4x/day data refreshes stay Claude-free after the
  # first.
  ANALYZED_TODAY_FLAG="$LOG_DIR/.analyzed-$(date '+%Y-%m-%d')"

  NEEDS_CLAUDE=0
  REASON=""
  HAS_PREDICTIONS=0

  if [ "$TEST_EXIT" -ne 0 ]; then
    NEEDS_CLAUDE=1
    REASON="test failure"
  fi
  if echo "$PIPELINE_OUTPUT" | grep -qE "Generated [1-9][0-9]* predictions"; then
    HAS_PREDICTIONS=1
    if [ ! -f "$ANALYZED_TODAY_FLAG" ]; then
      NEEDS_CLAUDE=1
      REASON="${REASON:+$REASON, }predictions to analyze (first check today)"
    fi
  fi
  if echo "$PIPELINE_OUTPUT" | grep -q "STOP_CONDITION_MET=True"; then
    NEEDS_CLAUDE=1
    REASON="${REASON:+$REASON, }stop condition met"
  fi

  if [ "$NEEDS_CLAUDE" -eq 1 ] && [ -s "$TOKEN_FILE" ]; then
    echo "Invoking Claude ($REASON)..."
    export CLAUDE_CODE_OAUTH_TOKEN
    CLAUDE_CODE_OAUTH_TOKEN="$(cat "$TOKEN_FILE")"

    PROMPT="$(cat "$PROMPT_FILE")

## This run's output (already executed by the wrapper script -- do NOT
## re-run pytest or run-routine yourself, just react to this):

### pytest -- exit $TEST_EXIT
$TEST_OUTPUT

### run-routine -- exit $PIPELINE_EXIT
$PIPELINE_OUTPUT
"
    claude -p "$PROMPT" \
      --allowedTools "Bash,Read,Write,Edit,Glob,Grep" \
      --permission-mode acceptEdits
    CLAUDE_EXIT=$?

    if [ "$CLAUDE_EXIT" -eq 0 ] && [ "$HAS_PREDICTIONS" -eq 1 ]; then
      touch "$ANALYZED_TODAY_FLAG"
    fi
  else
    echo "No Claude invocation needed -- tests pass, stop condition not met."
    if [ "$HAS_PREDICTIONS" -eq 1 ]; then
      NOTE="Predictions refreshed with latest odds/injuries/weather; already analyzed once today."
    else
      NOTE="$(echo "$PIPELINE_OUTPUT" | grep -o 'No games to predict yet[^.]*\.' || echo 'Pipeline ran cleanly; nothing new to flag.')"
    fi
    {
      echo ""
      echo "## Run at $TIMESTAMP (automated, no Claude call)"
      echo ""
      echo "Tests: pass. Pipeline: $NOTE"
    } >> "$TODAY_LOG"
  fi

  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') — routine run finished (exit 0) ====="
} >> "$RUN_LOG" 2>&1
