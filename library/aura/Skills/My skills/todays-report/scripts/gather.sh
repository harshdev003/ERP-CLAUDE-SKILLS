#!/usr/bin/env bash
# Gather the GitHub and local-git side of Ashish's daily report.
#
# Usage: gather.sh [--since <ISO-8601 UTC>]
#   Default --since is the start of today in IST (UTC+5:30), i.e. 18:30Z the day before.
#   For "update it", pass the time of the previous gather (printed as WINDOW below).
#
# Linear is not covered here; the skill reads it through the Linear MCP tools.
set -uo pipefail

SINCE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --since) SINCE="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$SINCE" ]; then
  # Start of the current IST day, expressed in UTC.
  ist_date=$(TZ=Asia/Kolkata date +%Y-%m-%d)
  SINCE=$(TZ=UTC date -j -v-5H -v-30M -f "%Y-%m-%d %H:%M:%S" "$ist_date 00:00:00" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
    || date -u -d "$ist_date 00:00 +0530" +%Y-%m-%dT%H:%M:%SZ)
fi

ME=$(gh api user --jq .login 2>/dev/null || echo devashishHp04)
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "WINDOW since=$SINCE now=$NOW (IST now: $(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M'))  me=$ME"

echo
echo "== MY PRS touched in the window"
gh search prs --author=@me --updated=">=$SINCE" --limit 100 \
  --json repository,number,title,state,isDraft,createdAt,closedAt \
  --jq '.[] | "\(.repository.nameWithOwner)#\(.number)\t\(.state)\tdraft=\(.isDraft)\tcreated=\(.createdAt)\tclosed=\(.closedAt)\t\(.title)"' \
  > "${TMPDIR:-/tmp}/todays-report-prs.$$" 2>/dev/null
cat "${TMPDIR:-/tmp}/todays-report-prs.$$"

echo
echo "== DETAIL per PR (my non-merge commits, my comments, reviews, merge, base)"
while IFS=$'\t' read -r ref _rest; do
  [ -z "$ref" ] && continue
  repo="${ref%%#*}"; num="${ref##*#}"
  info=$(gh api "repos/$repo/pulls/$num" --jq '"base=\(.base.ref) draft=\(.draft) merged=\(.merged) merged_by=\(.merged_by.login // "-") merged_at=\(.merged_at // "-")"' 2>/dev/null)
  echo "-- $ref  $info"
  gh api "repos/$repo/pulls/$num/commits?per_page=100" \
    --jq ".[] | select(.commit.committer.date >= \"$SINCE\") | select(.commit.message|startswith(\"Merge\")|not) | \"   commit \(.commit.committer.date) \(.commit.message|split(\"\n\")[0])\"" 2>/dev/null | head -8
  gh api "repos/$repo/issues/$num/comments?per_page=100" \
    --jq ".[] | select(.created_at >= \"$SINCE\") | select(.user.login == \"$ME\") | select(.body|test(\"^bugbot run\";\"i\")|not) | \"   my-comment \(.created_at) \(.body|gsub(\"\n\";\" \")|.[0:160])\"" 2>/dev/null | head -4
  gh api "repos/$repo/issues/$num/comments?per_page=100" \
    --jq "[.[] | select(.created_at >= \"$SINCE\") | select(.user.login == \"$ME\") | select(.body|test(\"^bugbot run\";\"i\"))] | if length>0 then \"   bugbot-runs \(length)\" else empty end" 2>/dev/null
  gh api "repos/$repo/pulls/$num/reviews?per_page=100" \
    --jq ".[] | select(.submitted_at >= \"$SINCE\") | \"   review \(.submitted_at) \(.user.login) \(.state)\"" 2>/dev/null | sort -u -k4 | head -6
done < "${TMPDIR:-/tmp}/todays-report-prs.$$"
rm -f "${TMPDIR:-/tmp}/todays-report-prs.$$"

echo
echo "== OTHERS' PRS I reviewed or commented on"
{
  gh search prs --reviewed-by=@me --updated=">=$SINCE" --limit 50 --json repository,number,title,author,state \
    --jq ".[] | select(.author.login != \"$ME\") | \"\(.repository.nameWithOwner)#\(.number) by \(.author.login) \(.state) | \(.title)\"" 2>/dev/null
  gh search prs --commenter=@me --updated=">=$SINCE" --limit 50 --json repository,number,title,author,state \
    --jq ".[] | select(.author.login != \"$ME\") | \"\(.repository.nameWithOwner)#\(.number) by \(.author.login) \(.state) | \(.title)\"" 2>/dev/null
} | sort -u
echo "   (a review only counts if I submitted it in the window; check with: gh api repos/<repo>/pulls/<n>/reviews)"

echo
echo "== LOCAL COMMITS in ~/Work repos and worktrees (author devashish, deduped by hash)"
for d in "$HOME"/Work/*/ "$HOME"/Work/*/.claude/worktrees/*/; do
  [ -e "$d/.git" ] || continue
  TZ=Asia/Kolkata git -C "$d" log --all --author=devashish --since="$SINCE" --format="%h|%cd|%s" --date=format:'%H:%M' 2>/dev/null \
    | awk -F'|' -v n="$(basename "$d")" '{print $1"\t"$2" IST\t"n"\t"$3}'
done | sort -u -k1,1 | sort -t$'\t' -k2 | grep -v $'\tMerge ' | head -80
