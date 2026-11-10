#!/bin/bash
# Software factory loop for one lane. One `opencode run` per pass; the
# harness, not the agent, decides whether the pass's commits stay.
#
#   systemctl --user start grok-factory-lane@lane-2   (normal)
#   FACTORY_LANE=lane-2 ... bash loop.sh               (by hand)
#
# Graceful stop: touch <lane state>/stop (the board's Stop button does this).
set -u

HOME_DIR="$(cd "$(dirname "$0")" && pwd)"
LANE="${FACTORY_LANE:-lane-1}"
STATE="${FACTORY_STATE:-$HOME_DIR/state}"
WT="${FACTORY_WORKTREE:-/srv/factory/target-lane}"
BRANCH="${FACTORY_BRANCH:-grok/improvements}"
TITLE="${FACTORY_TITLE:-grok-improve}"
FOCUS="${FACTORY_FOCUS:-$HOME_DIR/focus/$LANE.md}"
UNTIL="${FACTORY_UNTIL:-}"
DEFAULT_MODEL="${FACTORY_MODEL:-fracktron/grok-4.7}"
MODELFILE="$HOME_DIR/state/model"
# A lane may pin its own model in its state dir. Card lanes do this so a test
# run can use a cheap model without changing the model every other lane uses.
[ -f "$STATE/model" ] && MODELFILE="$STATE/model"
REVIEWFILE="$HOME_DIR/state/inline_review"
REVIEW_MODELFILE="$HOME_DIR/state/review_model"
DEFAULT_REVIEWER="${FACTORY_REVIEWER:-fracktron/claude-opus-5-5}"
# Round 1's median landing pass took 8 minutes; a pass still running at 25 is
# re-reading context, not working.
PASS_TIMEOUT="${FACTORY_PASS_TIMEOUT:-1500}"
STALL_SECS="${FACTORY_STALL_SECS:-420}"
MAX_EMPTY=6
MASTER_REF="${FACTORY_MASTER_REF:-master}"
export FACTORY_SCRATCH="${FACTORY_SCRATCH:-/srv/factory/scratch/scratch-$LANE}"

if [ "${FACTORY_FROZEN:-}" = "1" ]; then echo "lane $LANE is frozen (review only)" >&2; exit 0; fi

LOG="$STATE/loop.log"
PIDFILE="$STATE/loop.pid"
STOP="$STATE/stop"
mkdir -p "$STATE/gate"
touch "$LOG"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG"; }
g() { git -C "$WT" "$@"; }

# Another instance for this lane (for example the one being replaced) is
# allowed to finish its pass first.
while [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; do
  sleep 15
done
echo $$ > "$PIDFILE"
trap 'rm -f "$PIDFILE"' EXIT
rm -f "$STOP"

if [ -n "$UNTIL" ]; then
  DEADLINE=$(date -u -d "$UNTIL" +%s)
else
  DEADLINE=$(( $(date +%s) + ${FACTORY_HOURS:-11} * 3600 ))
fi
UNTIL_ISO=$(date -u -d "@$DEADLINE" +%Y-%m-%dT%H:%M:%SZ)

if [ "$(g rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
  log "refusing to start: worktree is not on $BRANCH"
  exit 1
fi

last_n=$(sed 's/\x1b\[[0-9;]*m//g' "$LOG" \
  | grep -oE '^[0-9TZ:-]+ (iteration|pass) [0-9]+ begin' | awk '{print $3}' | sort -n | tail -1)
n=${last_n:-0}
empty=0
note=""
current_model() { cat "$MODELFILE" 2>/dev/null || echo "$DEFAULT_MODEL"; }
log "start lane=$LANE model=$(current_model) until=$UNTIL_ISO head=$(g rev-parse --short HEAD)"

# Uncommitted leftovers are saved as a patch in the lane's state and discarded.
# Round 1 used `git stash`, which every worktree of a repository shares, and
# two lanes swapped each other's work. Nothing is stashed any more.
discard_leftovers() {
  if [ -n "$(g status --porcelain)" ]; then
    mkdir -p "$STATE/leftovers"
    { g diff HEAD; g ls-files --others --exclude-standard | sed 's/^/untracked: /'; } > "$STATE/leftovers/pass-$1.patch" 2>/dev/null
    g checkout -q -- . 2>/dev/null
    g clean -fdq 2>/dev/null
    log "pass $1 discarded leftovers=leftovers/pass-$1.patch"
  fi
}

# Round 1 ended with every lane returning empty passes in the same twelve
# minutes (the provider, not the code) and the harness kept spending passes.
# When most lanes go empty together, stop them all and say so.
EMPTIES="$HOME_DIR/state/empties.log"
note_empty() {
  printf '%s %s\n' "$(date +%s)" "$LANE" >> "$EMPTIES"
  local now cutoff lanes active
  now=$(date +%s); cutoff=$((now - 480))
  lanes=$(awk -v c="$cutoff" '$1 >= c {print $2}' "$EMPTIES" 2>/dev/null | sort -u | wc -l)
  active=$(ls "$HOME_DIR"/state/*/loop.pid 2>/dev/null | wc -l)
  if [ "$active" -ge 4 ] && [ "$lanes" -ge $(( (active * 6 + 9) / 10 )) ]; then
    log "synchronized empties: $lanes of $active lanes empty within 8 minutes; stopping all lanes"
    for pid in "$HOME_DIR"/state/*/loop.pid; do touch "$(dirname "$pid")/stop"; done
    ( cd /srv/factory/target && timeout 60 uv run python -c "
import asyncio,sys
sys.path.insert(0,'/srv/factory/target')
from scripts.build_log_media_canary import _post
asyncio.run(_post('🏭 Factory stopped itself: $lanes of $active lanes returned empty passes within 8 minutes. Usually the model provider or quota, not the code. Check https://fracktron.com/factory/'))
" ) >> "$LOG" 2>&1 || log "could not post synchronized-empty alert"
  fi
}

# Card lanes take one accepted card at a time from the board's queue.
is_card_lane() { case "$LANE" in cards-*) return 0 ;; *) return 1 ;; esac; }
card=""

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if [ -f "$STOP" ]; then log "stop file present"; break; fi
  if [ "$(g rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
    log "worktree left $BRANCH; stopping"
    break
  fi
  n=$((n + 1))
  discard_leftovers "$((n - 1))"
  # Permissions live outside the repo and are re-installed every pass.
  cp "$HOME_DIR/opencode-policy.json" "$WT/opencode.json"
  # Sanitized config (secrets replaced) so the gate can run the ~55 tests
  # that read it; VLLM_MAX_MODEL_LEN pins the tool budget llm.py would
  # otherwise probe from the live gateway.
  python3 "$HOME_DIR/fixture.py" "$WT" >/dev/null 2>&1 || log "pass $n fixture install failed"
  export VLLM_MAX_MODEL_LEN=24576
  model=$(current_model)
  before=$(g rev-parse HEAD)
  started=$(date +%s)

  if is_card_lane; then
    card=$(cd "$HOME_DIR" && FACTORY_AREAS="${FACTORY_AREAS:-}" python3 cards.py next --lane "$LANE" --focus "$FOCUS" 2>>"$LOG")
    if [ -z "$card" ] || [ "$card" = "none" ]; then log "card queue empty; stopping"; break; fi
    log "pass $n card $card"
  fi
  log "pass $n begin $before model=$model"

  focus=""
  [ -f "$FOCUS" ] && focus="$(cat "$FOCUS")"
  lessons="$(cd "$HOME_DIR" && python3 review.py lessons --lane "$LANE" 2>/dev/null)"
  feedback=""
  [ -f "$STATE/feedback.txt" ] && feedback="$(cat "$STATE/feedback.txt")"
  # A lane that restarted re-fixed what it had already fixed last run. Show it
  # its own branch so it does not repeat itself.
  done_log="$(g log --format='- %h %s' "$MASTER_REF..HEAD" 2>/dev/null | head -40)"
  [ -n "$done_log" ] && done_log="Already committed on this branch (do not redo these):
$done_log"
  prompt="$(cat "$HOME_DIR/prompt.md")

$focus

$done_log

$feedback

$lessons

$note

This is pass $n of $LANE. HEAD is $(g rev-parse --short HEAD)."
  ( cd "$WT" && timeout --kill-after=60 "$PASS_TIMEOUT" \
      opencode run --standalone --auto --model "$model" --title "$TITLE-$n" "$prompt" ) >> "$LOG" 2>&1 &
  pass_pid=$!
  # Stall watchdog: a pass that writes nothing for STALL_SECS is hung on the
  # provider, not working. Kill it rather than wait out the whole timeout.
  stalled=0
  while kill -0 "$pass_pid" 2>/dev/null; do
    sleep 30
    if [ $(( $(date +%s) - $(stat -c %Y "$LOG") )) -ge "$STALL_SECS" ]; then
      stalled=1
      pkill -TERM -P "$pass_pid" 2>/dev/null; kill -TERM "$pass_pid" 2>/dev/null
      sleep 10
      pkill -KILL -P "$pass_pid" 2>/dev/null; kill -KILL "$pass_pid" 2>/dev/null
      break
    fi
  done
  wait "$pass_pid"; code=$?
  [ "$stalled" -eq 1 ] && { code=124; log "pass $n stalled: no output for ${STALL_SECS}s; killed"; }
  after=$(g rev-parse HEAD)
  log "pass $n end exit=$code head=$after"
  [ "$code" -eq 124 ] && log "pass $n timeout"
  discard_leftovers "$n"

  if [ "$before" = "$after" ]; then
    empty=$((empty + 1))
    log "pass $n no-commit ($empty/$MAX_EMPTY)"
    note="The previous pass made no commit. Pick a different, smaller target than last time."
    if [ "$code" -eq 124 ] && [ -s "$STATE/leftovers/pass-$n.patch" ]; then
      touched=$(grep -oE '^\+\+\+ b/[^ ]+' "$STATE/leftovers/pass-$n.patch" | sed 's|^+++ b/||' | head -8 | tr '\n' ' ')
      note="Your previous pass ran out of time before committing; its uncommitted edits were discarded. They touched: $touched. If that change was nearly done, redo it directly now without re-reading the whole area, then commit. If it was not close, pick something smaller."
    fi
    note_empty
    if is_card_lane; then
      res=$(cd "$HOME_DIR" && python3 cards.py strike --lane "$LANE" --why "pass $n made no commit" 2>>"$LOG")
      log "pass $n card $card strike -> $res"
      note=""
    fi
    if [ "$empty" -ge "$MAX_EMPTY" ]; then log "stalled; stopping"; break; fi
    # A pass that dies in seconds is usually the model provider, not the
    # code. Back off so a short outage does not use up the empty-pass budget.
    if [ $(( $(date +%s) - started )) -lt 90 ]; then
      log "pass $n ended in under 90s; waiting 300s"
      sleep 300
    fi
    continue
  fi
  empty=0

  log "pass $n gate start"
  report="$STATE/gate/pass-$n.json"
  ( cd "$HOME_DIR" && python3 gate.py --repo "$WT" --base "$before" --out "$report" ) >> "$LOG" 2>&1
  verdict=$?
  secs=$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('seconds',''))" "$report" 2>/dev/null)
  if [ "$verdict" -eq 0 ]; then
    log "pass $n gate passed secs=$secs"
    note=""
    # Legacy single-card lanes (card-N) stop after their one landed pass.
    case "$LANE" in
      card-*) log "card done; stopping"; break ;;
    esac
    if [ "$(cat "$REVIEWFILE" 2>/dev/null || echo off)" = "on" ]; then
      reviewer=$(cat "$REVIEW_MODELFILE" 2>/dev/null || echo "$DEFAULT_REVIEWER")
      log "pass $n review start model=$reviewer"
      dropped=""
      for sha in $(g rev-list --reverse "$before..$after"); do
        row=$(cd "$HOME_DIR" && python3 review.py one --lane "$LANE" --sha "$sha" --model "$reviewer" 2>>"$LOG" | tail -1)
        rc=$?
        kind=$(printf '%s' "$row" | python3 -c "import json,sys;print(json.load(sys.stdin).get('verdict','none'))" 2>/dev/null || echo none)
        why=$(printf '%s' "$row" | python3 -c "import json,sys;print(json.load(sys.stdin).get('reason','')[:900])" 2>/dev/null)
        short=$(g rev-parse --short "$sha")
        log "pass $n review $kind $short"
        case "$kind" in
          drop) dropped="$short"; note="The reviewer dropped your last commit ($short), and it was removed from the branch: $why
Do not repeat that kind of change."; break ;;
          fix) note="The reviewer says your last commit ($short) needs a fix before it can go to master: $why
This pass must fix exactly that, in one small commit whose body has the line: Fixes: $short" ;;
          keep) [ -z "$note" ] && note="The reviewer kept your last commit ($short): $why" ;;
        esac
      done
      if [ -n "$dropped" ]; then
        keep="factory/dropped/$LANE-pass-$n"
        g branch -f "$keep" "$after" >/dev/null 2>&1
        g reset --hard "$before" >/dev/null 2>&1
        log "pass $n dropped kept=$keep"
      fi
    fi
    if is_card_lane; then
      if [ -n "${dropped:-}" ]; then
        res=$(cd "$HOME_DIR" && python3 cards.py strike --lane "$LANE" --why "reviewer dropped pass $n: ${why:-}" 2>>"$LOG")
        log "pass $n card $card strike -> $res"
      else
        # Kept (or kept with a fix request, which the lane handles next pass
        # as an ordinary commit on the same branch): the card is built.
        res=$(cd "$HOME_DIR" && python3 cards.py done --lane "$LANE" --sha "$(g rev-parse --short "$after")" 2>>"$LOG")
        log "pass $n card $card done"
      fi
    fi
  else
    keep="factory/rejected/$LANE-pass-$n"
    g branch -f "$keep" "$after" >/dev/null 2>&1
    g reset --hard "$before" >/dev/null 2>&1
    log "pass $n gate failed secs=$secs"
    log "pass $n rejected kept=$keep"
    reasons=$(python3 -c "import json,sys;print('; '.join(json.load(open(sys.argv[1])).get('reasons',[]))[:1500])" "$report" 2>/dev/null)
    note="Your previous pass ($n) was removed from the branch because the gate failed: $reasons
The removed work is on branch $keep. Do not re-apply it as-is."
    if is_card_lane; then
      res=$(cd "$HOME_DIR" && python3 cards.py strike --lane "$LANE" --why "gate failed on pass $n: ${reasons:0:300}" 2>>"$LOG")
      log "pass $n card $card strike -> $res"
    fi
  fi
done

log "done passes=$n head=$(g rev-parse --short HEAD)"
