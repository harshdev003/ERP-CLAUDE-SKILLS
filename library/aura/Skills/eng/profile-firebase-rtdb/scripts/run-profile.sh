#!/bin/bash
# Profile the Firebase Realtime Database and print a privacy-safe summary, then delete the raw logs.
# Usage: run-profile.sh [--project firebase-auratech16] [--minutes 15 | --seconds 90] [--watch node1,node2]
#                       [--rules path/to/rules.json] [--probe-json path/to/probes.jsonl]
#                       [--probe-detail path/to/detail.jsonl] [--rest-detail path/to/rest-sources.jsonl]
# --probe-json appends counts-only JSON lines for the RTDB Probe Log: every root and whole top-level node read
# and denied operation (root-attempts.js), and allowed REST reads below a node (rest-reads.js).
# --probe-detail appends one line per probe attempt WITH its IP and user agent (mode 600), for the
# Security Incident Log only; the caller deletes it once that log is written.
# --rest-detail writes allowed REST reads per IP + user agent + UTC day WITH the IP (mode 600, rest-reads.js),
# for the same log: a REST burst's caller can't be recovered once the raw log is gone.
# Prints no IPs, keys or values. Read-only: the profiler records operation metadata, it never reads data.
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
PROJECT=firebase-auratech16; MIN=15; SECS=""; WATCH=""; RULES=""; PROBE_JSON=""; PROBE_DETAIL=""; REST_DETAIL=""
while [ $# -gt 0 ]; do case "$1" in
  --project) PROJECT=$2; shift 2;; --minutes) MIN=$2; shift 2;; --seconds) SECS=$2; shift 2;;
  --watch) WATCH=$2; shift 2;; --rules) RULES=$2; shift 2;;
  --probe-json) PROBE_JSON=$2; shift 2;; --probe-detail) PROBE_DETAIL=$2; shift 2;; --rest-detail) REST_DETAIL=$2; shift 2;;
  *) echo "unknown arg $1"; exit 2;; esac; done
if [ -n "$SECS" ]; then DUR="${SECS}s"; else SECS=$((MIN * 60)); DUR="$MIN min"; fi
command -v firebase >/dev/null || { echo "firebase CLI not found (npm i -g firebase-tools)"; exit 1; }
RUN=$(mktemp -d "${TMPDIR:-/tmp}/rtdb-profile.XXXXXX") || exit 1
mkdir -p "$RUN/profiler"; touch "$RUN/start.marker"
# Rules: given file, else master of AuraHealth/Firebase if a checkout exists, else unknown.
if [ -z "$RULES" ]; then
  FB=${FIREBASE_RULES_REPO:-$HOME/Development/Aura/Data/Firebase}
  if git -C "$FB" rev-parse >/dev/null 2>&1; then
    git -C "$FB" fetch -q origin 2>/dev/null; git -C "$FB" show origin/master:rules.json > "$RUN/rules.json" 2>/dev/null && RULES="$RUN/rules.json"
  fi
fi
T=${TMPDIR:-/tmp}; T=${T%/}
CLI_PID=""; OWNER_PIDS=""; STREAM=""
cleanup() {
  if [ -n "$CLI_PID" ] && kill -0 "$CLI_PID" 2>/dev/null; then
    kill ${OWNER_PIDS//,/ } 2>/dev/null; wait "$CLI_PID" 2>/dev/null
  fi
  [ -n "$OWNER_PIDS" ] && node "$DIR/delete-raw.js" "$RUN/start.marker" "$OWNER_PIDS"
  rm -rf -- "$RUN"
  echo "Raw logs deleted."
}
trap cleanup EXIT
# The CLI runs in the background, where bash ignores SIGINT for it: kill it ourselves, then clean up.
trap 'echo "Interrupted."; exit 130' INT TERM
# This run's stream file: $T/tmp-<pid>-*, pid being the node process (or a child of a wrapper).
find_stream() {
  local c
  for c in $(pgrep -P "$CLI_PID" 2>/dev/null); do
    case ",$OWNER_PIDS," in *",$c,"*) ;; *) OWNER_PIDS="$OWNER_PIDS,$c";; esac
  done
  for c in ${OWNER_PIDS//,/ }; do
    STREAM=$(ls "$T" 2>/dev/null | grep "^tmp-$c-" | head -1)
    [ -n "$STREAM" ] && return 0
  done
  return 1
}
START=$(date -u +%FT%TZ)
echo "Profiling $PROJECT for $DUR from $START ..."
# The CLI streams to $TMPDIR/tmp-<pid>-*, re-opens that file by name at the end to write -o, and ends
# with a harmless "Unexpected error from response stream: This operation was aborted".
# Cleanup deletes only this CLI's file, so overlapping runs on one machine don't break each other.
firebase database:profile --project "$PROJECT" --raw -d "$SECS" -o "$RUN/profiler/firebase.log" >"$RUN/cli.out" 2>&1 &
CLI_PID=$!; OWNER_PIDS=$CLI_PID
for _ in $(seq 1 60); do
  find_stream && break
  kill -0 "$CLI_PID" 2>/dev/null || break
  sleep 0.5
done
if [ -n "$STREAM" ]; then SP=${STREAM#tmp-}; echo "Stream owner pid ${SP%%-*} (for peek.sh --pid)."
else echo "Stream temp file not seen yet (CLI pid $CLI_PID)."; fi
wait "$CLI_PID"
CLI_EXIT=$?
END=$(date -u +%FT%TZ)
# A failed run (not logged in, no permission, wrong project) must never read as a clean profile.
OPS=0; [ -s "$RUN/profiler/firebase.log" ] && OPS=$(grep -c '"name"' "$RUN/profiler/firebase.log")
if [ "$OPS" -eq 0 ]; then
  echo "PROFILE FAILED: no operations captured (firebase CLI exit $CLI_EXIT). This is NOT a clean result."
  echo "Last CLI output (check firebase login and access to $PROJECT):"
  grep -v -F 'Unexpected error from response stream: This operation was aborted {}' "$RUN/cli.out" | tail -5
  if [ -n "$STREAM" ] && [ ! -e "$T/$STREAM" ]; then
    echo "The CLI's stream temp file was deleted mid-run — another profiler's cleanup? (pre-fix delete-raw.js deleted every tmp-* file)"
  fi
  exit 3
fi
echo "Captured $OPS operations."
echo "Profile $START -> $END ($DUR)"
echo
(cd "$RUN" && node "$DIR/summarize-denied.js" 2>&1 | sed -n '/^Permission denied/,$p')
echo
node "$DIR/root-attempts.js" "$RUN/profiler" "${RULES:-}" "$PROBE_JSON" ${PROBE_DETAIL:+--probe-detail "$PROBE_DETAIL"}
echo
node "$DIR/signedout-users.js" "$RUN/profiler"
echo
node "$DIR/rest-reads.js" "$RUN/profiler" ${REST_DETAIL:+--source-detail "$REST_DETAIL"} ${PROBE_JSON:+--probe-json "$PROBE_JSON"}
echo
node "$DIR/realtime-reads.js" "$RUN/profiler"
if [ -n "$WATCH" ]; then echo; node "$DIR/watched-nodes.js" "$RUN/profiler" "$WATCH"; fi
echo
echo "Cloud Monitoring rule evaluations, same window (DENY/ALLOW per 10 min):"
bash "$DIR/monitoring-denies.sh" "$PROJECT" "$START" "$END"
