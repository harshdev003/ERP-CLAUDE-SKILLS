---
name: update-aura-skill
description: >
  Update an existing skill in the AuraHealth/AINative GitHub repo. Reads the current
  skill files, applies user-requested changes, commits to a new branch, and opens a PR.
  Use this skill when someone says "update the prepare-monday-deck skill", "fix the
  PR metrics skill", "edit the shared plugin skill", "change the description of a skill",
  "add a reference file to a skill", "modify a skill in AINative", "improve this skill",
  "the skill needs updating", or "change what the skill does". Also use when the user
  wants to add scripts, references, or assets to an existing skill, rename a skill,
  or refactor a skill's workflow. If the user says "update a skill" without specifying
  AINative, use this skill if context suggests they mean the AINative repo.
metadata:
  version: "3.2.10"
knowledge:
  reads:
    - Compliance/Privacy   # PII rules to surface when editing skills that touch user data
    - Guide/Index          # baseline routing aid (universal)
---

# Update Aura Skill

Modify an existing skill in the AuraHealth/AINative plugin repo. This skill reads the
current state, helps the user make changes, and opens a PR with the diff.

## Prerequisites

The `gh` CLI must be installed and authenticated, and the AINative repo must be cloned locally.

Run these checks before doing anything else:

```bash
# Check gh auth
gh auth status 2>&1

# Find the local repo. The trailing "$HOME"/*/AINative glob is what covers a
# clone kept under an org-named parent (e.g. ~/AuraHealth/AINative) — a common
# layout that no fixed list of dev-directory names reaches. The loop runs under
# bash because zsh aborts the whole loop when any one glob matches nothing.
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

`REPO_NOT_FOUND` means no clone exists anywhere the glob reaches — not that the
environment is unconfigured. Read it together with `gh auth status` and take the
matching branch; both branches must leave `REPO_DIR` pointing at a real checkout
before Step 1, because every later step runs `git -C "$REPO_DIR"`.

**`gh` authenticated, no clone** — clone it and continue. A full environment
setup is not needed:

```bash
REPO_DIR="$HOME/AINative"
gh repo clone AuraHealth/AINative "$REPO_DIR"
echo "REPO_FOUND at $REPO_DIR"
```

**`gh` not authenticated** — whether or not a clone exists, tell the user:

> It looks like your environment isn't set up yet. Let me run the setup first.

Then invoke the `setup-ainative` skill. After setup completes, **re-run the probe
above** so `REPO_DIR` is set, and continue with this workflow.

## Workflow

**Before you start: read [`references/best-practices.md`](references/best-practices.md).** When revising a skill, re-check it against every principle in that file — among them atomic scope, plan-before-side-effects, deterministic-first, MCP-first external service access, and an unattended run that never ends in silence. Updates are a good moment to migrate deterministic LLM steps to scripts, split overgrown SKILL.md files, or tighten vague trigger descriptions. The narrative version with rationale lives at `Procedures/AI Native/Skill Authoring` in the AI Knowledge teamspace.

**Compliance check (mandatory when the update touches user data).** If the skill being edited reads user PII (Aura API `/users/<id>`, Mixpanel engage profile, Braintrust transcripts, journal records) **or** posts user-scoped output to Slack, Notion, Linear, GitHub, or any other external surface, verify `Compliance/Privacy` is in its `knowledge.reads`. Add it if missing, and cross-check that any new external-output step in the diff cites the canonical PII rule (UID prefix + email domain only; never full email/givenName/familyName). The canonical page is at `Compliance/Privacy` in AI Knowledge.

### Step 1: Refresh the remote ref

Fetch the latest `origin` so the worktree created in Step 5 branches off current `main`.
**Don't** `git checkout` / `git pull` / `git stash` in the shared checkout — all work happens
in an isolated worktree, so the shared tree (and any uncommitted state in it) stays untouched.

```bash
git -C "$REPO_DIR" fetch origin --quiet
```

### Step 2: Identify the skill to update

If the user specified a skill name, locate it:

```bash
cd "$REPO_DIR"
# Skills live under plugins/<plugin>/skills/<skill>/SKILL.md (see .claude-plugin/marketplace.json).
find plugins -maxdepth 4 -name "SKILL.md" -path "*/skills/*" \
  | while read f; do
      skill_dir=$(dirname "$f")
      skill_name=$(basename "$skill_dir")
      plugin_dir=$(echo "$f" | cut -d'/' -f2)
      echo "- $plugin_dir / $skill_name → $f"
    done
```

If the user didn't specify which skill, present the list and ask them to pick one.

If the skill name is ambiguous (e.g. exists in multiple plugins), ask the user to clarify
which plugin's version they mean.

### Step 3: Read current skill files

Read the full contents of the skill directory so you understand what exists:

```bash
SKILL_DIR="$REPO_DIR/plugins/<plugin-name>/skills/<skill-name>"

# Show directory structure
find "$SKILL_DIR" -type f | sort

# Read SKILL.md
cat "$SKILL_DIR/SKILL.md"
```

Also read any supporting files (scripts, references, assets) so you have full context
before making changes.

**Run the conformance checks.** Work through
[`references/conformance-checks.md`](references/conformance-checks.md) — the baseline
manifest read, `metadata.version` presence, UTF-8 stdout on Python scripts, folder shape,
`SKILL.md` filename casing, hardcoded personal paths, unguarded GNU-isms, raw `gcloud` calls, PR-body heredocs,
and the `Tools/*` manifest. Each reports into the summary below; propose every flagged fix
as part of this update unless the caller declines it.

Present a summary to the user:

> Here's what the **<skill-name>** skill currently contains:
> - **SKILL.md**: <brief summary of what it does>
> - **version**: <current `metadata.version` value, e.g. `1.2.0` — or "⚠️ missing `metadata.version` — proposing to add `1.0.0`">
> - **scripts/**: <list files if any>
> - **references/**: <list files if any>
> - **assets/**: <list files if any>
> - **knowledge manifest**:
>   - baseline: <"OK — declares Guide/Index" or "⚠️ missing baseline Guide/Index — proposing to add">
>   - Tools/* reads: <"OK — covers all detected services" or "⚠️ missing Tools/<service>, Tools/<service> — proposing to add">
> - **spec conformance**:
>   - folder shape: <"OK — only SKILL.md + scripts/references/assets" or "⚠️ non-standard: <entry> — proposing to move/gitignore">
>   - filename casing: <"OK — SKILL.md" or "⚠️ lowercase skill.md — proposing rename">
>   - portability: <"OK — no hardcoded paths" or "⚠️ hardcoded <path> in <file> — proposing dynamic discovery">
>   - shell portability: <"OK — no unguarded GNU-isms" or "⚠️ GNU-only `<flag>` in <file> — proposing BSD-first fallback">
>   - body authoring: <"OK — no command-substitution heredocs" or "⚠️ `--body \"$(cat <<…)\"` in <file> — proposing --body-file rewrite">
>
> What would you like to change?

### Step 4: Understand the requested changes

Ask the user what they want to modify. Common change types:

- **Update the skill's behavior** — modify the workflow, add steps, change logic
- **Update the description/triggers** — change when the skill activates
- **Add supporting files** — new scripts, references, or assets
- **Remove files** — delete scripts or references that are no longer needed
- **Fix a bug** — the skill isn't working correctly and needs a fix
- **Refactor** — restructure without changing behavior

For each change, confirm with the user before proceeding. Show them a preview of what
will change if the modification is significant.

### Step 4b: Decide the version bump — both levels

A skill edit moves **two** versions, and CI enforces both:

1. the skill's `metadata.version` in its SKILL.md frontmatter, and
2. the owning plugin's `version` in **all three** manifest flavours —
   `plugins/<plugin-name>/.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`,
   `.cursor-plugin/plugin.json`.

The plugin manifest version is the install-cache path
(`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>`), so a skill shipped under
an unchanged plugin version never reaches anyone who already installed the plugin —
and their reload still reports success. Nothing about that is visible at runtime, which
is why the gate in Step 7 is the thing that catches it.

**The plugin moves at the same level you chose for the skill**, from its own current
value — a minor skill change takes the plugin `3.4.0` → `3.5.0`. The two numbers are
independent: never set the plugin to the skill's version, and never recompute it as
max(skill versions).

Read both current values, propose the level from the change type, and confirm with the
caller before editing anything:

```bash
V="${CLAUDE_PLUGIN_ROOT}/skills/update-aura-skill/scripts/version_bump.py"
python3 "$V" read        "$SKILL_DIR/SKILL.md"                    # skill version
python3 "$V" plugin-read "$REPO_DIR/plugins/<plugin-name>"        # all three manifests
```

Level definitions, the proposal wording, the frontmatter-slice-only edit rule and the
assertion that the edit landed inside the frontmatter:
[`references/version-bump-levels.md`](references/version-bump-levels.md).

### Step 5: Apply changes

Create an **isolated worktree** off fresh `origin/main` and make all edits inside it. Worktrees live under `<repo>/.claude/worktrees/<slug>` (gitignored, never a sibling of the repo — see [`/CLAUDE.md`](../../../../CLAUDE.md) → "PR workflow"):

```bash
git -C "$REPO_DIR" fetch origin --quiet
DEFAULT_BRANCH=$(git -C "$REPO_DIR" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
DEFAULT_BRANCH=${DEFAULT_BRANCH:-main}
WT="$REPO_DIR/.claude/worktrees/update-skill-<skill-name>"
git -C "$REPO_DIR" worktree add "$WT" -b update-skill/<skill-name> "origin/$DEFAULT_BRANCH"

# Re-point SKILL_DIR at the worktree so all edits below land there, not in the shared checkout
SKILL_DIR="$WT/plugins/<plugin-name>/skills/<skill-name>"
cd "$WT"
```

Make the requested changes. Depending on the change type:

**Editing SKILL.md:**
- Read the current file
- Apply the user's changes while preserving the overall structure
- **Write edits as the canonical workflow, not a changelog.** The skill must read as if the new behaviour was always the intended one — a future runner has no memory of what it used to say. Drop before/after framing ("was X, now Y"), corrective asides (`a GET returns "Cannot GET …"`, "`{...}` fails with `ERROR_KEY`"), and gotcha/fix language; state the correct step as a plain instruction. Keep reasoning only when it changes what the runner *does* (a precedence order, a caveat to flag), not as a record of what changed. The "what changed / why" narrative belongs in the PR description (Step 8), which is the right place for history. This applies to every edited file — `references/` and script comments included.
- Ensure frontmatter `name` and `description` are still valid
- **Apply the version bump decided in Step 4b** — set `metadata.version` to the confirmed value, and update any footer literal that hardcodes the skill's own id so it stays in lockstep
- Keep the file under 500 lines; move overflow to `references/`. **CI enforces this** —
  the `Skill length` action fails a PR that crosses the budget, and an already-over
  file may only shrink. Check before opening one:
  `python3 scripts/check-skill-length.py --base origin/main`
- **When trimming, move a whole semantic section — never a line range.** A cut that
  lands inside a fenced block or heredoc silently takes the rest of the procedure with
  it, and moved content sits one directory deeper so `](references/x.md)` and `](../…)`
  links need rewriting. Both are caught by `python3 -m unittest discover -s tests -q`
  — run it after any extraction, and diff the result against `origin/main` to confirm
  every removed line landed in the new reference.

**Bumping the plugin manifests** (the second half of Step 4b — every skill edit needs it):

```bash
cd "$WT"
V="${CLAUDE_PLUGIN_ROOT}/skills/update-aura-skill/scripts/version_bump.py"
python3 "$V" plugin-bump "plugins/<plugin-name>" --level <patch|minor|major>
python3 "$V" assert "$SKILL_DIR/SKILL.md"   # the skill bump landed in the frontmatter
```

`plugin-bump` writes the same next version into all three flavours and refuses to guess
when they already disagree. Pass the level confirmed in Step 4b — it is applied to the
plugin's own current version, not to the skill's.

**Adding new files:**
```bash
mkdir -p "$SKILL_DIR/scripts"   # or references/, assets/ as needed
# Write the new file(s)
```

**Removing files:**
```bash
git rm "$SKILL_DIR/scripts/old-script.py"
# Remove empty directories
rmdir "$SKILL_DIR/scripts" 2>/dev/null
```

**Renaming the skill:**
If the user wants to rename the skill, this requires moving the entire directory:
```bash
cd "$WT/plugins/<plugin-name>/skills"
git mv <old-name> <new-name>
```
Also update the `name` field in the SKILL.md frontmatter. A rename is a **major** bump (Step 4b) — breaking change to the skill's identity/trigger surface.

### Step 6: Review changes before committing

Show the user a diff of all changes:

```bash
cd "$WT"
git diff
git diff --cached
git status
```

**Changelog-framing check — run this on the diff before showing it.** A SKILL.md instructs a reader who has never seen the previous version, so every sentence must describe current behaviour in the present tense. Relative-to-a-past-state wording ("now also passes `rating_id`", "new in this version", "no longer dropped") describes two behaviours and leaves the model to infer which is live — see best-practices item 12. Grep the **added** lines only:

```bash
cd "$WT"
git diff -U0 origin/main -- "$SKILL_DIR" | grep '^+' | grep -v '^+++' \
  | grep -inE '\b(now also|now additionally|new in this version|newly added|previously|used to (be|do|have)|no longer|as of this version|changed to|updated to|instead of before|ha[sv]e? since|since been|formerly|originally|historically|in the past|until recently|up until|prior to th(is|at)|went unnoticed|the old (behaviou?r|version|way))\b' || true
```

Judgement call, not an auto-fail — `now` has legitimate uses (a *Why* paragraph may legitimately contrast with a prior incident, and a step may say "read the row, now complete"). Two known self-matches: updating `create-aura-skill` or `update-aura-skill` hits the anti-pattern examples in this check and in `references/best-practices.md` item 12, which document the wording rather than commit it. Flag only the hits that describe *this edit* rather than the skill's behaviour:

**A hit is evidence; silence is not.** No word list covers every way English narrates a past state, so read the added prose for present-tense voice instead of treating an empty result as a pass. Script docstrings and comments are in scope exactly like SKILL.md prose — a docstring explaining why a guard still exists is where narration most often reappears.

> ⚠️ `SKILL.md:212` reads "the profile branch **now also** passes `rating_id`" — changelog framing. I'll rewrite it in the present tense ("the profile branch passes `rating_id` and `user_id`") and leave the before/after contrast to the PR body, unless you'd rather keep it.

**Description-limit check.** The `description` is the routing surface, and the plugin loader caps it at 1024 characters and rejects `<angle-bracket>` placeholders (`{uid}` is the accepted form). The gate sweeps the whole repo rather than the diff, so it also surfaces a skill whose description was already over before this edit:

```bash
cd "$WT"
python3 scripts/check-skill-description.py
```

If it flags **the skill being edited**, fix it in this PR — cut mechanism detail, rationale, ticket links and output format from the description, and keep every trigger phrase and every `Do NOT use for X (use Y)` clause. Dropping a trigger phrase is a routing regression that nothing reports, which is why the trim goes in that order rather than shortest-first. If it flags **a different skill**, say so and leave it alone: an unrelated fix needs its own version bump, so it belongs in its own PR.

**Confirm both versions moved.** The CI gates that check this run in Step 7, after the commit —
so at this point read the diff itself and check that it carries the skill's frontmatter bump
**and** three changed `plugin.json` files:

```bash
cd "$WT"
git diff origin/main --stat -- plugins/<plugin-name>
```

Present this in a readable format:

> Here's what I'm about to commit:
> - Modified: `plugins/<plugin-name>/skills/<skill-name>/SKILL.md` — <summary of changes>
> - Added: `plugins/<plugin-name>/skills/<skill-name>/scripts/new-file.py` (if applicable)
> - Removed: `<file>` (if applicable)
> - Versions: skill `<old>` → `<new>`, plugin `<plugin-name>` `<old>` → `<new>` (all three manifests)
> - Changelog framing: <"OK — none in the added lines" or "⚠️ <n> hit(s), rewriting to present tense">
> - Description limits: <"OK — <n> chars, no tags" or "⚠️ over by <n>, trimming">
>
> Does this look right?

Wait for the user to confirm before committing.

### Step 7: Commit and push

Stage all changes, commit with a descriptive message, and push:

```bash
cd "$WT"
git add plugins/<plugin-name>/skills/<skill-name>/ \
        plugins/<plugin-name>/.claude-plugin/plugin.json \
        plugins/<plugin-name>/.codex-plugin/plugin.json \
        plugins/<plugin-name>/.cursor-plugin/plugin.json
git commit -m "Update <skill-name> skill in <plugin-name> plugin

<1-2 sentence description of what changed>"
```

The three manifests are staged explicitly because they sit outside the skill directory —
staging only `skills/<skill-name>/` leaves the plugin bump behind as an uncommitted local
change, which fails the same gate the bump was made to satisfy.

If the skill was renamed, stage the deletion of the old directory in the same commit:

```bash
cd "$WT"
git add -A plugins/<plugin-name>/skills/
```

**Run the three CI gates on the commit, before pushing — and only on a clean tree.** They take
their *file list* from `origin/main...HEAD`, which is committed work, but read versions and
line counts from the **working tree**. The two halves disagree on an uncommitted tree, and the
disagreement is silent in the dangerous direction: a `plugin-bump` written but never staged
reads as bumped here, so the gate passes and CI — which sees only the commit — fails on the
exact miss this step exists to catch. Fetch first too; a stale ref passes here and fails there:

```bash
cd "$WT"
git fetch origin --quiet
[ -z "$(git status --porcelain)" ] || echo "⚠️ uncommitted — commit before trusting the gates"
python3 scripts/check-version-bumps.py --base origin/main   # both levels: skill + all three manifests
python3 scripts/check-skill-length.py  --base origin/main   # ~500-line SKILL.md budget, ratchet
python3 -m unittest discover -s tests -q                    # fences, heredocs, links
```

The version gate is the only signal a missing manifest bump has — nothing about it breaks
locally, and the install it freezes still reports a successful reload. A failure names exactly
what didn't move; fix it (Step 5's `plugin-bump` for a manifest, the frontmatter for the
skill), fold it into the commit, and re-run until all three pass:

```bash
cd "$WT"
git add -A plugins/<plugin-name>/ && git commit --amend --no-edit
```

`-A` over the plugin path, never `git add -u`: a length failure is fixed by extracting into
a **new** `references/` file, which `-u` leaves untracked — the amended commit then carries a
shorter SKILL.md without the extracted procedure, and the gate passes on it.

Then push:

```bash
cd "$WT"
git push -u origin update-skill/<skill-name>
```

### Step 8: Create a PR (as a draft)

Open the pull request **as a draft** (`--draft`). This repo has cursor-bot
(Bugbot) enabled and merges manually — opening as a draft is the signal that the
PR isn't ready to merge until Bugbot has reviewed and any findings are addressed.
Marking it ready (Step 8b) is a deliberate gate: a PR opened ready-for-review can
be merged before the review lands, stranding any follow-up fix on a merged branch
(observed on [#388](https://github.com/AuraHealth/AINative/pull/388) → [#392](https://github.com/AuraHealth/AINative/pull/392)).

Write the PR body to a file first, then pass it with `--body-file`. **Never**
the command-substitution heredoc form `--body "$(cat <<'EOF' … )"` — apostrophes,
backticks and em-dashes in the body break bash's heredoc-inside-`$()` parse
(the "heredoc apostrophe curse"). A `cat > file` redirect (or the Write tool)
is safe because the heredoc isn't nested inside `$()`.

```bash
cd "$WT"
cat > /tmp/pr-body-<skill-name>.md <<'EOF'
## Updated Skill: <skill-name>

**Plugin:** <plugin-name>
**Skill version:** <old> → <new> (<patch|minor|major>)
**Plugin version:** <old> → <new> (all three manifest flavours)

### What changed
- <bullet point summary of each change>

### Why
<brief explanation of why these changes were made>

### Files modified
- `plugins/<plugin-name>/skills/<skill-name>/SKILL.md` — <what changed>
- <list any other files added/removed/modified>
EOF
gh pr create \
  --draft \
  --title "Update <skill-name> skill in <plugin-name>" \
  --body-file /tmp/pr-body-<skill-name>.md
```

Share the PR URL with the user, noting it's a **draft** pending Bugbot.

### Step 8b: Run Bugbot, address findings, then mark ready

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

### Step 9: Confirm and summarize

Tell the user:
- The PR URL (and that it's now **marked ready**, Bugbot clean)
- A summary of what was changed
- A reminder that merging is theirs to do (or ask a teammate to review)

The shared checkout was never touched (all work happened in the worktree), so there's
nothing to restore. Once the PR merges, the worktree can be removed:
```bash
git -C "$REPO_DIR" worktree remove "$WT"   # or run eng:clean-up-worktrees later
```

## Edge cases

See [`references/edge-cases.md`](references/edge-cases.md).
