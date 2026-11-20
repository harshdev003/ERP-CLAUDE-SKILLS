# iOS simulator visual verification (Phase 5, gate 3)

Build the screen, run it on the iOS simulator, screenshot it, compare against the Figma frame PNG
from Phase 1, and fix drift. iOS only. (Gate 1 = per-element spec fidelity; gate 2 = design-rules.)

## Fast path (during development) - Metro debug build

For quick iteration, run the app in debug and drive/screenshot it via the iOS Simulator tools
(mcp__Claude_Code_iOS_Simulator__control):
1. `attach` first (opens the live panel; cheap; errors harmlessly if nothing booted -> boot/build then retry).
2. Build + launch the app (Xcode, or your normal RN run), navigate to the target screen.
3. `screenshot` -> compare to the Figma PNG side by side.
4. `tap` / `swipe` to check interactive states.

## Rigorous path - release-config harness (matches production)

For a faithful check (real fonts, no __DEV__ branches, release rendering) use the AuraSimTests
harness (see .claude/rules/sim-testing.md and .claude/skills/sim-test/SKILL.md):

```bash
bash AuraSimTests/Scripts/sim_start.sh --reset      # clean boot
bash AuraSimTests/Scripts/sim_install.sh            # build + install release Aura.app
# drive to the screen and capture:
python3 AuraSimTests/Scripts/sim_bridge.py --session $SID --verbose screen 30
```

Or invoke the /sim-test skill with a goal that navigates to the target screen, then screenshot.

## Full-coverage scroll sweep (vertical + horizontal) - REQUIRED, not a single screenshot

Figma frames are tall (Home ~4026pt) with horizontal carousels; one screenshot verifies only the
top band. Sweep the whole screen on both axes and drive it to completion.

Drive it with `mcp__Claude_Code_iOS_Simulator__control` (coordinates are device points; the `launch`
result reports the device's point size, e.g. iPhone 17 Pro ~ 402 x 874):

Anchor the sweep to the **named-section inventory** (Phase 1 artifact a), NOT arbitrary bands -
blind bands skip section boundaries and half-check content. Walk the inventory in order.

### Section-by-section vertical sweep
For each named section (`Today's practice`, `Aura Live`, `Celebrity Masterclass`, ...):
1. Swipe up until that section's heading + body are fully in view (`swipe` `(200,720)`->`(200,180)`
   is ~one viewport; screenshot after each swipe; stop when the section heading text is visible).
2. `screenshot`. Verify the section against its Figma node: heading text matches, every child
   component present, fonts/shadows/radii/images per the spec sheet, and **spacing/gaps**: the gap
   above/below the section (section-to-section `itemSpacing`), the heading-to-content gap, the
   card-to-card gap in its row, and the section's horizontal padding all match the spec (not just
   "looks about right"). A too-tight or too-loose gap is a mismatch to fix.

### Horizontal sweep inside a section - MANDATORY when the section scrolls sideways
A section needs a horizontal sweep if its Figma node is wider than the viewport, or its child card
list is longer than what fits on screen (a carousel/row). This is not optional - skipping it is how
off-screen cards go unverified:
1. Put the swipe y at that row's center; `swipe` `(340,y)`->`(60,y)`; `screenshot`.
2. Repeat until the row stops moving (two identical shots = last card reached).
3. Compare EVERY card (including the ones only revealed by scrolling) to the Figma row's full card
   list. Then swipe right to reset the row before moving to the next section.

### The re-verify loop is STATELESS - re-check every section each pass
Do NOT keep per-section "done" ticks between passes. Passing last time does not exempt a section this
time (a later fix, especially to a shared component, can change it). One pass = from the top, walk
EVERY named section in order (scrolling each horizontal row to its end), and record match/mismatch
for THIS pass only. If any section mismatched: auto-fix each mismatched section (run Phases 1-4 for
its Figma nodes), then start a fresh pass from the top that re-checks ALL sections again - not just
the fixed one. Done only when a single uninterrupted top-to-bottom pass finds zero mismatches across
every named section and every horizontal row. Correctness is proven by one clean full sweep, never by
accumulated ticks.

Notes:
- Do not use fixed sleeps; screenshot after each swipe and diff consecutive shots to detect the end.
- If a swipe near a screen edge triggers an OS gesture (back / control center), start the swipe
  >4pt from the edge (see the iOS Simulator tool notes).
- Also run the sweep in the other theme (`-AuraTheme=light` / `dark`) unless the screen is
  constant-dark.

## What to compare (overlay sim screenshot vs the Figma frame PNG)

Per element (using the Phase 1 spec sheet) and per design-rules.md:
- Type: family/weight/size/line-height match (Rule 1). Watch for bolder-than-Figma (#1 kickback)
  and faux-light weights; confirm sizes match the spec even if the family is not yet bundled.
- Shadows / blur present and at the right radius/offset (spec + Rule 4/5).
- Background images rendered (not flat colors / placeholders).
- Spacing: symmetric margins, gaps match; nothing crowded or clipped (Rules 2, 3, 5).
- Safe area: no control under the Dynamic Island / home indicator; content not overflowing; check
  at 135% text size (Rule 3).
- Surfaces: frosted vs clear correct for the backdrop; card elevation present; not washed out over
  colorful backgrounds (Rule 4).
- Edges: fades under pinned chrome, no hard cuts, no clipped glows/pixelation (Rule 5).
- Chrome: selected/unselected states, dark-surface tint (Rule 10).

## testID for the sim harness to find the screen

Ensure the screen root has `testID="<feature>_screen"` (snake_case, ends `_screen`) and interactive
elements have their own testIDs forwarded onto the touch target - otherwise the harness can't drive
or verify it. See .claude/rules/accessibility.md for the propagation rules and known iOS
auto-promotion pitfalls.

## Deviations

If RN genuinely cannot reproduce a Figma effect (blur behavior, gradient banding, exact glass),
record the deviation and raise it with design (Rule 7). Never silently ship a mismatch.

## Android

Out of scope for now. When Android is picked up later, frosted/blur surfaces need a separate
fallback and perf budget (documented as a follow-up card, not here).
