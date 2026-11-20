# Keeping the Claude Code CLI plugins current automatically

The reload flow in `SKILL.md` is manual. This is the optional automation that stops the Claude
Code install cache drifting in the first place.

`autoUpdate: true` in `known_marketplaces.json` is **not** sufficient on its own — it advances the
marketplace clone and refreshes same-version plugins, but leaves a version increase behind (see
`SKILL.md` Step 2). The sweep below runs the verb that actually moves a version:
`claude plugin update`.

Claude Desktop needs no equivalent — it re-snapshots from the marketplace each session (see the
Hosts section in `SKILL.md`).

## The script

`~/.claude/scripts/update-aura-plugins.sh`, throttled so many sessions don't each hit the network:

```bash
#!/bin/bash
set -u
STAMP="$HOME/.claude/.aura-plugins-last-update"
LOG="$HOME/.claude/logs/aura-plugin-update.log"
mkdir -p "$(dirname "$LOG")"

# At most one refresh per hour, however many sessions start.
if [ -f "$STAMP" ]; then
  last=$(cat "$STAMP" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ $(( $(date +%s) - last )) -lt 3600 ] && exit 0
fi

# The hook's PATH is not the login shell's — resolve the CLI explicitly.
CLAUDE_BIN=$(command -v claude 2>/dev/null || true)
[ -z "$CLAUDE_BIN" ] && [ -x /opt/homebrew/bin/claude ] && CLAUDE_BIN=/opt/homebrew/bin/claude
[ -z "$CLAUDE_BIN" ] && exit 0

date +%s > "$STAMP"   # stamp up front: a hung call must not make every later session retry
{
  echo "=== $(date -u +%FT%TZ) ==="
  "$CLAUDE_BIN" plugin marketplace update aura-tools 2>&1 | tail -1
  for p in shared eng product data marketing qa; do
    printf '%-10s ' "$p"; "$CLAUDE_BIN" plugin update "$p@aura-tools" 2>&1 | tail -1
  done
} >> "$LOG" 2>&1
exit 0
```

Two details that matter:

- **Stamp before the work, not after.** A hung network call would otherwise leave the stamp unset
  and make every subsequent session retry the same hang.
- **Always `exit 0`.** A plugin refresh must never break a session start.

## Wiring it up

Append to the existing `SessionStart` array in `~/.claude/settings.json` — do not replace it, other
hooks commonly live there:

```json
{
  "type": "command",
  "command": "/bin/bash",
  "args": ["/Users/<you>/.claude/scripts/update-aura-plugins.sh"],
  "async": true,
  "timeout": 300
}
```

`async: true` is what keeps it off the startup path.

Verify the merge didn't clobber anything:

```bash
jq -r '.hooks.SessionStart[0].hooks[] | "\(.command) \(.args|join(" ")) async=\(.async // false)"' \
  ~/.claude/settings.json
```

## What this guarantees, and what it doesn't

`claude plugin update` prints *"Restart to apply changes"* — the refresh lands for the **next**
session. So the guarantee is *never more than one session behind `main`*, **not** *current within
the running session*.

To pick up a merge immediately, run it by hand. Clear the stamp first or it no-ops:

```bash
rm -f ~/.claude/.aura-plugins-last-update && ~/.claude/scripts/update-aura-plugins.sh
```

## Checking it is working

The log is the record:

```bash
tail -20 ~/.claude/logs/aura-plugin-update.log
```

Each run prints one line per plugin — either `already at the latest version (x.y.z)` or
`updated from <old> to <new>`. If the log is empty or stale by more than an hour of active use,
the hook isn't firing: confirm the script is executable (`chmod +x`) and that the `jq` check above
lists it.
