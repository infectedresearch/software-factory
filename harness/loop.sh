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
REVIEWFILE="$HOME_DIR/state/inline_review"
REVIEW_MODELFILE="$HOME_DIR/state/review_model"
DEFAULT_REVIEWER="${FACTORY_REVIEWER:-fracktron/claude-opus-5-5}"
PASS_TIMEOUT="${FACTORY_PASS_TIMEOUT:-2400}"
MAX_EMPTY=6
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

stash_leftovers() {
  if [ -n "$(g status --porcelain)" ]; then
    g stash push --include-untracked -m "factory: leftovers after pass $1" >/dev/null 2>&1
    log "pass $1 stashed leftovers=$(g rev-parse --short 'stash@{0}' 2>/dev/null)"
  fi
}

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if [ -f "$STOP" ]; then log "stop file present"; break; fi
  if [ "$(g rev-parse --abbrev-ref HEAD)" != "$BRANCH" ]; then
    log "worktree left $BRANCH; stopping"
    break
  fi
  n=$((n + 1))
  stash_leftovers "$((n - 1))"
  # Permissions live outside the repo and are re-installed every pass.
  cp "$HOME_DIR/opencode-policy.json" "$WT/opencode.json"
  model=$(current_model)
  before=$(g rev-parse HEAD)
  started=$(date +%s)
  log "pass $n begin $before model=$model"

  focus=""
  [ -f "$FOCUS" ] && focus="$(cat "$FOCUS")"
  lessons="$(cd "$HOME_DIR" && python3 review.py lessons --lane "$LANE" 2>/dev/null)"
  feedback=""
  [ -f "$STATE/feedback.txt" ] && feedback="$(cat "$STATE/feedback.txt")"
  prompt="$(cat "$HOME_DIR/prompt.md")

$focus

$feedback

$lessons

$note

This is pass $n of $LANE. HEAD is $(g rev-parse --short HEAD)."
  ( cd "$WT" && timeout --kill-after=60 "$PASS_TIMEOUT" \
      opencode run --standalone --auto --model "$model" --title "$TITLE-$n" "$prompt" ) >> "$LOG" 2>&1
  code=$?
  after=$(g rev-parse HEAD)
  log "pass $n end exit=$code head=$after"
  [ "$code" -eq 124 ] && log "pass $n timeout"
  stash_leftovers "$n"

  if [ "$before" = "$after" ]; then
    empty=$((empty + 1))
    log "pass $n no-commit ($empty/$MAX_EMPTY)"
    note="The previous pass made no commit. Pick a different, smaller target than last time."
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
  else
    keep="factory/rejected/$LANE-pass-$n"
    g branch -f "$keep" "$after" >/dev/null 2>&1
    g reset --hard "$before" >/dev/null 2>&1
    log "pass $n gate failed secs=$secs"
    log "pass $n rejected kept=$keep"
    reasons=$(python3 -c "import json,sys;print('; '.join(json.load(open(sys.argv[1])).get('reasons',[]))[:1500])" "$report" 2>/dev/null)
    note="Your previous pass ($n) was removed from the branch because the gate failed: $reasons
The removed work is on branch $keep. Do not re-apply it as-is."
  fi
done

log "done passes=$n head=$(g rev-parse --short HEAD)"
