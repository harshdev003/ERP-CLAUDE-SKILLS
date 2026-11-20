# Aura design-QA rules (the self-review gate)

Evidence-grounded from 6 AuraConsumerApp (Aurie) design-QA PRs (#1268-#1277) and ~25 Linear
design-feedback tickets across Aura + Aurie (Apr-Jul 2026). Run this as a checklist in Phase 5.
Ranked by recurrence. Each rule: what to check, and the citing evidence.

## 1. Typography fidelity (THE #1 kickback, ~10 tickets)
- Every text style names a real `theme.fonts.font<NNN>Default<Weight>` matching the Figma value
  (size + weight + lineHeight + letterSpacing). No "close enough" key.
- Never fake a weight (no `fontWeight:'300'` for Light) - use the actual cut; flag if not bundled.
- Sweep ALL surfaces rendering the same content class in one pass (loaded + skeleton + history).
- Evidence: PROD-3568 ("confirm it is Light (300)"), PROD-3725 (17pt, -1% tracking), PROD-1930
  ("type weight is load-bearing"), PR #1270 (.display -> .displayLight sweep).

## 2. Breathing room / symmetric spacing (~9)
- Gaps from `theme.spaces.space<N>` / `responsiveDimension`. Pinned-bar clearances balanced vs top
  spacing; a home indicator needs a few points, NOT the whole safe-area inset. One owner per constant.
- Evidence: PROD-3640 ("match the top margin"), PR #1273 (fixed 12pt reserve), PROD-3568 ("more
  breathing room").

## 3. Viewport / safe-area / Dynamic Type (~8, Aura-heavy)
- Verify at 135% font scale and on a small device; content must not overflow the viewport.
- No interactive control inside the status-bar / Dynamic Island / home-indicator zones.
- Evidence: PROD-3452/ENGMOB-2716 (paywall overflow), ENGMOB-1946 (close button unhittable),
  ENGMOB-2692 (player controls overlap tab bar).

## 4. Backdrop-dependent surfaces (~7) - critical for Aura 2.0 frosted cards
- Frost/clear choice depends on what is BEHIND the surface: frosted over flat/light pages; clear
  only when a gradient behind must show through. Never a dark tint on top of frosted glass.
- Card elevation = one shared shadow token. Backgrounds = shared tokens, not per-screen one-offs.
- Test chrome against the most colorful background the user sees first.
- Evidence: PR #1270/#1269 (frosted vs clear per backdrop), PROD-3566 ("washed out... more
  translucent"), PROD-3638 ("brighter than other screens").

## 5. Edges & transitions: fades, not hard cuts; nothing clipped/pixelated (~5)
- Scrollable content under pinned chrome gets a ~20pt fade mask. Glows never clip. Raster assets
  decode at destination pixel size (FastImage sizing); don't animate geometry transforms over
  rasterised subtrees.
- Evidence: ENG-883 (hard cut -> fade; glow cut off), PROD-3723 (pixelated orb).

## 6. Motion: organic, state-faithful, never dizzying; tuned on device (~5)
- Motion params reviewed on device; states persist their real duration; honor Reduce Motion;
  express motion via offset/opacity, not geometry transforms over filtered subtrees.
- Evidence: PROD-3608 ("float slowly, pause, resume"), PROD-3725 (dizzy <-> too subtle loop).

## 7. "Match Figma" is the acceptance bar; deviations negotiated, never silent (~5)
- Final check = built screen vs Figma frame (sim screenshot compare). Where RN cannot reproduce an
  effect, raise it with design before shipping.
- Evidence: PROD-3041 ("UI completely different from Figma"), PROD-2692 ("match the Figma exactly";
  verify on device Safari not Chrome inspector), PR #1269 (SwiftUI glass deviation discussed).

## 8. UI truthfulness: never show state the user doesn't have (~4)
- Entitlement-dependent copy derives from the real entitlement source; affordances render only when
  their backing object exists; tab destinations carry no back button; retired variant keys fail
  closed to control.
- Evidence: PR #1268 ("Text only" while voice works; "Manage" with no subscription), PR #1269
  (back button on a tab destination removed).

## 9. Companion surfaces mirror the primary exactly (3 of 6 PRs)
- Skeleton / empty / history variants ship with identical tokens (glass, wash, fonts) as the loaded
  surface. Renames never change testIDs.
- Evidence: PR #1270 (skeleton missing wash + wrong glass), PR #1277 (chat history font sweep).

## 10. Chrome states & analytics land with the UI, not after (~3)
- Nav chrome ships with explicit selected/unselected/dark specs (filled vs outline icon, tint pair
  per surface luminance). Every new entry point gets its own Trackings source value on day one.
- Evidence: PROD-3566 (selected states, dark tint split), PROD-3639/PR #1273 (attribution + funnel
  start on tap).

## 11. One design, two themes (no experiment gate)
- The app has only the 4.0 design. There is no `isRedesignTheme` / legacy render: a frame is built
  into the screen's one render. Never branch on `auraDesignChange3` in app code — it is assigned only
  for UserWeb's paywall and the Aurie upsell.
- Every screen reads correctly in both 4.0 themes (light and dark), or pins dark through
  `getCurrentThemeDarkMode()` when its frame is constant-dark. Read light values off the light frame.
- Check both themes in the sim (`-AuraTheme=light` / `-AuraTheme=dark`).

## 12. Output hygiene: UI-only scope, no overrides, comment budget, naming (from the PR-6753 audit)
- **UI-only diff.** Nothing but this screen's UI. No `.circleci/`, `ios/fastlane/`,
  `scripts/`, `Gemfile*`, `.claude/`, `AuraSimTests/`, lockfiles, payment/IAP files, or unrelated
  bugfixes. Rebase on master first so already-merged commits don't replay in the diff.
- **No functional changes** - presentation only; behavior-preserving refactors must reuse the same
  handlers/testIDs.
- **No committed experiment override** - there is no local override constant; do not add one (PR-6753 shipped
  `'a'`, forcing the design on for everyone and defeating the kill switch). MERGE BLOCKER.
- **Comment budget <~10%** of added lines - no Figma spec transcription in `@module` headers, no
  `// 24px from Figma` provenance on constants/styles, no Figma layer names on spacers. Keep only
  WHY-comments (RN/iOS limits, deliberate deviations).
- **No bare Figma node ids in source comments** (`(node 1048:16312)`, `3234-30772`). They don't
  resolve without the file key and they move when the file is restructured. Name the **layer**
  (`Figma \`Component/Carousel/Tab\``), or use a full Figma URL where a link earns its place. Node
  ids go in the PR description / Linear issue. Raised on PR 6776; full rationale in
  `SKILL.md § H3`.
- **Naming** - NEVER name anything after a design version (`aura4/`, `aura2*`): the repo has no such
  identifier. Components go in FEATURE folders (`homeFeed/`, `coachProfile/`, `tabBar/`) - check
  `ls src/components/` for an existing one first. The design-generation signal lives in `newDesign*`
  tokens/props and `src/img/newDesign/`, matching `newDark`/`isNewDesignSystem()`. Match the target
  folder's convention exactly; existing variant vocabulary (`.fill`, not `.filled`); spell-check asset
  names (they become permanent generated keys); no overlapping sibling folders; helpers out of
  component folders; test casing mirrors source; no `-TRIAL` assets committed. Experiment-derived
  names (`AURA_DESIGN_CHANGE`) mirror the Firebase key and are never renamed.
- Evidence: PR 6753 - 27-30% of the diff out of scope, 12-30% comment density, `selectd` typo
  propagated into `iconList.js`, a version-named `aura2/`-then-`aura4/` folder with no precedent in the repo.

## Aura conventions also enforced (from .claude/rules/)
- No hardcoded color/font/px (ui-design-system.md). propTypes on every prop (coding.md).
- testID on the actual touch target, screen root `<feature>_screen` (accessibility.md, sim-testing.md).
- Errors via `bugsnag.report`; analytics via `Trackings(...)`; strings via `t(...)`.
- Navigation via `Actions.navigateTo(...)` wired in Dashboard.js.
