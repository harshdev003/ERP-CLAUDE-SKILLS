#!/usr/bin/env bash
# Commit and push changes in this repo to GitHub, then reinstall locally.
# Usage:
#   bash ~/erp-claude-skills/scripts/publish.sh "commit message" [skill-name ...]
# Any skill names given are first imported from ~/.claude/skills/<name>
# (useful when a skill was created there instead of in the repo).
set -e

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# Only ever commit/push as the harshdev003 account.
git config user.name harshdev003
git config user.email 358348782+harshdev003@users.noreply.github.com
git config credential.https://github.com.username harshdev003

msg="${1:-Update skills and rules}"
shift || true

for name in "$@"; do
  src="$HOME/.claude/skills/$name"
  if [ -f "$src/SKILL.md" ]; then
    rm -rf "skills/$name"
    cp -r "$src" "skills/$name"
    echo "Imported $name from ~/.claude/skills"
  else
    echo "Skip $name: no $src/SKILL.md" >&2
  fi
done

git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit."
else
  git commit -q -m "$msg"
  echo "Committed: $msg"
fi

git pull --rebase --quiet
git push -u origin main
bash "$REPO/scripts/sync.sh"
