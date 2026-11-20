#!/usr/bin/env bash
# Pull the latest skills & rules from GitHub and install them into ~/.claude.
# Runs automatically at every Claude Code session start (SessionStart hook).
# Safe to run by hand:  bash ~/ERP-CLAUDE-SKILLS/scripts/sync.sh

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CLAUDE_DIR="$HOME/.claude"
mkdir -p "$CLAUDE_DIR/skills"

cd "$REPO" || exit 0

# 1. Pull (never prompt, never hang the session)
if GIT_TERMINAL_PROMPT=0 GCM_INTERACTIVE=never timeout 20 git pull --ff-only --quiet >/dev/null 2>&1; then
  status="pulled latest from GitHub"
else
  status="WARNING: could not pull from GitHub (offline, auth, or local changes) - using local copy"
fi

# 2. Install skills: every folder in skills/ that has a SKILL.md
installed=()
for dir in "$REPO"/skills/*/; do
  [ -f "$dir/SKILL.md" ] || continue
  name="$(basename "$dir")"
  rm -rf "$CLAUDE_DIR/skills/$name"
  cp -r "$dir" "$CLAUDE_DIR/skills/$name"
  installed+=("$name")
done

# 3. Install global rules
if [ -f "$REPO/rules/CLAUDE.md" ]; then
  cp "$REPO/rules/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md"
fi

echo "[ERP-CLAUDE-SKILLS sync] $status. Skills installed: ${installed[*]:-none}. Rules: rules/CLAUDE.md -> ~/.claude/CLAUDE.md"
exit 0
