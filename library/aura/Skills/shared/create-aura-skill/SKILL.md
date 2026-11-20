---
name: create-aura-skill
description: >
  Create a new skill inside the AuraHealth/AINative GitHub repo. Handles plugin selection,
  folder scaffolding, file creation, and PR submission. Use this skill whenever someone wants
  to add a skill to AINative, create a new AINative skill, scaffold a skill for a plugin,
  or says things like "add a skill to shared", "create a new eng skill", "I want to build
  a skill for the Monday deck plugin", or "make a new skill in AINative". Even if the user
  just says "create a skill" without mentioning AINative, use this skill if context suggests
  they mean the AINative repo (e.g. they mention a plugin name like "shared" or "eng").
metadata:
  version: "3.2.10"
knowledge:
  reads:
    - Compliance/Privacy   # PII rules to surface to authors of new skills that touch user data
    - Guide/Index          # baseline routing aid (universal)
---

# Create Aura Skill

Add a new skill to the AuraHealth/AINative plugin repo. This skill walks you through
discovering the target plugin, gathering the skill's purpose and files from the user,
scaffolding the correct folder structure, and opening a PR for review.

## Prerequisites

The `gh` CLI must be installed and authenticated, and the AINative repo must be cloned locally.

Run these checks before doing anything else:

```bash
# Check gh auth
gh auth status 2>&1

# Find the local repo. The trailing "$HOME"/*/AINative glob is what covers a
# clone kept under an org-named parent (e.g. ~/AuraHealth/AINative, the
# setup-ainative default). The loop runs under bash because zsh aborts the
# whole loop when any one glob matches nothing.
REPO_DIR=$(bash -c 'for dir in "$HOME/AINative" "$HOME/code/AINative" "$HOME/repos/AINative" \
           "$HOME/src/AINative" "$HOME/dev/AINative" "$HOME/projects/AINative" \
           "$HOME"/*/AINative "$HOME"/*/*/AINative; do
  if [ -d "$dir/.git" ]; then echo "$dir"; exit 0; fi
done')
if [ -n "$REPO_DIR" ]; then
  echo "REPO_FOUND at $REPO_DIR"
else
  echo "REPO_NOT_FOUND"
fi
```

**If the repo is not found or gh is not authenticated**, tell the user:

> It looks like your environment isn't set up yet. Let me run the setup first.

Then invoke the `setup-ainative` skill to handle cloning, authentication, and MCP configuration.
After setup completes, continue with this skill's workflow.

**If the repo is found**, ask the user to confirm the location if it's not the default `~/AINative`:

> I found the AINative repo at `<path>`. Is that the right one?

Store the confirmed path as `REPO_DIR` for the rest of the workflow.

## Workflow

**Before you start: read [`references/best-practices.md`](references/best-practices.md).** It captures the principles every AINative skill follows — among them atomic scope, plan-before-side-effects, deterministic-first, MCP-first external service access, and an unattended run that never ends in silence. They inform naming, structure, and the LLM-vs-script split. The narrative version with rationale lives at `Procedures/AI Native/Skill Authoring` in the AI Knowledge teamspace.

**Compliance check (mandatory for any skill that touches user data).** If the new skill will read user PII (Aura API `/users/<id>`, Mixpanel engage profile, Braintrust transcripts, journal records) **or** post user-scoped output to Slack, Notion, Linear, GitHub, or any other external surface, add `Compliance/Privacy` to `knowledge.reads` in the frontmatter and cite the canonical PII rule (UID prefix + email domain only; never full email/givenName/familyName) anywhere the skill draws an external output. The canonical page is at `Compliance/Privacy` in AI Knowledge — surface this requirement when prompting the author about the skill's data sources.

### Step 1: Pull latest from main

Always start by pulling the latest changes to avoid conflicts:

```bash
cd "$REPO_DIR"
git checkout main 2>/dev/null || git checkout master
git pull
```

If there are uncommitted changes on main, stash them:
```bash
cd "$REPO_DIR"
git stash
git checkout main 2>/dev/null || git checkout master
git pull
git stash pop 2>/dev/null
```

### Step 2: Gather skill details from the user

**Ask for the skill name first — before anything else.** This is the very first interactive question, before you show the plugin list, ask about the skill's content, pre-fetch knowledge paths, or propose any description text. Once the user gives you a candidate name, validate it against the naming conventions (item 1 below), propose a compliant alternative if it doesn't fit, confirm the final name back to the user, *then* move on to the remaining items. Locking the name up front anchors everything that follows — frontmatter, file paths, branch name, PR title — so the rest of the conversation doesn't drift into a mid-flow rename.

You need four things before you can scaffold anything:

1. **Skill name** *(ask first — see the bold note above)* — must follow the repo naming conventions in `/CLAUDE.md` → "Naming conventions" (kebab-case, verb-first for action skills, noun-first only for artifact skills, third-party names verbatim, 2–3 tokens ideal). If the user gave a human-readable name, convert it (e.g. "Update PR Metrics" → `update-pr-metrics`). If a proposed name doesn't fit the convention, propose a compliant alternative before scaffolding.

2. **Target plugin** — which plugin directory in the repo should this skill live in.
   If the user already specified one (e.g. "add it to shared"), use it.
   If not, discover the available plugins dynamically (see Step 3) and ask the user to pick one.

3. **Skill content** — the user should provide either:
   - The full SKILL.md content (or a detailed description you can turn into one)
   - Any supporting files (scripts, references, assets, templates) and details about what the skill does, when it triggers, and its workflow

4. **AI Knowledge dependencies** — every skill gets `reads: [Guide/Index]` scaffolded by default (universal baseline — see `/CLAUDE.md` → "Knowledge manifest" → "Baseline read"). On top of the baseline, the skill must declare a `Tools/<service>` read for every external service it talks to (`CLAUDE.md` → "Knowledge manifest" → "Tools section"). Detect those first from the content gathered in item 3, then ask the user for any other paths.

   **4a. Auto-detect `Tools/*` reads from the skill content.** Scan the proposed SKILL.md text, scripts, and references for the markers below (case-insensitive substring; also match the env-var names). For every match, propose the corresponding `Tools/<service>` read.

   | Markers found in content | Proposed read |
   |---|---|
   | `slack`, `slack.com`, `SLACK_BOT_TOKEN`, `chat.postMessage` | `Tools/Slack` |
   | `linear`, `linear.app`, `LINEAR_API_KEY` | `Tools/Linear` |
   | `notion`, `NOTION_TOKEN`, `notion-search`, `notion-fetch` | `Tools/Notion` |
   | `github`, `gh pr`, `gh api`, `GITHUB_TOKEN` | `Tools/GitHub` |
   | `braintrust`, `BRAINTRUST_API_KEY` | `Tools/Braintrust` |
   | `mixpanel`, `MIXPANEL_SERVICE_ACCOUNT` | `Tools/Mixpanel` |
   | `11labs`, `elevenlabs` | `Tools/ElevenLabs` |
   | `gemini`, `GEMINI_API_KEY` | `Tools/Gemini` |
   | `openai`, `OPENAI_API_KEY`, `OPENAI_ADMIN_API_KEY` | `Tools/OpenAI` |
   | `youtube` | `Tools/YouTube` |
   | `services.aurahealth.io`, `AURA_API_TOKEN`, `/ai/dialogue/ask`, `/users/<id>` | `Tools/Aura API` |
   | `bq.py`, `aura-main`, `llm_ledger.py`, `llm.llm_call` | `Tools/BigQuery` |
   | `bugsnag`, `BUGSNAG_TOKEN` | `Tools/Bugsnag` |
   | `gcloud`, `gcloud_sa.py`, `GCP_KEY`, `cloudlogging.app.goo.gl` | `Tools/GCP` |

   Surface the matches before the main dependency prompt:

   > Scanning the skill content, I detected references to **`<service-A>`** and **`<service-B>`** — I'll add `Tools/<service-A>` and `Tools/<service-B>` to `knowledge.reads`. Say if either is a false positive (the word appears in a comment or unrelated copy but the skill doesn't actually call the service).

   If the scan finds nothing, skip the surface step and go straight to 4b. If a match is a false positive, drop it and don't include it in the manifest.

   **4b. Ask the user for any other reads/writes.** Beyond the baseline + auto-detected Tools/* reads, prompt for everything else:

   > Beyond `Guide/Index` and the Tools/* reads above, any other AI Knowledge paths this skill depends on? Examples: `Products/Aurie` (brand voice), `Learnings/Experiments` (prior runs), `Signals/Conversation Patterns` (recent rated-conversation signal). Any writes? (e.g. `Learnings/Experiments`).

   If the user says "no extras" or "not sure," ship with `reads: [Guide/Index, <auto-detected Tools/*>]` and no `writes`. If the user gives extras, append them to `reads` and add a `writes` list if applicable. If the user knows there's relevant content but can't name paths, run `search-knowledge` against keywords from the skill's purpose to surface candidates, or open the live `Guide/Index` page in Notion to browse the current Path → Description map.

   Only include paths the skill will actually use on its first version — over-declaring leaks scope and triggers spurious pre-fetches.

   **4c. First-consumer documentation.** If any auto-detected `Tools/<service>` page doesn't exist yet in AI Knowledge (e.g. `Tools/Bugsnag` for the first Bugsnag-using skill), best-practices item 10 ("Document new external dependencies in the same change") says the same PR should create that page. Flag it to the user and offer to draft the stub now or after the skill PR — the read still goes in `knowledge.reads` either way so `healthcheck-knowledge` can audit it.

If any of 1–3 are missing, ask the user before proceeding. Item 4's *additional* paths are optional, but the baseline `Guide/Index` read is always scaffolded and any auto-detected `Tools/*` reads from 4a are added unless the user flags them as false positives.

### Step 3: Discover available plugins

Inspect the local repo to find available plugins:

```bash
cd "$REPO_DIR"
# Plugins live under plugins/<name>/.claude-plugin/plugin.json (see .claude-plugin/marketplace.json).
find plugins -maxdepth 3 -name "plugin.json" -path "*/.claude-plugin/*" \
  | while read f; do
      dir=$(dirname $(dirname "$f"))
      name=$(basename "$dir")
      desc=$(python3 -c "import json; print(json.load(open('$f')).get('description',''))" 2>/dev/null)
      echo "- **$name**: $desc"
    done
```

Present the list to the user and ask which plugin the skill belongs in. If there's only
one plugin, confirm with the user rather than assuming.

### Step 4: Check for existing and similar skills

Two checks against the target plugin before scaffolding anything: exact-name conflict (cheap and deterministic) and semantic similarity (a reasoning step). Both protect against the same failure mode — two skills competing for the same trigger surface and forcing the user to maintain parallel implementations.

#### 4a. Exact-name conflict

```bash
ls "$REPO_DIR/plugins/<plugin-name>/skills/<skill-name>/" 2>/dev/null && echo "CONFLICT" || echo "OK"
```

Also check the `commands/` directory for legacy command files with the same name:

```bash
ls "$REPO_DIR/plugins/<plugin-name>/commands/<skill-name>.md" 2>/dev/null && echo "CONFLICT" || echo "OK"
```

If there's a conflict, tell the user and ask how they want to proceed (rename, overwrite, or hand off to `update-aura-skill`).

#### 4b. Semantic similarity (avoid duplicate workflows)

A different file path is not the same as a different skill. Two skills that fire on overlapping trigger phrases or cover the same job are duplicates even if their names differ — they steal traffic from each other and double the maintenance surface. Before scaffolding, scan the rest of the target plugin for overlap.

List the plugin's existing SKILL.md frontmatters:

```bash
for f in "$REPO_DIR/plugins/<plugin-name>"/skills/*/SKILL.md; do
  name=$(basename $(dirname "$f"))
  echo "=== $name ==="
  head -30 "$f"
done
```

Read each existing skill's frontmatter `description` and opening section. Apply judgement — this is a reasoning step, not a regex. Does an existing skill cover the same job, target system, or trigger phrases as the proposed one?

**The bar for "similar":** at least two of these signals are present —

- Name keyword overlap (verb-object collision, e.g. proposed `ship-linear-card` vs existing `work-on-linear-card`).
- Trigger phrase overlap in `description` (shared user phrases like "work on this card", "implement X").
- Workflow overlap: same MCP tools, same target system, same outcome.

One signal alone (same plugin, same external service) is not enough — it would over-trigger on every name in the plugin.

If you find candidates that pass the bar, surface them with this exact shape:

> "I found N existing skill(s) that may overlap with what you want to build:
>
> **`<existing-skill-name>`**
> Description: `<one-line summary>`
> Key triggers: `<2-3 trigger phrases lifted from its description>`
> Overlap with proposed skill: `<what overlaps>` — Differences: `<what's genuinely new in the proposal>`
>
> [repeat per candidate, max 3]
>
> Three options:
>
> 1. **Update an existing skill** — extend `<best-fit-name>` with the new behaviour. I'll hand off to `update-aura-skill` with your proposed changes.
> 2. **Create a new skill anyway** — only if the new skill has a genuinely different trigger surface and workflow. Reply with one sentence explaining why; that justification will go in the PR description so reviewers can sanity-check the duplication call.
> 3. **Stop and rethink** — bail out so you can adjust the proposal.
>
> I won't scaffold until you reply."

On **option 1**: stop this skill. Hand off to `update-aura-skill` with the target skill name and the proposed changes (carry over content + files from Step 2).

On **option 2**: continue to Step 5. Record the user's justification verbatim and surface it in the PR body under a "Why a new skill instead of extending `<existing-name>`" section.

On **option 3**: stop.

If no candidates pass the bar, proceed to Step 5 with no extra prompt — but a brief note in chat ("Scanned N skills in `<plugin>`; no significant overlap found") makes the check visible to the user.

### Step 5: Scaffold the skill

Create the skill directory under the correct plugin. Every skill follows this structure:

```
plugins/<plugin-name>/
  skills/
    <skill-name>/
      SKILL.md          # Required — the main skill file
      scripts/           # Optional — executable code for deterministic tasks
      references/        # Optional — docs loaded into context as needed
      assets/            # Optional — templates, icons, fonts used in output
```

**Rules:**

- `SKILL.md` is always required and must have YAML frontmatter with at least `name`, `description`, and `metadata.version` (order: `name` → `description` → `metadata` → `knowledge`). Per the Agent Skills spec, the version is a key inside the `metadata:` map (a quoted string), not a top-level field
- **Every new skill starts at `metadata.version: "1.0.0"`.** It's the first release; subsequent edits bump it via semver (patch/minor/major) — `update-aura-skill` owns the bump. See `/CLAUDE.md` → "Skill versioning"
- **The `description` has two hard limits the plugin loader enforces: at most 1024 characters, and no `<angle-bracket>` placeholders** — write `{uid}` / `{name}`; backticks do not exempt them, since the loader scans the raw string. `references/best-practices.md` item 7 has the limits and the tie-break for when enumeration and the cap pull against each other
- Only create `scripts/`, `references/`, or `assets/` subdirectories if the user provided files for them
- Use kebab-case for all directory and file names
- Do not create empty directories — only what's needed

**SKILL.md frontmatter format:**

```yaml
---
name: skill-name
description: >
  A clear description of what the skill does and when to trigger it.
  Be specific about trigger phrases and contexts. Err on the side of
  being "pushy" — list concrete scenarios where this skill should activate.
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index                  # baseline routing aid (universal — always include)
    # Add any Tools/* reads auto-detected in Step 2.4a (one per external service), e.g.:
    # - Tools/Slack                # if the skill posts to Slack
    # - Tools/Linear               # if the skill reads/writes Linear
    # Then any other reads the user named in Step 2.4b, e.g.:
    # - Products/Aurie
    # - Learnings/Experiments
  # Include `writes:` only if the user named paths the skill writes to.
  # writes:
  #   - Learnings/Experiments
---
```

Always emit `reads: [Guide/Index]` — it's the universal baseline (see `/CLAUDE.md` → "Knowledge manifest" → "Baseline read"). Append every `Tools/<service>` read detected in Step 2.4a (unless the user flagged it as a false positive), then any other reads the user named in Step 2.4b. Add a `writes:` list only if the user named at least one write path; omit the key entirely otherwise — don't write empty `writes: []`. See `/CLAUDE.md` → "Knowledge manifest" for how paths resolve and what declaring them unlocks.

If the user provided raw content rather than a formatted SKILL.md, structure it properly:
- Add the YAML frontmatter
- Write a clear title and summary
- Organize the workflow into numbered steps
- Reference any bundled scripts/assets/references with relative paths
- Keep the SKILL.md under 500 lines (overflow to `references/`) and the `description`
  inside the loader's limits. **CI enforces both** — check before opening a PR:
  `check-skill-length.py --base origin/main`, `check-skill-description.py` (in `scripts/`)

**Python script convention (UTF-8 stdout).** When scaffolding any `scripts/*.py` file that prints to stdout, include the UTF-8 reconfigure guard at the very top of `main()` — before argparse, before any output:

```python
def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # ... rest of main
```

This prevents `UnicodeEncodeError` on Windows consoles (default `cp1252`) when the script emits Markdown arrows, em-dashes, curly quotes, or any other non-ASCII. See `references/best-practices.md` → "Cross-platform: UTF-8 stdout for Python scripts" for the full rationale. **GCP convention.** A skill that runs `gcloud` copies the canonical `gcloud_sa.py` (`docs/canonical-sources.md`) into its `scripts/` and calls gcloud only through it — the `GCP_KEY` service account, the user login only when `GCP_KEY` is unset; `tests/test_gcloud_gate.py` fails on a raw call.

### Step 6: Write files, commit, and push

Create an **isolated worktree** off fresh `origin/main`, then write all files, commit, and push from inside it. Worktrees live under `<repo>/.claude/worktrees/<slug>` (gitignored, never a sibling of the repo — see [`/CLAUDE.md`](../../../../CLAUDE.md) → "PR workflow"):

```bash
git -C "$REPO_DIR" fetch origin --quiet
DEFAULT_BRANCH=$(git -C "$REPO_DIR" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
DEFAULT_BRANCH=${DEFAULT_BRANCH:-main}
WT="$REPO_DIR/.claude/worktrees/add-skill-<skill-name>"
git -C "$REPO_DIR" worktree add "$WT" -b add-skill/<skill-name> "origin/$DEFAULT_BRANCH"
cd "$WT"   # all writes / commit / push / PR happen inside the worktree
```

Create the skill directory and write all files **inside the worktree**:

```bash
mkdir -p "$WT/plugins/<plugin-name>/skills/<skill-name>"
# Write SKILL.md
# Write any scripts/, references/, assets/ files the user provided
```

**Bump the owning plugin's version — all three manifest flavours.** A new skill is new
capability in that plugin, so it takes a **minor** bump of `version` in
`plugins/<plugin-name>/.claude-plugin/plugin.json` and its `.codex-plugin` /
`.cursor-plugin` siblings. That version is the install-cache path
(`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>`) — Claude Code skips the copy
when the version already exists, so adding a skill without bumping means nobody who already
installed the plugin ever receives it, and their reload still reports success.

The plugin version is independent of skill versions — do **not** set it to the new skill's
`1.0.0`, and do not recompute it as max(skill versions). Read the current plugin version and
bump its minor:

```bash
cd "$WT"
python3 - <<'PY'
import json, pathlib, re
plugin = "<plugin-name>"
cur = json.loads(pathlib.Path(f"plugins/{plugin}/.claude-plugin/plugin.json").read_text())["version"]
maj, minor, _ = (cur.split(".") + ["0", "0"])[:3]
new = f"{maj}.{int(minor) + 1}.0"
for d in (".claude-plugin", ".codex-plugin", ".cursor-plugin"):
    f = pathlib.Path(f"plugins/{plugin}/{d}/plugin.json")
    f.write_text(re.sub(r'(?m)^(\s*"version"\s*:\s*)"[^"]*"', rf'\g<1>"{new}"',
                        f.read_text(encoding="utf-8"), count=1), encoding="utf-8")
print(f"{plugin}: {cur} -> {new}")
PY
```

Stage and commit — `plugins/<plugin-name>/` covers both the new skill folder and the three
bumped manifests:

```bash
cd "$WT"
git add plugins/<plugin-name>/
git commit -m "Add <skill-name> skill to <plugin-name> plugin"
```

**Verify both levels on the commit, before pushing — and only on a clean tree.** These are the
checks CI runs. They list changed files from `origin/main...HEAD` but read versions from the
**working tree**, so an uncommitted bump reads as bumped here and fails there. An unbumped
manifest has no other symptom — the install it freezes still reports a successful reload.

```bash
cd "$WT" && git fetch origin --quiet
[ -z "$(git status --porcelain)" ] || echo "⚠️ uncommitted — commit before trusting the gates"
python3 scripts/check-version-bumps.py --base origin/main   # skill + all three manifests
python3 scripts/check-skill-length.py  --base origin/main   # ~500-line SKILL.md budget
```

Fix anything they name and fold it in from the worktree — `cd "$WT" && git add -A
plugins/<plugin-name>/ && git commit --amend --no-edit` — then re-run. Stage with `-A`, never
`git add -u`: a length failure is fixed by extracting into a **new** `references/` file, which
`-u` leaves untracked, so the gate passes on a SKILL.md missing the extracted procedure. Push:

```bash
cd "$WT" && git push -u origin add-skill/<skill-name>
```

### Step 7: Create a PR (as a draft)

Open the pull request **as a draft** (`--draft`). This repo has cursor-bot
(Bugbot) enabled and merges manually — opening as a draft is the signal that the
PR isn't ready to merge until Bugbot has reviewed and any findings are addressed.
Marking it ready (Step 7b) is a deliberate gate, not a formality: a PR opened
ready-for-review can be merged before the review lands, which strands any
follow-up fix on a merged branch (observed on [#388](https://github.com/AuraHealth/AINative/pull/388) → [#392](https://github.com/AuraHealth/AINative/pull/392)).

Write the PR body to a file first, then pass it with `--body-file`. **Never**
the command-substitution heredoc form `--body "$(cat <<'EOF' … )"` — apostrophes,
backticks and em-dashes in the body break bash's heredoc-inside-`$()` parse
(the "heredoc apostrophe curse"). A `cat > file` redirect (or the Write tool)
is safe because the heredoc isn't nested inside `$()`.

```bash
cd "$WT"
cat > /tmp/pr-body-<skill-name>.md <<'EOF'
## New Skill: <skill-name>

**Plugin:** <plugin-name>
**Plugin version:** <old> → <new> (minor, all three manifest flavours)
**Trigger:** <brief description of when this skill activates>

### What it does
<1-3 sentence summary>

### Files added
- `plugins/<plugin-name>/skills/<skill-name>/SKILL.md`
- <list any other files>
- Modified: the three `plugins/<plugin-name>/.*-plugin/plugin.json` manifests — version <old> → <new>

### Folder structure
```
plugins/<plugin-name>/skills/<skill-name>/
├── SKILL.md
├── scripts/     (if applicable)
├── references/  (if applicable)
└── assets/      (if applicable)
```
EOF
gh pr create \
  --draft \
  --title "Add <skill-name> skill to <plugin-name>" \
  --body-file /tmp/pr-body-<skill-name>.md
```

Share the PR URL with the user when done, noting it's a **draft** pending Bugbot.

### Step 7b: Run Bugbot, address findings, then mark ready

Trigger the cursor-bot review on the draft, wait for it, and resolve anything it
flags before handing the PR to the user as merge-ready. Reply in each finding's thread with what changed and the commit sha (or why you won't fix it), then resolve it; never resolve silently (canonical: AI Knowledge `Procedures/Engineering/PR Process`):

```bash
cd "$WT"
gh pr comment <pr-number> --body "bugbot run"   # or post "@cursor review"
```

**Clean means the `Cursor Bugbot` check-run reached `completed` — not a summary plus a quiet hold.** The **PR Summary** issue-comment posts ~1 minute *before* the inline review comments, and the inline set can land minutes later still: on [#859](https://github.com/AuraHealth/AINative/pull/859) a 100s hold after a HEAD-citing summary saw zero findings while the check-run was still `in_progress`, and **three Medium findings appeared at once** when it completed. Bugbot also **edits the summary in place** on a re-run (the comment count does *not* change), so never gate on the summary alone or on comment counts either.

A run is clean only when **all three** hold: (1) the latest cursor/bugbot summary cites the PR's **actual head SHA** (compare against `gh pr view --json headRefOid`, not just any SHA in the body); (2) the `Cursor Bugbot` check-run on that head has **`status == completed`** — gate on `status`, never on `conclusion`, which is `neutral` on some clean runs and `success` on others; and (3) no Bugbot inline comment's own footer SHA equals head. Read the check-run from the check-runs API, **not** by parsing `gh pr checks` output — its rows are TAB-separated (`Cursor Bugbot<TAB>pending`), so whitespace-splitting yields `Bugbot`, never a status.

**Genuine open finding vs stale carried-forward anchor.** Do **not** treat `line != null` as "open" — GitHub re-anchors an already-fixed finding's comment to the new head whenever its line still exists, so that filter catches findings the current run does not raise. Every Bugbot inline comment ends with `Reviewed by Cursor Bugbot for commit <sha>`; a finding is genuinely open only when that **footer** SHA equals head. Full rationale: [`references/best-practices.md`](references/best-practices.md) → *Bugbot review*.

**If the summary never cites HEAD, or the check-run never appears**, keep waiting or re-trigger — never conclude clean off a summary citing an older commit. The summary can lag the head by minutes, a Bugbot **credit outage** means it never posts, and a `bugbot run` buried inside a longer comment silently never fires (the trigger must be the whole comment). Re-post it as its own comment on the current head and resume polling.

```bash
HEAD=$(gh pr view <pr-number> --json headRefOid -q .headRefOid)   # the commit you just pushed

# (1) the latest Bugbot summary must cite HEAD — otherwise it reviewed an older
#     commit, hasn't finished, or (credit outage) never ran.
gh api repos/AuraHealth/AINative/issues/<pr-number>/comments --paginate \
  | jq -rs 'add | [.[] | select(.user.login|test("cursor|bugbot";"i"))] | last | .body' | grep -qF "$HEAD" \
  && echo "summary is for HEAD" || echo "summary NOT yet for HEAD — keep waiting / re-trigger"

# (2) the check-run on HEAD must reach `completed`. Poll every ~25-30s until it does
#     — `queued`, `in_progress`, or no row at all each mean findings can still land.
#     A 403 here is a credential fault, not a verdict (/CLAUDE.md → GitHub credentials).
gh api repos/AuraHealth/AINative/commits/"$HEAD"/check-runs --paginate \
  --jq '.check_runs[] | select(.name|test("bugbot";"i")) | "\(.status) \(.conclusion)"'

# (3) only once (1) AND (2) hold: GENUINE findings — footer cites HEAD. Clean = empty.
gh api repos/AuraHealth/AINative/pulls/<pr-number>/comments --paginate \
  | jq -r --arg H "$HEAD" '.[] | select(.user.login|test("cursor|bugbot";"i")) | select(.line!=null) | select(.body|test($H)) | "FILE: \(.path):\(.line)\n\(.body)\n---"'
```

- **Findings present** → fix them in the worktree, commit, push, and re-run
  `bugbot run` on the new head until clean. Reply on the thread summarising each fix.
- **Clean (or findings explicitly waived)** → mark the PR ready for review:

```bash
gh pr ready <pr-number>
```

Only then tell the user it's ready to merge. Don't merge it yourself — merging is
the user's call (this repo merges manually).

### Step 8: Confirm and summarize

Tell the user:
- The PR URL (and that it's now **marked ready**, Bugbot clean)
- Which plugin the skill was added to
- The full path of the skill in the repo
- A reminder that merging is theirs to do (or ask a teammate to review)
- That the worktree at `$WT` can be removed once the PR merges:
  `git -C "$REPO_DIR" worktree remove "$WT"` (or run `eng:clean-up-worktrees` later)

## Edge cases

Situations that divert or halt a run — a command rather than a skill, a skill that
already exists, files with no SKILL.md, an unauthenticated `gh`, a dirty working
tree: [`references/edge-cases.md`](references/edge-cases.md).

## Reference

See `references/plugin-structure.md` for a detailed breakdown of the AINative repo layout
and plugin conventions.
