# Action Vocabulary & Findings

## Action Vocabulary (same for all modes)

| Action | Required | Optional | When to use |
|--------|----------|----------|-------------|
| `adjustSlider` | `target`, `value` | — | Adjust slider to normalized position (0.0–1.0). Target is slider identifier (e.g., `player_scrubberSlider`). Use to fast-forward audio instead of waiting for full playback |
| `tap` | `target` | `coordinateTap`, `fallbackLabel` | Tap element by ID or label. Standard tap is always tried first — executor auto-falls back to coordinate tap only if element is not hittable. `fallbackLabel` tries a second selector if primary fails |
| `type` | `target`, `value` | `clearFirst`, `secure` | Type into field |
| `swipeUp` | — | — | Scroll up |
| `swipeDown` | — | — | Scroll down |
| `back` | — | — | Navigation back |
| `waitFor` | `target` | `timeout` | Wait for element (default 10s) |
| `handlePermission` | `target` (`"allow"`/`"deny"`) | — | System dialog |
| `rotate` | `target` | — | Rotate device: `portrait`, `landscapeLeft`, `landscapeRight`, `portraitUpsideDown` |
| `done` | — | — | Goal achieved / exploration complete |
| `terminate` | — | — | Cannot proceed |
| `refreshState` | — | — | Re-read the screen without touching anything. The way to poll during a long wait (a track, a timer, a reply): each one counts as an action, so the runner's 30-minute idle exit never fires. There is **no** `noop` — the runner rejects it as `Unknown action` |

All actions require `reasoning` — **keep it short** (3-6 words). Every token in reasoning accumulates across 100+ steps. Use `"Tap Get Started"` not `"Tapping the Get Started button to begin the onboarding flow"`. All actions accept optional `finding`.

## User-Assisted Interaction (Last Resort)

When automated taps cannot trigger an action (e.g. unusual system dialogs not reachable by any tap strategy), **ask the user to manually tap on the simulator** before giving up or terminating:

1. **Pause the loop** — do NOT issue a `terminate` action yet
2. **Announce what you need**: tell the user exactly what to tap and where
3. **Wait for the user** to confirm they tapped it (they can see the simulator window)
4. **Resume the loop** with `waitFor` on the element that should appear after the action
5. **Continue normally** — user-assisted steps are fine; document them in the session notes

**Do not terminate just because an element is not hittable.** Exhaust automated options first (tap, coordinateTap, tapScreen with `frames`, Return key), then ask the user before giving up.

**Note:** Google OAuth webview buttons CAN be automated — see the Webview Interaction section below and `screen-guide.md` for the full Google SSO flow.

## Coordinate Tap Safety (MANDATORY)

**Default: Do NOT set `coordinateTap: true`.** The executor now handles this automatically:
1. Standard `element.tap()` is always tried first
2. If `coordinateTap: true` is set, the executor checks `isHittable` — if true, uses standard tap; if false, falls back to coordinate tap with frame validation
3. Container-sized elements (>80% of screen) are automatically rejected

**You rarely need `coordinateTap: true`.** Standard tap works in most cases even when `isHittable` reports false. Only set it when you've confirmed standard tap doesn't trigger the expected action.

## tapScreen — LAST RESORT ONLY (MANDATORY)

**`tapScreen` is the nuclear option.** It bypasses all accessibility-based element resolution and taps raw screen coordinates. It should ONLY be used as a last resort when you are stuck and all other methods have failed.

**MANDATORY: A screenshot must be taken immediately before any `tapScreen`.** This serves two purposes:
1. Confirms the screen actually shows what you expect (the app may have navigated, crashed, or shown a dialog)
2. Provides visual evidence in the session for debugging if the tap goes wrong

**Escalation order — exhaust each step before moving to the next:**
1. `tap` by identifier (e.g., `"target": "journal_saveButton"`)
2. `tap` by label (e.g., `"target": "Save"`)
3. `coordinateTap: true` (auto-fallback to coordinate tap if not hittable)
4. Screenshot → `frames` → `tapScreen` (LAST RESORT)

**The correct tapScreen workflow is:**
1. **Take a screenshot** to confirm what's on screen
2. Run `frames` to get exact `{x, y, w, h}` for the target element
3. Calculate center: `(x + w/2, y + h/2)`
4. Issue `tapScreen` with the calculated center coordinates

**Never guess coordinates.** Visual estimation from screenshots is unreliable because:
- Webview content has browser chrome offsets that shift element positions
- Button positions change between pages on the same webview (e.g., Google email page vs password page)
- Different screen sizes have different layouts
- Hardcoded coordinates from knowledge/ are device-specific and break on other screen sizes

**Never use `tapScreen` for:** text fields (use `type` action), elements you haven't verified with a screenshot, or as a first attempt at any interaction.

## Webview Interaction (SSO / OAuth flows)

When the app opens an `ASWebAuthenticationSession` (e.g., Google Sign-In), the webview runs as a **separate system process**. XCTest's accessibility tree becomes unreliable — elements may appear intermittently, positions shift between pages, and system dialogs are invisible to `handlePermission`.

### MANDATORY rules for webview steps

1. **Take a screenshot at EVERY step.** The accessibility tree does not reliably reflect webview state. Screenshots are the only way to know what's actually on screen.

2. **Always use `frames` before `tapScreen`.** Webview button positions shift between pages — the Next button moves to a different position on each page. Never reuse coordinates from a previous page; always get fresh frames.

3. **System dialogs (e.g., "Wants to Use google.com") require `tapScreen`, not `handlePermission`.** These are ASWebAuth-specific dialogs that XCTest cannot detect as permission alerts. Use screenshot + `frames` to locate the "Continue" button.

4. **`type` action works on webview fields.** Target by placeholder label (e.g., `"Email or phone"`, `"Enter your password"`). Use `clearFirst: true` and `secure: true` for password fields. **NEVER store social login passwords** (Google, Apple) — always ask the user at runtime unless they explicitly grant permission to store.

5. **`tap` by label works IF XCTest detects the element.** Webview buttons sometimes appear in the XCTest tree (e.g., "Continue" on Google consent page) and sometimes don't. Try label-based tap first; fall back to `tapScreen` with `frames` coordinates if it fails.

6. **Wait for page transitions.** After tapping Next/Continue in a webview, wait 5-15s for the page to load. Take a screenshot to confirm before acting.

7. **Verify typed text with a screenshot.** Webview autocorrect can silently corrupt input (e.g., "gmail.com" → "gmail.como"). Always screenshot after typing to confirm, and retype with `clearFirst: true` if corrupted.

8. **Dismiss keyboard before tapping buttons.** The keyboard covers webview buttons. Tap the checkmark on the keyboard toolbar (label `"selected"` in frames) to dismiss. Then get **fresh frames** — button positions shift after keyboard dismiss.

9. **"Save Password?" dialog** appears after successful OAuth. Dismiss with `tap` target `"Not Now"` — this dialog IS visible to XCTest.

### Workflow for webview steps

```
type → screenshot (verify input) → dismiss keyboard (tap "selected") → frames → tapScreen → wait → screenshot → repeat
```

This is more cautious than native app interaction but necessary because webview content is a third-party product (Google, Apple) with its own layout that changes independently of the app.

## Inline Findings Format

Attach a `finding` to any action when you observe an issue:

```json
{
  "finding": {
    "type": "bug|ux|accessibility|layout|performance|crash|copy",
    "severity": "critical|major|minor|suggestion",
    "description": "Clear description of the issue",
    "element": "optional_element_id"
  }
}
```

**Finding types and what to look for:**

| Type | Look for |
|------|----------|
| `bug` | Broken functionality, wrong data, missing content, unexpected behavior |
| `ux` | Confusing flows, missing feedback, poor discoverability, unclear labels |
| `accessibility` | Missing labels, low contrast, small touch targets (<44pt), hidden interactive elements |
| `layout` | Text truncation, overlapping elements, broken alignment, missing scroll |
| `performance` | Slow transitions (>2s), janky animations, unresponsive elements |
| `copy` | Typos, grammar errors, inconsistent terminology, placeholder text |
| `crash` | (Automatic — XCTest records these) |

## Crash Detection (automatic)

XCTest detects app crashes and automatically:
1. Records a crash finding with `severity: critical`
2. Relaunches the app
3. Continues the loop (up to 3 crash recoveries)
4. Reports `status: "crashed"` if recovery fails

After a crash recovery, `lastAction.error` will say "App crashed and was relaunched". Resume from the current screen.

## Smart Loop Protection (automatic)

**Goal mode:** Same screen 3x → loop detected, test aborts.

**Explore mode:** Context-aware thresholds:
- **Hub screens** (3+ outgoing transitions): Up to 10 revisits allowed
- **Screens with unexplored elements**: Up to 5 revisits allowed
- **Fully explored non-hub screens**: 3 revisits → loop detected

The `loopGuard` in `state.json` shows current counts. Vary your strategy when counts are high.

## Explore Mode Reasoning

In explore mode, `state.json` includes an `exploration` object:

```json
{
  "exploration": {
    "coverage": {
      "screensVisited": 5,
      "elementsExplored": 18,
      "elementsTotal": 47,
      "findingsCount": 3
    },
    "currentScreenUnexplored": ["settings_tab", "profile_button"],
    "isHub": true,
    "screensVisited": ["welcome_screen", "home_screen", "journal_screen"]
  }
}
```

**Exploration strategy rules:**

1. **Prioritize unexplored elements**: Check `currentScreenUnexplored` and tap the first unexplored item
2. **Breadth-first**: Visit all tabs/sections from a hub before going deep into any one
3. **Depth-first**: Follow a path to its end before backtracking
4. **Backtrack to hubs**: When a screen has no unexplored elements, go `back` to find a hub with unexplored paths
5. **Coverage target**: Aim for 100% screens visited. Send `done` when `currentScreenUnexplored` is empty on all reachable screens
6. **Analyze while exploring**: On each new screen, examine the screenshot for UX and accessibility issues. Report findings inline

**When stuck or confused on a screen:** You have full access to the app source code. Read the SwiftUI view file to understand:
- How elements are structured (e.g., `.accessibilityElement(children: .combine)` creates combined elements)
- Custom view modifiers that affect accessibility (e.g., `.gradientToggleStyle()`)
- Navigation flow and coordinator logic
- Feature flags or experiment conditions that change UI

Look in `AuraConsumerApp/Features/{Feature}/Views/` for the relevant view file. This is white-box testing — use it.

**When to send `done` in explore mode:**
- All reachable screens visited
- No `currentScreenUnexplored` on any hub
- Coverage is satisfactory (or max iterations approaching)

**When to `terminate`:**
- Stuck with no unexplored elements and can't navigate anywhere new
- App in unrecoverable state after crash
