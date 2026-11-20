---
paths:
  - "AuraSimTests/**"
---

# Sim Testing Conventions

Operational rules for the `/sim-test` skill and any agent driving a simulator via `sim_bridge.py`. These are workflow conventions — the facts about the app live in `AuraSimTests/knowledge/`, and the skill itself lives in `.claude/skills/sim-test/`. This file is the short checklist agents should read before running or debugging a sim test.

## Related Rules

- `.claude/rules/sim-testing.md` — fundamentals of the sim test harness
- `.claude/skills/sim-test/SKILL.md` — the full skill with modes and orchestration
- `AuraSimTests/knowledge/rules.md` — safety constraints + experiment/premium APIs
- `AuraSimTests/knowledge/workarounds.md` — app-behaviour quirks

## Screen docs are the QA spec (base-level QA)

The per-screen docs under `AuraSimTests/knowledge/screens/` are not just navigation hints — they are the **specification** each sim run verifies the app against. The job of a sim run is base-level QA: drive the screen, then check that what the app actually shows matches what the doc says it should.

Every screen doc MUST enumerate:

1. **Every action** the screen supports — tap / type / select / skip / back / scroll-to-reveal — with the exact testID or label and how it is targeted.
2. **Every option** available — all cards / list items / toggles — not just the ones a given persona happens to pick.
3. **Per-experiment option lists** — when an A/B experiment changes the options, copy, or layout, document each variant, its gate (e.g. `experiments.mlOnbMotivationIOS10 === 'a'`), and how to tap it. Orient on testIDs/labels; never hardcode a server-driven phrase.
4. **Static vs personalized content** — mark which regions are **dynamic / per-user** (home-feed rows, recommended lists, "for you" content, the topic-derived motivation cards). For those, the doc specifies the **structure to verify** — section keys, `*_section_<key>` / `*_card_<id>` testID *patterns*, count/ordering rules, the action that opens an item — NOT the exact items, which legitimately differ per user.

### The mismatch rule

On every screen, compare the live actions/options against the doc. A list/content difference has **three** possible causes — decide which before acting:

- **Match** → proceed.
- **Bug** → the difference isn't explained by an experiment or by personalization → **flag it** (record a finding and surface it in the run report).
- **Experiment** → source confirms an A/B gate changed the list/layout → not a bug; it's a doc gap. Add the variant to the doc (cite the experiment gate from source) and move on.
- **Personalized / recommendation difference** → the list is per-user dynamic content (source pulls it from a recommendations / feed API, not a static array or an experiment gate — e.g. **all the rows on the home screen**, recommended coaches, topic-derived cards). This is **expected variation, not a bug and not an experiment.** Verify the *structure* (section/card testID patterns, counts, the open-item action) and make sure the doc marks the region dynamic.

Use the source-code advantage: when a list or behaviour differs from the doc, grep `src/screens/` for the screen, the experiment gate, AND whether the data comes from a recommendation/feed fetch **before** deciding bug-vs-experiment-vs-personalization. Reading the JSX is faster and more reliable than guessing from the runtime. (A doc that's merely *stale* — e.g. testIDs that now surface where the doc said they didn't — is reconciled by updating the doc, and noted as a fixed-since-documented change, not an app bug.)

This is the core QA loop: the docs are the source of truth, the sim verifies reality against them, and every divergence is a bug to file, a newly-documented variant, or expected per-user personalization.

## Runner strategy

### Single test → run in the main conversation
For a **single** sim test, drive the command loop directly in the main conversation. Do NOT spawn an agent. Users want live progress: current screen, action taken, findings as they happen. Spawned agents are silent until completion which is frustrating for a single run.

### Parallel tests → spawn one agent per session
For **multiple** concurrent runs, spawn one agent per simulator UDID. Silent execution is fine because users can't watch multiple streams anyway. Each agent owns its session ID end-to-end.

### Always use session IDs (simulator UDID)
Session ID is mandatory regardless of single/multi mode. **Never fall back to `"default"`** — it routes all sessions to the same directory and breaks multi-session support.

Resolution order the test code follows: `SIM_SESSION_ID` env → `SIMULATOR_UDID` env → `AdHocTestConfig.defaultSessionId`. Always provide one explicitly.

### Agent prompts must be concise
Keep sim-test agent prompts short. Inline the essential info. Do NOT tell agents to read large reference files (`knowledge.json`, `actions.md`, `screen-guide.md`) — reading big files exhausts context, which is the #1 cause of agent failures on long flows.

### Runners write findings as they find them, not at the end
Every runner or sweep agent writes each finding to a file the moment it sees it: `failures/<RUN>.md` in the orchestrator's scratchpad, or the path the orchestrator gives. One row per finding: `screen/testID | category | exact text or behaviour | screenshot path`.

After each screen it appends `covered: <screen>`. If the harness dies or restarts, it notes that with the step reached, then carries on.

The orchestrator puts this instruction in the runner prompt from the start, and watches the file (e.g. a Monitor on the file) so findings are filed as they land.

**Why:** the runner can die at the 30-minute idle guard, at a harness restart or at a context limit, and findings held for a final report die with it. Observed 2026-10-06 in the QA-37 Spanish sweep, where the harness restarted midway with nothing written. Same principle as "record verdicts as they are reached" for test-case runs.

### Spawn an agent per session even for single runs (when parallelising is likely)
If the intent is "I'll scale this to parallel later", spawn the agent now so the pattern is consistent. The orchestrator logs per session ID; each agent drives one simulator independently via the MCP tools.

### Prefer MCP tools over raw scripts
When the `sim-bridge` MCP server is connected, use `mcp__sim-bridge__sim_start` / `sim_act` / `sim_wait` / `sim_read` / etc. Cleaner tool integration and native MCP UX. Fall back to `sim_bridge.py` / `sim_start.sh` via Bash only when the MCP server isn't loaded.

### Shut down simulators on `goalAchieved`
When a sim test reaches `goalAchieved`, shut that simulator down immediately to free resources. Don't wait for sibling agents. Tear down as each one finishes.

## Scripts-only rule

Only use the pre-approved scripts (`sim_start.sh`, `sim_bridge.py`, `sim_cleanup.sh`, etc.) and the Read tool for sim test operations. Do NOT use raw `cat` / `echo` / variable assignments / piped shell commands to read or write sim-test files.

- Read config/state files → Read tool
- Interact with a running test → `python3 AuraSimTests/Scripts/sim_bridge.py`
- Start a test → `bash AuraSimTests/Scripts/sim_start.sh`
- Clean up → `bash AuraSimTests/Scripts/sim_cleanup.sh`

Raw bash commands trigger permission prompts and aren't pre-approved. The scripts and Read tool are pre-approved in `.claude/settings.json`.

## Action writing

### Use `tap` first, `tapScreen` only as fallback
Always try standard `tap` on an element identifier first. Only fall back to `tapScreen` with coordinates if the standard tap provably fails, AND you've validated the coordinates against a screenshot first. Coordinate taps without that validation are the source of many flaky and wrong-target bugs.

Process:
1. `tap` with the element identifier
2. If it fails or times out, take a screenshot to verify what's on screen
3. Only then use `tapScreen` with coordinates confirmed from `frames` output AND matching the screenshot

### Write defensive XCTest action code
Avoid patterns that crash XCTest. Known examples:
- `app.keys["delete"].tap()` can crash with `AXErrorCannotComplete` when the keyboard key isn't scrollable in XCTest context — use select-all + type-over instead of select + delete + type.
- Prefer atomic operations. Always consider what happens if an element exists but isn't interactable.
- Use coordinate-based taps as fallback for elements that report `isHittable == false`.

### Escape `!` as `\u0021` in sim_bridge JSON
When passing JSON to `sim_bridge.py` that contains an exclamation mark (passwords, text values), replace literal `!` with the Unicode escape `\u0021`. Literal `!` causes `Invalid \escape` errors from Python's JSON decoder through the shell argument parser.

```json
// ❌ breaks
{"value":"SimTest123!"}

// ✅ works
{"value":"SimTest123\u0021"}
```

### Wait for audio state before advancing past audio-playing screens
Aura plays meditation tracks via `react-native-track-player` (`src/logic/AudioHelper.js`). Tests must NOT use fixed `Thread.sleep` to wait for playback. Two options:

1. **Preferred (implemented as of session `_1779224...`)**: `waitFor` the hidden anchor inside `NewAudioPlayer.render()` that flips between `audio_playing` and `audio_stopped` based on `this.state.isPlaying`. Mirrors the Aurie / AuraConsumerApp pattern. Works in BOTH the full and minimal player views. The anchor uses `accessible={true}` + a 1pt absolute frame — invisible to users, queryable by XCTest. See `src/screens/AudioPlayer/NewAudioPlayer.js` and `AuraSimTests/knowledge/screens/meditationPlayer.md § Audio playback state hint`.

   ```json
   {"action":"waitFor","target":"audio_playing","timeoutSeconds":15,"reasoning":"Wait for playback start"}
   {"action":"waitFor","target":"audio_stopped","timeoutSeconds":5,"reasoning":"Confirm paused"}
   ```

2. **Fallback (track-complete instead of pause)**: use the `adjustSlider` action against the player's scrubber `testID` to fast-forward to ~99% — then `waitFor` a stable post-track label (e.g. `"Mindful day"`) to detect completion. Two actions, not four:

```json
{"action":"adjustSlider","target":"meditationPlayer_scrubberSlider","value":"0.99","reasoning":"Seek near end"}
{"action":"waitFor","target":"Mindful day","timeoutSeconds":120,"reasoning":"Wait for track-complete overlay"}
```

Pitfalls — do NOT do these:
- **Don't iterate** with successive `adjustSlider` calls (0.95 → 0.995 → 0.999). After natural playout, the scrubber element disappears and the next call fails with `Slider not found` — looks like a regression but is just the post-track overlay replacing the player UI.
- **`value` must be a JSON string** (`"0.99"`), not a number. The Swift executor parses with `Double(valueStr)` and silently rejects raw numerics.
- **Don't expect `state.screen` to change.** The post-track overlay renders above the player without remounting the screen-root testID. Verify completion by the `"Mindful day"` label or `Play next` / `Done` buttons, not by screen identifier.

See `AuraSimTests/knowledge/workarounds.md § Meditation player — seek-to-complete in two actions, not four` for the full diagnostic history (ENGMOB-1799 Phase F).

Never use `noop {waitSeconds: 30}` to "wait for audio" — it burns clock without observability and hides timing bugs.

## Explore mode

### Default goal = deep feature testing, not just navigation
When running `/sim-test` in explore mode, the default goal should exercise features, not just visit screens. Just navigating screens misses real bugs — the value is toggling settings and verifying persistence, typing invalid input and checking error messages, testing state after navigation.

Default explore goal:

> Explore the entire app with deep feature testing. Don't just visit screens — interact with every element, toggle settings and verify persistence, test input validation, check state after navigation. Report bugs, UX issues, and accessibility concerns.

Override only when the user specifies a narrower goal.

## Debugging failed taps / navigation

### Hierarchy dump first, source code second
When a tap, navigation, or element lookup fails unexpectedly, consult the raw XCTest hierarchy **before** reading the React Native source.

The hierarchy reveals the actual iOS accessibility tree at runtime — element positions in scroll-content space, `testID` propagation bugs, duplicate elements, off-screen coordinates. Source code alone can't show you what the runtime tree looks like — JS-side `testID` props need to make it through RN's bridging layer before they appear in `XCUIApplication` queries.

Process:
1. Read the most recent `/tmp/adhoc_hierarchy_*.txt` file for the failing step
2. Check element frames (x, y, w, h) against screen height to detect off-screen issues
3. THEN read the relevant RN screen / component source (`src/screens/<Name>.js`, `src/components/<Name>.js`) to understand why the tree looks that way — common causes: missing `testID` forwarding, `accessible={true}` collapsing children, `<Touchable>` swapped for `<View onTouchEnd>` so the role is wrong
4. Only after both: propose the fix

## Post-run report format

Every sim test run generates a report with:

1. **Header** — result, steps, date, session ID, email, password (for reproducibility)
2. **Step table** — step #, screen name, action taken, description of what was seen/tapped
3. **Key findings** — what was verified, bugs/issues found, analyzer findings if any
4. **Observations** — A/B experiments hit, overlays, timing quirks, anything notable

Sub-agents driving parallel runs each produce this same report; the main agent aggregates them.

## Premium redemption bookkeeping

If a sim-test grants premium to its test user via a backend code-redeem API, IMMEDIATELY edit `AuraSimTests/users.json` to add the `premium` block (`active`, `grantedVia`, `grantedAt`, `expiresAt`, `productId`, `firebaseProfileFlag`) for that user. Do not defer to end-of-run cleanup — if the test crashes before the write, the grant is effectively orphaned because we have no local record identifying the user as premium for future runs.

**Use the script — don't curl by hand.** `AuraSimTests/Scripts/sim_grant_premium.py` (landed in ENGMOB-1742) handles the full redeem-verify-persist flow:

```bash
# Looks up password from users.json
python3 AuraSimTests/Scripts/sim_grant_premium.py --email <<redacted-email>>

# Re-read server state without re-redeeming (drift recovery)
python3 AuraSimTests/Scripts/sim_grant_premium.py --email <user> --refresh-only
```

**Code per app:**

- **Aura** (this repo): `POST /codes/QA_AURA_SIM_TEST_1MONTH/redeem`
- **AuraConsumerApp / Aurie**: `POST /codes/QA_AURIE_SIM_TEST_1MONTH/redeem` (was renamed from `QA_SIM_TEST_1MONTH` — see [AuraConsumerApp #987](https://github.com/AuraHealth/AuraConsumerApp/pull/987))

The two codes identify different products and **do not cross-redeem**. Make sure the sim-test driver picks the code for the app it's exercising; full per-code details (re-redeem semantics, the one-month window, productId, side-effects) live in `AuraSimTests/knowledge/rules.md § Premium redemption API`. The Aura code is **not** single-use: `--force` on a lapsed grant issues a fresh month.

### Verify the FLAG, not just the redeem response

A 200 from `/codes/*/redeem` is necessary but **not sufficient**. The app reads premium state from the Firebase user profile (`src/models/user.js § isUserPremium`), so any sim-test driver that grants premium MUST verify the canonical flag landed before treating the user as premium downstream:

- **Aura**: `profile.premium` (boolean) is the primary flag; `profile.premiumExpirationDate` is the expiry. The coaching variants are `profile.premiumCoaching` and `profile.premiumCoach`.
- **Aurie / AuraConsumerApp**: `profile.premiumAI` (or `profile.aiPremium` depending on the build — check both). The Aurie user model has a different flag name; do NOT assume Aura's `profile.premium` applies.

`sim_grant_premium.py` enforces this — after a 200 redeem it reads `users/{uid}.json` from Firebase Realtime DB with the user's idToken and bails loudly if `premium !== true`. **Do not record an active grant in users.json unless the Firebase flag is verified.** A 200 redeem without the flag flipping indicates a backend regression and the sim-test driver should fail rather than proceed under a false-positive premium assumption.

The `expiresAt` field in users.json should mirror the **server-side `premiumExpirationDate`**, not a locally-computed `now + 30 days`. Local clock skew + the backend's actual grace-period math can drift by hours/days. Use `--refresh-only` to repair drift in existing entries.

### Sandbox IAP

Sandbox StoreKit purchases (`react-native-iap` → Apple sandbox) follow the same rule: log the productId, the grant timestamp, and the sandbox transaction id into `users.json` as soon as the receipt validates. Same flag-verification requirement applies — the IAP receipt may validate but `profile.premium` lags by a few seconds while the server processes the receipt; wait for the flag before treating the user as premium.

See `knowledge/workarounds.md § Persist users.json immediately on premium redemption` (file will be created during Phase 3 of the port).

## Locale change bookkeeping

If a sim test changes a user's app locale via the in-app **DisplayLanguage picker** (sidebar → Display Language → \<language\>), record it in `AuraSimTests/users.json` for that user. This mirrors the premium bookkeeping pattern: persist *user-level* state changes immediately so future runs and other contributors know the user's current state.

**Add a `locale` block on the user entry:**

```json
{
  "email": "<redacted-email>",
  "...": "...",
  "locale": {
    "code": "fr",
    "setVia": "in-app DisplayLanguage picker",
    "setAt": "<ISO 8601 timestamp>",
    "serverPersistedVerified": false
  }
}
```

- `code` — `en` / `de` / `es` / `fr` / `ja` / `ko` / `pt_BR`.
- `setVia` — short, machine-tag-ish phrase: `in-app DisplayLanguage picker` (canonical), or another path if one is later added (e.g. `profile API write`).
- `setAt` — ISO 8601 of when the test made the change.
- `serverPersistedVerified` — `true` only if you actually inspected Firebase (`users/{uid}.json` profile fields) and confirmed the locale persisted server-side. Otherwise `false`. Today `Translator.setUserLocale` triggers `Actions.onLanguageChanged` + `Actions.clearContentCache`, but the *firebase write* path is a TODO in `Translator.js` — observed server-side content **does** localize after the switch (the content cache is refetched with the new locale on subsequent API calls), but the canonical profile-locale field is not provably written yet. Default this to `false` until verified.

### Do NOT record `--locale` launch-arg overrides

The harness's `--locale fr` flag (PROD-2223) injects `-AppleLanguages`/`-AppleLocale` for one launch. It does NOT mutate the user's profile — the next launch without the flag goes back to the device locale. Don't add a `locale` block for `--locale` runs; that's per-launch UI state, not a user-level change.

### Verify, don't just write

After the test changes the locale, verify on the same run before recording — e.g. re-open the sidebar and confirm sidebar/profile labels are in the new language. Recording `{ code: "fr", … }` for a switch that didn't actually take is worse than leaving the field off.

## The sim-test JSON is syntax-gated

`AuraSimTests/simulators.json` (tracked) and `AuraSimTests/users.json` (**local only**,
gitignored) are edited by hand, by agents, mid-run — premium grants and locale changes
into `users.json`, coverage flags and known issues into `simulators.json`. A syntax gate
exists because a convention was not enough:

```bash
python3 AuraSimTests/Scripts/validate_json.py    # exit 1 with the line and column
```

Enforced in CI by `.github/workflows/simtest-json-check.yml` on any PR touching `simulators.json` or
`users.example.json`; the local `users.json` is checked whenever the script runs where it exists.

**Why.** `simulators.json` did not parse for three months — a missing comma introduced
2026-06-23 in `9b086a358`, found 2026-09-22. It failed silently because
`device_name_for_udid` piped the parse through `2>/dev/null || echo ""`, making a malformed
file indistinguishable from "this UDID isn't in the registry"; the cross-machine
UDID → device-name fallback simply never resolved. That swallow is gone — a parse error now
warns and names the line, while an honest miss stays quiet. (PROD-6413.)

## `users.json` is local; `simulators.json` is tracked

`AuraSimTests/users.json` holds the QA accounts **and their passwords**, so it is
**gitignored and never committed** (ENGMOB-3179, 2026-11-10). Start one from
`AuraSimTests/users.example.json`, keep it `chmod 600`, and keep only **aura-dev** accounts
in it: the harness never signs in to prod with stored accounts, and refuses a prod build
(`sim_test.sh` / `sim_drive.py`). Record premium grants and locale changes in your local
copy as before.

Existing checkouts keep their copy (it was untracked with `git rm --cached`). A new
checkout gets one from a teammate, never through git; CI restores it from the optional
`SIMTEST_USERS_JSON` repo secret (`.github/workflows/sim-test.yml`). Recipe accounts such as
`sim_capture.py`'s member account must exist in the local copy.

`AuraSimTests/simulators.json` is still tracked: a sim-test change that updates it must
include it in the commit.
