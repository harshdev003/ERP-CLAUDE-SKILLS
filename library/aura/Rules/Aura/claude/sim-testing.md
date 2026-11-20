---
paths:
  - "AuraSimTests/**"
---

# Simulator Testing

## Purpose

`AuraSimTests` is an autonomous, Claude-driven UI test harness that boots the **release-config** Aura React Native app on a real iOS simulator and drives it via XCUITest — reading the live iOS accessibility tree and writing actions through a file-based observe → plan → execute loop. Real Firebase auth, real API calls, real SDK init — no mocks, no Metro, no `__DEV__` branches.

## Related Rules

- `architecture.md` for the Reflux + Dashboard.js navigation boundaries the test exercises.
- `accessibility.md` for `accessibilityLabel` / `accessibilityRole` requirements that VoiceOver and XCUITest both depend on.
- `state-management-reflux.md` for the action/store flow each tap triggers.

## When to Use Sim Tests vs Jest

| Aspect | `npm test` (Jest, `test/`) | `AuraSimTests` |
|--------|--------------------------|---------------------------|
| Layer | JS unit + integration | End-to-end on a real device |
| Services | Mocked / stubbed | Real (Firebase, API, IAP sandbox) |
| Speed | Fast (~30s) | Slower (~5-15 min — release build + network) |
| Bundle | None (Node) | Release `Aura.app` with JS bundled in |
| Use case | Logic regression, store + helper tests | Onboarding, paywall, deep-link, IAP, account-delete validation |
| Determinism | High | Lower (network dependent) |

## Directory Structure

```
AuraSimTests/
├── Base/
│   └── BaseSimTestCase.swift       # Base class — no -UITesting, longer timeouts
├── Flows/
│   └── AdHocSimTest.swift          # Autonomous ad-hoc test runner
├── Helpers/
│   ├── AdHocTestModels.swift       # SimState, SimAction, config models
│   ├── ExplorationTracker.swift    # Navigation graph and findings
│   └── HierarchyParser.swift       # XCTest hierarchy → categorized elements
├── Scripts/
│   ├── sim_start.sh                # Boot simulator (optionally reset)
│   ├── sim_shutdown.sh             # Shut down simulator to free resources
│   ├── sim_install.sh              # Build app + test target, install on simulator
│   ├── sim_test.sh                 # Write session config + launch XCTest runner
│   ├── sim_common.sh               # Shared constants and helpers (sourced by other scripts)
│   ├── sim_bridge.py               # Command loop (wait/act/read/screen/hierarchy)
│   ├── sim_cleanup.sh              # Clean session artifacts
│   ├── sim_kill.sh                 # Kill test or recording processes
│   ├── sim_record.sh               # Start/stop video recording
│   └── sim_mcp.py                  # MCP server for native Claude tool integration
├── knowledge/         # Shared knowledge (tracked in git)
├── users.json              # Test user accounts + passwords: LOCAL ONLY, gitignored (copy users.example.json)
├── users.example.json      # Values-free example of users.json (tracked)
├── simulators.json         # Device inventory and test coverage (tracked in git)
└── Sessions/
    └── {sessionId}/                # Per-session (gitignored)
        ├── config.json
        ├── state.json
        ├── action.json
        ├── exploration.json
        └── findings.json
```

## Configuration

Tests are configured via a JSON file at `AuraSimTests/Sessions/{sessionId}/config.json`, written by the `/sim-test` skill before each run.

**Session ID resolution** (in priority order):
1. `SIM_SESSION_ID` environment variable
2. `SIMULATOR_UDID` environment variable (set automatically by xcodebuild)
3. Falls back to `"default"`

When running via `xcodebuild test`, the session ID will be the **simulator UDID** (e.g., `80A4802A-4906-41D9-9B6B-78845EFDAFD8`). Find it with:
```bash
xcrun simctl list devices available | grep "iPhone 17 Pro"
```

**Config format** (matches `AdHocTestConfig` in `AdHocTestModels.swift`):

```json
{
  "auth": {
    "email": "<redacted-email>",
    "password": "Sim4567890x!",
    "autoGenerateEmail": false
  },
  "goal": "Sign up a new user, complete onboarding, reach home screen.",
  "maxIterations": 150,
  "mode": "goal",
  "safetyConstraints": {
    "allowAccountDeletion": false,
    "allowDeleteConversation": false,
    "allowDeleteJournal": false,
    "allowLogout": false,
    "allowPurchases": false,
    "blockedIdentifiers": []
  },
  "sessionId": "default",
  "takeScreenshots": true,
  "timeoutPerStep": 120
}
```

**Optional fields:**
- `explorationConfig`: `{ "analyzeAccessibility": true, "analyzeUX": true, "strategy": "breadthFirst" }` — for explore/uiAnalysis/uxAnalysis modes
- `mode`: `"goal"` (default), `"explore"`, `"uiAnalysis"`, `"uxAnalysis"`
- `locale`: app UI locale to launch in — `en`/`de`/`es`/`fr`/`ja`/`ko`/`pt_BR` (set via `sim_test.sh --locale`). `AdHocSimTest` maps it to `-AppleLanguages`/`-AppleLocale` launch args, which `react-native-localize` reads. Omitted → simulator device locale (en). Some features are English-only by design and are hidden in other locales (see `Translator`/`UserController.canShow*`).

## Test Every Supported Locale

A copy or UI change is tested in all 7 locales the app supports: `en`, `de`, `es`, `fr`, `ja`, `ko`, `pt_BR`. English and French alone are not enough. Why: #7649 and #7650 were first tested only in `en` and `fr`, and a greeting trim, a translation overlay or CJK text can break in a locale nobody opened. A change with no user-visible text still gets one non-English locale.

- **The test plan names, per locale, the screens to check and the text to expect.** For example, the Home greeting ends on the name with no `!`, `！` or trailing space; `es` has no leading `¡`; `ja` keeps `さん` after the name.
- **Switch an existing account with Me > Display Language, then cold relaunch.** `--locale` sets only the launch locale, and an account's stored language wins over it.
- **Seed dev data the server translates.** Coach text comes from ContentService `/localization`, and dev has no rows by default. Ask BackendContent for dev rows per locale whose text starts with a tag (`[DE test]`). Use native characters for `ja` and `ko`, and leave one coach unseeded to check the English fallback.
- **A feature that is English-only by design** (see `localization.md`) is expected to be missing in other locales. It is not a failure.
- **Report per locale:** the exact text seen, pass or fail per screen, and any cut-off or overlapping text. Include a screenshot per screen.

## Test Email Convention

Always use: `test+{unix_timestamp}@aurahealth.io`

- The `test+` segment is what production filters out of analytics dashboards.
- The timestamp ensures uniqueness across runs.
- This is what `sim_test.sh` and `sim_mcp.py` actually generate, and it matches
  AuraConsumerApp / Aurie byte for byte — the two harnesses share the format so a
  test account is recognisable whichever app created it.

> This section previously documented `{prefix}+test+{unix_timestamp}@aurahealth.io`,
> which would produce `<redacted-email>`. Nothing has ever generated
> that. The same stale text is still in AuraConsumerApp's copy of this rule file.

## Timeouts

Real services are slower than mocks. Use these guidelines:

| Operation | Timeout |
|-----------|---------|
| Network calls (auth, API) | 30s |
| UI animations | 10s |
| Quick element checks | 5s |
| Home screen after sign-up | 60s |

## Account Cleanup

Set `deleteAccountAfterTest: true` to have the test navigate to Settings → Account and Data → Delete Account after completing the flow. This keeps the test environment clean.

## Simulator Reset (MANDATORY Before Exploratory Runs)

**Always reset the simulator before a full exploratory or new-user-onboarding run.** This ensures a clean state — no cached auth tokens, no prior account data, no leftover app state that would skip onboarding.

```bash
# Find the target simulator UDID
xcrun simctl list devices available | grep "iPhone 17 Pro"

# Reset it (erases all data, returns to factory state)
xcrun simctl shutdown "iPhone 17 Pro"
xcrun simctl erase "iPhone 17 Pro"
```

**When to reset:**
- Before any test that starts from the welcome/age-gate screen
- Before any new-user registration flow
- When prior test state might affect the flow (e.g., logged-in account, skipped onboarding)
- Before **re-drawing a client-side A/B variant** — variants are cached per install, so a fresh draw needs fresh device state (one erase per draw)

Each session has its own simulator (`"iPhone 17 Pro (<worktree>)"`, created on first use — see the `sim-test` skill), so erase it by UDID — `bash AuraSimTests/Scripts/sim_start.sh --reset --session "$SID"` — never by the shared name, which matches other sessions' devices.

**When NOT to reset:**
- Regression tests that test an already-logged-in user state
- Tests that intentionally continue from a prior session

**An app uninstall is NOT a substitute for an erase.** Firebase Auth persists its session in the iOS **Keychain**, which survives app uninstall — relaunching after an uninstall-only reset comes up *already logged in as the previous test user*, silently skipping onboarding. Always erase. See `AuraSimTests/knowledge/workarounds.md § App uninstall does NOT reset auth or experiment draws`.

## Running Sim Tests

Use the `/sim-test` skill for interactive runs. For manual execution:

```bash
# 1. Make sure the release-config Aura.app is built + installed (see sim_install.sh)
# 2. Write config JSON to AuraSimTests/Sessions/{sessionId}/config.json
# 3. Run:
xcodebuild test \
  -workspace ios/Aura.xcworkspace \
  -scheme AuraSimTests \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=26.2' \
  -only-testing:AuraSimTests \
  -derivedDataPath DerivedData-CLI
```

Note the **`-workspace`** (NOT `-project`) — the React Native project is opened through `Aura.xcworkspace` so that CocoaPods linkage is honored. The shared scheme is `AuraSimTests`, not `Aura`.

## Adding New Flows

1. Create a new test file in `Flows/` (e.g., `LoginSimTest.swift`).
2. Extend `BaseSimTestCase` for real-service timeouts and helpers.
3. Read config from `SimTestConfig.load()`.
4. Add the flow enum case to `SimTestConfig.Flow`.
5. Update the `/sim-test` skill to support the new flow.

## Knowledge Files

The `/sim-test` skill uses a set of knowledge files at `AuraSimTests/knowledge/` as its source of truth. **These files must be kept up to date in interactive mode. In headless / CI mode, do NOT write to these files** — CI workspaces are ephemeral, parallel runs would race, and changes don't propagate back. Report findings in the run summary instead and let a human follow up.

Structure:

- `rules.md` — global rules, safety rules, company context, experiment API
- `flows.md` — onboarding, login, delete account, QP, feed cards
- `workarounds.md` — known bugs and platform-specific quirks
- `screens/<name>.md` — one file per screen (see `screens/README.md` for index)

### When to update

Update the relevant file whenever:
- A quiz question is added, removed, or reordered → `screens/onboarding_quiz.md`
- An option label changes in `OnboardingQuizConfig.json` → relevant `screens/*.md`
- An accessibility identifier is added, renamed, or removed → relevant `screens/*.md`
- A new screen is added → new file under `screens/`, add to `screens/README.md` index
- A new flow is added to sim tests → `flows.md`
- A **new workaround** is discovered during test runs → `workarounds.md`

### Mandatory: capture learnings

After every sim test run (pass or fail), check if any new learnings should be captured:
- New XCTest quirks (elements not found, tap not triggering, timing issues)
- Workarounds that fixed a failure
- Timeout adjustments that were needed
- Identifier changes discovered during self-healing

Add these to `workarounds.md` with:
- A short H2 title
- What the issue is
- Affected elements/screens
- Fix
- Discovery context

Always bump the "Last updated" line at the top of the file you touched.

## React Native `testID` Propagation (CRITICAL)

React Native maps the `testID` prop on a `<View>`, `<TouchableOpacity>`, `<Pressable>`, `<TextInput>`, or `<Text>` directly to the iOS `accessibilityIdentifier` on the underlying `UIView`. XCUITest then sees that identifier in the accessibility tree.

Two RN behaviours to know:

1. **No automatic descendant propagation** — unlike SwiftUI's `.accessibilityIdentifier(...)`, RN does **not** copy a parent `<View testID="foo_screen">`'s `testID` down to its children. A screen-root `testID` will live on the root container only; interactive children need their own `testID` props.
2. **`accessible={true}` collapses children** — when a `<View>` has `accessible={true}` (the default for `<TouchableOpacity>`), its descendants stop appearing as separate accessibility elements. XCUITest will see one combined element with the parent's label/identifier. Don't wrap multiple interactive children in a single `accessible` `<View>` — break them out or set `accessible={false}` on the parent so children stay queryable.

### Convention for new screens

```jsx
// Screen root — always _screen suffix, snake_case
<View testID="meditationPlayer_screen">
  {/* Each interactive descendant gets its own testID */}
  <TouchableOpacity testID="meditationPlayer_playPauseButton" onPress={...}>
    <Image source={icons.play} accessible={false} />
  </TouchableOpacity>

  <TextInput
    testID="meditationPlayer_noteField"
    autoCorrect={false}        // disable autocorrect for fields tests type into
    autoComplete="off"
  />
</View>
```

### Custom components that wrap touchables

When you build a reusable component (`AppButton`, `CapsuleButton`, etc.) that wraps a `<TouchableOpacity>`, expose `testID` as a prop and forward it onto the touchable — not the outer `<View>`:

```jsx
// ✅ GOOD — testID forwarded to the actual touch target
const AppButton = ({ testID, onPress, label }) => (
  <TouchableOpacity testID={testID} onPress={onPress}>
    <Text>{label}</Text>
  </TouchableOpacity>
)

// ❌ BAD — testID on wrapper View, tap target reports no identifier
const AppButton = ({ testID, onPress, label }) => (
  <View testID={testID}>
    <TouchableOpacity onPress={onPress}>
      <Text>{label}</Text>
    </TouchableOpacity>
  </View>
)
```

### Symptoms when this is wrong

- The screen's `_screen` identifier shows up on a child button in the XCUITest hierarchy → a parent `<View accessible={true}>` is collapsing the tree. Set `accessible={false}` on the parent or remove the prop.
- A button is visible on screen but not in `state.elements` → the wrapping component swallowed `testID`. Forward it through.
- Multiple elements share the same `testID` → reusable component used twice without per-instance suffix. Add `{feature}_{name}_{index}` for list items.

## ⚠️ `<GestureHandlerRootView>` collapses the entire subtree on iOS (ENGMOB-1692 Phase A)

When `<GestureHandlerRootView>` from `react-native-gesture-handler` wraps a screen at the top level, iOS auto-promotes its inner `<View style={{flex: 1}}>` to a single accessibility element. The entire descendant subtree disappears from XCUITest queries — `state.elements` returns `{}` empty, every leaf `testID` becomes unfindable, even the wrapper's own testID stops surfacing in the iOS hierarchy.

**Symptom signature:**

- `state.elements` is `{}` after `wait 0` even though the screenshot shows a fully rendered screen
- `waitFor <any-testID-on-this-screen>` times out at 45-60s
- The iOS hierarchy dump collapses to ~18 lines with 3 nested full-screen `Other` nodes sharing the same merged label

**Fix:** remove the top-level `<GestureHandlerRootView>` wrapper. Use `<>` (Fragment) instead. The other GHR usages elsewhere in the app keep `maybeInitializeFabric()` running on mount, and on iOS the native side of GHR is a no-op (`RNGestureHandlerRootViewCls` returns `nil`) — the wrapper was only providing a JS-side gesture context.

```jsx
// ❌ BAD — iOS collapses the whole Dashboard subtree
return (
  <GestureHandlerRootView>
    <View testID="dashboard_screen">
      …
    </View>
  </GestureHandlerRootView>
)

// ✅ GOOD — Fragment lets iOS expose every descendant testID
return (
  <>
    <View testID="dashboard_screen">
      …
    </View>
  </>
)
```

**Workarounds that DO NOT help** (verified empirically — don't rediscover them):

- Adding `accessible={false}` on the wrapper or any inner full-screen `<View>` — the collapse happens at the GHR native View
- Setting `accessibilityElementsHidden={false}` + `importantForAccessibility="yes"` on the testID View
- Spreading `accessible={false}` through `<GestureHandlerRootView>` via `...rest` — `accessibilityLabel` propagates but `accessible` does not
- Bypassing GHR with manual `<GestureHandlerRootViewContext.Provider value={true}>` + plain `<View accessible={false}>` — the plain View itself auto-promotes at the same position

See `AuraSimTests/knowledge/workarounds.md § Dashboard Home — whole-screen accessibility collapse` for the full diagnostic history (15 verification runs, ~90 min of investigation).

**Where it's OK to use `<GestureHandlerRootView>`:**

- ✅ Around a specific sub-flow that needs gesture handling (e.g. `src/screens/playlists/playlist.js`, `src/screens/appReusable/GestureleftSwipeUX.js`) — these don't have deep child trees
- ✅ At a true app-root location wrapping nothing else above it — but Aura currently does not need this on iOS
- ❌ As a top-level wrapper around a screen with deep content (Dashboard, Onboarding6 — though Onboarding6 happens to not be wrapped today, which is why its testIDs surface fine)

## Debugging: Screenshot on Confusion (MANDATORY)

When the `sim_bridge.py` times out, state appears stale (same step after an action), or behavior is unexpected (action succeeded but screen didn't change):

1. **ALWAYS take a screenshot first** — run `sim_bridge.py --verbose screen 30` to capture and read the screenshot
2. **Do NOT guess** or retry the same action blindly
3. **Diagnose from the screenshot**: the XCTest runner may have crashed, the app may have closed, or the screen may show something different from the last `state.json`
4. **If the app/runner died**, restart the session instead of retrying actions

```bash
# After any timeout or unexpected result:
python3 AuraSimTests/Scripts/sim_bridge.py --session $SID --verbose screen 30
```

A screenshot reveals the actual situation instantly — guessing wastes steps and can make things worse.

## Security

- **Never commit test credentials** — config files are in `.gitignore`.
- **One simulator per session** — use the session's own simulator (see the `sim-test` skill), never another session's.
- **Never hardcode passwords** — `sim_test.sh` auto-generates a unique password per run. Read it from `config.json`.
- **Recordings are gitignored** — screenshots, videos, and result bundles stay local.
