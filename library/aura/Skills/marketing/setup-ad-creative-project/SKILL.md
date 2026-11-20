---
name: setup-ad-creative-project
description: >
  Set up a team member's local environment for working with the Ad Creative Claude Project.
  Handles cloning AuraHealth/AIAdCreatives, installing Python dependencies, and configuring
  API keys (OpenAI and Gemini). Use when someone says "set up ad creative", "I want
  to work on ad creatives", "clone the ad creative project", "set up my marketing environment",
  "help me get started with ads", or "I need access to the ad creative project".
metadata:
  version: "3.0.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI                   # OpenAI project keys + stored-completions access
    - Tools/GitHub                   # gh CLI auth + PR conventions
    - Tools/Gemini                   # Gemini API access (GEMINI_API_KEY)
---

# Setup Ad Creative Project

Get a team member's machine ready to work on the Ad Creative project.
This skill clones the repo, sets up Python dependencies, configures API keys,
and opens the project in Claude Code.

## Before You Start

Tell the user what this skill will do:

> I'll get you set up to work on the Ad Creative project. This involves:
> 1. Checking that the GitHub CLI is installed and authenticated
> 2. Cloning (or updating) the AuraHealth/AIAdCreatives repo
> 3. Installing Python dependencies (Pillow, etc.)
> 4. Setting up your API keys (OpenAI + Gemini)
> 5. Opening the project in Claude Code

Then proceed through each phase.

---

## Phase 1: Check and Install gh CLI

Check if `gh` is available:

```bash
which gh && gh --version 2>&1
```

**If installed**, move to Phase 2.

**If NOT installed**, determine OS and guide installation:

```bash
uname -s
```

- **macOS**: Install via Homebrew (see below)
- **Linux**: https://cli.github.com for distro-specific instructions
- **Windows/WSL**: `sudo apt install gh` or `winget install GitHub.cli`

**macOS — check for Homebrew first:**

```bash
which brew 2>/dev/null && brew --version || echo "HOMEBREW_MISSING"
```

- **If Homebrew is present**: run `brew install gh`
- **If Homebrew is missing**: install it first (no external terminal needed — run directly here):

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

After Homebrew finishes, install `gh`:

```bash
brew install gh
```

Verify after installation:

```bash
gh --version
```

---

## Phase 2: Authenticate GitHub

Check current auth status:

```bash
gh auth status 2>&1
```

**If already authenticated** with access to AuraHealth org, skip to Phase 3.

**If not authenticated**, run the following directly in this session (no external terminal needed):

```bash
gh auth login --hostname github.com --git-protocol https --web
```

This prints a one-time code and opens your browser automatically. In the browser:
1. Paste the one-time code shown in the output
2. Click **Authorize GitHub CLI**
3. Return here — authentication completes automatically

Once confirmed, verify:

```bash
gh auth status 2>&1
gh repo view AuraHealth/AIAdCreatives --json name,description 2>&1
```

**Troubleshooting**:
- **"SSO authorization required"**: Go to https://github.com/settings/tokens, find the `gh` token, click **Configure SSO** → **Authorize** for AuraHealth.
- **"Bad credentials"**: Run `gh auth logout` then `gh auth login` again.

Do not proceed until GitHub auth is confirmed.

---

## Phase 3: Clone or Update the Repo

Search for an existing AIAdCreatives clone before asking anything:

```bash
FOUND_DIR=$(find "$HOME" -maxdepth 5 -name ".git" -type d 2>/dev/null \
  | while read gitdir; do
      repo_root=$(dirname "$gitdir")
      remote=$(git -C "$repo_root" remote get-url origin 2>/dev/null)
      echo "$remote" | grep -qi "AIAdCreatives" && echo "$repo_root" && break
    done | head -1)

REPO_DIR="${FOUND_DIR:-$HOME/AuraHealth/AIAdCreatives}"

if [ -d "$REPO_DIR/.git" ]; then
  echo "REPO_EXISTS at $REPO_DIR"
else
  echo "NEEDS_CLONE to $REPO_DIR"
fi
```

**If repo exists**, pull latest:

```bash
git -C "$REPO_DIR" checkout main 2>/dev/null || git -C "$REPO_DIR" checkout master
git -C "$REPO_DIR" pull origin main 2>/dev/null || git -C "$REPO_DIR" pull origin master
echo "✓ Repo updated at $REPO_DIR"
```

**If repo does not exist**, clone it:

```bash
gh repo clone AuraHealth/AIAdCreatives "$REPO_DIR"
echo "✓ Repo cloned to $REPO_DIR"
```

Tell the user the project is at `$REPO_DIR`.

---

## Phase 4: Install Python Dependencies

Check Python is available:

```bash
python3 --version 2>&1
```

If Python is missing, install it via Homebrew (set up in Phase 1):

```bash
brew install python
```

Install dependencies:

```bash
pip3 install Pillow requests python-dotenv 2>&1
```

Verify key imports:

```bash
python3 -c "from PIL import Image; import requests; import dotenv; print('✓ Dependencies ready')" 2>&1
```

If any import fails, re-run the pip install or flag the specific missing package.

---

## Phase 5: Configure API Keys

Check what's already set in `.env`:

```bash
if [ -f "$REPO_DIR/.env" ]; then
  echo "ENV_EXISTS"
  grep -q "OPENAI_API_KEY=." "$REPO_DIR/.env" && echo "OPENAI_KEY_SET" || echo "OPENAI_KEY_MISSING"
  grep -q "GEMINI_API_KEY=." "$REPO_DIR/.env" && echo "GEMINI_KEY_SET" || echo "GEMINI_KEY_MISSING"
else
  echo "ENV_MISSING"
fi
```

**If `.env` is missing**, copy the sample file first:

```bash
cp "$REPO_DIR/.env.sample" "$REPO_DIR/.env"
echo "✓ Created .env from .env.sample"
```

### 5a: OpenAI API Key

**If `OPENAI_API_KEY` is already set**, confirm and skip.

**If missing**, tell the user:

> You need an OpenAI API key — it's the default provider for image generation in `generate-ads.py`.
> Get one at https://platform.openai.com/api-keys, then paste it here and I'll add it to your `.env`.

After the user provides the key:

```bash
sed -i '' "s/OPENAI_API_KEY=.*/OPENAI_API_KEY=THEIR_KEY/" "$REPO_DIR/.env"
```

### 5c: Gemini API Key

**If `GEMINI_API_KEY` is already set**, confirm and skip.

**If missing**, tell the user:

> You need a Gemini API key to generate ad images. Get one at https://aistudio.google.com/apikey.
> Once you have it, paste it here and I'll add it to your `.env`.

After the user provides the key:

```bash
sed -i '' "s/GEMINI_API_KEY=.*/GEMINI_API_KEY=THEIR_KEY/" "$REPO_DIR/.env"
```

Verify all keys are set (without exposing values):

```bash
echo -n "OpenAI API key: "
grep -q "OPENAI_API_KEY=." "$REPO_DIR/.env" && echo "✓ set" || echo "✗ missing"

echo -n "Gemini key: "
grep -q "GEMINI_API_KEY=." "$REPO_DIR/.env" && echo "✓ set" || echo "✗ missing"
```

---

## Phase 6: Open the Project in Claude Desktop (Code mode)

Tell the user the exact path of their project folder (the value of `$REPO_DIR` from Phase 3), then guide them to start a new session:

> **You're ready to start! Here's how to open the Ad Creative project:**
>
> 1. Open the **Claude** desktop app
> 2. Switch to **Code** mode (the `</>` icon in the sidebar or top navigation)
> 3. Click **New Session** (or press **⌘N**)
> 4. When prompted for a working directory, navigate to:
>    `<REPO_DIR value from Phase 3>`
>    and select the **AIAdCreatives** folder
>
> Claude Code will load the project's `CLAUDE.md` automatically, giving you access to all
> brand guidelines, workflows, and skills right away.

---

## Phase 7: Verify Everything

Run a final environment check:

```bash
echo "=== Ad Creative Project Setup ==="
echo ""

echo -n "gh CLI: "
which gh > /dev/null 2>&1 && echo "✓ installed" || echo "✗ missing"

echo -n "GitHub auth: "
gh auth status > /dev/null 2>&1 && echo "✓ authenticated" || echo "✗ not authenticated"

echo -n "Repo cloned: "
[ -d "$REPO_DIR/.git" ] && echo "✓ at $REPO_DIR" || echo "✗ not found"

echo -n "Python deps: "
python3 -c "from PIL import Image; import requests; import dotenv" > /dev/null 2>&1 && echo "✓ ready" || echo "✗ missing"

echo -n "OpenAI API key: "
[ -f "$REPO_DIR/.env" ] && grep -q "OPENAI_API_KEY=." "$REPO_DIR/.env" && echo "✓ configured" || echo "✗ missing"

echo -n "Gemini API key: "
[ -f "$REPO_DIR/.env" ] && grep -q "GEMINI_API_KEY=." "$REPO_DIR/.env" && echo "✓ configured" || echo "✗ missing"

echo ""
echo "=== Done ==="
```

Present results to the user. Fix anything that failed before wrapping up.

If everything passes, tell the user:

> You're all set! Open the Claude desktop app, switch to **Code** mode (`</>`), start a new session (⌘N), and open the project folder at `<REPO_DIR>`.
> Once inside the project, you can:
> - **Generate ads**: "Generate briefs for the anxiety vertical"
> - **Review creatives**: "Review the latest batch of creatives"
> - **Score ads**: "Score all unscored creatives"
> - **Analyse winning ads**: "Analyse this Meta ad library URL"
> - **Update knowledge**: "Add this learning to the character-building notes"

---

## Edge Cases

**User already has the repo at a non-standard path:**
Ask for the path and verify it rather than cloning again.

**pip3 not available:**
Try `pip install` or suggest `python3 -m pip install` as a fallback.

**User doesn't have access to AuraHealth/AIAdCreatives:**
Ask them to contact the team to be added as a collaborator on GitHub.

**OpenAI API key already set but generation fails:**
The key may be invalid or quota-exhausted. Direct them to https://platform.openai.com/usage to verify.

**Gemini API key already set but generation fails:**
The key may be invalid or quota-exhausted. Direct them to https://aistudio.google.com to verify.
