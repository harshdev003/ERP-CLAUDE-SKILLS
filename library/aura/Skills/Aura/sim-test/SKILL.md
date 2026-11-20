---
name: sim-test
description: Autonomous AI-driven simulator testing — navigate any app flow on a real simulator using goal-driven AI that analyzes the screen hierarchy at each step. Supports goal-based testing (onboarding, login, delete account, custom) and multiple analysis modes (ad-hoc exploration, full exploration, UI analysis, UX analysis, copy analysis, test generation). Captures screenshots and video. Use when the user wants to test, demo, explore, or audit any feature with real Firebase auth and API calls.
---

# Autonomous Simulator Testing

## Overview

This skill runs **autonomous, AI-driven tests** on a real iOS Simulator. Claude analyzes the live accessibility hierarchy at each step and decides what to do next.

**Two axes** — pick the mode and (optionally) enable analyzer workers:

### Modes (how the test navigates)

- **`goal`** (default) — navigate toward a specific target, stop when achieved
  - Onboarding — create a new account through full onboarding to home
  - Login and Delete Account — log in, navigate to settings, delete the account
  - Custom Goal — describe any specific objective
- **`explore`** — systematically visit every screen and every element, report findings.
  For "explore after login", set `mode: "explore"` and write a goal that
  describes the starting point (e.g., `"Log in with existing credentials, then
  explore every screen"`). The main agent performs login as the first phase,
  then switches to systematic exploration. **No separate mixed-mode is needed.**

### Analyzers (reviewers that run alongside the test)

Analyzers are **opt-in** workers that review the app on every screen change
(first visit per session only). Any combination can be enabled. By default
no analyzers run.

- **`ui`** — layout, touch targets (≥44pt), overlaps, alignment, safe area
- **`ux`** — flow, discoverability, dead-ends, unconfirmed destructive actions, form friction
- **`copy`** — typos, tone, brand (`Aura` vs `Aurie`), consistency, placeholder text
- **`logs`** — errors, warnings, exceptions, crashes in `app.log` (delta-only)
- **`ideas`** — growth/retention/conversion proposals (features, friction, UX, copy, icons, navigation, paywall). Writes per-session `findings_ideas.json`. After the run, ingest into the `Learnings/Ideas/Aura` Notion database via the AINative skill `product:record-ideas` — review / approve / reject lives in Notion (see `product:manage-ideas`).
- **`i18n`** — English leakage in non-English builds, raw i18next keys, uninterpolated placeholders, clipping/overflow from longer translated strings. Pair with `--locale <de|es|fr|ja|ko|pt_BR>` for full value. Does NOT flag features that are English-only by design (see `.claude/rules/localization.md § English-only features`).

Config:
```json
"analyzers": { "ui": true, "ux": true, "copy": false, "logs": true, "ideas": true, "i18n": false }
```

How they run:

1. Swift side detects a first-time screen visit, emits `state.analyzerTrigger`.
2. Main agent reads the trigger, invokes `sim_bridge.py analyze <name>` for each
   enabled analyzer — script prints a focused input bundle.
3. Main agent spawns a sub-agent per analyzer (via `Agent` tool) with the
   bundle + prompt template from `Scripts/analyzers/prompts/{name}.md`.
4. Sub-agent reviews, writes `findings_{name}.json`, exits.
5. End of run: `sim_bridge.py report` merges findings into `findings.md`.

Uses a **file-based observe → plan → execute loop** between Claude and XCTest (`AdHocSimTest`):

1. XCTest observes the screen → writes compact `state.json`
2. Claude reads `state.json` + screenshot, plans the next action
3. Claude writes `action.json` (one flat action + optional finding)
4. XCTest validates and executes → repeats

Works with **real services** (Firebase auth, real API, real SDKs) — not mocks.

## Bundled Resources

| File                             | When to read                                                                                         |
| -------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `references/actions.md`          | During command loop — action vocabulary, findings format, explore mode strategy, crash/loop handling |
| `references/screen-guide.md`     | During command loop — screen-specific navigation rules (QP, feed cards, settings, etc.)              |
| `references/analyzers.md`        | When any analyzers are enabled (ui/ux/copy/logs/ideas/i18n) — checklists, severity levels, sub-agent orchestration |
| `references/parallel-testing.md` | When running multiple sessions — agent spawning, session isolation, device combinations              |
| `references/personas.md`         | Before onboarding — persona pool, quiz answer mapping, personality-driven responses                  |
| `references/report-templates.md` | After test completes — report format for each mode                                                   |

## Usage Examples

**Goals:**

```
Run a sim test — onboard a new user
Sim test: login and explore the app
Sim test: delete account flow, record video
Run a sim test — complete all 4 feed cards on home
```

**Modes:**

```
Explore the entire app on the simulator — find bugs and UX issues
Audit the app UI — find layout issues, overlapping elements, missing labels
Run a UX analysis — evaluate flows, discoverability, and user experience
```

## Prerequisites

- Simulator booted (`iPhone 17 Pro` preferred)
- Node + CocoaPods installed (`nvm use` → `npm install --legacy-peer-deps` → `cd ios && pod install`)
- Project buildable from `ios/Aura.xcworkspace` (NOT `.xcodeproj` — Pods are linked via the workspace)
- `AuraSimTests` UI test target present in `ios/Aura.xcodeproj` with a shared scheme
- The app is built in **Release configuration** so the JS bundle is embedded in `Aura.app` — sim tests do **not** need Metro running

## Permission Pre-Approval

All commands go through scripts — no raw `xcrun`, `xcodebuild`, `kill`, or `rm` allowed.

**Scripts** (`AuraSimTests/Scripts/`):

| Script            | Purpose                                                          |
| ----------------- | ---------------------------------------------------------------- |
| `sim_start.sh`    | Boot simulator (optionally reset first)                          |
| `sim_shutdown.sh` | Shut down simulator to free resources                            |
| `sim_install.sh`  | Build app + test target, install on simulator                    |
| `sim_test.sh`     | Write session config + launch XCTest runner                      |
| `sim_common.sh`   | Shared constants and helpers (sourced by other scripts)          |
| `sim_bridge.py`   | Command loop: wait/act/read/screen/hierarchy (one call per step) |
| `sim_cleanup.sh`  | Clean session files, TestResults, DerivedData                    |
| `sim_kill.sh`     | Kill test or recording processes                                 |
| `sim_record.sh`   | Start/stop video recording (saves to session dir with --session) |
| `sim_mcp.py`      | MCP server (wraps bridge for native tool integration)            |

## Workflow

**Runner strategy:**

- **Single test run** → run the command loop directly in the main conversation. Report progress at each step.
- **Multiple parallel runs** → spawn one agent per session. Read `references/parallel-testing.md` for setup, agent spawning, and session isolation details.
- **Always use session IDs** (simulator UDID).
- **MANDATORY: Keep agent prompts CONCISE.** Inline all rules, quiz answers, and flow steps directly. NEVER tell agents to read knowledge/, actions.md, or screen-guide.md — reading large files exhausts context and agents die mid-flow. **Always inline the screenshot-on-confusion rule** (below) — it's the single most important safety behavior.
- **MANDATORY: Minimize token usage everywhere.** Use compact mode (default), avoid screenshots unless stuck, use `tail`/`grep` for large agent output files (never read full JSONL), shut down simulators immediately on goal achieved. Keep action `reasoning` strings short (3-6 words): `"Tap Get Started"` not `"Tapping the Get Started button to begin the onboarding flow"`.
- **MANDATORY: Screenshot on confusion — overrides "avoid screenshots".** The moment ANY of the following happens: (a) `state.step` didn't advance after an action, (b) `loopGuard.sameActionFailureCount >= 2` (consecutive identical action keys that all FAILED — distinct from `sameActionCount`, which also counts successful repetitions like vibe-check cards or quiz questions), (c) `lastAction.success == false`, (d) bridge times out, (e) `state.hint` is set — **take a screenshot BEFORE retrying anything**. Use `sim_bridge.py --verbose screen 30` and Read the PNG. The XCTest runner may have died, the target element may not exist on the current screen, or the screen may have silently changed. **Blind retries burn context and destroy runs.** The "avoid screenshots" rule applies only when things are progressing normally; "stuck = screenshot" always wins.
- **MANDATORY: Verify target exists BEFORE tapping — never assume an ID.** Screens change across experiments, builds, and flow variants. Always `read` state first; only tap IDs/labels actually present in `state.elements`. Tapping a non-existent target can silently crash the app or kick the user back to the iOS home screen, killing the test runner. If an expected target is missing, stop, screenshot, and adapt — do not retry the same tap.
- **Chain agents carefully — NEVER during onboarding.** Agent inference slows as context accumulates. Chaining is safe when the user is already logged in (the continuation agent can log back in). **NEVER chain agents during onboarding, sign-up, or any pre-login flow** — restarting loses all progress (account creation, quiz answers, etc.) and the test starts over from scratch. Onboarding flows must complete in a single agent run (typically 30-45 steps). For post-login explore/analysis runs over ~50 steps, spawn a fresh continuation agent with: current step number, session ID, email/password, progress summary, remaining goals, and all rules inlined. **Compress history** in continuation prompts — use `"Completed exploration steps 1-50, visited 12 screens"` instead of replaying the full step log.

**Follow these steps in order. Do not skip steps.**

### Step 0: Load knowledge

Read only the relevant file(s) under `AuraSimTests/knowledge/`:

- `rules.md` — global rules, safety rules, company context, experiment API
- `flows.md` — onboarding, login, delete account, QP, feed cards
- `workarounds.md` — known bugs and platform-specific quirks
- `screens/<name>.md` — per-screen identifiers and tips (see `screens/README.md` for the index)

Load only what you need for the current flow to keep the context small.

**Before an ideation run, confirm the experiment catalogue is current:**

```bash
python3 AuraSimTests/Scripts/sim_experiments.py check
```

`experiments.md` tells the analyzer which screens exist for whom. A stale entry produces a
proposal against a surface that was switched off; a missing one hides a live experiment.
The check is offline and takes milliseconds. It also reports gates that are read but never
assigned — permanently-false branches that read like live experiments.

### Headless / CI Mode

Headless mode is active when ANY of these are true:
- Environment variable `SIM_TEST_HEADLESS=true` is set
- The prompt contains "headless" or "HEADLESS"
- The prompt says "do not ask" or "no confirmation"

In headless mode:

- **Skip Step 1** (wizard) — determine the flow and mode from the prompt/context automatically
- **Skip Step 2** (confirmation) — proceed directly without asking "Proceed?"
- **Auto-generate persona** for onboarding flows (random from pool)
- **Auto-decide strategy** based on changed files or prompt instructions
- **Never use AskUserQuestion** — make all decisions autonomously
- **Skip Steps 9, 10, 10b** (knowledge / users.json / simulators.json updates) — CI workspaces are ephemeral (knowledge and simulators.json are tracked; users.json is local-only), so parallel runs would race and changes won't propagate back. Report findings in the summary instead.

### Step 1: Ask the user what to test (3-step wizard)

**Skip this step in headless/CI mode — decide automatically from prompt context.**

**Step 1a — What flow or feature?**

> 1. **Onboarding** — new account creation through full onboarding to home
> 2. **Login and Explore** — log in with existing credentials, explore the app
> 3. **Login and Delete Account** — log in, navigate to settings, delete account
> 4. **Custom** — describe a specific flow or feature

**Step 1b — Which mode?**

> 1. **Goal** — navigate toward the target, stop when achieved (default)
> 2. **Explore** — systematically visit every screen and every element

**Step 1c — Which analyzers (optional)?**

Pick any combination (or none — default). These run on screen change and
write findings to `findings_{analyzer}.json` without interfering with the
main test.

> - **UI** — layout, touch targets, overlaps, alignment
> - **UX** — flow, discoverability, dead-ends, friction
> - **Copy** — typos, tone, brand voice, consistency across translated strings (i18next keys)
> - **Logs** — errors, warnings, exceptions in `app.log`
> - **i18n** — English leakage, raw keys, uninterpolated placeholders, overflow from longer translations (pair with `--locale <de|es|fr|ja|ko|pt_BR>`)

Common combinations:

- No analyzers (fastest, cheapest) — default
- `logs` only — cheap CI smoke check
- `ui + copy` during onboarding — visual + text audit
- `i18n` with `--locale fr` + `--mode explore` — full localization audit pass
- All analyzers — full audit pass

### Step 1c: Generate persona (new-user flows only)

**MANDATORY for onboarding / new account flows.** Read `references/personas.md` for the persona pool.

- **Default:** Randomly select a persona from the pool (or generate a new one). Vary across runs — do not reuse the same persona consecutively.
- **User overrides:** If the user specifies traits (e.g., "depressive user", "older user", "anxious student"), select or generate a persona matching those traits. Override only the specified fields — randomize the rest.
- **Store immediately** in `users.json` with the persona object when the account is created (Step 10).
- **Use during onboarding:** The persona's `quizAnswers` map directly to `onboarding_quizOption_{index}` taps. The `firstName` is typed into the name field. The `personality` field guides text input style during Quick Processing and conversations.

### Step 2: Confirm config with the user

**Skip this step in headless/CI mode — proceed directly.**

**MANDATORY in interactive mode.** Present config summary and wait for approval:

> **Test Configuration:**
>
> - **Flow / Mode / Persona / Email / Password / Max iterations / Safety constraints**
>   **Proceed?**

Include the persona name and key traits in the config summary (e.g., "Persona: Maya, 26-35, reflective creative professional").

**Config rules:**

- Empty email → omit `--email` flag (script auto-generates)
- Provided email → pass with `--email` (skip onboarding)
- Video recording defaults to off — enable only if user asks
- **Flow → config.mode mapping**: All flows use `mode: "goal"` with goal description
- **Mode → config.mode mapping**: only `"goal"` (default) or `"explore"`. Analyzers are orthogonal — set via the `analyzers` config field, e.g. `"analyzers": {"ui": true, "ux": true}`. The old `uiAnalysis`/`uxAnalysis`/`copyAnalysis` modes were removed in the analyzer-workers refactor.

### Step 3: Boot simulator

> **Headless / CI mode: SKIP steps 3 and 4.** The `sim-test` GitHub workflow boots the
> simulator and builds + installs the Release app as plain workflow steps *before*
> invoking Claude, and hands you the UDID in `resolved-session-id.txt`. Read that file
> for `$SID` and start at step 5. Do NOT run `sim_start.sh` or `sim_install.sh` there —
> a cold Release build takes 15-25 min, which exceeds the Bash tool's 600s
> auto-background threshold; the backgrounded build then strands the one-shot `claude -p`
> session and the run produces no report (ENGMOB-2752).

```bash
SID=$(bash AuraSimTests/Scripts/sim_start.sh --reset | tail -1)
```

**CRITICAL: Capture the SID.** The last line of output is the simulator UDID (e.g., `7477188A-AF82-404B-826C-27B59A63E2FB`). Use this exact value for ALL subsequent `--session` flags. Do NOT use a UDID from a previous run or from memory.

**Each session gets its own simulator.** With no `--sim`, every script targets `"<model> (<label>)"` — e.g. `iPhone 17 Pro (eng-1462)` — and creates it on first use. The label is `$SIM_SESSION_NAME`, else `ci` on CI, else the worktree's name, else `main` for the primary checkout. Other sessions drive their own simulators on this Mac at the same time, so:

- Never pick a UDID off `xcrun simctl list devices | grep Booted` — that lists every session's devices. Re-run `sim_start.sh` (it resolves this session's own) or use the SID you captured.
- Two sessions in **one** checkout must set different `SIM_SESSION_NAME`s, or they share a simulator.
- `sim_kill.sh` and `sim_record.sh stop` act only on this session's processes, and refuse rather than guess when they cannot tell which simulator is this session's. Pass `--session "$SID"`.

Always pass `--session "$SID"` explicitly after this step.

### Step 4: Build + install app

Run this in the **foreground** — it must finish before step 5, so there is nothing to
gain by backgrounding it. (Skipped entirely in headless/CI — see the note in step 3.)

```bash
bash AuraSimTests/Scripts/sim_install.sh --session "$SID"
```

### Step 5: Launch test

`run_in_background=true` applies to **this step only** — `sim_test.sh` must keep running
while you drive it via `sim_bridge.py` in step 6. Steps 3 and 4 block.

```bash
# Use Bash tool's run_in_background=true
bash AuraSimTests/Scripts/sim_test.sh \
  --session "$SID" \
  --goal "Complete onboarding and reach home" \
  --mode goal \
  --max-iterations 150 \
  --email "<redacted-email>"
```

**`sim_test.sh` flags:** `--session`, `--goal`, `--mode`, `--max-iterations`, `--email`, `--password`, `--sim`, `--test-timeout`, `--allow-logout`, `--allow-purchases`, `--experiment` (repeatable, `key=variant`), `--launch-arg` (repeatable), `--storefront` (ISO alpha-3, e.g. `USA`/`FRA` — pins the App Store storefront via `-AuraStorefront=`), `--no-run`, `--analyzers` (comma-separated list of `ui`/`ux`/`copy`/`logs`/`ideas`/`i18n`, or `none` to disable; defaults to none in headless mode unless a caller opts in), `--locale` (`en`/`de`/`es`/`fr`/`ja`/`ko`/`pt_BR` — launches the app in that language via `-AppleLanguages`/`-AppleLocale` launch args; omit for the simulator's device locale, which is `en`)

**Forcing an experiment variant:** `--experiment <key>=<variant>`, repeatable. The key is validated against the set the build actually reads, so a mistyped or renamed one fails the run instead of silently setting an entry nothing reads (`--experiment-unchecked` skips the check). The app has only the 4.0 design, so no override changes the app's own design. `auraDesignChange3` (not `auraDesignChange` or `auraDesignChange2`, retired generations) is still assigned and can still be forced, but only UserWeb's WebView paywall and the Aurie upsell read it, to pick their own design. This is the **only** way to reach a screen behind a **rolled-out** experiment. A per-user force-set (`POST /experiments/<name>/set/<uid>/<value>`) returns 200 and changes nothing, because once an experiment is rolled out its `active` field holds a single variant and `ExperimentHelper` assigns that to every user without ever reading the per-user value.

```bash
bash AuraSimTests/Scripts/sim_test.sh --experiment cancellationDiscountDesign=a --goal "..."
```

The variant is applied **on read** in `ExperimentHelper.returnExperiments()` and is never persisted — `save`/`saveAll` would write it to `users/<id>/experiments/<name>` in Firebase and register a Mixpanel super property, which would corrupt the results of the experiment being tested. It survives crash-recovery relaunch, so a recovered run cannot silently continue on a different variant.

Note experiments are only fetched for a **logged-in** user, so nothing pre-login is gated — verify a forced variant after login, not on the welcome screen.

**Storefront testing:** Aurie-in-Aura and the `webViewPaywall2` web paywall are gated on the **US App Store storefront** (`isUsStorefront`, `src/logic/Storefront.js`), which otherwise comes from the simulator's sandbox account and resolves only after IAP products load. Pin it per run:

```bash
bash AuraSimTests/Scripts/sim_test.sh --storefront USA --goal "Open Aurie from Home"   # gated surfaces on
bash AuraSimTests/Scripts/sim_test.sh --storefront FRA --goal "..."                   # gated surfaces off
```

Applied on read only — never cached or reported to analytics. See `knowledge/rules.md § App Store storefront`.

**Locale testing:** the default is **English** — omit `--locale` and the app uses the simulator's device locale (en). To QA a non-English build, pass `--locale fr` (etc.); the app derives its language from the device locale (`react-native-localize`), so the launch args force it per session without changing the whole simulator. **A large set of features are English-only by design** (Programs, Aura Live, Coaching, Guest Pass, Podcasts, Series, Challenges, Celebrity content, etc. — gated by `UserController.canShow*` on `locale === 'en'`); they are correctly hidden in other locales, so don't file their absence as a bug. Full list + rationale: `.claude/rules/localization.md § English-only features`.

**MANDATORY: Capture the full SESSION_ID from `sim_test.sh` output.** The script creates a new session ID as `{UDID}_{epoch}` (e.g., `7477188A-AF82-404B-826C-27B59A63E2FB_1775527543`) and prints `SESSION_ID: ...`. Use this full session ID (not the raw UDID from `sim_start.sh`) for ALL subsequent `--session` flags on `sim_bridge.py`. The bridge constructs `Sessions/{session_id}/` — passing just the UDID will fail because the directory is `Sessions/{UDID}_{epoch}/`.

```bash
# Capture from sim_test.sh output:
# SESSION_ID: 7477188A-AF82-404B-826C-27B59A63E2FB_1775527543
# CREDENTIALS: email=<redacted-email> password=Sim527262x!
FULL_SID="7477188A-AF82-404B-826C-27B59A63E2FB_1775527543"  # from output
```

**MANDATORY: Capture credentials from `sim_test.sh` output.** The script prints `CREDENTIALS: email=... password=...`. Immediately store these in `users.json` — do NOT defer to Step 10. If the test crashes or times out, the credentials are lost and the user cannot be reused.

Optional video: `bash AuraSimTests/Scripts/sim_record.sh start --session "$SID"`

### Step 5b: Store user in users.json (MANDATORY — do this NOW)

**Immediately after `sim_test.sh` runs**, add the user to `AuraSimTests/users.json` with at minimum:

```json
{
  "email": "<from CREDENTIALS output>",
  "password": "<from CREDENTIALS output>",
  "createdAt": "<ISO 8601 now>",
  "onboardingCompleted": false,
  "persona": { ... }
}
```

Update `onboardingCompleted` and `notes` in Step 10 after the test finishes. But the email and password **must** be stored now.

### Step 6: Command loop

**MANDATORY: Always use `sim_bridge.py`.** Never manually read/write state/action JSON files.

```bash
# IMPORTANT: Use the full session ID from sim_test.sh output ({UDID}_{epoch}), not the raw UDID from sim_start.sh

# Wait for initial state
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID wait 0

# Write action + wait for next state (ONE call per step)
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID act 1 '{"action":"tap","target":"welcome_getStartedButton","reasoning":"Tapping Get Started"}'

# Debugging commands
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID read
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID screen 120
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID hierarchy 5
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID frames     # element positions for coordinate safety
```

#### Output modes

**Compact mode (default)** strips `screenshotPath`, `exploration`, and empty element lists (~45% smaller output). This is the default — no flag needed.

**Verbose mode** includes all fields. Use `--verbose` when you need screenshot paths or exploration data:

```bash
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID --verbose read
```

**Interactive mode** strips non-interactive elements (labels, decorative images) — keeps only buttons, fields, tabs, toggles, and screen identifiers. Reduces tokens ~30-50% on busy screens:

```bash
python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID --interactive act 5 '{"action":"tap","target":"..."}'
```

Can be combined with compact mode (default). Use `--interactive` for action-heavy loops where the agent doesn't need to read labels for context. Use full compact mode (no `--interactive`) when the agent needs labels to identify screens or verify text content.

Without compact mode, agents exhaust context after ~15–20 steps. With compact (default), ~30–40 steps. With compact + interactive, ~40–50 steps. Onboarding/sign-up flows must complete in a single run — never chain mid-onboarding. Post-login flows can chain since the continuation agent can log back in.

#### Per-step decision loop (MANDATORY sequence)

This is the single authoritative flow for every step. Don't skip steps, don't act from memory of prior runs.

```
1. READ     — get the current state (sim_bridge.py wait/act/read returns it)
2. ORIENT   — identify the screen + the exact target you need in state.elements
3. CONFUSED? — if screen is unknown, target is missing, lastAction.success == false,
               state.hint is set, or bridge timed out → SCREENSHOT + re-orient
4. DECIDE   — pick an action using this fallback chain:
              a. Target IS in state.elements → standard `tap` by id or label
              b. Target IS NOT in state.elements but IS visible in screenshot →
                 `frames` to get coords, then `tapScreen` at calculated center
              c. Target is nowhere visible → it's not this screen; go back to 2
              Never assume an ID from prior runs or the knowledge file.
5. ACT      — send via sim_bridge.py act — returns next state → back to step 1
```

**Step 2 (ORIENT) sub-checks:**
1. Check `screen` field for `*_screen` identifier
2. If null, infer from `elements` (buttons, labels, fields)
3. Cross-reference with `knowledge/screens/<name>.md`
4. If unknown, read app source in `src/screens/<ScreenName>.js` or `src/components/`

**Step 3 (CONFUSED) triggers a MANDATORY screenshot:**

```bash
python3 AuraSimTests/Scripts/sim_bridge.py --session $SID --verbose screen 30
```

Then `Read` the PNG and re-enter the loop at step 2. Do NOT retry the prior action without re-orienting — that's how runs die (see `workarounds.md § Never launch xcodebuild manually after --no-run` for one failure mode, and `workarounds.md § Screenshot on confusion` for the general rule).

**Step 4 (DECIDE) target verification:** before emitting `tap` / `type` / etc., confirm the id or label is in `state.elements.buttons` / `.fields` / `.tabs` / `.toggles`. Tapping a non-existent label can kick the app to the iOS home screen and kill the test runner.

#### Execute action loop

**Read state output:** `screen`, `elements`, `lastAction`, `loopGuard`, `status`, `timeoutCount`, `analyzerTrigger`, `hint` (compact default; add `--verbose` for `exploration`, `screenshotPath`)

**Timeout recovery:** If `timeoutCount > 0`, respond immediately — don't over-analyze.

**Read `references/actions.md`** for action vocabulary, findings format, and explore mode strategy.
**Read `references/screen-guide.md`** for screen-specific navigation rules.
**Read `references/analyzers.md`** when analyzers are enabled.

#### Analyzer trigger (when `state.analyzerTrigger` is present)

Whenever state contains an `analyzerTrigger` block, it means the screen just
changed AND one or more analyzers are enabled. Handle this BEFORE deciding the
next action:

1. For each name in `analyzerTrigger.analyzers`, gather the input bundle:
   ```bash
   python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID analyze <name>
   ```
   Output is a JSON bundle on stdout (screenshot path, elements, checklist).

2. Spawn one **Agent sub-agent** per analyzer with the prompt template from
   `AuraSimTests/Scripts/analyzers/prompts/<name>.md` and the bundle
   as input. **Read the template file and pass its body verbatim as the
   sub-agent prompt** (inline the contents of the .md file into the `prompt`
   argument, plus a header with session/screen/step + the bundle). Do NOT
   write your own ad-hoc prompt — the template's output schema (required
   fields like `lever`, `rationale`, `effort: S/M/L`) is the contract the
   downstream `record-ideas` skill expects. If you rewrite the schema inline,
   fields silently go missing and the Notion ingest is incomplete.

   Model per analyzer:
   - `ui` → **`model: "haiku"`** (layout classification)
   - `ux` → **`model: "sonnet"`** (flow/friction judgment needs stronger reasoning)
   - `copy` → **`model: "haiku"`** (text pattern matching)
   - `logs` → **`model: "haiku"`** (log-line classification)
   - `ideas` → **`model: "opus"`** (open-ended product ideation benefits from the strongest model)
   - `i18n` → **`model: "sonnet"`** (language detection + screenshot-grounded layout calls need judgment)

3. Sub-agents run **in parallel in the background** (`run_in_background: true`).
   They write their own `findings_{name}.json` and exit. Do NOT wait for them —
   continue the main test loop immediately.

4. If the `logs` analyzer returns `suspiciousLines: []`, skip spawning a
   sub-agent for it — nothing to review.

**Critical:** analyzer sub-agents must NEVER touch the simulator, `action.json`,
`state.json`, or `knowledge/`. They only write `findings_{name}.json`. The prompt
templates enforce this.

**Terminal states:** `"goalAchieved"`, `"loopDetected"`, `"crashed"`, `"timeoutGraceful"` → stop loop, go to Step 7.

**MANDATORY: Screenshot on confusion.** When the bridge times out, state appears stale (same step after action), or behavior is unexpected — ALWAYS take a screenshot (`sim_bridge.py --verbose screen 30`) before trying another action. The XCTest runner may have exited, the app may have closed, or the screen may differ from the last state. Do NOT guess or retry blindly.

**IMPORTANT:** Always set `block_until_ms: 120000` on Shell tool calls to sim_bridge.py.

### Step 7: Stop video and collect results

1. Stop video: `bash AuraSimTests/Scripts/sim_record.sh stop`
2. Read final `state.json`
3. Read `exploration.json` and `findings.json` (explore mode)
4. **If any analyzers were enabled**, wait briefly (~10s) for background
   sub-agents to finish writing their `findings_{name}.json` files, then:

   a. Merge them into a session-local `findings.md`:
      ```bash
      python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID report
      ```

   b. Merge the session's findings into the cross-run global store:
      ```bash
      python3 AuraSimTests/Scripts/sim_bridge.py --session $FULL_SID merge
      ```
      Writes / updates `AuraSimTests/findings/{ui,ux,copy,logs,i18n}.json`
      (tracked in git). Each finding is hashed on
      `(analyzer, screen, category, description)` — repeat occurrences bump
      `count` and append the session ID instead of duplicating.
      **Skip in headless/CI mode** — same reasoning as Steps 9/10/10b:
      parallel runs would race, changes can't propagate back.

### Step 8: Generate report

**MANDATORY.** Read `references/report-templates.md` for the format matching your mode.

**Include analyzer findings:** If `Sessions/{sessionId}/findings.md` exists,
include its contents in the final summary (or link to it in CI runs). This is
where the UI / UX / Copy / Logs worker output lands.

**Include app logs:** If `Sessions/{sessionId}/app.log` exists, append the last 50 lines in a collapsed `<details>` block. This is especially useful in CI — when a test fails, the app logs show exactly what went wrong (auth errors, API failures, service crashes).

```bash
tail -50 AuraSimTests/Sessions/$FULL_SID/app.log
```

### Step 8b: Offer to ingest ideas into Notion

**Interactive mode only — SKIP in headless/CI mode** (no human to prompt, and CI workspaces are ephemeral).

If `Sessions/$FULL_SID/findings_ideas.json` exists and is a non-empty JSON array, the `ideas` analyzer produced new proposals this session. Offer to push them to the **Aura Ideas** Notion database via the AINative `product:record-ideas` skill (the URL/UUID for the Aura ideas database has not been set up yet — point this at the right Notion page on first use; the AuraConsumerApp equivalent was `7e721e75374645a7acfa085e3aeca6df`):

```bash
test -s AuraSimTests/Sessions/$FULL_SID/findings_ideas.json && \
  python3 -c "import json,sys; print(len(json.load(open(sys.argv[1]))))" \
    AuraSimTests/Sessions/$FULL_SID/findings_ideas.json
```

If the count is > 0, ask the user:

> Found **N** new ideas this session. Ingest into the Aura Ideas Notion database? \[Y/n\]

If yes, ingest via the AINative **`product:record-ideas`** skill — invoke it as a
skill (the runtime resolves the plugin location; do **not** hardcode a local
checkout path), passing the session's findings file:

1. **Plan first** — run the skill in `plan` mode (dry-run) so the user can scan
   duplicates / new entries:
   `product:record-ideas plan --from-json AuraSimTests/Sessions/$FULL_SID/findings_ideas.json`
2. **Apply on confirmation** — run the skill in `apply` mode:
   `product:record-ideas apply --from-json AuraSimTests/Sessions/$FULL_SID/findings_ideas.json --confirm`

If you must call the underlying script directly, resolve it from your AINative
checkout (e.g. `"$AINATIVE_HOME"/plugins/product/skills/record-ideas/scripts/record_ideas.py`) —
never a hardcoded `~/Development/...` path, which only exists on one machine.

Dedupes against existing fingerprints — re-running is a no-op. Approve / reject lives in Notion via `product:manage-ideas`; don't try to set status here.

Skip silently if `findings_ideas.json` is missing or empty.

### Step 9: Update knowledge with learnings

**MANDATORY in interactive mode. SKIP in headless/CI mode.** Update the relevant file(s) under `AuraSimTests/knowledge/` with new workarounds, changed identifiers, discovered screens. Bump the "Last updated" line at the top of the file you touched.

### Step 10: Update test users file

**MANDATORY in interactive mode. SKIP in headless/CI mode.** Manage your local `AuraSimTests/users.json` (gitignored: it holds passwords; never commit it, keep it `chmod 600`, aura-dev accounts only):

- New user → credentials were already stored in Step 5b. Now update `onboardingCompleted`, `notes`, and `persona` with session results.
- Account deleted → remove entry
- Existing user → update state, enrich persona with session learnings
- See `references/personas.md` for the persona storage format
- **Every user MUST have `email` and `password` stored** — without these, the account cannot be reused for login tests.

### Step 10b: Update simulators file

**MANDATORY in interactive mode. SKIP in headless/CI mode.** Manage `AuraSimTests/simulators.json` (tracked in git):

- After each test run, update the simulator entry's `testing` flags (onboarding, feeds, conversation, journal, history, settings, exploration, etc.)
- Add the user email to `usersTestedWith`
- Increment `sessions` count
- Add any discovered issues to `knownIssues`
- Update `notes` with session context

### Step 11: Shut down simulator

```bash
bash AuraSimTests/Scripts/sim_shutdown.sh
```

### Step 12: Clean up

```bash
bash AuraSimTests/Scripts/sim_cleanup.sh              # state files, TestResults
bash AuraSimTests/Scripts/sim_cleanup.sh --full       # also removes DerivedData-CLI
```

## File Contract

```
AuraSimTests/
├── Scripts/           ← All sim test scripts
├── knowledge/     ← App-specific screen knowledge (tracked in git)
├── users.json         ← Test accounts + passwords (LOCAL, gitignored; start from users.example.json)
├── users.example.json ← Values-free example (tracked)
├── simulators.json    ← Device inventory and test coverage (tracked in git)
└── Sessions/{UDID}/   ← Per-session (gitignored)
    ├── config.json    ← Written by sim_test.sh
    ├── state.json     ← Written by XCTest each step
    ├── action.json    ← Written by Claude via sim_bridge.py
    ├── exploration.json ← Written by XCTest (explore mode)
    ├── findings.json  ← Written by XCTest on termination
    └── Screenshots/   ← Screenshots, hierarchy dumps, recordings
```

**Communication flow per step:**

```
Claude calls: sim_bridge.py act <step> '<action_json>'
  → 1. Writes action.json
  → 2. Blocks until state.json updates
  → 3. Prints new state JSON to stdout
```

## Known Workarounds

See knowledge file for full details. Key ones:

- **Audio**: Always `waitFor` `audio_stopped`, never fixed sleeps
- **Capsule buttons**: Standard tap works — executor auto-falls back to coordinate tap only if element is not hittable. No need to set `coordinateTap: true`
- **ZStack IDs**: Not visible in XCTest, use child elements
- **Service init**: ~50s after Get Started, use long timeouts
- **Text fields**: Always `clearFirst: true` (autofill may pre-populate)
- **Combined elements**: Settings rows are buttons, use label targeting
- **Feed card delayed nav**: Wait 2-3s after tap, navigation may be async
- **Voice buttons**: NEVER tap — voice requires microphone
- **Google sign-in**: FULLY AUTOMATABLE via ASWebAuthenticationSession webview. Login path: standard label tap works. Get Started path: tapScreen only (standard tap crashes xcodebuild). Inside the webview: screenshot + fresh `frames` before every `tapScreen`, `type` works on fields by placeholder. **NEVER store social login passwords** — always ask the user at runtime unless they explicitly grant permission. See `references/screen-guide.md` and `references/actions.md` for the full step-by-step flow.
- **Apple sign-in**: BLOCKED — crashes the simulator (no real Apple ID). Always use email signup or Google sign-in unless user explicitly requests Apple sign-in testing.
- **Back buttons**: Use `tap` with label, not `back` action type
- **Landscape testing**: iPhone is **portrait-locked** (Info.plist only allows portrait orientation on iPhone). If the user asks for landscape testing on iPhone, **inform them it's not supported** and suggest using an iPad simulator instead. Landscape testing requires iPad simulators. When running landscape tests on iPad, rotate the simulator to landscape _before_ launching the app (use `xcrun simctl orientation <UDID> landscape-left`) — do NOT rotate mid-test unless the user specifically asks for a landscape transition at a specific event
