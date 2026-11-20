---
paths:
  - "src/**"
  - "app.js"
  - "AuraSimTests/**"
description: Accessibility for React Native — labels, roles, traits, dynamic state
globs: src/**/*.{js,jsx}
alwaysApply: false
---

# Accessibility

Every screen should be usable with VoiceOver (iOS) and TalkBack (Android). Accessibility is not a polish step — it's part of the definition of done for any new UI.

**See also: [`AuraSimTests/knowledge/workarounds.md`](../../AuraSimTests/knowledge/workarounds.md)** — the production-learnings catalog (iOS auto-promotion, the hidden-anchor pattern, overlay collapse, the Save-Password springboard interception, and other hard-won quirks). Per-screen catalog: [`AuraSimTests/knowledge/screens/`](../../AuraSimTests/knowledge/screens/) (130+ files).

## Touchables

Every tappable element needs an `accessibilityLabel` and a `accessibilityRole`.

```js
// ✅ GOOD
<TouchableOpacity
  onPress={onSave}
  accessibilityRole="button"
  accessibilityLabel={t('common.save')}
>
  <Text>...</Text>
</TouchableOpacity>

// ❌ BAD — screen reader announces nothing useful
<TouchableOpacity onPress={onSave}>
  <Text>...</Text>
</TouchableOpacity>
```

If the label is a translated string, pass `t('...')` directly — don't reuse a shortened/abbreviated form for the label.

## Roles

Common roles: `button`, `link`, `header`, `image`, `imagebutton`, `text`, `adjustable`, `summary`, `switch`, `checkbox`, `radio`, `tab`. Match the visual meaning.

## Labels

- **Icon-only buttons** must have a label — VoiceOver has nothing to read otherwise.
- **State changes** should be reflected in the label: `accessibilityLabel={isMuted ? t('a11y.unmute') : t('a11y.mute')}`.
- **Decorative images / animations** (Lottie, background art) should be hidden:

```js
<LottieView source={...} accessibilityElementsHidden importantForAccessibility="no-hide-descendants" />
<Image source={...} accessible={false} />
```

## Hints

Use `accessibilityHint` when the action is non-obvious from the label alone:

```js
<TouchableOpacity
  accessibilityRole="button"
  accessibilityLabel={t('player.skip')}
  accessibilityHint={t('a11y.player.skipHint')}
  onPress={onSkip}
/>
```

## Live Regions

For elements whose text changes in response to user interaction (loading status, timer, validation message), set `accessibilityLiveRegion='polite'` on Android and use `AccessibilityInfo.announceForAccessibility(...)` on iOS for important transitions.

## Focus Management

When a modal or sheet opens, move focus to the first meaningful element using `AccessibilityInfo.setAccessibilityFocus(reactTag)` — otherwise VoiceOver stays on whatever was behind the overlay.

## Testing

- Turn on VoiceOver / TalkBack and walk through any new screen before merging.
- Check that the order in which the screen reader visits elements matches the visual order.
- Check dynamic font scaling: set the system font size to its largest value and confirm layouts don't break (use `allowFontScaling` deliberately if you need to cap it).

## Don't

- Don't rely on color alone to convey state (success vs error). Add an icon, a label, or a shape.
- Don't disable touch feedback (`activeOpacity={1}` everywhere) — users with motor impairments rely on the visual ack.
- Don't use long, unstructured paragraphs as a single `accessibilityLabel` — split into smaller labelled elements.

## Checklist for New Screens

- [ ] Every touchable has `accessibilityRole` and `accessibilityLabel`
- [ ] Icon-only buttons have a translated label
- [ ] Decorative images use `accessible={false}` / `accessibilityElementsHidden`
- [ ] State-changing buttons update their label when state changes
- [ ] Modals move focus to the first meaningful element
- [ ] Manual VoiceOver pass + TalkBack pass on at least one device
- [ ] Root container has `testID="<feature>_screen"` (snake_case, ends with literal `_screen`)
- [ ] Interactive elements have `testID="<feature>_<action>Button"`, `testID="<feature>_<name>Field"`, etc. (mirrors the AuraConsumerApp pattern — see `.claude/rules/sim-testing.md § React Native testID Propagation`)
- [ ] `testID` lives on the actual touch target / `TextInput` (or is forwarded onto one via a reusable wrapper), **never** on a non-interactive outer `<View>` — see [How testID works](#how-testid-works) below

## How testID works

`testID` is the bridge between JSX and XCUITest. Getting it wrong silently breaks sim-test coverage — the screen renders fine, accessibility looks fine, but `state.elements` can't see the element. The rules below are mechanical (how RN + iOS actually behave) rather than stylistic; they apply to every screen, not just smoke-test screens.

### The mapping

- React Native maps `testID` on a `<View>`, `<TouchableOpacity>`, `<Pressable>`, `<TextInput>`, `<Text>`, or `<Image>` directly to the iOS `accessibilityIdentifier` on the underlying `UIView`. XCUITest queries (`app.buttons["foo"]`, `app.textFields["foo"]`) read that identifier.
- On Android the same `testID` becomes the `resource-id` accessed by UIAutomator / Espresso.
- **There is no descendant propagation.** A `testID` on a parent `<View>` does **not** flow down to children. Each interactive descendant needs its own `testID`.

### Mechanics that bite

1. **`accessible={true}` collapses children.** When a `<View>` has `accessible={true}` (the default on `<TouchableOpacity>`), its descendants stop appearing as separate accessibility elements. XCUITest sees one combined node with the parent's identifier/label. Don't wrap multiple interactive children in a single accessible `<View>`; either break them out, or set `accessible={false}` on the parent so children stay queryable.

2. **`testID` must live on the actual touch target.** If a custom wrapper component takes a `testID` prop and applies it to its outer `<View>`, the inner `<TouchableOpacity>` ships with no identifier and the button is invisible to XCUITest queries by id. Always forward `testID` onto the touchable:

   ```jsx
   // ✅ GOOD — testID on the touch target
   const AppButton = ({ testID, onPress, label }) => (
     <TouchableOpacity testID={testID} onPress={onPress}>
       <Text>{label}</Text>
     </TouchableOpacity>
   )

   // ❌ BAD — testID on the wrapper, button has no identifier
   const AppButton = ({ testID, onPress, label }) => (
     <View testID={testID}>
       <TouchableOpacity onPress={onPress}><Text>{label}</Text></TouchableOpacity>
     </View>
   )
   ```

3. **`{...rest}` spreads do not carry `testID` through every component.** Many third-party wrappers (`GestureHandlerRootView`, some `Animatable.View` variants) drop `testID` / `accessible` even when they spread `accessibilityLabel`. Don't assume props pass through — verify by reading the wrapper's source, or by checking `state.elements` after a sim run.

4. **Full-screen wrappers can auto-promote on iOS.** A `<View style={{flex: 1}}>` with text-bearing descendants can be treated as a single VoiceOver "accessibility container", flattening every child into one composed label. See `⚠️ Avoid wrapping screens in <GestureHandlerRootView>` below — that's the worst offender, but any stacked full-screen wrappers above a testID-bearing root carry the same risk.

5. **Derived testIDs in lists need a per-instance suffix.** Reusing the same literal testID on every row of a `FlatList` puts duplicate identifiers in the accessibility tree; XCUITest picks one arbitrarily. Use the row's stable backend id:

   ```jsx
   renderItem = ({ item }) => (
     <View testID={`dashboard_card_${item.id}`}>
       <TrackCard track={item} />
     </View>
   )
   ```

   `index` is acceptable only if the list never reorders.

### Reusable-component rule

When you build a reusable wrapper around a touchable / input (e.g. `ActionButton`, `CapsuleButton`, `NavigationBarToolbarItem`, `NavigationBarSearchInput`, `Input`):

- Accept `testID` as a prop (and declare it in `propTypes`).
- Forward it onto the underlying `<TouchableOpacity>` / `<TextInput>` — never the outer wrapper `<View>`.
- If the component contains **multiple** interactive children (e.g. a search input + a close button), derive distinct child testIDs from the caller's id: `<testID>_input`, `<testID>_closeButton`. Document the derivation in a comment so call sites know which suffixes are surfaced.

### `Animated.createAnimatedComponent(TouchableOpacity)` swallows testID

A pattern that bites repeatedly in this codebase: `const AnimatedTouchableOpacity = Animated.createAnimatedComponent(TouchableOpacity)` then `<AnimatedTouchableOpacity testID="foo" onPress={...}>`. The testID does **not** surface in the XCTest a11y tree on iOS — it gets absorbed into the parent screen's auto-promoted accessibility container. Verified empirically on `ProgramDetails`, `CelebrityMasterClassDetails`, and `SeriesDetails` (all had `testID="backArrow"` that never surfaced).

`accessible={false}` on the AnimatedTouchableOpacity does not fix it. Wrapping in an `<Animated.View accessible={false}>` + plain `<TouchableOpacity>` inside does not fix it either — even a non-Animated `<View>` wrapper around a `<TouchableOpacity>` can absorb the child.

**Fix:** render the `<TouchableOpacity>` at the same tree depth as other queryable siblings (e.g. the screen's primary CTA). Move the `position: absolute` and other layout styles directly onto the touchable. If you need a scroll-tied opacity, keep an `<Animated.View>` only for the variant that actually animates — render a plain `<TouchableOpacity>` for the always-visible variant.

```jsx
// ❌ BAD — testID absorbed by parent's a11y container
const AnimatedTouchableOpacity = Animated.createAnimatedComponent(TouchableOpacity)
<AnimatedTouchableOpacity testID="foo_backButton" style={{ position: 'absolute', ..., opacity }} onPress={...} />

// ❌ BAD — wrapper View also absorbed
<View style={{ position: 'absolute', ... }}>
  <TouchableOpacity testID="foo_backButton" onPress={...} />
</View>

// ✅ GOOD — TouchableOpacity at top level, layout styles on the touchable
<TouchableOpacity
  testID="foo_backButton"
  style={{ position: 'absolute', ..., /* no opacity, always 1 */ }}
  onPress={...}
/>
```

If the element legitimately needs an animated opacity tied to scroll, gate on whether the animation is needed for that variant. The "always-visible inline" variant should use plain `<TouchableOpacity>`; the "scroll-fade header" variant can keep `AnimatedTouchableOpacity` (with `accessible={false}`), but most call sites do not need a second variant at all.

### Verifying

A testID is "working" iff:

1. It appears as `identifier="..."` in the iOS hierarchy dump (`sim_bridge.py hierarchy`).
2. It surfaces in `state.elements` after `sim_bridge.py wait` / `read`.
3. `tap` by that id from sim_bridge succeeds (not `lastAction.success: false`).

If (1) is fine but (2) fails, the screen has an a11y-collapse problem above the testID — look up the tree for a full-screen wrapper or `accessible={true}` parent. If (3) fails but (2) passes, the element is queryable but not hittable (off-screen, occluded, `disabled`, or `pointerEvents: 'none'`).

If **both (1) and (2) fail** — the hierarchy dump collapses to a small number of nested full-screen `Other` nodes carrying a single merged label of every descendant's text, and `state.elements` comes back empty even though the screenshot shows a fully rendered screen — you're hitting a top-level full-screen wrapper auto-promotion (the GHR signature; see `⚠️ Avoid wrapping screens in <GestureHandlerRootView>` below). Adding `accessible={false}` or `importantForAccessibility="yes"` on inner Views won't fix it — the collapse happens at the outermost wrapper. Remove or replace the wrapper instead.

## sim-test `testID` convention

The `/sim-test` skill drives the simulator by reading the iOS accessibility tree. RN `testID` maps directly to iOS `accessibilityIdentifier`, so testIDs in JSX become the targets the sim test taps and waits on.

**Screen root**: `<View testID="<feature>_screen">` — snake_case, **must end with literal `_screen`** (the parser does `hasSuffix("_screen")`).

```js
// ✅ GOOD — snake_case, _screen suffix
<View testID="meditationPlayer_screen">
<View testID="onboarding_emailForm_screen">

// ❌ BAD — camelCase "Screen" not detected
<View testID="meditationPlayerScreen">
```

**Interactive elements**: `<TouchableOpacity testID="<feature>_<action>Button">`.

**Forwarding `testID` through reusable wrappers**: when you build a component that wraps a `<TouchableOpacity>`, forward `testID` as a prop onto the inner touchable, not the outer `<View>`. Putting it on the wrapper `<View>` makes the actual tap target invisible to XCUITest queries. See `.claude/rules/sim-testing.md § React Native testID Propagation` for the full rationale.

This convention is enforced opportunistically — the smoke-test screens (Welcome, Onboarding6, Login, Dashboard, EditAccount, OnboardingEmailForm) are covered as of ENGMOB-1653; the rest of `src/screens/` is added screen-by-screen as sim runs land on them.

## ⚠️ Avoid wrapping screens in `<GestureHandlerRootView>` (iOS a11y collapse)

`<GestureHandlerRootView>` from `react-native-gesture-handler` renders a native `<View style={{flex: 1}}>` that iOS auto-promotes to a single accessibility element with a composed label of every descendant's text. When this wrapper sits at the top level of a screen subtree, **the entire subtree disappears from XCUITest queries** — `state.elements` returns empty, every leaf `testID` becomes unfindable, even the wrapper's own testID stops surfacing.

Symptom (verified during ENGMOB-1692 Phase A on the Dashboard):

- `waitFor dashboard_screen` times out for 45–60s despite the screen being fully rendered
- The iOS hierarchy dump collapses to 3 nested full-screen `Other` nodes (~18 lines), all carrying the same merged label
- Adding `accessible={false}` to inner wrappers has no effect because the collapse happens at the outermost GHR-or-equivalent wrapper

**Where to put `<GestureHandlerRootView>`:**

- ✅ At app root only (once). The app currently doesn't have one at this level — see the comment in `Dashboard.js` for why.
- ✅ Around a specific sub-flow that needs custom gesture handling (e.g. `src/screens/playlists/playlist.js`, `src/screens/appReusable/GestureleftSwipeUX.js`).
- ❌ As a top-level wrapper around a screen with deep content (Dashboard / Home / any tab destination). The wrapper hides the entire subtree from XCUITest.

**On iOS, `<GestureHandlerRootView>` is a no-op natively** — `RNGestureHandlerRootViewCls` in `react-native-gesture-handler/apple/RNGestureHandlerRootViewComponentView.mm` returns `nil`. It only renders meaningfully on Android. So removing it from iOS-only render paths costs nothing for gesture handling and unblocks accessibility.

**If you must keep it,** know that prop spreading is partial — `accessibilityLabel` propagates through `{...rest}` to the inner View, but `testID` and `accessible` do NOT (empirically verified). Don't expect `<GestureHandlerRootView accessible={false}>` to fix the collapse; you'd need to patch the package itself.

## iOS auto-promotion of full-screen wrappers (general principle)

iOS treats any full-screen `<View>` with significant text-bearing descendants as a "VoiceOver accessibility container" and aggregates children's labels up to it. When that happens, descendants stop appearing as separate accessibility elements in `XCUIApplication.debugDescription`, and XCUITest queries by `testID` start failing.

This is hard to detect without running the sim test — the screen renders correctly, `accessibilityLabel`s on children get composed into the parent, but you can't tap individual elements.

Mitigations:

- **Avoid stacking multiple full-screen `<View>`s** above a `testID`-bearing root. The deeper the tree of full-screen wrappers above the testID, the more likely iOS collapses one of them.
- **Don't put `accessibilityLabel` on a parent that should be transparent to a11y traversal.** Explicit labels override iOS' auto-composition but trigger the parent to be treated as an a11y leaf.
- **Verify with a sim-test run** — `state.elements` empty + `waitFor <testID>` timing out is the diagnostic signature.

See `AuraSimTests/knowledge/workarounds.md § Dashboard Home — whole-screen accessibility collapse` for the full diagnostic history.

## Hidden screen-root anchor pattern (proven fix for tab-level collapse)

When a top-level tab/screen wraps its `_screen` testID on a `<View style={{flex:1}}>` with deep text-bearing descendants, iOS auto-promotes the wrapper and collapses the whole subtree (see preceding section). The proven fix — verified across Dashboard (PR #5918) and the Coaches + Live tabs (ENGMOB-1698) — is to **move the screen-root testID off the full-screen wrapper and onto a tiny hidden anchor View**, then render the actual content from a Fragment:

```jsx
// ❌ BAD — full-screen wrapper carries the testID, iOS auto-promotes
return (
  <View testID="coachesTab_screen" style={{ flex: 1 }}>
    <CoachingTabFeed … />
  </View>
)

// ✅ GOOD — Fragment + 1pt anchor with accessible={true}
return (
  <>
    <View
      testID="coachesTab_screen"
      // 1pt absolute anchor: present in the iOS a11y tree so XCUITest's
      // screen-root detector sees the `_screen` suffix, but small enough
      // that iOS won't promote it into an auto-collapsed container.
      accessible={true}
      style={{ width: 1, height: 1, position: 'absolute' }}
    />
    <CoachingTabFeed … />
  </>
)
```

### Anchor a11y flags — the load-bearing detail

`accessible={true}` is **mandatory** on the anchor. RN bridges every other shape to `isAccessibilityElement = NO`, which removes the testID from the XCUITest tree entirely. Verified dead ends during ENGMOB-1698 (four iterations):

| Shape | Content surfaces? | `state.screen` correct? | Why |
|---|---|---|---|
| `accessibilityElementsHidden={true}` + `importantForAccessibility="no"` | ✅ | ❌ | Both flags → `isAccessibilityElement = NO`; testID dropped |
| `accessible={false}` | ✅ | ❌ | Same: `accessible={false}` → `isAccessibilityElement = NO` |
| No flag, empty `{width:0, height:0}` View | ✅ | ❌ | RN only emits an a11y element for Views with content OR explicit `accessible={true}` |
| `accessible={true}` + 1pt absolute anchor | ✅ | ✅ | Final shape — iOS includes the leaf, no auto-promotion |

VoiceOver impact: zero. The 1pt anchor has no label and no announceable content; VoiceOver skips it the same way it skips any unlabelled empty leaf.

### When to reach for this pattern

- A screen-root `_screen` testID lives on a `<View style={{flex:1}}>` wrapping a scroll/list with deep content
- Sim verification shows `state.elements` returning only the screen-root id (descendants invisible)
- The screen does NOT use `<GestureHandlerRootView>` at top level (if it does, fix that first per the preceding section — the anchor pattern is for the auto-promotion case where GHR isn't the culprit)

## Superimposed-overlay screens: drive by label, not by testID (ENGMOB-1701 / 1707)

When a screen is mounted via `Actions.navigateTo(...)` and rendered through `Dashboard.renderSecondaryView` (CoachProfile, LiveEventDetails, programDetails, seriesDetails, etc.), it sits **inside** the `dashboard_screen` wrapper as a `position:absolute` sibling overlay. iOS auto-promotion at the dashboard level **collapses the entire overlay subtree**:

- `state.screen` keeps reporting `dashboard_screen` after navigation
- **No per-element testIDs inside the overlay surface** — the iOS hierarchy dump shows zero `identifier:` entries for the overlay; descendant labels are concatenated into the dashboard_screen's composite label
- Setting `accessible={false}` on the dashboard_screen wrapper degrades the home tab (verified + reverted in `bdbbec99a`)

So neither the hidden-anchor pattern (preceding section) **nor** the per-element-testID approach (the original Option C of ENGMOB-1701) actually surfaces ids through this layout. The earlier hypothesis in ENGMOB-1701 that "per-element testIDs surface" was wrong — verified empirically in ENGMOB-1707 session `_1779034567`.

### What DOES work: label-based tap with predicate match

`sim_bridge.py`'s `resolveElement` includes a label-predicate fallback (`NSPredicate(format: "label == %@", target)`). The composite label on dashboard_screen contains every overlay descendant's text, and XCUITest's `app.descendants(.any)[label]` query still matches against individual leaves via predicate, so:

```python
sim_bridge.py tap "Reserve Spot"     # ← works (label-based match)
sim_bridge.py tap "Share this event" # ← works
sim_bridge.py tap "Message"          # ← works (Coach profile)
```

Verified live: tapping `"Share this event"` on the LiveEventDetails overlay returned `success: true` in session `_1779034567`.

### Convention

Keep adding per-element testIDs to detail screens — they're harmless and may surface if iOS behavior changes or the layout is restructured. But sim tests targeting overlay-rendered detail screens should:

1. Confirm "we reached the screen" by `waitFor` on a known **label string** (e.g., `"Reserve Spot"`, `"Share this event"`, the coach's name)
2. Drive the screen's actions by `tap` with the **label** as target — the predicate fallback resolves it

Document this in your sim-test flow so future contributors don't waste cycles trying to make the testID surface.

### Out of scope: structural fix — verified not viable

A structural fix (pulling `renderSuperimposeView` calls out of the dashboard_screen wrapper as Fragment siblings, or moving overlays into a `<Modal>`/Portal) was originally documented as Options A/B in ENGMOB-1701. Multiple independent attempts have now confirmed it **does not surface overlay testIDs** — the collapse happens deeper in the wrapper chain than the Dashboard layer:

| Attempt | Result |
|---|---|
| ENGMOB-1701 hidden-anchor on overlay screens (Option C, session `_1779034567`) | ❌ overlay testIDs still collapsed |
| Dashboard `dashboard_screen` → 1pt anchor refactor (Section 25 attempt 1) | ❌ dashboard core preserved; overlay testIDs still collapsed |
| Dashboard full hoist of `navigationRoutes.map` + ~20 modals out of the alignItems-center wrapper as Fragment siblings (Section 25 attempt 2) | ❌ same — hierarchy identifier count unchanged, overlay subtree still composite-labelled |

`CoachProfile.js` (and other overlay screens added in ENGMOB-1694) already use the proven 1pt-anchor pattern at their own root — yet their testIDs don't surface when mounted through `renderSecondaryView`. The auto-promotion is at the navigation-wrapper layer (`<View accessible={false} position:absolute>` + `<GestureSwipe>`), which is unreachable from JS source without rewriting the navigation system to use native `<Modal>` or React Native's `<Portal>` (a cross-cutting change that affects animations, swipe-to-close, gesture handlers, and ~30 overlay-mounted screens).

**Do not re-attempt the structural refactor unless you're also signing up to rewrite the navigation system.** The label-tap-by-text workaround documented above is the right path for sim tests targeting overlay screens.

### `accessible={false}` on the navigated-view wrapper — VoiceOver impact

`Dashboard.renderSecondaryView` (`src/screens/Dashboard.js`) sets `accessible={false}` on the `<Animatable.View>` wrapping each overlay-rendered detail screen (`renderSuperimposeView` callers — CoachProfile, LiveEventDetails, programDetails, seriesDetails, webViewer, etc.). The comment in that file explains the iOS auto-promotion problem this fixes. This subsection covers the VoiceOver implication separately because the prop name reads alarming at first glance.

**What `accessible={false}` actually does in RN → iOS:**

- Bridges to `isAccessibilityElement = NO` on the underlying UIView.
- The View itself is removed from VoiceOver focus traversal — swiping right does NOT land on the wrapper as a separate stop.
- **Descendants remain unaffected.** Each child UIView that has `isAccessibilityElement = YES` (every `<TouchableOpacity>` does by default, every `<Text>` with content does, every `<Image>` with a label does) continues to surface as its own VoiceOver focus stop. VoiceOver still reads them one at a time on swipe-right.
- Setting `accessible={false}` is **not** the same as `accessibilityElementsHidden={true}` (which DOES hide descendants from VoiceOver). The two flags are independent.

**Why this is correct, not regressive:**

Without `accessible={false}` on the navigated-view wrapper, iOS auto-promoted the full-screen `<View>` into a single VoiceOver accessibility container with a composite label of every descendant's text. That is the WORSE VoiceOver experience — VoiceOver users would hear one giant string of concatenated coach name + event time + button labels + descriptions, with no individual focus stops. With `accessible={false}`, descendants regain their individual focus stops and labels read correctly.

Verified by:

- The sibling `dashboard_screen` wrapper has carried `accessible={false}` since PR #5918 (Dashboard a11y-collapse fix). Live VoiceOver pass at the time confirmed every home-feed card, tab-bar button, and overlay element remained announceable individually.
- The XCUITest hierarchy dump after `accessible={false}` shows descendants surface as separate a11y elements (verified ENGMOB-1701/1707 sessions). XCUITest's a11y tree mirrors VoiceOver's focus traversal — what XCUITest sees is what VoiceOver sees, modulo VoiceOver's text-coalescing.

**Manual smoke-test checklist before merge** (per `pr-process.md § Manual Smoke Test`):

- [ ] Enable VoiceOver (Settings → Accessibility → VoiceOver, or `xcrun simctl spawn <UDID> notifyutil -p com.apple.accessibility.voiceover.toggle`)
- [ ] Navigate to a CoachProfile overlay — swipe-right should land on coach name, then bio, then tabs/buttons, in visual order
- [ ] Navigate to a programDetails overlay — swipe-right should land on program title, day cards, "Start program" CTA in order
- [ ] If swipe-right ever announces multiple labels in one composite string, the auto-promotion is back — revisit the wrapper / look up the stack for a parent that has gained an `accessibilityLabel` or `accessible={true}`
