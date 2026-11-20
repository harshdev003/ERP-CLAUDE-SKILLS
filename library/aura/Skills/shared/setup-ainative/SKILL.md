---
name: setup-ainative
description: >
  Set up a contributor's local environment for working with the AuraHealth/AINative plugin
  repo. Handles cloning the repo, installing the gh CLI, authenticating with GitHub,
  configuring MCP servers (Slack, Linear, Notion, GitHub), and walking the user through adding
  the API tokens AINative skills depend on — Anthropic, Aura, Braintrust, Bugsnag, Cartesia,
  CircleCI, Deepgram, GCP, Gemini, Linear, Mixpanel, Notion, OpenAI, Postman and Slack — under
  the `env` block of ~/.claude/settings.json. Use when someone says "set up my environment",
  "I want to contribute to AINative", "clone the AINative repo", "set up MCP servers",
  "configure my dev environment", "I'm new and want to get started", "help me set up GitHub",
  "authenticate my tools", "configure my API keys", or "add the AINative tokens to my
  settings". Also use when another skill (like create-aura-skill or update-aura-skill) fails
  due to missing prerequisites — run this skill first, then retry.

metadata:
  version: "3.2.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
---

# Setup AINative Environment

Prepare a contributor's machine to work with the AuraHealth/AINative plugin repo.
This skill checks for prerequisites, clones the repo (or pulls latest if already cloned),
authenticates GitHub, and walks the user through connecting MCP servers.

## Before You Start

Tell the user what this skill will do in plain language:

> I'll get your environment ready to contribute to AINative. This involves:
> 1. Setting up a shared Python environment the skill scripts depend on
> 2. Making sure the GitHub CLI is installed
> 3. Authenticating you with GitHub
> 4. Cloning the AINative repo locally (or updating it if you already have it)
> 5. Helping you connect MCP servers (Slack, Linear, Notion) so skills can access your team's tools
> 6. Configuring the API tokens that AINative skills depend on (Anthropic, Aura, Braintrust, Bugsnag, GCP, Gemini, GitHub, Linear, Notion, OpenAI, Slack)

Then proceed through each phase.

**Safe to re-run (idempotent).** Every phase checks the current state before doing
anything and only fills what's missing — so a contributor can run this skill again
anytime (to add a token they skipped, repair a half-finished setup, or pull the latest
repo) without duplicating work or clobbering existing config. When a phase's check shows
it's already done, say so and move on rather than redoing it; never overwrite a value
the user already has.

---

## Phase 1: Python environment for skill scripts

Most AINative skills run a bundled Python script through a bare `python3` and import
third-party packages — `requests` (every Linear/data/eng script), plus `openai`,
`bugsnag`, and `google-*` for specific skills. On macOS the Homebrew/system `python3`
is externally-managed (PEP 668), so those imports fail and the skill exits with
`error: ... requires 'requests'`. This phase creates **one** shared, persistent
virtualenv at `~/.claude/python-env` and points the bare `python3` at it — set up once,
not per session — so every skill script has its dependencies.

### 1a: Ensure uv is available

`uv` builds and manages the venv (it's fast and can fetch a suitable Python if none is
installed):

```bash
which uv && uv --version || echo "NEEDS_UV"
```

If `NEEDS_UV`, install it — **macOS**: `brew install uv`; otherwise follow
<https://docs.astral.sh/uv/getting-started/installation/>. Re-check with `uv --version`.

### 1b: Create the shared venv and install skill dependencies

Idempotent — re-running it just tops up packages on the existing venv:

```bash
VENV="$HOME/.claude/python-env"
[ -x "$VENV/bin/python" ] || uv venv --seed "$VENV"
uv pip install --python "$VENV/bin/python" \
  requests openai bugsnag google-cloud-logging google-cloud-bigquery google-auth google-genai
uv pip freeze --python "$VENV/bin/python" > "$VENV/requirements.txt"
echo "✓ shared skill venv ready at $VENV"
```

### 1c: Put the venv on PATH via a SessionStart hook

The bundled scripts call a bare `python3`, so a `SessionStart` hook prepends the venv's
`bin/` to `PATH` at the start of every session. This is purely additive — it only
changes what the bare `python3` resolves to; project virtualenvs
(`<repo>/.venv/bin/python`) and explicit interpreter paths are unaffected.

Check whether the hook is already present:

```bash
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.claude/settings.json")
d = json.load(open(p)) if os.path.exists(p) else {}
hooks = d.get("hooks", {}).get("SessionStart", [])
print("HOOK_PRESENT" if "python-env/bin" in json.dumps(hooks) else "HOOK_MISSING")
PY
```

If `HOOK_MISSING`, **delegate to the `update-config` skill** to add the hook below —
never hand-edit `~/.claude/settings.json` from inside this skill:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash",
            "args": ["-c", "[ -n \"$CLAUDE_ENV_FILE\" ] && echo 'export PATH=$HOME/.claude/python-env/bin:$PATH' >> \"$CLAUDE_ENV_FILE\""],
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

The hook takes effect on the **next** session start, so the bare `python3` won't resolve
to the venv until Claude Code restarts — fold this into the "restart Claude Code"
reminder at the end of setup (Phases 6–7).

### 1d: Verify

```bash
"$HOME/.claude/python-env/bin/python" -c "import requests, openai, bugsnag, google.cloud.bigquery, google.cloud.logging; print('✓ skill deps importable')"
```

If a skill ever needs a package that isn't here, add it to this same venv
(`uv pip install --python ~/.claude/python-env/bin/python <pkg>`, then re-freeze) — don't
create a second venv.

### 1e: Scripts run outside a session use the venv by absolute path

The hook only fixes `python3` inside Claude Code. A wrapper started by launchd, cron or a
shell script has its own `PATH`, where a bare `python3` is usually Homebrew's (3.14 on
current macOS), with none of these packages and PEP 668 blocking `pip install --user`.
So anything that runs a skill script outside a session calls the venv's interpreter by
absolute path:

```bash
"$HOME/.claude/python-env/bin/python3" "$SKILL_DIR/scripts/detect.py" …
```

Not `/usr/local/bin/python3` or another interpreter that happens to have the packages:
the skills are tested against this venv. If the venv is missing (a new machine), re-run
1b. A skill script started under the wrong interpreter stops with one line naming it and
this venv.

---

## Phase 2: Check and Install gh CLI

Check if the `gh` CLI is available:

```bash
which gh && gh --version 2>&1
```

**If gh is installed**, move to Phase 3.

**If gh is NOT installed**, determine the user's OS and guide installation:

```bash
uname -s
```

- **macOS**: `brew install gh` (if Homebrew is available) or direct download
- **Linux**: Follow https://cli.github.com for distro-specific instructions
- **Windows/WSL**: `sudo apt install gh` or `winget install GitHub.cli`

After installation, verify:

```bash
gh --version
```

If the user doesn't have Homebrew or a package manager, walk them through installing
from the GitHub CLI releases page: https://github.com/cli/cli/releases

---

## Phase 3: Authenticate GitHub

### 3a: Pre-flight — does the user have a GitHub account?

Before running any `gh` command, ask the user:

> Do you already have a GitHub account tied to your `aurahealth.io` email?

**If no**: Send them to create one before proceeding:

> Create a GitHub account at <https://github.com/join>. We recommend signing up with your `aurahealth.io` email — it makes org access mapping cleaner. Let me know once the account exists; I'll wait.

Pause until they confirm the account is created, then continue.

**If yes**, continue.

### 3b: Authenticate with `gh`

Check current auth status:

```bash
gh auth status 2>&1
```

**If already authenticated**, skip to Phase 3c.

**If not authenticated**, tell the user to run the following in their terminal and follow the prompts:

```bash
gh auth login
```

Walk them through what to expect:
1. **What account?** → Select **GitHub.com**
2. **Preferred protocol?** → Select **HTTPS**
3. **Authenticate Git with GitHub credentials?** → **Yes**
4. **How to authenticate?** → Select **Login with a web browser**
5. Copy the one-time code shown, press Enter — a browser window will open
6. Paste the code at https://github.com/login/device and click **Authorize**

Once they confirm it's done, verify the auth:

```bash
gh auth status 2>&1
```

### 3c: Configure git commit identity

Git needs `user.name` and `user.email` set so commits are attributed correctly. Check what's currently configured:

```bash
echo "name:  $(git config --global user.name)"
echo "email: $(git config --global user.email)"
```

**If either is empty**, set them from the authenticated GitHub account (mirrors what `parallel-agents` does per-worktree):

```bash
GH_LOGIN=$(gh api user --jq .login)
GH_EMAIL=$(gh api user --jq .email)

# User keeps their email private → fall back to GitHub's noreply form
if [ -z "$GH_EMAIL" ] || [ "$GH_EMAIL" = "null" ]; then
  GH_EMAIL="${GH_LOGIN}@users.noreply.github.com"
fi

[ -z "$(git config --global user.name)" ]  && git config --global user.name  "$GH_LOGIN"
[ -z "$(git config --global user.email)" ] && git config --global user.email "$GH_EMAIL"

echo "git user: $(git config --global user.name) <$(git config --global user.email)>"
```

Confirm with the user that the resulting name/email looks right. If they'd rather commit under their `aurahealth.io` address, have them re-run `git config --global user.email "<their-aura-email>"` themselves.

### 3d: Verify access to the AINative repo

```bash
gh repo view AuraHealth/AINative --json name,description 2>&1
```

**Troubleshooting** — if verification fails:

- **"Could not resolve to a Repository"** / **"Not Found"** / **"Resource not accessible by integration"** — the user likely isn't a member of the AuraHealth GitHub org yet. Tell them:

  > Ping `#ai-eng` on Slack and tag Jerome or Rohan — ask to be invited to the **AuraHealth** GitHub org. Accept the invite from the email GitHub sends (or at <https://github.com/orgs/AuraHealth/invitation>). Once you're in, re-run `gh repo view AuraHealth/AINative` and let me know when it succeeds.

  Pause until repo access is confirmed.

- **"SSO authorization required"** — the user IS in the org but the token isn't authorized. Go to <https://github.com/settings/tokens>, find the token `gh` created, click **Configure SSO** → **Authorize** for AuraHealth.

- **"Bad credentials"** — run `gh auth logout` then `gh auth login` again to get a fresh token.

Do not proceed to Phase 4 until `gh repo view AuraHealth/AINative` succeeds.

---

## Phase 4: Clone or Update the Repo

First, search the current working directory tree for an existing AINative clone before asking anything. If one is found, skip the location prompt entirely.

If no clone is found, ask the user where they'd like the repo to live:

- **Default location** (`~/AuraHealth/AINative`) — simplest option
- **Custom location** — let them specify a path

Then check if the repo already exists at the user-provided location:

```bash
# Search for any AINative repo clone under the current working directory
FOUND_DIR=$(find "$(pwd)" -maxdepth 5 -name ".git" -type d 2>/dev/null \
  | while read gitdir; do
      repo_root=$(dirname "$gitdir")
      remote=$(git -C "$repo_root" remote get-url origin 2>/dev/null)
      echo "$remote" | grep -qi "AINative" && echo "$repo_root" && break
    done | head -1)

REPO_DIR="${FOUND_DIR:-${REPO_DIR:-$HOME/AuraHealth/AINative}}"

if [ -d "$REPO_DIR/.git" ]; then
  echo "REPO_EXISTS at $REPO_DIR"
  git -C "$REPO_DIR" remote get-url origin 2>&1
else
  echo "NEEDS_CLONE"
fi
```

**If repo exists**, pull latest:
```bash
cd "$REPO_DIR"
git checkout main 2>/dev/null || git checkout master
git pull origin main 2>/dev/null || git pull origin master
echo "✓ Repo updated at $REPO_DIR"
```

**If repo does not exist**, clone it:
```bash
gh repo clone AuraHealth/AINative "$REPO_DIR"
echo "✓ Repo cloned to $REPO_DIR"
```

Verify the clone by listing plugins:
```bash
find "$REPO_DIR" -maxdepth 2 -name "plugin.json" -path "*/.claude-plugin/*" \
  | while read f; do
      dir=$(dirname $(dirname "$f"))
      name=$(basename "$dir")
      desc=$(python3 -c "import json; print(json.load(open('$f')).get('description',''))" 2>/dev/null)
      echo "  - $name: $desc"
    done
```

Tell the user which plugins are available in the repo.

---

## Phase 5: Configure MCP Servers

Explain to the user that MCP servers let Claude connect to external tools like Slack,
Linear, and Notion. These connections are needed for many AINative skills to function.

Check which MCP servers are already connected by looking at available tools. Then walk
the user through connecting any that are missing.

### 5a: Slack

Check if Slack MCP tools are available. If Slack tools exist in the current session,
tell the user Slack is already connected.

If Slack is NOT connected:
- Use the `suggest_connectors` MCP tool (if available) to suggest the Slack connector
- Or guide the user: "You can connect Slack through Claude's MCP settings. Look for the Slack connector in the plugin marketplace."

### 5b: Linear

Check if Linear MCP tools are available. If Linear tools exist, confirm it's connected.

If Linear is NOT connected:
- Use `suggest_connectors` to suggest the Linear connector
- Or guide manually: "Connect Linear through Claude's MCP connector settings."

### 5c: Notion

Check if Notion MCP tools are available. If Notion tools exist, confirm it's connected.

If Notion is NOT connected:
- Use `suggest_connectors` to suggest the Notion connector
- Or guide manually: "Connect Notion through Claude's MCP connector settings."

### 5d: GitHub

GitHub access is already confirmed via the gh CLI in Phase 3. No additional MCP server
is needed — skills use the gh CLI directly for GitHub operations.

---

## Phase 6: Configure API Keys and Tokens

Walk the contributor through the keys they actually need — most skills run without
most tokens (see `/CLAUDE.md` → "Tokens are soft requirements").

Per-token setup steps, issuer, and degrade-without behaviour:
[`references/tokens.md`](references/tokens.md).

## Phase 7: Verify Everything

Run a final check to confirm the environment is ready:

```bash
echo "=== Environment Check ==="
echo ""

# gh CLI
echo -n "gh CLI: "
which gh > /dev/null 2>&1 && echo "✓ installed" || echo "✗ missing"

# GitHub auth
echo -n "GitHub auth: "
gh auth status > /dev/null 2>&1 && echo "✓ authenticated" || echo "✗ not authenticated"

# Repo
REPO_DIR="${REPO_DIR:-$HOME/AuraHealth/AINative}"
echo -n "AINative repo: "
[ -d "$REPO_DIR/.git" ] && echo "✓ cloned at $REPO_DIR" || echo "✗ not found"

# Repo access
echo -n "Repo access: "
gh repo view AuraHealth/AINative --json name > /dev/null 2>&1 && echo "✓ confirmed" || echo "✗ denied"

# Shared Python env for skill scripts
echo -n "Shared Python env: "
"$HOME/.claude/python-env/bin/python" -c "import requests" > /dev/null 2>&1 && echo "✓ ready at ~/.claude/python-env" || echo "✗ missing — re-run Phase 1"

# Env vars in settings.json
echo ""
echo "API tokens in ~/.claude/settings.json:"
SETTINGS="$HOME/.claude/settings.json"
EXPECTED='ANTHROPIC_API_KEY AURA_API_TOKEN AURA_USER_TOKEN BRAINTRUST_API_KEY BUGSNAG_TOKEN CARTESIA_ADMIN_API_KEY CIRCLE_TOKEN DEEPGRAM_OWNER_API_KEY ELEVENLABS_ADMIN_API_KEY ELEVENLABS_API_KEY GCP_KEY GEMINI_API_KEY INWORLD_API_KEY IPGEOLOCATION_API_KEY LINEAR_API_KEY MIXPANEL_SERVICE_ACCOUNT_USERNAME MIXPANEL_SERVICE_ACCOUNT_SECRET MIXPANEL_PROJECT_ID NOTION_TOKEN OPENAI_ADMIN_API_KEY OPENAI_CLAUDE_API_KEY OPENAI_EVALUATIONS_API_KEY OPENAI_CONVERSATION_API_KEY OPENAI_INSIGHTS_API_KEY OPENAI_JOURNAL_API_KEY OPENAI_MEMORY_API_KEY POSTMAN_API_KEY SLACK_BOT_TOKEN SLACK_USER_TOKEN'
for key in $EXPECTED; do
  if [ -f "$SETTINGS" ] && python3 -c "import json,sys; d=json.load(open('$SETTINGS')); sys.exit(0 if d.get('env',{}).get('$key') else 1)" 2>/dev/null; then
    echo "  ✓ $key"
  else
    echo "  ✗ $key (missing)"
  fi
done

echo ""
echo "=== Done ==="
```

Present the results to the user. If anything failed, offer to fix it. If any tokens
were just added, remind them to **restart Claude Code** so the new env values load.

If everything passes, tell the user:

> Your environment is ready! You can now:
> - **Create a new skill**: Just say "create a new skill for [plugin name]"
> - **Update an existing skill**: Just say "update the [skill-name] skill"
> - **Browse existing skills**: Ask me to list skills in a plugin

---

## Edge Cases

**User is behind a corporate proxy or VPN:**
If `gh` commands fail with network errors, suggest checking proxy settings or VPN configuration.
This is outside the scope of this skill but worth flagging.

**User already has the repo cloned in a non-standard location:**
If the user says they already have it cloned somewhere, ask for the path and verify it
instead of cloning again. Update `REPO_DIR` accordingly.

**User doesn't have admin access to install software:**
If they can't install `gh` via package manager, suggest downloading the binary directly
from GitHub releases or asking their IT team.

**MCP servers require org-level approval:**
Some MCP connectors may need admin approval in the user's Claude organization. If connector
setup fails, suggest they contact their org admin.
