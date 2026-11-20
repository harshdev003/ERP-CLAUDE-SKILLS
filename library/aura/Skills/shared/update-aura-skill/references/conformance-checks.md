# Conformance checks

Run these against the skill's **current** state in Step 3, before proposing any edit.
Each one reports into the Step 3 summary, and anything flagged is proposed as part of
the same update unless the caller opts out.

These are the checks that a skill passes silently until the day it doesn't: a manifest
missing its baseline read, a GNU-only flag that misbehaves on macOS, a heredoc that
mis-parses on an apostrophe. None of them surface at authoring time, so they are run
mechanically rather than remembered.

**Baseline manifest check.** Before showing the summary, verify the skill's `knowledge.reads` includes `Guide/Index` — every skill must declare it as the universal baseline (see `/CLAUDE.md` → "Knowledge manifest" → "Baseline read"). It's missing if any of these is true:

- The frontmatter has no `knowledge:` block at all
- `knowledge.reads` exists but doesn't list `Guide/Index`
- `knowledge.reads` is `["*"]` only — wildcard alone doesn't satisfy the baseline; `Guide/Index` must be listed alongside `"*"`

If missing, flag it in the summary and propose adding it as part of this update:

> ⚠️ This skill's manifest is missing the baseline `Guide/Index` read. I'll add it as part of this update unless you'd rather skip.

Either way, when you apply changes in Step 5, ensure the final SKILL.md includes `Guide/Index` in `knowledge.reads` (unless the user explicitly opted out).

**Version presence check.** Verify the frontmatter carries `metadata.version` — a `version:` key inside the `metadata:` map, per the Agent Skills spec (order: `name` → `description` → `metadata` → `knowledge`; see `/CLAUDE.md` → "Skill versioning"). If it's missing — a legacy or externally-authored skill predating the convention — flag it and seed it as part of this update, then apply the normal bump (Step 4b) on top of it for the change you're making:

```yaml
metadata:
  version: "1.0.0"
```

> ⚠️ This skill has no `metadata.version`. I'll seed it at `"1.0.0"` and bump per the change below.

**UTF-8 stdout check (Python scripts).** For each `scripts/*.py` file under the skill, check whether the top of `main()` (or the module top-level if there's no `main()`) calls `sys.stdout.reconfigure(encoding="utf-8")`. The check is missing if the file prints to stdout and there's no `reconfigure(encoding="utf-8")` anywhere before the first `print(`. Quick grep:

```bash
for f in "$SKILL_DIR"/scripts/*.py; do
  if grep -q "print(" "$f" && ! grep -q 'reconfigure(encoding="utf-8")' "$f"; then
    echo "MISSING: $f"
  fi
done
```

If anything is `MISSING`, flag it in the summary and propose adding it as part of this update:

> ⚠️ `scripts/<file>.py` prints to stdout but doesn't force UTF-8 — fails on Windows consoles (`cp1252`) when non-ASCII (`→`, `—`, emoji) is emitted. I'll add the guard as part of this update unless you'd rather skip.

When you apply changes in Step 5, add the two-line guard at the top of `main()` (before argparse, before any output) — see `references/best-practices.md` → "Cross-platform: UTF-8 stdout for Python scripts".

**GCP auth check (`gcloud` calls).** Anything a skill does with GCP runs as the `GCP_KEY` service account; the user's gcloud login is the fallback only when `GCP_KEY` is unset (CLAUDE.md → *GCP: the `GCP_KEY` service account*). Every `gcloud` call goes through the skill's own `scripts/gcloud_sa.py`, a byte-identical copy of the canonical named in `docs/canonical-sources.md`. A script calls `gcloud_sa.run([...])`; a SKILL.md command runs `python3 ${CLAUDE_PLUGIN_ROOT}/skills/<skill>/scripts/gcloud_sa.py <args>`. The repo gate finds the misses:

```bash
python3 -m unittest tests.test_gcloud_gate -q
```

If it names a file in this skill, flag it and propose the fix as part of this update: copy the canonical `gcloud_sa.py` into `scripts/` and route the call through it. A fenced block that must run as a person (granting the service account a role, creating it) instead starts with a `# user-gcloud: <why>` line.

> ⚠️ `<file>:<line>` runs `gcloud` on whichever account is active, usually your login, which lapses under Google's reauth policy and silently breaks unattended runs. I'll route it through `scripts/gcloud_sa.py` (the `GCP_KEY` service account) unless you'd rather skip.

**Folder-shape check (spec conformance).** The spec allows only `SKILL.md` plus the `scripts/`, `references/`, `assets/` subdirectories beside it. Flag any other top-level entry (a root `README.md`, a `prompts/` folder, a committed `outputs/` dir, stray notes):

```bash
ls -1 "$SKILL_DIR" | grep -vxE 'SKILL\.md|scripts|references|assets' || true
```

Any line printed is non-standard. Propose the fix in the summary — move a useful `README.md` to `references/README.md`, relocate other docs into `references/`, or gitignore run-artifact dirs:

> ⚠️ `<name>` is a non-standard top-level entry (spec allows only `SKILL.md` + `scripts/`/`references/`/`assets/`). I'll move it into `references/` (or gitignore it) as part of this update unless you'd rather skip.

**Filename-casing check.** The file must be `SKILL.md` exactly — a lowercase `skill.md` breaks skill loading on case-sensitive filesystems/CI. Confirm via git (case-insensitive macOS `ls` hides this):

```bash
git -C "$REPO_DIR" ls-files -- "$SKILL_DIR" | grep -E '/skill\.md$' || true
```

Any hit is a real violation — propose renaming with a two-step case-only `git mv` (`skill.md` → `SKILL.md.tmp` → `SKILL.md`) and fixing any `../<skill>/skill.md` cross-links.

**Portability check (no hardcoded personal paths).** Grep the skill for machine-specific absolute paths — someone's home dir or a pinned interpreter — which break on every other machine:

```bash
grep -rnE '/Users/[^/]+/|/Library/Frameworks/Python' "$SKILL_DIR" || true
```

Any hit (outside an illustrative example) is a portability bug. Propose replacing it with dynamic discovery (Glob / ask the user) or a bare `python3`:

> ⚠️ `<file>` hardcodes a machine-specific path (`<path>`). I'll replace it with dynamic discovery as part of this update unless you'd rather skip.

**Shell portability check (no unguarded GNU-isms).** Skill scripts run on macOS (BSD userland) *and* Linux/CI, so a GNU-only flag silently misbehaves on macOS — e.g. `stat -c %Y` errors on BSD `stat`, and a `|| echo 0` fallback then reads mtime as 0 (observed making a clone-cache never hit). Grep for the common offenders:

```bash
# Drop lines that already pair the GNU flag with a BSD alternate or a
# macOS/Linux guard marker — the portable `stat -f … || stat -c` fallback and
# the `# macOS: … # Linux: …` form (as in clean-up-worktrees) legitimately
# contain the GNU token and must not be flagged.
grep -rnE '\bstat +-c|\bdate +(-d |--date)|\breadlink +-f|\bsed +-r|\bgrep +-P|\bxargs +(-r|-d)' "$SKILL_DIR" \
  | grep -vE 'Linux:|macOS|stat -f|date -v' || true
```

Any surviving hit — a GNU-only flag with no BSD alternate/guard (the filter drops the portable and macOS-guarded forms; eyeball the rest) — is a portability bug. Propose the portable form — try the BSD flag first with a GNU fallback:

**`sed -i` is a manual check** (a regex can't reliably classify it): `sed -i` is **not cross-portable** — BSD/macOS wants a separate backup-suffix arg (`sed -i '' 's/…/'`), while on GNU that same `''` is read as the (empty) *script* and the real script becomes a filename → error. Eyeball any `grep -rn "sed -i" "$SKILL_DIR"` hit and rewrite it to something portable: write to a temp file + `mv` (`sed 's/…/' f > f.tmp && mv f.tmp f`), or `perl -i -pe`, rather than trying to make `sed -i` work on both.

> ⚠️ `<file>` uses the GNU-only `<flag>` (breaks on macOS). I'll make it portable (e.g. `stat -f %m 2>/dev/null || stat -c %Y`) as part of this update unless you'd rather skip.

**Body-authoring check (no command-substitution heredocs).** A PR / commit / Notion / Slack body built with a heredoc **nested inside command substitution** (`--body "$(cat <<'EOF' … )"`) or process substitution (`--body-file <(cat <<'EOF' … )`) mis-parses when the body contains apostrophes, backticks, or em-dashes — the "heredoc apostrophe curse" (`/CLAUDE.md` → PR workflow → "PR body sourcing"). Grep for it:

```bash
# Drop backtick-wrapped lines — prose/comments that *document* the anti-pattern
# (including this very check, best-practices.md, and the offender skills' warnings)
# write it inside inline code; a real `gh` invocation never has a backtick on the line.
grep -rnE '(--body[[:space:]]+"\$\(cat[[:space:]]+<<|--body-file[[:space:]]+<\(cat[[:space:]]+<<)' "$SKILL_DIR" | grep -v '`' || true
```

Any hit **in an actual command invocation** — not a prose/backtick example documenting the anti-pattern (the backtick filter drops most of those; eyeball the rest) — is a body-authoring bug. Propose rewriting it to write the body to a file first — a plain `cat > /tmp/pr-body.md <<'EOF' … EOF` redirect (not nested in `$()`/`<()`) or the Write tool — then pass `--body-file`:

> ⚠️ `<file>` builds a PR/commit body with a command-substitution heredoc (`--body "$(cat <<'EOF' … )"`), which breaks on apostrophes/backticks/em-dashes. I'll rewrite it to write the body to a temp file + `--body-file` unless you'd rather skip.

**`Tools/*` manifest check.** Grep the skill's `SKILL.md`, `scripts/`, and `references/` for known external-service markers and compare against the declared `knowledge.reads`. A skill that talks to an external service must declare the corresponding `Tools/<service>` read (`CLAUDE.md` → "Knowledge manifest" → "Tools section"). Detection map (same one used by `create-aura-skill` Step 2.4a — keep in sync):

| Markers found in skill content | Required read |
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

Quick grep (run per service against the whole skill dir):

```bash
# Example: detect Slack usage that isn't declared
if grep -riE "slack\.com|SLACK_BOT_TOKEN|chat\.postMessage|\bslack\b" "$SKILL_DIR" >/dev/null \
   && ! grep -q "Tools/Slack" "$SKILL_DIR/SKILL.md"; then
  echo "MISSING: Tools/Slack (skill references Slack but doesn't declare the read)"
fi
```

Run the equivalent check for each row in the detection table. Collect the set of missing reads.

If any are missing, flag them in the summary and propose adding them as part of this update:

> ⚠️ This skill references **Slack** (in `scripts/post.py`) and **Linear** (in `SKILL.md`), but `knowledge.reads` declares neither `Tools/Slack` nor `Tools/Linear`. I'll add them as part of this update unless you'd rather skip — say which, if any, are false positives (e.g. the word appears in a comment but the skill doesn't actually call the service).

When you apply changes in Step 5, append every confirmed missing `Tools/<service>` read to `knowledge.reads` (alongside `Guide/Index`). If a `Tools/<service>` page doesn't exist yet in AI Knowledge for one of the additions, follow best-practices item 10 ("Document new external dependencies in the same change") — flag it and offer to draft the stub now or after the skill PR.
