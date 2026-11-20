# Screen-Specific Reasoning Guide

Applies to all modes — use these when navigating through onboarding or specific screens.

> **Note**: this file was originally ported from the AuraConsumerApp (`Aurie`) sim-test skill, which had screen-specific rules for Quick Processing, Feed Cards, Voice Mode, Affirmation, two-step paywall, etc. — features that don't exist in **Aura**. Those sections have been removed. As Aura sim runs land on each screen, capture the per-screen rules in `AuraSimTests/knowledge/screens/<name>.md` (one file per screen, indexed in `screens/README.md`) rather than re-stuffing them here.

The sections below are **universal** — they apply to any iOS app driven via XCUITest, regardless of which app, and stay valid for Aura.

## Audio Status Detection (universal — applies when present)

The pattern for waiting on audio playback (Aurie uses `audio_playing` / `audio_stopped` hidden accessibility elements driven off the TTS state). **Aura's meditation player does NOT currently expose these** — when a Phase-6 run hits the meditation player, either:

1. Add a hidden `<View testID="audio_playing">` / `testID="audio_stopped">` element to `src/screens/AudioPlayer/` driven off `react-native-track-player` state, OR
2. Use the `adjustSlider` action against the player's scrubber `testID` to jump to ~95% so the track completes immediately without real-time waiting.

If the pattern lands in Aura, the rule is the same:

- `audio_playing` in `elements.other` → `waitFor` target `audio_stopped`, timeout 30
- `audio_stopped` → safe to proceed
- **Never use a fixed sleep for audio**

## Audio Player — scrubber fast-forward (universal)

The meditation player has a scrubber slider for seeking. **For tracks over 3 minutes, fast-forward instead of waiting for full playback** — waiting 5+ minutes wastes sim test time.

```json
{"action": "adjustSlider", "target": "<player_scrubber_testID>", "value": "0.95", "reasoning": "Skip to end"}
```

Capture the actual scrubber `testID` (from `src/screens/AudioPlayer/`) in `knowledge/screens/audioPlayer_screen.md` on first encounter.

## Google Sign-In (OAuth Webview) — universal

Google SSO opens an `ASWebAuthenticationSession` webview. **Can be fully automated** without manual user interaction. The flow below applies regardless of app.

### Password handling (CRITICAL — SECURITY)

**NEVER store social login passwords** (Google, Apple) in `users.json`, `knowledge/`, config files, or any tracked/committed file. These are credentials for external services the user owns — not test-generated passwords.

- **Always ask the user** for the password at runtime before typing it into the webview
- If the user explicitly grants permission to store it, only then save it — and note it was user-authorized
- Do not log or echo the password in session output

### Step-by-step flow

1. **Trigger Google sign-in** — typically a "Continue with Google" CTA on the login or sign-up screen.

2. **System dialog "Wants to Use google.com to Sign In"** — uses `ASWebAuthenticationSession`, NOT a regular `handlePermission` alert.
   - `handlePermission` does NOT work here (the alert is owned by `springboard`, not the app).
   - Take a screenshot, use `frames` to locate the "Continue" button, then `tapScreen` at its center.

3. **Email page** — input field with placeholder "Email or phone".
   - `type` action with `clearFirst: true`. Verify with a screenshot — autocorrect can corrupt emails (`gmail.com` → `gmail.como`).
   - If corrupted: type again with `clearFirst: true`.

4. **Dismiss keyboard before tapping Next** — the keyboard covers webview buttons. Tap the keyboard's checkmark/done button (label `"selected"` in `frames` output) first. Then get **fresh frames** — button positions shift after keyboard dismiss.

5. **Password page** — input field with placeholder "Enter your password".
   - Type with `clearFirst: true`, `secure: true`. **Always ask the user** for the password unless they explicitly granted permission to store.

6. **"Save Password?" dialog** appears after successful OAuth — IS visible to XCTest as a regular alert. Dismiss with `tap` target `"Not Now"`.

### Webview gotchas

- **Always screenshot at EVERY step** — accessibility tree is unreliable in webviews
- **Always get fresh `frames` before each `tapScreen`** — webview button positions shift between pages
- **`type` works on webview fields** — target by placeholder label
- **System dialog may be skipped** — the "Wants to Use google.com" dialog appears on first use but may be skipped on subsequent runs (even after simulator reset) if the permission was cached
- This is a third-party product (Google) — layout may change independently of the app

## Apple Sign-In — universal

**Blocked**. Apple Sign-In crashes the simulator (no real Apple ID configured). Always use email signup or Google sign-in unless the user explicitly requests Apple sign-in testing on a physical device.

## Navigation / Back Buttons (universal)

Aura uses a Reflux-driven navigation system (`Actions.navigateTo`, `Actions.navigateBack`). Most screens render their own custom Back button rather than relying on iOS's system nav bar.

- The `back` action type will likely fail (the system nav bar is bypassed)
- Always use `tap` with the screen's explicit `testID` for that Back button (typically `{feature}_backButton`) OR by label `"Back"` / `"Go back"`
- The executor auto-falls back to coordinate tap if standard tap fails on non-hittable elements

## Voice Mode Buttons — NEVER TAP (universal)

Any label or `testID` containing `voice`, `mic`, or `record` — never tap. Microphone permission prompts and live recording disrupt the test.

## TextInput / Keyboard handling (universal)

- **Always `clearFirst: true`** when typing into a `TextInput` — autofill may pre-populate the field; a fresh `type` without clearing would append rather than replace.
- **`secureTextEntry={true}` fields** — XCTest returns masked bullets (`•••••`) for the field value. The post-type validation in `executeType` correctly skips secure fields.
- **Disable autocorrect on test-driven fields**: in `src/`, add `autoCorrect={false}` + `autoComplete="off"` to any `TextInput` an automated test types into. iOS keyboard autocorrect can silently inject extra characters (`.io` → `.ioy`).

## React Native `testID` propagation reminder

See `.claude/rules/sim-testing.md § React Native testID Propagation` for the full rationale, but for screen-by-screen navigation purposes:

- **Screen root**: `<View testID="<feature>_screen">` — snake_case, MUST end with literal `_screen` (the parser does `hasSuffix("_screen")`)
- **`accessible={true}` collapses children** — when XCTest reports the screen's `_screen` identifier on a child button, the parent `<View>` is collapsing the tree. Fix at the parent.
- **Custom touchable wrappers** must FORWARD `testID` to the inner `<TouchableOpacity>`, not put it on the outer `<View>`. Otherwise the tap target reports no identifier in `state.elements`.

## General Rules (universal)

- **Error recovery**: try alternative selectors on failure (id → label → frames+tapScreen)
- **Combined accessibility elements**: rows wrapped in `<View accessible={true}>` collapse children — query by label rather than expecting individual child identifiers
- **ZStack-equivalent identifiers**: `<View>` containers may not be visible in XCTest if no testID — use child elements
- **Screen-specific rules go in `AuraSimTests/knowledge/screens/<name>.md`** — one file per screen, indexed in `screens/README.md`. Don't grow this file with Aura-specific knowledge.

## Coordinate Tap Safety (CRITICAL — universal)

**Default: Do NOT use `coordinateTap: true`.** Standard taps work for most elements and are safer across different screen sizes. XCTest's standard `.tap()` handles scroll-into-view automatically.

**When `coordinateTap` IS needed:**
- Capsule-clipped buttons (`.clipShape(Capsule())` in SwiftUI; `borderRadius` + `overflow: hidden` patterns in RN) that report `isHittable == false`
- Elements where standard tap hits the wrong target due to overlapping accessibility frames

**Safety rules for coordinate taps:**

1. **Never use coordinate tap blindly** — on different screen sizes (Pro vs Pro Max), element positions differ and a tap at the center of one element's frame may hit a neighboring element.
2. **The framework rejects container-sized elements** — if the resolved element covers >80% of the screen, the coordinate tap will fail with an error asking you to verify positions.
3. **When coordinate tap fails**, use the `frames` command to inspect actual element positions:

   ```bash
   python3 sim_bridge.py --session $SID frames
   ```

   This returns a JSON array of interactive elements with `{x, y, w, h}` frame rects.
4. **Prefer label-based targeting** over identifier when coordinates are needed — labels resolve to smaller, more specific elements.
5. **For text fields**: NEVER use coordinate tap — standard `type` action with field identifier is always safe.
