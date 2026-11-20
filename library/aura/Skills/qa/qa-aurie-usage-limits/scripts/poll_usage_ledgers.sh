#!/usr/bin/env bash
# Sample the usage-limit ledgers repeatedly so a scenario produces a timeline.
#
# One-line-per-sample output, appended to a file so it survives the shell. Run it
# BEFORE starting a scenario and let it keep going through the whole thing —
# reconstructing a timeline afterwards is not possible.
#
# The interval is wall-clock sleep BETWEEN reads, and each read itself takes
# anywhere from ~3s to ~25s over Wi-Fi. So effective spacing is larger and
# irregular: read the timestamps, never assume a fixed cadence, and reason from
# running totals rather than consecutive deltas (see references/interpreting-results.md).
#
# Usage:
#   bash poll_usage_ledgers.sh --udid <IOS_UDID> [--uid <userId>] [--count 26] [--interval 45] [--out FILE]

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UDID=""
UID_ARG=""
COUNT=26
INTERVAL=45
OUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --udid)     UDID="$2"; shift 2 ;;
    --uid)      UID_ARG="$2"; shift 2 ;;
    --count)    COUNT="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    --out)      OUT="$2"; shift 2 ;;
    -h|--help)  sed -n '2,17p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[ -n "$UDID" ] || { echo "--udid is required (get it from: ios-deploy -c)" >&2; exit 2; }
[ -n "$OUT" ] || OUT="usage-timeline-$(date +%Y%m%d-%H%M%S).txt"

: > "$OUT"
echo "sampling $COUNT times, ~${INTERVAL}s apart → $OUT"
echo "note the wall-clock time of every transition (call start, background, hang-up)."

i=0
while [ "$i" -lt "$COUNT" ]; do
  # --json keeps each sample on predictable keys; collapse to one grep-able line.
  if raw=$(python3 "$SCRIPT_DIR/read_usage_ledgers.py" --udid "$UDID" \
             ${UID_ARG:+--uid "$UID_ARG"} --json 2>/dev/null); then
    line=$(python3 - "$raw" <<'PY'
import json, sys, time
try:
    d = json.loads(sys.argv[1])
except json.JSONDecodeError:
    print(f"{time.strftime('%H:%M:%S')}  decode-failed"); raise SystemExit
free, pkg = d.get("free") or {}, d.get("package") or {}
voice, msg = pkg.get("voice") or {}, pkg.get("message") or {}
# Carry the pinned user and any stale UIDs into every line: a series read off the
# wrong account is indistinguishable from a correct one after the fact.
uid = d.get("userId") or "unresolved"
stale = d.get("staleUserIds") or []
print(
    f"{d.get('readAt', time.strftime('%H:%M:%S'))}"
    f"  free={free.get('voiceUsedSeconds', 0):.4f}s"
    f"  daily={voice.get('dailyUsed', 0)}ms"
    f"  weekly={voice.get('weeklyUsed', 0)}/{voice.get('weeklyLimit', 0)}ms"
    f"  msg={msg.get('dailyUsed', 0)}"
    f"  uid={uid[:8]}"
    + (f"  [stale:{','.join(s[:8] for s in stale)}]" if stale else "")
)
PY
)
  else
    # A failed read is normal (Wi-Fi stall, device locked). Record it rather than
    # dropping it — a gap in the series must not look like a flat stretch.
    line="$(date +%H:%M:%S)  read-failed"
  fi
  echo "$line" | tee -a "$OUT"
  i=$((i + 1))
  [ "$i" -lt "$COUNT" ] && sleep "$INTERVAL"
done

echo "=== polling finished ===" | tee -a "$OUT"
