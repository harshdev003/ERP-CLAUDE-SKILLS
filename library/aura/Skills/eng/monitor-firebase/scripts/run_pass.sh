#!/bin/bash
# One read-only monitoring pass over prod Firebase (firebase-auratech16).
# Usage: run_pass.sh [--minutes 10] [--watch users,node2] [--run-dir DIR]
# Profiles RTDB for N minutes (eng:profile-firebase-rtdb), then checks database load, Cloud Functions
# and allowed reads' networks for the same window. Writes, in the run dir (mode 700):
#   findings.jsonl   one finding per line (scripts/common.py), counts only
#   detail.jsonl     probe attempts WITH IPs and user agents: Security Incident Log input only
#   probes.jsonl     counts-only probe groups
#   rest-sources.jsonl  allowed REST reads per IP + user agent + UTC day, WITH IPs (evaluate_profile.py input)
#   rest-bursts.jsonl   on a REST burst, its sources at 100+ reads / 10 min, WITH IPs: Incident Log input only
#   verdicts.json    check_networks' known-teammate and console addresses, for the incident planner
#   code/            the copy of both skills' scripts this pass runs from
# Prints a short summary, then "SCRIPTS <dir>" (the copied scripts, for the caller's later steps)
# and, last, "RUN_DIR <dir>". The caller deletes the run dir when done.
# A check that doesn't finish (non-zero exit, missing script) is a FAIL finding naming the script, so
# the pass never says PASS OK without having checked. The scripts run from the copy, so a plugin update
# or prune that removes this version mid-pass can't take them away.
set -u
SRC=$(cd "$(dirname "$0")" && pwd)
PLUGIN=$(cd "$SRC/../../.." && pwd)
MIN=10; WATCH=users; RUN=""
while [ $# -gt 0 ]; do case "$1" in
  --minutes) MIN=$2; shift 2;; --watch) WATCH=$2; shift 2;; --run-dir) RUN=$2; shift 2;;
  *) echo "unknown arg $1"; exit 2;; esac; done
[ -f "$PLUGIN/skills/profile-firebase-rtdb/scripts/run-profile.sh" ] \
  || { echo "eng:profile-firebase-rtdb not found next to this skill ($PLUGIN/skills)"; exit 2; }
case ",$WATCH," in *,users,*) ;; *) WATCH="users,$WATCH";; esac
[ -n "$RUN" ] || RUN=$(mktemp -d "${TMPDIR:-/tmp}/monitor-firebase.XXXXXX")
mkdir -p "$RUN" && chmod 700 "$RUN"
umask 077
: > "$RUN/findings.jsonl"

# Scripts, SKILL.md (the user agent reads the version from it) and the plugin manifest.
CODE="$RUN/code"
for s in monitor-firebase profile-firebase-rtdb; do
  mkdir -p "$CODE/skills/$s"
  cp -R "$PLUGIN/skills/$s/scripts" "$CODE/skills/$s/"
  cp "$PLUGIN/skills/$s/SKILL.md" "$CODE/skills/$s/" 2>/dev/null
done
[ -d "$PLUGIN/.claude-plugin" ] && cp -R "$PLUGIN/.claude-plugin" "$CODE/"
D="$CODE/skills/monitor-firebase/scripts"
PROFILE="$CODE/skills/profile-firebase-rtdb/scripts/run-profile.sh"

# fail_if <script> <exit status>: a check that didn't finish is a finding, never silence.
fail_if() {
  [ "$2" -eq 0 ] && return
  printf '{"check": "run", "level": "fail", "key": "run-%s", "text": "%s exited %s: its check did not run"}\n' \
    "$1" "$1" "$2" >> "$RUN/findings.jsonl"
}

bash "$PROFILE" --project firebase-auratech16 --minutes "$MIN" --watch "$WATCH" \
  --probe-json "$RUN/probes.jsonl" --probe-detail "$RUN/detail.jsonl" \
  --rest-detail "$RUN/rest-sources.jsonl" > "$RUN/profile.txt" 2>&1
fail_if run-profile.sh $?
python3 "$D/evaluate_profile.py" --profile "$RUN/profile.txt" --minutes "$MIN" \
  --rest-detail "$RUN/rest-sources.jsonl" --rest-bursts "$RUN/rest-bursts.jsonl" >> "$RUN/findings.jsonl"
fail_if evaluate_profile.py $?
rm -f "$RUN/profile.txt" "$RUN/rest-sources.jsonl"
python3 "$D/check_db_load.py" --minutes "$MIN" >> "$RUN/findings.jsonl"
fail_if check_db_load.py $?
python3 "$D/check_shallow_gets.py" --minutes "$MIN" >> "$RUN/findings.jsonl"
fail_if check_shallow_gets.py $?
python3 "$D/check_functions.py" window --minutes "$MIN" >> "$RUN/findings.jsonl"
fail_if check_functions.py $?
python3 "$D/check_networks.py" --detail "$RUN/detail.jsonl" --verdicts "$RUN/verdicts.json" \
  --rest-bursts "$RUN/rest-bursts.jsonl" >> "$RUN/findings.jsonl"
fail_if check_networks.py $?

python3 - "$RUN/findings.jsonl" <<'PY'
import json, sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
rows = []
for line in open(sys.argv[1], encoding="utf-8"):
    if line.strip():
        try:
            rows.append(json.loads(line))
        except ValueError:  # a check that died mid-line: count it, never skip it
            rows.append({"level": "fail", "check": "run", "text": "a check wrote an unreadable line"})
for lvl in ("fail", "alert", "note"):
    for r in rows:
        if r.get("level") == lvl:
            print(f"{lvl.upper():5} {r.get('check', '?'):9} {r.get('text', '')}")
n = {l: sum(r.get("level") == l for r in rows) for l in ("alert", "fail")}
print("PASS", "OK" if not any(n.values()) else f"{n['alert']} alert(s), {n['fail']} failed check(s)")
PY
[ $? -eq 0 ] || echo "PASS FAILED: the summary did not run"
echo "SCRIPTS $D"
echo "RUN_DIR $RUN"
