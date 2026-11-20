# Screen-Specific Reasoning Guide

Applies to all modes — use these when navigating through onboarding or specific screens.

## Audio Status Detection (CRITICAL)

- `audio_playing` in `elements.other` → `waitFor` target `audio_stopped`, timeout 30
- `audio_stopped` → safe to proceed
- **Never use a fixed sleep for audio**

## Audio Player (Full Player, Mini Player)

The audio player has a scrubber slider for seeking. **For tracks over 3 minutes, fast-forward instead of waiting for full playback** — waiting 5+ minutes wastes sim test time.

**Slider identifiers:**
- `player_scrubberSlider` — fullscreen player
- `miniPlayer_scrubberSlider` — mini player
- ~~`miniPlayerPill_scrubberSlider`~~ — pill mini player (**NOT accessible** — the pill is not in the XCTest accessibility tree, see ENGMOB-1613. Tap the pill to expand to full player first, then use `player_scrubberSlider`.)

**Fast-forward pattern:**
1. Confirm audio is playing (`audio_playing` exists)
2. Adjust slider to near the end: `{"action":"adjustSlider","target":"player_scrubberSlider","value":"0.95","reasoning":"Skip to end"}`
3. Wait for `audio_stopped` (track finishes within seconds)

**Other player controls:**
- Skip forward: label `"Skip forward 15 seconds"`
- Skip backward: label `"Skip backward 15 seconds"`
- Play/Pause: dynamic label `"Play"` or `"Pause"`

**When to listen fully vs skip:**
- Tracks under 3 minutes → let them play (wait for `audio_stopped`)
- Tracks over 3 minutes → use `adjustSlider` to 0.95
- If testing audio quality/UX specifically → let it play fully

## Welcome Screen

Identified by `welcome_getStartedButton` in buttons.

- Standard tap on `welcome_getStartedButton`
- Then `waitFor` audio/next screen with timeout 60

## Google Sign-In (OAuth Webview) — FULLY AUTOMATABLE

Google SSO opens an `ASWebAuthenticationSession` webview. Unlike previous understanding, this flow **can be fully automated** without manual user interaction. Follow this exact sequence:

### Password handling (CRITICAL — SECURITY)

**NEVER store social login passwords** (Google, Apple) in `users.json`, `knowledge/` (split into `rules.md`, `flows.md`, `workarounds.md`, `screens/*.md`), config files, or any tracked/committed file. These are credentials for external services the user owns — not test-generated passwords.

- **Always ask the user** for the password at runtime before typing it into the webview
- If the user explicitly grants permission to store it, only then save it — and note it was user-authorized
- Do not log or echo the password in session output

### Step-by-step flow

1. **Trigger Google sign-in:**
   - From `signIn_screen`: tap `"Continue with Google"` by label (standard tap works)
   - From `onboarding_signUp_screen`: use `tapScreen` with `frames` coordinates (standard tap crashes xcodebuild)

2. **System dialog "Wants to Use google.com to Sign In":**
   - `handlePermission` does NOT work — this is an ASWebAuth dialog, not a standard permission alert
   - Take screenshot to confirm dialog is showing
   - Use `tapScreen` with `frames` coordinates to tap "Continue"
   - On fresh/reset simulators this always appears; on subsequent runs it may be skipped

3. **Google email page:**
   - `type` action works: target `"Email or phone"`, value = email address, `clearFirst: true`
   - **VERIFY email after typing:** take a screenshot to confirm the email is correct. Webview autocorrect can corrupt the input (e.g., "gmail.com" → "gmail.como"). If wrong, retype with `clearFirst: true`
   - **Dismiss keyboard before tapping Next:** the keyboard covers the Next button. Tap the checkmark (✓) on the keyboard toolbar (label `"selected"` in frames) to dismiss, then get fresh `frames` for Next
   - Run `frames` to get exact "Next" button position — only after keyboard is dismissed
   - `tapScreen` at Next button center
   - Wait 5-10s for password page to load

4. **Google password page:**
   - **Ask the user for the password** — do not assume or reuse from prior sessions
   - `type` action works: target `"Enter your password"`, `secure: true`, `clearFirst: true`
   - **Dismiss keyboard** (tap checkmark `"selected"` on toolbar) before tapping Next — same as email page
   - **CRITICAL: Run `frames` again** — button positions shift between pages. Never reuse coordinates from the email page.
   - `tapScreen` at new Next button center
   - Wait for Google to authenticate (shows "One moment please...")

5. **Google consent page ("You're signing back in to Aura"):**
   - May appear on first sign-in or after long absence
   - "Continue" and "Cancel" buttons sometimes appear in XCTest tree — try `tap` by label first
   - Fall back to `tapScreen` with `frames` if label tap fails
   - Wait for redirect back to app

6. **"Save Password?" system dialog:**
   - Tap `"Not Now"` by label — this dialog IS visible to XCTest

7. **App resumes:**
   - `waitFor` target `onboarding_valueProps_screen` (new users) or `home_screen` (returning users), timeout 60

### Key rules
- **Screenshot at every step** — webview state is not reliably reflected in the accessibility tree
- **Verify email/text after typing** — webview autocorrect can silently corrupt input
- **Dismiss keyboard before tapping buttons** — keyboard covers webview buttons; use the checkmark (label `"selected"`) on the keyboard toolbar to dismiss
- **Fresh `frames` before every `tapScreen`** — positions change between webview pages and after keyboard dismiss
- **Never reuse coordinates from a previous page**
- **`type` works on webview fields** — target by placeholder label
- **System dialog may be skipped** — the "Wants to Use google.com" dialog appears on first use but may be skipped on subsequent runs (even after simulator reset) if the permission was cached
- This is a third-party product (Google) — layout may change independently of the app

## Quick Processing (CRITICAL RULES)

- **Default to chat/text mode** — use "Start voice mode" only when voice is under test (see Voice Mode below)
- QP has ~4 exchanges. Always complete ALL prompts until the completion/rating screen appears
- **Never close early** — answer every prompt: type → send → wait for AI response → check for Continue → repeat
- The "Continue" button may NOT appear between exchanges — if a new question field appears directly, type the answer
- Returning users skip the "Get Started" intro sheet — chat opens directly after tapping QP button
- After AI response, wait up to 30s. Check for "Continue" OR a new "Your answer" field
- Completion screen has rating buttons (GREAT/OKAY/NOT HELPFUL) + "Done for now" → tap rating then "Done for now"
- Like/Dislike buttons on AI response screens are optional feedback — not required to proceed

## Home "For today" practices

- Home renders **no feed** — no "FOR YOU" header and no feed cards (`home_affirmationsCard`, `home_dailyGroundingCard`, `home_useCasesCard`, … never appear). Don't wait for them. There is no Daily Grounding screen; Use Cases (`useCases_screen`) opens only by the `aurie://useCases` deep link.
- Every user sees the Daily Practices list (`home_forTodaySection`); rows are `dailyPractices_row_<index>`. See `knowledge/screens/dailyPractices_forToday.md` for where each practice leads.

## Navigation / Back Buttons

- Most screens use **custom Back buttons** (not UINavigationBar). The `back` action type will fail.
- Always use `tap` with target `"Back"` or `"Go back"` instead
- The executor auto-falls back to coordinate tap if standard tap fails on non-hittable elements

## Voice Mode — drivable by acoustic coupling

Entry points: `home_voiceButton` (home), `"Start voice mode"` (Quick Processing),
`"Switch to voice input"` (conversation_screen, conversationDetail_screen).

- **Speak** by playing TTS out of the Mac speakers — the Simulator's mic is the Mac's mic:
  `say -o /tmp/x.aiff "..."; afplay /tmp/x.aiff`
- **First entry:** app "Permissions needed to talk with Aurie" sheet → iOS mic dialog
  (`handlePermission` allow — not in `elements.alerts`) → connect. Free users get
  `voiceIntro_screen` after the grant.
- **Mic already granted:** the Home voice button connects immediately. An extra tap on the
  mic **mutes** the session — read state after the entry tap.
- **Turn-taking:** after each line, `waitFor` `agent_speaking` then `agent_listening` (hidden element; `agent_disconnected` until the session is live) before the next line.
- **Not deterministic** (volume, room noise) — assert a turn committed and Aurie replied,
  never an exact transcript.
- Full procedure: `knowledge/rules.md` § Voice mode.

## Settings Sidebar

- All rows are Buttons (not StaticTexts) due to `.accessibilityElement(children: .combine)`
- Rows: Notifications, Account and Data, About, Log out, Contact Us, Join Slack Community, Rate Aurie 5-Stars
- External links (Contact Us, Join Slack, Rate) open external apps — avoid in automated tests

## Notifications Screen

- Toggle switches accessible via `app.switches` with labels "Daily Notifications", "Journal Notifications"
- Loading indicator appears first — wait for toggle labels before interacting
- Nested Switch elements: outer wraps row, inner is actual control

## Account and Data Screen

- Shows "First name: {value}", "Email address: {value}" as labels
- "Manage My Data" button opens bottom sheet for data deletion

## Affirmation Screen

- Full-screen affirmation display with "Press & hold" interaction and "Back" button
- Opened from the "For today" affirmation practice row, or from an affirmation notification / deep link (free users get the paywall there instead)

## Journal/Chats Screen

- Two tabs: "Journals" (journals_tab) and "Chats" (conversations_tab)
- Lists past journal entries and conversation history
- Each row tappable to view details

## Paywall (pricing-tiers onboarding + package paywall)

Shown when the user has not yet seen the first-time subscription modal. All paywall screens require `safetyConstraints.allowPurchases: false`. The whole lead-in lives inside one full-screen cover (`pricingTiers_onboarding_screen`).

### paywall_intro_screen — lead-in step 1
- Detected by `paywall_intro_continueButton` in buttons
- Tap `paywall_intro_continueButton` to advance (~0.4s opacity animation)
- No close button on this screen — must tap Continue or swipe the cover down

### paywall_socialProof_screen — lead-in social-proof step
- Social proof cards; no close button
- Tap `paywall_socialProof_continueButton` to advance to the paywall

### packagePaywall_screen — the paywall itself
- **NEVER tap the CTA** — triggers a real purchase
- Products may still be loading on arrival from the lead-in — wait for the CTA before interacting with plan cards
- Close via `packagePaywall_closeButton`. It is legitimately **absent** under `hardPaywall` variant `a` and in the catalog error state — use a ≥10s timeout and screenshot before concluding the screen is broken
- **Restore flow:** tap the restore button → Apple sign-in alert auto-dismissed → result sheet appears → tap `subscriptionResult_okButton` to dismiss → close the paywall
- When the `aurieWebViewPaywall3` gates pass, the paywall renders as `paywall_web_screen` (a Stripe WebView overlay) instead; treat the WebView area as opaque and leave via `paywallWeb_closeButton`

See `knowledge/screens/package_paywall_screen.md`, `package_paywall_webview.md`, and `pricing_tiers_onboarding.md` for the full element reference.

## Quiz, Signup, Permissions, Delete Account

All the same screen-specific rules from the knowledge file apply. Refer to `knowledge/` (split into `rules.md`, `flows.md`, `workarounds.md`, `screens/*.md`) for details.

## General Rules

- **Error recovery**: Try alternative selectors on failure
- **Combined accessibility elements**: Settings rows are buttons, use label targeting
- **ZStack identifiers**: Not visible in XCTest, use child elements

## Coordinate Tap Safety (CRITICAL)

**Default: Do NOT use `coordinateTap: true`.** Standard taps work for most elements and are safer across different screen sizes. XCTest's standard `.tap()` handles scroll-into-view automatically.

**When `coordinateTap` IS needed:**
- Capsule-clipped buttons (`.clipShape(Capsule())`) that report `isHittable == false`
- Elements where standard tap hits the wrong target due to overlapping accessibility frames

**Safety rules for coordinate taps:**
1. **Never use coordinate tap blindly** — on different screen sizes (Pro vs Pro Max), element positions differ and a tap at the center of one element's frame may hit a neighboring element
2. **The framework rejects container-sized elements** — if the resolved element covers >80% of the screen, the coordinate tap will fail with an error asking you to verify positions
3. **When coordinate tap fails**, use the `frames` command to inspect actual element positions:
   ```bash
   python3 sim_bridge.py --session $SID frames
   ```
   This returns a JSON array of interactive elements with `{x, y, w, h}` frame rects
4. **Prefer label-based targeting** over identifier when coordinates are needed — labels resolve to smaller, more specific elements
5. **For text fields**: NEVER use coordinate tap — standard `type` action with field identifier is always safe
