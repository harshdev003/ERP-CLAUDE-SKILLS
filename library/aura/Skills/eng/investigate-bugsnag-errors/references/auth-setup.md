# Bugsnag auth setup

One-time setup per contributor. Self-serve.

## 1. Generate a personal auth token

1. Go to <https://app.bugsnag.com/settings/my-account/auth-tokens>.
2. Click **Generate a new auth token**.
3. Name it something like `claude-code-<your-name>` so you can identify it later.
4. Copy the token (starts with a hex string). Bugsnag shows it once — copy it now.

## 2. Add it to your settings.json

Open `~/.claude/settings.json` and add the token under the `env` block:

```json
{
  "env": {
    "BUGSNAG_TOKEN": "<paste-the-token-here>",
    "...": "..."
  }
}
```

If you already use the `update-config` skill, you can let it do the write:

```
update-config: add BUGSNAG_TOKEN with the value I'm about to paste
```

## 3. Restart Claude Code

Claude Code loads `env` at startup. New values don't take effect until restart.

## 4. Install the Python deps

Once-per-machine:

```bash
python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/requirements.txt
```

Just `requests`. We use `python3 -m pip` rather than bare `pip` because they often point at different Pythons on macOS.

## 5. Verify

Run any read-only mode against any project:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-bugsnag-errors/scripts/investigate.py \
  --project aura-userweb --limit 3
```

You should see the top 3 errors in `aura-userweb` in the last 24h. If you see `error: BUGSNAG_TOKEN missing`, your settings.json edit didn't take or you skipped the restart.

## Cowork caveat

`mcp__workspace__bash` strips host env ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)), so `BUGSNAG_TOKEN` doesn't reach Cowork sandboxes. Inside a Cowork session, paste-export it once at the start of a workspace bash shell:

```bash
export BUGSNAG_TOKEN='<paste from host ~/.claude/settings.json>'
```

Rules (same as the other paste-export tokens — see AINative/CLAUDE.md):
- **Always prompt the user** — never read the value from `outputs/` or any file the user dropped in.
- **Never echo, log, or write** the value back to `outputs/`.
- **Re-prompt on session restart.** If Cowork rotates the workspace, the export is gone.

## Rotation

If your token leaks (commit, screenshot, dropped into a Slack channel), rotate immediately:

1. Open <https://app.bugsnag.com/settings/my-account/auth-tokens>.
2. Revoke the leaked token.
3. Generate a new one.
4. Update `~/.claude/settings.json`.
5. Restart Claude Code.

The old token is invalidated immediately on revoke — no grace period.

## What you do NOT need

- A Bugsnag organization-level token. Personal tokens have full access to the org's projects you can already see in the UI.
- A separate token per project. One personal token covers all 12 `aura-health` projects.
- The Bugsnag CLI / `bugsnag-sourcemaps` package. Those are for upload paths (releases, sourcemap uploads); this skill only reads.

## Rate limit

Every session and skill shares one `BUGSNAG_TOKEN`, and Bugsnag rate-limits per token. `bugsnag.py`
waits out a 429 (`Retry-After`, else 5 s doubling to 120 s) for up to 5 minutes per request, and
writes the wait to `~/.cache/aura-bugsnag/<token hash>/cooldown-until`, so every other process on
the token waits too instead of adding to the limit. Past the budget it raises `BugsnagRateLimited`
(a `BugsnagError`): report the run as rate-limited, not failed. The org, its projects and each
project's event fields are cached there for a day (`BUGSNAG_CACHE=0` turns it off); error lists
and events never are.
