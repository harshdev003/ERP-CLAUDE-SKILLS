# Step 4b — choosing the version bump level

How `shared:update-aura-skill` picks patch / minor / major for a change, applies it to both versions a skill edit moves, and keeps the skill-side edit inside the frontmatter. The semver definitions themselves are the repo's, stated in `/CLAUDE.md` → Skill versioning; this page is the decision procedure.

Every change to a skill bumps two versions: its own `metadata.version`, and the owning
plugin's `version` in all three manifest flavours. Read both current values, classify the
change you're about to make, propose the next versions, and confirm with the user before
applying either.

**Never read or write the version with a whole-file grep** — some skills document the
frontmatter by example (this one does, in Step 3), so a whole-file match can hit a doc block
instead of the real value. Use the bundled helper, and assert after the bump in Step 5; a
non-zero exit means it landed in the wrong place. Rationale and the write-side recipe:
[`references/version-bump.md`](version-bump.md).

```bash
V="${CLAUDE_PLUGIN_ROOT}/skills/update-aura-skill/scripts/version_bump.py"
python3 "$V" read   "$SKILL_DIR/SKILL.md"   # current version
python3 "$V" assert "$SKILL_DIR/SKILL.md"   # run after applying the bump
```

| Bump | When | Examples |
|---|---|---|
| **patch** (`x.y.Z`) | No behaviour or trigger change — docs/wording, typo, comment, formatting, a bug fix that restores intended behaviour. | Fix a broken relative link; reword a step; add the UTF-8 guard; correct a footer literal. |
| **minor** (`x.Y.0`) | New backward-compatible capability — a new step, flag, mode, script, reference, or an *added* trigger phrase. Existing invocations still work unchanged. | Add a `--sweep` mode; add a new `references/` doc; broaden the `description` with extra triggers. |
| **major** (`X.0.0`) | Breaking change — rename the skill, remove or repurpose behaviour, change the output contract or a script's CLI, or materially narrow/redefine the trigger surface. | Rename `investigate-x` → `investigate-y`; drop a subcommand; change the report shape callers depend on. |

If a single edit spans multiple categories, take the **highest** applicable bump. Propose
both levels together, since the same level applies to each:

> This is a **minor** change (adds a new `--weekly` mode, backward-compatible), so I'll bump the skill's `metadata.version` from `1.2.0` → `1.3.0`, and the `eng` plugin manifests from `3.4.0` → `3.5.0`. Sound right, or would you class it differently?

Wait for confirmation, then apply both bumps in Step 5.

## The plugin level, and how it relates to the skill level

Changing **any** file under `plugins/<plugin>/` requires bumping `version` in all three
manifest flavours — `plugins/<plugin>/.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`,
`.cursor-plugin/plugin.json`. They move together, at the level chosen for the skill, applied
to the plugin's **own** current value:

| Skill change | Skill `metadata.version` | Plugin manifests |
|---|---|---|
| patch | `1.2.0` → `1.2.1` | `3.4.0` → `3.4.1` |
| minor | `1.2.0` → `1.3.0` | `3.4.0` → `3.5.0` |
| major | `1.2.0` → `2.0.0` | `3.4.0` → `4.0.0` |

The two numbers are otherwise **independent**. Never set the plugin to the skill's version,
and never recompute it as max(skill versions) — bumping a skill that isn't the current maximum
would leave the max unchanged, so the manifest wouldn't move and the install would no-op.
Max-of-skills was only the original seed.

The manifest version is the install-cache path
(`~/.claude/plugins/cache/<marketplace>/<plugin>/<version>`) — Claude Code skips the copy when
the version already exists, so shipping under an unchanged one silently freezes every existing
install while the reload still reports success.

Read it in Step 4b, apply it in Step 5. The bundled helper writes the same next version into
all three flavours and refuses to guess when they already disagree:

```bash
V="${CLAUDE_PLUGIN_ROOT}/skills/update-aura-skill/scripts/version_bump.py"

# Step 4b — read-only, so the absolute path into the shared checkout is safe
python3 "$V" plugin-read "$REPO_DIR/plugins/<plugin>"

# Step 5 — writes, so it runs inside the worktree created there
cd "$WT"
python3 "$V" plugin-bump "plugins/<plugin>" --level minor --dry-run  # preview the move
python3 "$V" plugin-bump "plugins/<plugin>" --level minor            # write all three
```

`plugin-bump` is the only writing call here, and the relative `plugins/<plugin>` resolves
against the current directory — run it from `$REPO_DIR` and it edits the shared checkout that
Step 1 keeps untouched. `cd "$WT"` first, every time.

Both bumps are enforced pre-merge by `scripts/check-version-bumps.py` (the **Version bump**
CI workflow), and an unbumped manifest has no local symptom at all — the gate is the only
signal. Run it in Step 7 on the commit, before the first push, and only on a clean tree — it
lists changed files from `origin/main...HEAD` (committed work) but reads each version from the
**working tree**, so a bump written and never staged reads as bumped and the gate passes while
CI fails on it. `git status --porcelain` must be empty before the result means anything.

```bash
cd "$WT"                        # the Step 5 worktree, not the shared checkout
git fetch origin --quiet
git status --porcelain          # must be empty
python3 scripts/check-version-bumps.py --base origin/main
```

Stage the manifests explicitly in Step 7 — they sit outside the skill directory, so
`git add plugins/<plugin>/skills/<skill>/` leaves them behind.

**Version is the single source of truth for the footer segment.** When you bump `metadata.version`,
also update any **footer literal** the skill hardcodes in its own files so it stays in
lockstep — the `Tools/Slack` attribution footer renders `<plugin>:<skill>:<version>` (see
`/CLAUDE.md` → "Skill versioning" and "Slack post conventions"). Grep the skill for its own
id after the bump:

```bash
grep -rn "Generated by \`$plugin:$skill" "$SKILL_DIR"   # footer literals in SKILL.md / scripts
```

Update each hit's version to match the new frontmatter value. (Scripts that read their
version from the sibling `SKILL.md` at runtime — e.g. `report-bugsnag-errors/scripts/format.py`
— need no edit; they follow the frontmatter automatically.)
