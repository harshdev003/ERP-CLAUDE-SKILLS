---
paths:
  - "AuraConsumerApp/**/Views/**"
  - "AuraConsumerApp/**/Components/**"
  - "AuraConsumerApp/**/Modals/**"
  - "AuraConsumerApp/**/*View.swift"
  - "AuraConsumerApp/Shared/**"
  - "AuraConsumerApp/Services/Theme/**"
  - "AuraConsumerAppUITests/**"
  - "AuraConsumerAppSimTests/**"
---

# Accessibility

## Related Rules

- `coding.md` for general Swift style and MARK ordering.
- `naming.md` for identifier naming conventions.
- `testing.md` for UI test synchronization patterns.
- AI Knowledge → **Design/Accessibility Baseline** for where the contrast and size numbers come from, and **Design/Platforms/{Apple, Google, Web}** for the primary sources.

## Purpose

Every SwiftUI view must be testable via UI tests and usable with assistive technologies (VoiceOver, Switch Control). Accessibility identifiers are the bridge between production code and the test suite.

## Apostrophe/Curly-Quote Text Truncation (MANDATORY)

SwiftUI splits `Text` nodes at apostrophes and curly quotes (`'`, `'`), causing VoiceOver to read only the fragment before the split. This affects **every** string containing a contraction (`I'm`, `don't`, `we'll`, `you're`, `what's`, `it's`) or possessive (`Aurie's`).

**Rule**: Any `Text` view whose string contains an apostrophe or curly quote **must** have an explicit `.accessibilityLabel()` with the full, unsplit string.

```swift
// ❌ BAD — VoiceOver reads "I" instead of "I'm here to help you"
Text("I'm here to help you")

// ✅ GOOD — VoiceOver reads the full string
Text("I'm here to help you")
    .accessibilityLabel("I'm here to help you")
```

For concatenated `Text` views (`Text(...) + Text(...)`), apply `.accessibilityElement(children: .ignore)` and `.accessibilityLabel()` on the **parent container** with the full combined string:

```swift
// ✅ GOOD — parent provides the complete label
VStack {
    (Text("That's why I'm here. ") + Text("lighter").italic())
    Text("We'll slow things down.")
}
.accessibilityElement(children: .ignore)
.accessibilityLabel(
    "That's why I'm here, lighter. " +
    "We'll slow things down."
)
```

**When adding or editing Text views**: check for apostrophes/contractions and add `.accessibilityLabel()` in the same change. Do not defer this to a follow-up.

## Accessibility Identifiers (MANDATORY)

**Every new or modified SwiftUI View MUST have accessibility identifiers.** This is not optional — identifiers are required for VoiceOver, UI tests, and autonomous sim tests. Missing identifiers cause test failures, ambiguous element matches, and coordinate-tap fallbacks that break across screen sizes.

### Screen-Level Identifiers

Every screen-level View **must** set its screen identifier on its root container, with
`.screenAccessibilityIdentifier(_:)` — **not** a bare `.accessibilityIdentifier`:

```swift
// ✅ GOOD — screen detectable, every control keeps its own id
var body: some View {
    VStack { ... }
        .screenAccessibilityIdentifier("journal_screen")
}

// ❌ BAD — the id spreads onto the controls inside and overrides their ids
var body: some View {
    VStack { ... }
        .accessibilityIdentifier("journal_screen")
}
```

**Why:** a bare `.accessibilityIdentifier` on a plain layout container (`VStack`, `ZStack`)
is applied to every element below it that doesn't declare itself a container. The
controls then report `journal_screen` instead of `journal_saveButton`, so id-based taps
fail and tests fall back to labels. Passing explicit ids to the children does not stop
it. On a container that is already an accessibility element, the id can vanish instead.
`.screenAccessibilityIdentifier` (`Shared/ViewModifiers/ScreenIdentifierModifier.swift`)
puts the id on a silent 1×1 background element, so the screen is found and the
children are untouched.

**Sections and lists** that need an id of their own (not a screen) pair it with
`.accessibilityElement(children: .contain)`, which keeps the id on the container and
leaves the children addressable:

```swift
// ✅ GOOD
LazyVStack { ForEach(...) { ... } }
    .accessibilityElement(children: .contain)
    .accessibilityIdentifier("practicePickerList")
```

Format: `"{feature}_screen"` — always **snake_case**, always ending with literal `_screen`.

**CRITICAL**: The sim test framework uses `hasSuffix("_screen")` to detect screen identifiers. CamelCase endings like `quizScreen` or `signUpScreen` are **invisible** to the parser and will cause screen detection failures.

```swift
// ✅ GOOD — snake_case, ends with _screen
.screenAccessibilityIdentifier("onboarding_quiz_screen")
.screenAccessibilityIdentifier("conversation_history_screen")

// ❌ BAD — camelCase "Screen" not detected by hasSuffix("_screen")
.screenAccessibilityIdentifier("onboarding_quizScreen")
.screenAccessibilityIdentifier("conversation_historyScreen")
```

For multi-word screen names, use snake_case for the `_screen` suffix but camelCase is acceptable for the feature/sub-feature portion: `"onboarding_valueProps_screen"`, `"onboarding_signUp_screen"`.

### Interactive Elements

All interactive elements **must** have an identifier:

| Element | Format | Example |
|---------|--------|---------|
| Buttons | `{feature}_{action}Button` | `"journal_saveButton"` |
| Text fields | `{feature}_{name}Field` | `"login_emailField"` |
| Secure fields | `{feature}_{name}SecureField` | `"login_passwordSecureField"` |
| Toggles | `{feature}_{name}Toggle` | `"settings_notificationsToggle"` |
| Pickers | `{feature}_{name}Picker` | `"profile_agePicker"` |
| Tabs | `{feature}_tab` | `"journals_tab"` |
| Links | `{feature}_{name}Link` | `"welcome_privacyPolicyLink"` |
| Cards | `{feature}_{name}Card` | `"home_dailyGroundingCard"` |

### Identifier Uniqueness (CRITICAL)

Identifiers **must be unique within a navigation stack**. Generic names like `onboarding_continueButton` cause ambiguous matches when multiple screens share the same stack. Use screen-specific prefixes:

```swift
// ❌ BAD — generic, conflicts with other screens in the same nav stack
.accessibilityIdentifier("onboarding_continueButton")

// ✅ GOOD — screen-specific, unambiguous
.accessibilityIdentifier("onboarding_didYouKnow_nextButton")
.accessibilityIdentifier("onboarding_signUp_continueButton")
.accessibilityIdentifier("quizInsight_nextButton")
```

### Non-Interactive State Elements

Add identifiers to elements that represent state changes tests need to observe:

```swift
// ✅ GOOD — test can wait for loading to finish
if viewModel.isLoading {
    ProgressView()
        .accessibilityIdentifier("journal_loadingIndicator")
}

// ✅ GOOD — empty state
Text("No journals yet")
    .accessibilityIdentifier("journal_emptyState")
```

### Lists and Collections

For dynamic content, use indexed identifiers:

```swift
// ✅ GOOD
ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
    JournalRow(item: item)
        .accessibilityIdentifier("journal_row_\(index)")
}
```

## Accessibility Labels and Hints

### Labels (MANDATORY for interactive elements)

Every interactive element must have a clear `accessibilityLabel`:

```swift
// ✅ GOOD — descriptive label for screen readers
CircularIconButton(icon: "speaker.wave.2", action: { ... })
    .accessibilityLabel("Mute audio")

// ❌ BAD — no label, VoiceOver reads "button"
CircularIconButton(icon: "speaker.wave.2", action: { ... })
```

### Hints (recommended for non-obvious actions)

```swift
Button("Skip") { viewModel.next() }
    .accessibilityLabel("Skip")
    .accessibilityHint("Skips introduction narration")
```

### Dynamic Labels

Update labels to reflect current state:

```swift
// ✅ GOOD — label reflects toggle state
.accessibilityLabel(isMuted ? "Unmute audio" : "Mute audio")
```

## Never Nest `.accessibilityElement(children: .combine)` (CRITICAL)

When a **parent container** applies `.accessibilityElement(children: .combine)` with an identifier, label, and hint, **child views must NOT also apply `.combine`**. Double-nesting `.combine` causes:

- Incorrect accessibility frame calculation in scroll views
- Tap interception — XCTest `coordinateTap` lands on the wrong element
- The parent's identifier works but the tap activates the wrong child's action

```swift
// ❌ BAD — double .combine causes tap interception in lists
struct ChildCard: View {
    var body: some View {
        Button(action: onTap) { content }
            .accessibilityElement(children: .combine)  // ← REMOVE THIS
            .accessibilityLabel("Card label")          // ← parent provides this
    }
}

struct ParentCardView: View {
    var body: some View {
        ChildCard(...)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("home_card")
            .accessibilityLabel("Card label")
    }
}

// ✅ GOOD — single .combine at the parent level only
struct ChildCard: View {
    var body: some View {
        Button(action: onTap) { content }
            .buttonStyle(PlainButtonStyle())
    }
}

struct ParentCardView: View {
    var body: some View {
        ChildCard(...)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("home_card")
            .accessibilityLabel("Card label")
    }
}
```

**Rule**: The outermost reusable container owns accessibility. Child components that are always used inside a parent container must **not** duplicate `.accessibilityElement(children: .combine)`, `.accessibilityLabel`, or `.accessibilityHint`.

## Tappable Views Using `onTapGesture` (MANDATORY)

Views that use `onTapGesture` instead of `Button` are **not exposed as buttons** in the accessibility tree. XCTest `app.buttons` queries cannot find them. Always add `.accessibilityAddTraits(.isButton)`:

```swift
// ❌ BAD — XCTest cannot find this as a button
.onTapGesture(perform: onCardTap)
.accessibilityElement(children: .combine)
.accessibilityLabel("Journal entry")

// ✅ GOOD — exposed as a button in accessibility tree
.onTapGesture(perform: onCardTap)
.accessibilityElement(children: .combine)
.accessibilityAddTraits(.isButton)
.accessibilityLabel("Journal entry")
```

## Hidden Decorative Elements

Hide purely decorative elements from assistive tech:

```swift
// ✅ GOOD — background images, decorative icons
Image("chatBackground")
    .accessibilityHidden(true)

// ✅ GOOD — animated character not meaningful for VoiceOver
CharacterViewAnimated(isSpeaking: true)
    .accessibilityHidden(true)
```

## XCTest Visibility Rules (CRITICAL)

Elements that UI tests or sim tests need to find **must remain in the accessibility tree**. XCTest queries (`descendants(matching: .any)`, `app.buttons`, `app.staticTexts`) only find elements that are in the accessibility tree.

### `accessibilityHidden` hides from VoiceOver, not from XCTest

Measured on the iOS 26 runtime: XCTest still lists — and can tap — views marked
`.accessibilityHidden(true)`, and fully transparent (`.opacity(0)`) views too, while
VoiceOver cannot reach either. So:

- Don't rely on hiding an element to keep a test away from it.
- Don't infer what VoiceOver sees from an XCTest dump — check with VoiceOver.

For test-only state markers the convention stays the silent 1×1 element (what
`.audioStatusAccessibility(ttsState:)` uses):

```swift
Color.clear
    .frame(width: 1, height: 1)
    .accessibilityElement(children: .ignore)
    .accessibilityIdentifier("audio_playing")
    .accessibilityLabel("")
```

### Zero-size frames can cause XCTest to skip elements

Use at least `1x1` for test-only elements. A `0x0` frame may be optimized away.

```swift
// ❌ BAD — may not appear in accessibility tree
Color.clear.frame(width: 0, height: 0)

// ✅ GOOD — guaranteed to exist
Color.clear.frame(width: 1, height: 1)
```

### Capsule-clipped buttons require coordinate taps

SwiftUI buttons clipped with `.clipShape(Capsule())` or custom shapes may report `isHittable == false` in XCTest even though they are visible. Use coordinate-based tapping:

```swift
// ✅ GOOD — works with clipped shapes
button.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()

// ❌ BAD — may fail on capsule-clipped buttons
button.tap()
```

### Bottom sheets and modals

Elements in bottom sheets (`.sheet`, `.fullScreenCover`, custom overlays) may not appear in `app.buttons` queries until the sheet animation completes. Always wait for element existence before interacting:

```swift
// ✅ GOOD — wait for sheet to present
let deleteButton = app.buttons["delete_confirmButton"].firstMatch
XCTAssertTrue(deleteButton.waitForExistence(timeout: 10))
```

### SwiftUI text in buttons

SwiftUI buttons with `Text` content expose the text as `accessibilityLabel`. When searching for buttons by label, use `NSPredicate`:

```swift
// ✅ GOOD — find button by its text label
let button = app.buttons.matching(
    NSPredicate(format: "label == 'Delete'")
).firstMatch
```

## Audio Status for UI Tests

Views that play audio **must** use the `audioStatusAccessibility` modifier so tests can synchronize on audio state instead of using `Thread.sleep`:

```swift
// ✅ GOOD — exposes test-visible element with audio state identifier
.audioStatusAccessibility(ttsState: viewModel.ttsState)
```

The modifier uses `accessibilityElement(children: .ignore)` + empty label to stay in the XCTest tree without announcing to VoiceOver.

Available identifiers:

| Identifier | TTSState |
|------------|----------|
| `audio_playing` | `.playing` |
| `audio_stopped` | `.idle`, `.paused` |
| `audio_loading` | `.loading` |
| `audio_error` | `.error` |

Test usage:

```swift
let playing = app.descendants(matching: .any)["audio_playing"].firstMatch
XCTAssertTrue(playing.waitForExistence(timeout: 10))
```

## Text Input in Tests

When typing into text fields during UI tests, autofill and autocorrect can corrupt input. Use the paste-text pattern:

```swift
// ✅ GOOD — triple-tap to select all, delete, then type fresh
field.tap()
field.tap(withNumberOfTaps: 3, numberOfTouches: 1)  // select all
app.keys["delete"].tap()                              // clear
field.typeText("user@example.com")
```

Disable autocorrection and autofill on text fields that tests interact with:

```swift
TextField("Email", text: $email)
    .textContentType(.none)         // disable autofill
    .autocorrectionDisabled(true)   // disable autocorrect
    .accessibilityIdentifier("signup_emailField")
```

## Contrast and Size (MANDATORY)

The numbers below are the reconciled bar from AI Knowledge → **Design/Accessibility
Baseline**, which resolves Apple's HIG, Material 3 and WCAG 2.2 AA. They are also the
*Accessibility — the bar* card in the [Aurie design system](https://claude.ai/design/p/963e5951-4785-4308-adfa-2ec8bef9b69d).
Change them there, not here.

| Rule | Minimum | Source |
|------|---------|--------|
| Body text contrast | **4.5:1** | WCAG 1.4.3 AA; Apple and Material concur |
| Large text contrast | **3:1** — large is ≥18 pt, or ≥14 pt bold | WCAG defines large; Apple gives the ratio but never defines it |
| Non-text: icons, borders, focus rings, control states | **3:1** | WCAG 1.4.11 AA |
| Tap target | **44 × 44 pt** | Apple's floor. WCAG AA asks only 24 × 24 px — passing AA is not passing this |
| Minimum text size | **11 pt** — 8 pt for decoration only | Apple. Material states none |
| Text scaling | **200%** without loss | WCAG 1.4.4 AA — Dynamic Type via text styles on the text role, `relativeTo:` on the display face |
| Colour alone | never the only signal | WCAG 1.4.1, Level **A** — the easiest criterion, and the one most often missed |

### Measure contrast in every sky, not on white

The app is locked to light mode but reads the time-of-day sky (`SkyState`), so **dusk and
night are its dark mode**. A colour that passes at midday and fails at night has failed.

Measure against the **worst case**, and behind any blur or scrim that sits over the
colour — not against the flat token on a white artboard. Four contrast bugs shipped
because the check was done on one sky: the Quick Processing intro sheet, the home sheets
(dark text on dark glass), the PMF survey sheet, and the sign-up password rules at
**1.6:1** on the night sky.

**Name the role; the measurement is in it.** `skyState.textPrimary`, `textSecondary`,
`textTertiary`, `textInverted` and the `figure*` roles in `Colors.swift` resolve per sky,
and the secondary and tertiary opacities are set per sky to the least that clears the bar
on that sky's worst gradient stop. A hand-written `prefersLightForeground ? … : …` is a
place the measurement has to be redone by hand; the role is a place it cannot be skipped.

The screenshot store keeps three pictures per screen — anytime, light and dark — for
exactly this. Apple asks for the same in its own words: test in light *and* dark, with
Bold Text, Increase Contrast and Reduce Transparency enabled.

### Pad the hit area when the visual is smaller

A 24 pt icon button is a 24 pt target unless you say otherwise. `.contentShape` extends
the tappable area without changing what is drawn — the app already does this in ~79
places.

```swift
// ❌ BAD — a 24 pt glyph is a 24 pt target
Button(action: close) { Image(systemName: "xmark").frame(width: 24, height: 24) }

// ✅ GOOD — 24 pt glyph, 44 pt target
Button(action: close) {
    Image(systemName: "xmark")
        .frame(width: 24, height: 24)
        .frame(width: 44, height: 44)      // hit area
        .contentShape(Rectangle())
}
.accessibilityLabel("Close")
```

### Text size: use the tokens, and name the text style

Never a literal size at a call site — `Fonts.*` tokens and the `Font.text` /
`Font.display` helpers only (see `coding.md`). The text role takes a **text style, never
a point size**: `Font.system(size:)` does not scale, so a size there is text that silently
opts out of Dynamic Type and cannot meet the 200% rule. On the display helpers, **always
pass `relativeTo:`** for the same reason.

**When a size goes up, check what was sized around it.** Raising text from a fixed
tiny size to a scaling style is never just the font line: the container was calibrated
for the old text. Four layouts broke the day the 11 pt floor was enforced — a rotated
chart label whose offset assumed its old width, a tab bar whose reserved height
(`HomeTabBarMetrics.dockedContentHeight`) was the old row, a `lineLimit(3)` on legal text,
and two 66 pt cards clipping their subtitle. Look for a fixed `frame(height:)`, a
`lineLimit`, a rotation offset, a reserved-height constant, or a `.dynamicTypeSize` clamp
sized to the old text, and reconcile it in the same change.

Every type token is at or above 11 pt — the three 10 pt `font250Default*` tokens were
retired and their 32 call sites moved to `caption2Regular` and its siblings.

**The decorative exception.** Design keeps Caption 2 (10 pt) and Caption 3 (8 pt) for
**decoration only**: never running text, never a label a person has to read to use the app.
They exist as `Fonts.caption2Decorative` (10 pt) and `Fonts.caption3Decorative`
(8 pt), and **the name carries the constraint** (`caption2Decorative`, not
`font250DefaultRegular`). The retired tokens are the argument: `font250DefaultRegular`
reads like a body token, so it set sidebar, sign-up and feed-card copy at 10 pt across 32
call sites. A number alone is a loophole; a name is the rule.

### Colour is never the only signal

Pair a state colour with an icon, text or shape. The sign-up password rules are the
pattern to copy: colour *and* a check icon, so the state survives both a colour-blind
reader and a greyscale screenshot.

## Checklist for New Views

When creating any new SwiftUI View:

- [ ] Root container sets `{feature}_screen` with `.screenAccessibilityIdentifier` (snake_case, ends with literal `_screen`) — never a bare `.accessibilityIdentifier` on a layout container
- [ ] Section/list ids that aren't screens are paired with `.accessibilityElement(children: .contain)`
- [ ] All buttons have `{feature}_{action}Button` identifier + `accessibilityLabel`
- [ ] All text fields have `{feature}_{name}Field` identifier
- [ ] Text fields have `.textContentType(.none)` and `.autocorrectionDisabled(true)` if tests type into them
- [ ] State indicators (loading, empty, error) have identifiers
- [ ] Decorative images use `.accessibilityHidden(true)`
- [ ] Audio-playing views use `.audioStatusAccessibility(ttsState:)`
- [ ] Dynamic labels reflect current state (mute/unmute, play/pause)
- [ ] Capsule-clipped buttons are documented for coordinate-tap in tests
- [ ] Bottom sheet elements have identifiers on confirm/cancel actions
- [ ] No nested `.accessibilityElement(children: .combine)` — only the outermost container uses `.combine`
- [ ] Views using `onTapGesture` have `.accessibilityAddTraits(.isButton)` after `.combine`
- [ ] Background animations (Rive, Lottie) use `.accessibilityHidden(true)`
- [ ] Text views with apostrophes/contractions (`'`, `I'm`, `don't`, `we'll`, `Aurie's`) have explicit `.accessibilityLabel()` with the full string
- [ ] Body text is **4.5:1** against its background, large text **3:1** — checked on the anytime, light **and** dark sky, and behind any blur or scrim over it
- [ ] Icons, borders, focus rings and control states are **3:1**
- [ ] Every tap target is **44 × 44 pt**; `.contentShape` pads the hit area where the visual is smaller
- [ ] No text below **11 pt** — the only exception is decorative text down to 8 pt, under a token whose name says so — and every text-role font names its text style and every display size passes `relativeTo:`, so it scales to 200%
- [ ] A text size that went up had its container checked — fixed heights, line limits, rotation offsets, reserved-height metrics, Dynamic Type clamps
- [ ] No state is conveyed by colour alone — it is paired with an icon, text or shape
- [ ] Orb, sky and Rive animations honour Reduce Motion
