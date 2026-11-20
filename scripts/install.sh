#!/usr/bin/env bash
# One-time setup on a machine: adds the SessionStart sync hook to
# ~/.claude/settings.json (merging, not overwriting) and runs a first sync.
set -e
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"

node - "$SETTINGS" "$REPO/hooks/settings.hooks.json" <<'EOF'
const fs = require('fs');
const [settingsPath, fragPath] = process.argv.slice(2);
const s = fs.existsSync(settingsPath) ? JSON.parse(fs.readFileSync(settingsPath, 'utf8')) : {};
const frag = JSON.parse(fs.readFileSync(fragPath, 'utf8'));
s.hooks = s.hooks || {};
for (const [event, groups] of Object.entries(frag.hooks)) {
  s.hooks[event] = s.hooks[event] || [];
  for (const g of groups) {
    const cmd = g.hooks[0].command;
    const exists = s.hooks[event].some(x => (x.hooks || []).some(h => h.command === cmd));
    if (!exists) s.hooks[event].push(g);
  }
}
fs.writeFileSync(settingsPath, JSON.stringify(s, null, 2) + '\n');
console.log('Hook installed in ' + settingsPath);
EOF

bash "$REPO/scripts/sync.sh"
