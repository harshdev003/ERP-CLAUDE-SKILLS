#!/bin/bash
# Summarize a profile that is still running, from the CLI's temp stream file, then delete the copy.
# Usage: peek.sh [--pid <pid>] [--watch node1,node2] [--rules path/to/rules.json]
# --pid picks the run whose "Stream owner pid" run-profile.sh printed; without it, the newest tmp-* file.
# Never deletes the CLI's own temp file: a running profiler re-opens it by name at the end.
set -u
DIR=$(cd "$(dirname "$0")" && pwd)
WATCH=""; RULES=""; PID=""
while [ $# -gt 0 ]; do case "$1" in --pid) PID=$2; shift 2;; --watch) WATCH=$2; shift 2;; --rules) RULES=$2; shift 2;; *) echo "unknown arg $1"; exit 2;; esac; done
case "$PID" in ""|*[!0-9]*) [ -z "$PID" ] || { echo "--pid must be a number"; exit 2; };; esac
T=${TMPDIR:-/tmp}
SRC=$(ls -t "$T" | grep "^tmp-${PID:+$PID-}" | head -1)
[ -n "$SRC" ] || { echo "no running profile found in $T"; exit 1; }
RUN=$(mktemp -d "$T/rtdb-peek.XXXXXX") || exit 1
trap 'rm -rf -- "$RUN"' EXIT
mkdir -p "$RUN/profiler" && cp "$T/$SRC" "$RUN/profiler/firebase-peek.log"
(cd "$RUN" && node "$DIR/summarize-denied.js" 2>&1 | sed -n '/^Permission denied/,$p')
echo
node "$DIR/root-attempts.js" "$RUN/profiler" "${RULES:-}"
echo
node "$DIR/rest-reads.js" "$RUN/profiler"
echo
node "$DIR/realtime-reads.js" "$RUN/profiler"
if [ -n "$WATCH" ]; then echo; node "$DIR/watched-nodes.js" "$RUN/profiler" "$WATCH"; fi
