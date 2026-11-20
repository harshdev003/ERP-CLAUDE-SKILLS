---
name: sync-translations
description: >
  Extract new i18n keys from an Aura product repo and fill them across all locales using
  Claude with brand-voice context. Use when the user says "sync translations in UserWeb",
  "fill missing translations for CoachWeb", "add new i18n keys and translate them in Aura",
  "translate untranslated keys", or pastes one of the AuraHealth repo SSH URLs (UserWeb,
  CoachWeb, Aura) and asks to update locales. Do NOT use for: AuraConsumerApp (no i18n
  today), adding a brand-new locale code (manual setup needed), or translating Notion pages
  / marketing copy (use the Notion-aware skills).
metadata:
  version: "3.0.4"
knowledge:
  reads:
    - Guide/Index               # baseline routing aid (universal)
    - Products/Aurie       # brand voice for translations
    - Tools/GitHub                   # gh CLI auth + PR conventions
    - Procedures/Engineering/Locales # canonical locale codes and conversions
---

# Sync Translations

Extract new translation keys from an Aura product repo, fill English defaults where missing, then translate the gaps into all six non-English locales (`es`, `fr`, `de`, `ja`, `ko`, `pt_BR`) using Claude with Aurie brand voice. Opens one PR per invocation.

## Goal

Eliminate translation drift between the source-of-truth English JSONs and the six other locales in one product repo, in a single PR, without overwriting any existing non-empty translation.

## Inputs

- **Target repo** — one of `UserWeb`, `CoachWeb`, `Aura` (the React Native repo). Accept a name, a local path, or an SSH URL (`git@github.com:AuraHealth/<Name>.git`). **Reject `AuraConsumerApp`** — strings are hardcoded in Swift, no i18n today.
- **Optional Linear ticket** — used in branch name and commit message.
- **Optional `--force-retranslate`** — re-translate keys that already have a non-empty value. Off by default.

## Repos this skill knows about

| Name | SSH | Stack | Locales dir | Source glob | Scanner |
|---|---|---|---|---|---|
| `UserWeb` | `git@github.com:AuraHealth/UserWeb.git` | Next.js | `public/locales/{locale}/*.json` | `src/**/*.{js,jsx,ts,tsx}` | inline (this skill) |
| `CoachWeb` | `git@github.com:AuraHealth/CoachWeb.git` | Next.js | `public/locales/{locale}/*.json` | `src/**/*.{js,jsx,ts,tsx}` | inline (this skill) |
| `Aura` | `git@github.com:AuraHealth/Aura.git` | React Native | `src/localization/{locale}/*.json` | `src/**/*.{js,jsx}` + `app.js` | `i18next-scanner` (already in repo root) |
| `AuraConsumerApp` | `git@github.com:AuraHealth/AuraConsumerApp.git` | Native iOS | — | — | **out of scope** |

Locales (identical across the three i18n repos): `en, es, fr, de, ja, ko, pt_BR`. English is the source of truth in all three. Read the locale list from disk at runtime — do not hardcode. Codes, URL forms and conversion rules are canonical in `Procedures/Engineering/Locales`: folders and keys always use `pt_BR`, never `pt-BR` or `pt-br`.

**Conventions (identical across all three):** flat keys, snake_case, `{{mustache}}` interpolation, 2-space JSON indent, trailing `\n`. Aura RN marks unscanned strings with `__STRING_NOT_TRANSLATED__` — treat that as missing, not as content.

## Workflow

### 1. Resolve the target repo

Map the user's input to a local clone. Search order (first hit wins):

1. `~/<Name>`
2. `~/code/<Name>`, `~/repos/<Name>`, `~/src/<Name>`, `~/dev/<Name>`, `~/projects/<Name>`
3. `/Volumes/HardDrive/<Name>` and `/Volumes/HardDrive/Aura/<Name>`

If not cloned, ask the user to clone via the SSH URL. Store the confirmed path as `REPO_DIR`.

If the user names `AuraConsumerApp`, stop and tell them: *"AuraConsumerApp has no i18n today (strings hardcoded in Swift). This skill doesn't support it yet — adding native iOS localization is a separate, heavier project."*

### 2. Create an isolated worktree off latest main

All work happens in a worktree off fresh `origin`, so the shared checkout (and any
uncommitted state in it) is never touched — **no stash dance needed.** Worktrees live
**inside** the repo at `<repo>/.claude/worktrees/<slug>` (gitignored), **never** as a
sibling like `../UserWeb-translations` — a sibling clutters the parent directory and isn't
where our tooling looks. See [`/CLAUDE.md`](../../../../CLAUDE.md) → "PR workflow".

```bash
MAIN="$REPO_DIR"
git -C "$MAIN" fetch origin --quiet
DEFAULT_BRANCH=$(git -C "$MAIN" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null | sed 's|^origin/||')
DEFAULT_BRANCH=${DEFAULT_BRANCH:-main}
BRANCH="<firstname>/translations-sync-$(date +%Y-%m-%d)"
# Or with a Linear ticket: BRANCH="<firstname>/PROD-1234-translations"
WT="$MAIN/.claude/worktrees/$(basename "$BRANCH")"
git -C "$MAIN" worktree add "$WT" -b "$BRANCH" "origin/$DEFAULT_BRANCH"

# The i18next-scanner + any repo i18n tooling need node_modules; symlink the main
# checkout's copy so the worktree works with no reinstall (fall back to `npm ci` in $WT).
if [ -d "$MAIN/node_modules" ] && [ ! -e "$WT/node_modules" ]; then
  ln -s "$MAIN/node_modules" "$WT/node_modules"
fi

REPO_DIR="$WT"   # re-point: every $REPO_DIR below now operates inside the worktree
```

### 3. Detect repo type

Check which i18n config exists:

```bash
[ -f "$REPO_DIR/next-i18next.config.js" ]    # → web (UserWeb / CoachWeb)
[ -f "$REPO_DIR/i18next-scanner.config.js" ] # → rn (Aura)
```

For web repos, locales live under `public/locales/<locale>/`. For RN, under `src/localization/<locale>/`. Refuse to proceed if neither config is present.

### 4. Extract new keys from source

**Aura RN** — reuse the existing scanner config:

```bash
cd "$REPO_DIR"
npx --yes i18next-scanner --config i18next-scanner.config.js
# Writes per-namespace JSONs to i18n/<lng>/<ns>.json — diff against src/localization/en/*.json next.
```

**UserWeb / CoachWeb** — no scanner config exists. Run an inline regex scan with Bash + Grep:

```bash
# Find all t('key') / t("key") / t(`key`) and <Trans i18nKey="key">
cd "$REPO_DIR"
grep -REho --include="*.js" --include="*.jsx" --include="*.ts" --include="*.tsx" \
  -e "[^A-Za-z0-9_\$\.]t\s*\(\s*['\"\`]([A-Za-z0-9_]+)['\"\`]" \
  -e "<Trans[^>]*i18nKey=['\"]([A-Za-z0-9_]+)['\"]" \
  src/ | sort -u
```

Parse the matches into `{key, file}` records. Namespace inference, in order:

1. **Explicit `<Trans ns="x" i18nKey="...">`** — use `x`.
2. **Same file contains `useTranslation('x')`** — use `x`.
3. **File path matches a route in `src/services/I18N.js`** (parse the `routeNamespaceMap` literal). UserWeb/CoachWeb convention: `src/pages/foo/bar.js` and `src/components/page/foo/bar/index.js` both map to route `/foo/bar`.
4. **Fallback** — `common`.

### 5. Diff against locale files

Two buckets to compute:

- **`new_in_en`** — keys in source code but absent from **every** `en/*.json`. Cross-check across all English namespaces, not just the inferred one — the inference is best-effort, so a `common` miss may already exist in `signup`. These need an English default authored.
- **`missing_per_locale`** — keys present in `en/<ns>.json` but missing or empty (or equal to `__STRING_NOT_TRANSLATED__`) in each non-English locale. These need translation only.

```bash
# Read every en/<ns>.json and every <locale>/<ns>.json with Read, then compute the diff.
# For each en namespace's keys, check the same-namespace JSON for each other locale.
# A key is "missing" if absent or value is "" or value == "__STRING_NOT_TRANSLATED__".
```

### 6. Show the plan and wait for approval

This is the only chance to abort cleanly. Print and stop:

```
<RepoName> — sync-translations plan
  New English keys to author:    23 (signup: 15, common: 6, refer: 2)
  Existing-key gaps to fill:    138 across 6 locales × 12 namespaces
  Estimated Claude calls:       ~24 (≤30 keys per batch)
  Branch:                       devashish/translations-sync-2026-05-01
Proceed? [y/N]
```

Do NOT call Claude or write files until the user types `y` / `proceed`. Required by AINative best-practice "plan before side-effects on multi-step work."

### 7. Author English defaults for new keys (LLM)

For each key in `new_in_en`, ask Claude to draft an English string from the surrounding source code (use Read on the file at the recorded line). Show the proposed key → string mapping to the user, accept inline edits, then write to `en/<ns>.json` in Step 9. Skip if `new_in_en` is empty.

### 8. Translate to other locales (LLM)

Pre-fetch `Products/Aurie` once via `retrieve-knowledge` (the `knowledge.reads` declaration handles this). Reuse the brand voice across every translation call.

**Batching.** Group keys by `(locale, namespace)`. Cap each request at **30 keys** per Claude call. For each batch, send Claude:

- The Aurie brand voice context.
- The English source strings (key → en value).
- The target locale code.
- The per-locale notes from the table below.
- The hard rules.

**Hard rules (universal):**

1. Preserve every `{{placeholder}}` exactly — same name, same braces. Do NOT translate text inside `{{...}}`.
2. Preserve inline tags `<br>`, `<strong>`, `<i>`, `<p>` verbatim; translate only the surrounding text.
3. Tone: warm, calming, second-person, concise — Aurie's brand voice.
4. Button labels stay short (≤4 words).
5. If source is empty or `__STRING_NOT_TRANSLATED__`, return it unchanged.
6. Never add or drop keys. Output exactly the same keys as input.

**Output format requested from Claude:**

```
Return ONLY a JSON object: { "<key>": "<translation>", ... }
No prose, no markdown fences, no commentary.
```

**Per-locale notes (append to the prompt for the target locale):**

| Locale | Notes |
|---|---|
| `es` | Neutral / Latin American Spanish. Use `tú` (warm, second-person). Avoid `vosotros`. Keep wellness terms in established forms (`meditación`, `respiración consciente`). |
| `fr` | Use formal `vous`. French typographic conventions: non-breaking spaces before `: ; ! ?` when natural; `« »` quotes. Don't translate `Aura` or `Aurie`. |
| `de` | Use formal `Sie`. Capitalize all nouns. Break compound words if a compound exceeds ~20 chars. Don't translate `Aura` or `Aurie`. |
| `ja` | Polite form (です／ます調). Render product nouns in katakana (`アウラ`, `オウリー`). Wellness loanwords stay katakana (`マインドフルネス`, `メディテーション`). Half-width digits and `{{placeholders}}`. |
| `ko` | Polite form (`-요` / `-습니다`). Hangul for product nouns (`아우라`, `오리`). `마음챙김` (mindfulness), `명상` (meditation). Preserve word-boundary spacing. |
| `pt_BR` | Brazilian Portuguese — never `tu` or European forms. Use `você`, `celular`, `tela`. Keep accents and cedillas. Don't translate `Aura` or `Aurie`. |

**Validation per output (run before writing):**

- Output is valid JSON, exact key set, every value a non-empty string.
- For each key, the set of `{{placeholder}}` tokens in the translation equals the set in the English source.
- If a batch fails, retry the failing keys once with a stricter "you returned X but rule Y requires Z" message. If still failing, drop those keys from the write step and list them in the PR body's "Needs human review" section.

### 9. Write JSONs

Write each locale's namespace file with these invariants:

- 2-space indent, UTF-8, trailing `\n`.
- **Preserve existing key order** — existing keys first in their original order, new keys appended at the end.
- **Never overwrite** an existing non-empty translation unless `--force-retranslate` was passed.
- **Reject** any translation for a key not present in `en/<ns>.json` (after Step 7's English authoring) — surface as "no-english-source" in "Needs human review".
- **Reject** any translation whose `{{placeholder}}` set differs from the English source — surface as "placeholder-mismatch".

Use the Read tool to load existing JSONs, merge in memory, then Write to overwrite.

### 10. Commit, push, open PR

```bash
cd "$REPO_DIR"
# Stage only the locales dir for this repo
git add public/locales/ 2>/dev/null   # web
git add src/localization/ 2>/dev/null # rn
git commit -m "Sync translations: <RepoName> — <N> new keys, <M> locale fills [<TICKET-IF-ANY>]"
git push -u origin "$BRANCH"
# Write the PR body to a file first — never the command-substitution heredoc
# form `--body "$(cat <<'EOF' … )"`, which mis-parses on apostrophes/backticks/em-dashes.
cat > /tmp/pr-body-translations.md <<'EOF'
## Summary
- Extracted <N> new keys from <RepoName> source code.
- Filled <M> missing translations across <K> locales using Claude (Aurie brand voice).
- Existing translations untouched.

## Per-locale × per-namespace counts
<table from the diff>

## Needs human review
<list any keys where translation validation failed; "(none)" if all good>

## Test plan
- [ ] Spot-check 5 random translated keys per locale for tone + interpolation correctness
- [ ] Build the app locally, switch language, confirm no missing-key fallbacks on touched screens
- [ ] Verify `{{placeholder}}` variables render correctly in 2-3 interpolated strings
EOF
gh pr create \
  --title "Sync translations: <RepoName> — <N> new keys, <M> locale fills" \
  --body-file /tmp/pr-body-translations.md
```

### 11. Confirm and summarize

Print:
- PR URL.
- Branch name.
- Per-namespace counts of files touched.
- Any keys flagged "Needs human review".

Once the PR merges, the worktree can be removed (its `node_modules` symlink goes with it;
the main checkout's real `node_modules` is untouched):

```bash
git -C "$MAIN" worktree remove "$WT"   # or run eng:clean-up-worktrees later
```

## Output

A single GitHub PR in the target repo. No writes to AI Knowledge.

## Edge cases

- **Already in sync** (no new keys, no missing translations) → "Already in sync, nothing to do." Remove the worktree (`git -C "$MAIN" worktree remove "$WT"`) and exit. Idempotent re-runs are no-ops.
- **Repo not cloned locally** → ask the user to clone via SSH; do not auto-clone.
- **Working tree dirty on `main`** → irrelevant: all work happens in an isolated worktree, so the shared checkout's uncommitted state is never touched. No stash needed.
- **`gh` CLI not authenticated** → redirect to the `setup-ainative` skill, then retry.
- **AuraConsumerApp passed in** → refuse with the message in Step 1.
- **New locale code requested** (e.g. `it`, `zh`) → out of scope v1; needs manual scaffolding across all three repos first.
- **Translation validation fails twice** → leave the key untranslated and list it under "Needs human review". Don't block the PR.
