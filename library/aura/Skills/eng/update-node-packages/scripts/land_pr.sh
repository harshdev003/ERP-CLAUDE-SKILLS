#!/bin/bash
# Wait for CI green + Bugbot clean on a repo's open PR. By default it stops there: merges need
# Jerome's "merge", so it reports READY and never merges. With --merge (pass it only after his
# "merge" for this PR) it then merges. Aborts (never merges) on CI failure or open Bugbot findings
# on HEAD.
#
# It does NOT release — that is `eng:release-version`, invoked after the merge
# (see the block at the end of this file for why it is not done here). Terminal
# verdicts: READY or MERGED (exit 0), ABORT (exit 1).
#
# Usage: land_pr.sh <repo-name> <pr> <tier> [--merge]
#   tier ∈ services | data | functions | cli | model
#   `tier` is informational except for `cli`, which is merge-only and needs no
#   release. Nothing is checked out here, so there is no repo-dir; any other 4th argument
#   (the old repo-dir) is accepted and ignored.
set -uo pipefail
FN="$1"; PR="$2"; TIER="$3"; MERGE=""
for a in "${@:4}"; do [ "$a" = "--merge" ] && MERGE=1; done
POLL_S="${LAND_PR_POLL_S:-20}"
REPO="AuraHealth/$FN"; CCI="gh/AuraHealth/$FN"
abort(){ echo "VERDICT: ABORT — $*"; exit 1; }
BR=$(gh pr view "$PR" --repo "$REPO" --json headRefName -q .headRefName)
HEAD=$(gh pr view "$PR" --repo "$REPO" --json headRefOid -q .headRefOid)
echo ">>> $FN PR #$PR ($TIER) branch=$BR HEAD=$HEAD"
CI_OK=""; BUG_OK=""; STABLE=0
for i in $(seq 1 30); do
  sleep "$POLL_S"
  # Re-read the PR tip every poll: a push mid-poll must RE-validate the new HEAD,
  # or CI/Bugbot get validated on an old commit while the branch tip (what gets
  # merged) was never reviewed. If HEAD moved, reset the gates.
  NEWHEAD=$(gh pr view "$PR" --repo "$REPO" --json headRefOid -q .headRefOid 2>/dev/null)
  if [ -n "$NEWHEAD" ] && [ "$NEWHEAD" != "$HEAD" ]; then
    HEAD="$NEWHEAD"; STABLE=0; echo "  HEAD moved → re-validating $HEAD"
  fi
  CI=$(curl -s -H "Circle-Token: $CIRCLE_TOKEN" "https://circleci.com/api/v2/project/$CCI/pipeline?branch=$BR" 2>/dev/null | python3 -c "
import sys,json,urllib.request,os
tok=os.environ['CIRCLE_TOKEN']
try: d=json.load(sys.stdin)
except: print('none'); raise SystemExit
its=d.get('items',[])
if not its: print('none'); raise SystemExit
req=urllib.request.Request(f\"https://circleci.com/api/v2/pipeline/{its[0]['id']}/workflow\",headers={'Circle-Token':tok})
try:
    wfs=json.load(urllib.request.urlopen(req)).get('items',[])
    # Aggregate ALL workflows of the latest pipeline, not just the first — a repo
    # with build+deploy workflows must have every one succeed. But keep only the
    # NEWEST run of each workflow name: a rerun adds a new workflow and leaves the
    # superseded one in place, so reading every workflow lets one stale failure make
    # the PR permanently unmergeable through this gate — which pushes the operator to
    # merge by hand and bypass it entirely.
    latest={}
    for w in sorted(wfs, key=lambda w: w.get('created_at') or ''):
        latest[w.get('name')]=w.get('status')
    sts=list(latest.values())
    if not sts: print('none')
    elif any(s in ('failed','error','failing') for s in sts): print('failed')
    elif all(s=='success' for s in sts): print('success')
    else: print('running')
except: print('none')
" 2>/dev/null)
  SUMBODY=$(gh api repos/$REPO/issues/$PR/comments --paginate 2>/dev/null | jq -rs 'add | [.[] | select(.user.login|test("cursor|bugbot";"i"))] | last | .body' 2>/dev/null)
  SUMHEAD=$(printf '%s' "$SUMBODY" | grep -cF "$HEAD")
  # Inspect the SUMMARY body itself for findings, not just inline comments — Bugbot
  # enumerates bugs in the summary (severity headers / BUGBOT_BUG_ID) and the inline
  # comments can lag it, so counting only inline comments can merge over open bugs.
  # Count findings in the summary by the one unambiguous per-finding marker Bugbot
  # emits (BUGBOT_BUG_ID) — not the loose word "severity", which appears in clean
  # summaries too. Only trusted when the summary is FOR the current HEAD (below), so
  # a stale summary from an older commit can't block a clean new HEAD.
  SUMBUGS=$(printf '%s' "$SUMBODY" | grep -cE 'BUGBOT_BUG_ID')
  FIND=$(gh api repos/$REPO/pulls/$PR/comments --paginate 2>/dev/null | jq -r --arg H "$HEAD" '[.[] | select(.user.login|test("cursor|bugbot";"i")) | select(.line!=null) | select(.body|test($H))] | length' 2>/dev/null)
  echo "  poll $i: ci=$CI sum_head=$SUMHEAD sum_bugs=$SUMBUGS inline=${FIND:-?} head=${HEAD:0:8}"
  case "$CI" in failed|error|failing) abort "CI $CI on #$PR";; esac
  [ "${FIND:-0}" -gt 0 ] && abort "Bugbot $FIND inline findings on #$PR (HEAD $HEAD)"
  [ "${SUMHEAD:-0}" -ge 1 ] && [ "${SUMBUGS:-0}" -gt 0 ] && abort "Bugbot summary reports findings on #$PR (HEAD $HEAD)"
  # Ready only when CI is success ON THIS POLL (not a stale latch), the Bugbot
  # summary is for the CURRENT HEAD with NO findings in it, and 0 inline findings —
  # persisting ~90s (≈4 polls) because inline comments land ~1 min after the summary.
  if [ "$CI" = "success" ] && [ "${SUMHEAD:-0}" -ge 1 ]; then
    STABLE=$((STABLE + 1))
    [ "$STABLE" -ge 4 ] && { CI_OK=1; BUG_OK=1; break; }
  else
    STABLE=0
  fi
done
# Require BOTH gates — never merge if the loop timed out with CI green but Bugbot
# never reporting on HEAD (`[ -z "$CI_OK$BUG_OK" ]` would pass with CI_OK=1 alone).
[ -n "$CI_OK" ] || abort "CI never went green on #$PR (not merging)"
[ -n "$BUG_OK" ] || abort "Bugbot never confirmed clean on HEAD of #$PR (not merging)"
if [ -z "$MERGE" ]; then
  echo "VERDICT: READY $FN #$PR (CI green, Bugbot clean on $HEAD). Not merged: merging needs Jerome's"
  echo "      \"merge\" for this PR; then re-run with --merge."
  exit 0
fi
gh pr merge "$PR" --repo "$REPO" --squash --delete-branch >/dev/null 2>&1 || abort "merge failed"

# Two shapes of "merged": cli never releases; every other tier needs the release
# skill next, so each verdict names which case applies.
if [ "$TIER" = "cli" ]; then echo "VERDICT: MERGED $FN (cli tier — merge-only, no release)"; exit 0; fi

# Releasing is `eng:release-version`, invoked after this script returns.
#
# Never use `npm run release` for it. The default branch requires a pull request with
# no bypass actors, so release-it's direct push is rejected with `GH013: Changes must
# be made through a pull request` — and its rollback deletes the tag it already
# pushed, which starts and then strands a CI pipeline, firing a red "test job has
# failed" alert for a test that never ran.
#
# Nothing is checked out here. `eng:release-version` resolves the repo itself, fetches
# the default branch, and bumps inside its own worktree, so this script leaves the
# user's working tree untouched and needs no repo-dir.
#
# The release is not reimplemented here. Skill self-containment (Architecture
# principle 1) would force copying release-version's three scripts into this skill,
# i.e. three drifting copies of the tag-safety rules, and its `verify` subcommand
# already owns tag-pipeline polling. So the handoff is at the agent level.
echo "VERDICT: MERGED $FN (branch $BR at $HEAD)"
echo "NEXT: release with eng:release-version — it plans, shows the plan for"
echo "      approval, lands the bump through a PR, tags the squash-merge commit"
echo "      and verifies the deploy. Do NOT run npm run release."
exit 0
