---
name: figma-to-code
description: >-
  Turn an Aura 2.0 React Native screen design (Figma frame) into correct, design-system-grounded
  RN code (iOS-first), reusing existing components instead of re-implementing, never modifying a
  shared component without a dependency check, and gating the result on the Aura design-QA rules
  plus an iOS-simulator visual check. Extracts design values via the Figma MCP when the file is
  readable, falling back to the Figma REST API (view-only access, FIGMA_TOKEN) on access errors;
  maps every value to a real theme token, and consults the
  component registry as the single point of reference. Trigger on "implement this Figma screen",
  "build this 2.0 screen in the app", "turn this Figma into React Native", or a Figma frame /
  screenshot handoff for the Aura repo. Aura-specific layer on top of the generic
  figma-design-to-code approach, not a replacement.
---

# Figma to React Native (Aura 2.0)

Build an Aura 2.0 screen the reusable way. Reusability first, not screen-first: a button in a
Figma frame is a component used across many screens, never a screen-local style. This skill is the
per-screen (or per-component) method; the component registry and token inventory hold the content
so this skill stays lean.

Scope: **iOS only** for now. Android is deferred. The verification gate uses the iOS simulator.

## Figma is READ-ONLY (hard rule — read before touching the MCP)

**This skill reads Figma and writes the repo. It never writes Figma.** No frames, layers,
components, styles, variables, comments, Code Connect mappings, uploaded assets, or new
files — not even to make an extraction cleaner. The connected account has an editor seat
because the MCP needs one to *read* some files and to run `download_assets`; that is not
permission to write.

When a design is missing, wrong, or ambiguous, the output is a **message to the user**
naming the node and the problem — never an edit that fixes it in the file. The designer
changes Figma; you re-extract.

The write tools (`use_figma`, `create_new_file`, `add_code_connect_map`,
`send_code_connect_mappings`, `upload_assets`, `create_shader`, `generate_diagram`, …) are
denied in `.claude/settings.json`, and the write-oriented `figma:figma-*` skills must not be
invoked. Full rule, including the per-request exception: [`.claude/rules/figma.md`](../../rules/figma.md).

## Prerequisites

- **Extraction is MCP-first, REST fallback.** Try the Figma MCP first (`get_design_context`,
  `get_metadata`, `get_variable_defs`, `get_screenshot`) - when the connected account can read the
  file it returns richer, composited data (including variables and effect-baked renders). MCP access
  is **file-dependent**: some files return design context fine, others return "you don't have edit
  access to this file" (the MCP wants an editor seat, and `download_assets` in particular needs one).
  On ANY MCP access error, fall back to the REST recipes in `references/figma-rest.md` - do not stop.
- `FIGMA_TOKEN` env var = a Figma personal access token, File-content Read-only scope (view access
  is enough), needed for the REST fallback. **Set this once so you never pass it again** - add
  `export FIGMA_TOKEN=figd_...` to your shell profile (`~/.zshrc`), OR a gitignored `.env` the shell
  sources. The token is a secret: read from the environment, never hardcoded in the skill and never
  committed. Hard blocker only if BOTH the MCP is access-denied AND `FIGMA_TOKEN` is unset - then say
  so and stop.
- **Default Figma file = the Aura 2.0 file, key `lwoV0lfkBSxNW2c2yWRl9C`.** You do NOT need to give
  the file key each time - just give the section/screen by **node id** (e.g. `800:6686`) or by name
  (the skill resolves the name to a node id by listing the file's sections). A full Figma URL still
  works and overrides the default file if it points elsewhere.
- This repo checked out (grounding = real theme tokens + component library).

## The registry is the point of reference

`references/component-registry.md` maps every known Aura 2.0 component to a repo component, with a
dependency count and a build-vs-extend verdict, plus the coverage map of which screens are in the
redesign. ALWAYS consult it before deciding to build anything. When you build a new component or
extend one, UPDATE the registry in the same change so the next screen benefits.

**The registry (and the "built so far" notes) tell you WHICH COMPONENTS exist to reuse - they do NOT
tell you what THIS screen looks like.** A new screen's background, colors, which components actually
appear, their per-instance props, and the layout all come from THIS screen's own Figma node (Phase 1),
never assumed from another screen. Reusing a component means reusing the component, not another
screen's styling: a reused Button/Card/GlassCard still takes THIS screen's values from its spec sheet.

## No assumptions - extract and DIFF every screen fresh (read this before building anything)

The failure this prevents: building a new screen by stamping a previously-built screen's styling
(same background, same card treatment) onto every section without ever checking THIS screen's Figma.

- **Every screen is extracted fresh from its OWN Figma node.** Even if it looks similar to one you
  built before, do Phase 1 for it - its own spec sheet, its own section inventory, its own assets.
  Never reuse another screen's spec sheet, background, or color choices.
- **Determine, from THIS screen's Figma, before you build:** what is the background (solid? gradient?
  image? which one)? which sections/components are actually present? for each, what are its real
  values (colors, fills, radii, shadows, spacing)? what is different from the similar screen you
  built (different bg, different card style, different components)?
- **If something changed vs a prior screen, build the change - do not carry the old styling over.**
  A different background is a different background; a restyled card is a restyled card. The spec
  sheet is the authority, not memory of the last screen.
- If you catch yourself applying a value you did not just read from THIS screen's node, stop and go
  read it.

## One design: no experiment gate

`auraDesignChange3` ended with 4.0 the winner, and the design cleanup (PROD-7647) removed the
legacy design: there is no legacy render, no `isRedesignTheme` / `isAuraDesignChange` check and
no `renderLegacy()` to keep. A screen has one render, and a Figma frame is built into it directly.
The two themes, `redesignLight` and `redesignDark`, are both 4.0 themes; `ThemeStore` resolves the
user's preference to one of them and has no design gate.

`auraDesignChange3` is still assigned (`AURA_DESIGN_CHANGE` in `src/data/enums.js`, bucketed in
`Onboarding6.chooseDesignExperiment` and the Dashboard list) only because UserWeb's WebView paywall
and the Aurie upsell read the arm to pick their own design. **Never read it in app code** — nothing
in the app may branch on it.

Rules:
- **Screens that render dark regardless of the user's theme** (onboarding, tab bar, players) use
  `getCurrentThemeDarkMode()`, NOT `getIndividualStyle(appTheme.NEW_DARK)`. Both return
  `redesignDark` today, but `getCurrentThemeDarkMode()` says what the screen means and is the one
  place to change if that ever differs:

  ```js
  // ❌ BAD — names a theme
  const theme = getIndividualStyle(appTheme.NEW_DARK)

  // ✅ GOOD — the constant-dark palette
  const theme = getCurrentThemeDarkMode()
  ```
- A row shared between the Home feed (which follows a light app theme) and constant-dark screens
  takes `getFeedRowTheme(feedTheme)` (`FeedThemeContext`), so it follows the page it is on.
- Both 4.0 themes declare the same token keys (`test/unit/redesignTokens.test.js`), so
  `theme.colors.surfaceSubtle`, `theme.fonts.bodySansRegular16` and `theme.gradients.gHomeBackdrop`
  resolve in light and dark alike. A token that comes back `undefined` is a typo or a missing
  token, and RN ignores `undefined` style values, so it shows up as an unstyled element.
- Build the light frame too. Read the light Figma frame for light values instead of assuming the
  dark one; only frame-independent values (brand accents, anything over a gradient or photo) repeat.

See `src/models/theme.js` (`getIndividualStyle`, `getCurrentThemeDarkMode`, `getFeedRowTheme`,
`isLightTheme`) and `ThemeStore.getThemeStyles` for the canonical semantics.

## Output hygiene (HARD RULES - a PR that breaks these is a FAIL)

Grounded in the PR-6753 audit. These are as binding as the design rules.

### H1. UI-ONLY scope - the diff may contain nothing but this screen's UI
The PR's only intent is the redesign of the named screen(s).
- **ALLOWED**: `src/components/homeFeed/` components, the redesigned screen files, style tokens
  (fonts/colors/gradients/radius/dimensions), new images/SVG icons + `iconList`, localization keys
  for NEW UI strings.
- **FORBIDDEN in this PR** (each needs its own PR + its own reviewers): `.circleci/`, `ios/fastlane/`,
  `scripts/`, `Gemfile*`, `.claude/` (skills and rules docs - including THIS skill's own files),
  `AuraSimTests/` (incl. `simulators.json`; `users.json` is local and gitignored), lockfiles (`Podfile.lock`, `Gemfile.lock`, `package-lock.json`)
  unless dependencies intentionally changed, and any unrelated bugfix.
- **The one carve-out: a FOUNDATION PR may change `.claude/` and the release tooling it gates.**
  A PR that introduces or renames the design-system API itself (tokens, themes,
  font bundling) must update the docs describing that API in the same change, or the next agent-driven
  screen is generated against names that no longer exist. Same for a check that guards an asset the
  PR adds. This carve-out is for the foundation of a redesign stack, NOT for per-screen PRs — if your
  diff contains a screen, H1 applies to you unchanged.
- **NEVER touch the payment/IAP path** in a UI PR: `src/stores/RNIapController.js`,
  `src/screens/SubscriptionModal*`, `src/lib/*Pricing*`, `src/logic/PriceGuards*`. Revenue risk +
  mandatory sandbox testing.
- **NO functional changes.** Only presentation. The legitimate exception, kept minimal:
  behavior-preserving refactors that let the new render reuse the SAME handlers/testIDs
  (extract, do not rewrite). If you believe logic must change, STOP
  and surface it - do not bundle it.
- **Rebase before review.** Run `git fetch origin && git rebase origin/master` first. PR-6753's diff
  replayed 5 already-merged commits (paywall pricing, saved-tracks fix, CI) purely from a stale merge
  base - reviewers cannot tell that from creep. Then verify: `git diff --stat origin/master...HEAD` -
  every path must serve the stated intent. If >~10% of changed lines fall outside it, SPLIT the PR.

### H2. No committed experiment/debug overrides - MERGE BLOCKER
Never commit a local testing override of an experiment. PR-6753 shipped a
`LOCAL_VARIANT_OVERRIDE = 'a'` constant that forced the then-new design on for **every** user AND
short-circuited the read so the server-side kill switch could not pull them back. That escape hatch
must not be reintroduced for any experiment — to exercise an arm locally, set the variant on your
own test user (`eng:set-user-experiment`) or force it in a sim run (`sim_test.sh --experiment`),
so you are testing the real bucketing path.

Before finishing, grep your own diff for any hardcoded variant, `__DEV__` bypass, or commented-out
gate — e.g. `grep -rnE "auraDesignChange[0-9]?.*=.*'a'|LOCAL_VARIANT" src/` must return nothing.

### H3. Comment budget - the Figma file is not documentation to reproduce in source
PR-6753's generated components ran 12-30% comment lines (2-4x the repo norm), almost all of it
Figma-value narration. Target: **under ~10% of added lines**, and follow `.claude/rules/comments.md`.
- **DO NOT** transcribe the node spec into the `@module` header. One line is enough:
  `@description Aura 4.0 mix card - Figma \`Card/Playlist\`.` A transcribed spec is a second source
  of truth that goes stale immediately.
- **DO NOT put bare Figma node ids in source comments** - not in `@module` headers, not inline,
  not as `(node 1048:16312)` / `(3234-30772)`. Raised on PR 6776 and applied to it: a node id is
  unresolvable without the file key, nobody opens Figma from a source comment to look one up, and
  the ids move whenever the file is restructured - so it is bloat that also rots. Name the **layer**
  instead (`Figma \`Component/Carousel/Tab\``): it survives renumbering, it is what a designer
  searches for, and it is what you would type into Figma anyway. A full **Figma URL** is acceptable
  where a link genuinely helps - it at least resolves. Node ids belong in the PR description, the
  Linear issue, and this skill's own extraction tables, all of which are read alongside the file
  they refer to.
- **DO NOT** annotate constants or style lines with Figma provenance:
  `// 24px from Figma`, `// Figma carousel itemSpacing is 20pt`, `// Exact node geometry (800:6776)`,
  `// Reconstructed footprint`. The constant's NAME and VALUE already carry the fact.
- **DO NOT** name Figma layers on spacers/wrappers: `// grow`, `// sp-sm`, `// pins the row to the
  bottom` on a bare `flex: 1` View.
- **DO** comment where the code deliberately DEPARTS from the design or the obvious implementation:
  RN platform limits (no backdrop-filter, no shadow spread), iOS quirks (shadow vs `overflow:hidden`),
  mask-vs-scrim trade-offs, intentional deviations from the mock - stating the REASON, never the pixels.
- **Gate:** delete the comment. If you only lost WHERE a value came from -> it stays deleted. If you
  lost WHY the code can't match the design literally -> keep it.

### H4. Naming - match the folder you are writing into, and spell-check
Per `.claude/rules/naming.md`; every item below is a real PR-6753 defect.
- **SVG icons** in `src/img/newDesign/svg/` use the folder's dot-namespaced camelCase
  `namespace.name[.variant].svg`, reusing an existing namespace (`action`, `icon`, `tapbar`,
  `specialty`, ...). No PascalCase (`IconEarth.svg` was the only one of 207 - it should be `icon.earth.svg`).
- **Variant suffixes come from the existing vocabulary** - the folder already uses `.fill`; do NOT
  coin `.filled` / `.selected`. Every member of one icon set uses the SAME suffix.
- **Spell-check asset names before writing them.** `tapbar.home.selectd.svg` shipped a typo that
  propagated into `src/generated/iconList.js` and two call sites - once it is a generated string key
  it is effectively permanent.
- **NEVER name anything after a design version.** The Figma file is called "Aura-Health-4.0"; that is
  a Figma filename, not a codebase vocabulary. This repo has NO version-numbered identifier anywhere -
  `grep -rn "aura2\|aura3\|aura4" src` must stay empty. PR-6753 invented `aura2/` then `aura4/`, and
  neither had a single precedent in 200k+ lines.
- **Components go in FEATURE folders, never a design folder.** Every `src/components/*` subfolder is
  a domain: `podcast/`, `messageCenter/`, `coachProfile/`, `guestPass/`, `emotions/`, `series/`.
  Before creating a folder, `ls src/components/` - if one already covers the domain, ADD to it.
  PR-6753 created `aura4/coachProfile/` beside an existing `components/coachProfile/`. Home-feed cards
  -> `components/homeFeed/`, tab bar -> `components/tabBar/`, coach profile -> `components/coachProfile/`.
- **The design-generation signal lives in tokens and assets, not in paths.** Prefix style tokens and
  opt-in props `newDesign*` (`displaySansRegular20`, `surfaceGlass`, `scaledDimension`,
  `newDesignHomeCardVariant`), matching the repo's existing `newDesign/`, `newDark`/`newLight` and
  `isNewDesignSystem()` vocabulary. Images go in `src/img/newDesign/` with plain descriptive names
  (`auraRing.png`), not `<version><Name>.png` at `src/img/` root.
- **Experiment-derived names are the ONE exception** - `AURA_DESIGN_CHANGE` mirrors the Firebase key
  `auraDesignChange3` verbatim, trailing `3` and all. Never "tidy" it; renaming it desyncs the
  code from the server and breaks bucketing. (The constant's name has no number; its *value* does.
  That mismatch is exactly what made the wrong key easy to type for months.)
- **No overlapping sibling folders** (`coach/` vs `coachProfile/` with `CoachHero` vs
  `CoachProfileHero`) - extend the existing folder or make the scopes disjoint.
- **Components folders hold PascalCase components.** A helper like `sectionTitleProps.js` belongs in
  `src/logic/`, not `src/components/`. First-party logic never goes in `src/lib/` (that is for
  vendored/third-party adapters).
- **Tests mirror their source's exact casing** (`PriceGuards.js` -> `PriceGuards.test.js`).
- **Never commit `-TRIAL` / unlicensed-watermark asset files** into shipped asset folders.

## Target (app side) - ask/confirm before BUILD

- **Which app screen** this Figma frame maps to (the file, e.g. `src/screens/...`; note Home lives
  inside `Dashboard.js`). Some frames have no existing screen yet.
- **How it lands.** The frame is built into the existing screen's render — there is no design gate
  and no old render to keep (see "One design" above). For a quick pilot you may instead mount the new
  render on a temporary route to eyeball it, but the shippable form is always the real screen file.
- **Variant** (light / dark / both) and the **route/testID** so the sim can reach it.

For modify-in-place: read the existing screen first (its components, Reflux wiring, navigation,
analytics, experiment gates, testIDs), diff current-vs-Figma, and build the delta. Rules: keep the
screen's handlers, analytics and testIDs; screen-local layout lives in the screen; a SHARED component
is changed additively or replaced by a new component - never restyled in place (it would regress
every other screen using it).

## Execution contract (run to completion - ENFORCED by a worklist, not willpower)

This skill processes the ENTIRE screen in a SINGLE run. Completing one section is NOT a task
boundary - do not summarize, hand back, or end your turn until the whole worklist below is drained.
The mechanism that guarantees this:

**Step 0 - RE-RUN / VERIFY MODE (whenever the screen already has code, or the user says "verify" /
"check" / "run again").** A second invocation must RE-AUDIT everything, not resume from ticks:
- **Persisted `verified` marks are stale on a new run.** They were only valid inside the run that
  produced that clean pass. On a fresh invocation, RESET every section's `verified` to unchecked.
  `built` may stay as a hint that files exist, but the content is re-checked, not trusted.
- **Re-extract THIS screen's Figma fresh** (Phase 1) and run the full stateless VERIFY WAVE over
  ALL sections again - every section, in order, against the current Figma. Do NOT skip a section
  because a previous run marked it done, and do NOT assume "unchanged sections are fine" - check each
  one yourself. A section that genuinely still matches passes this pass (but you verified it); a
  section that drifted, was never fully right, or differs from Figma gets FIXED, then you continue.
- The purpose of a re-run is exactly this: independently re-verify the whole UI against Figma and fix
  whatever is off. Finishing without re-checking a section - because it "looked done" - is the bug
  this step removes.

**Step A - build the worklist up front (do this before building any UI).**
After EXTRACT produces the named-section inventory, create a persistent worklist of EVERY section:
- Create a todo list with one item per named section using the task tool (TaskCreate one per
  section), AND write the same checklist to `<scratchpad>/figma-to-code-progress.md` with columns
  `section | built | verified`. This file is the durable source of truth if context resets WITHIN a
  run. Across runs, treat `verified` as historical only (see Step 0) - re-verification is re-earned.

**Step B - drain the worklist in two waves, never one-section-and-stop.**
1. **BUILD WAVE (all sections, code only - no sim):** loop over every worklist item and BUILD its
   code (Phases 2-4). Building code is cheap and needs no simulator, so there is no reason to stop
   between sections. Mark `built` in the progress file + TaskUpdate as you go. Do NOT start the sim
   until EVERY section is built. (This removes the per-section stopping point you hit - the heavy sim
   step no longer interleaves with each section's build.)
2. **VERIFY WAVE (the stateless sweep, Phase 5):** only after all sections are built, run the sim
   re-verify loop over the whole screen. Fix mismatches, mark `verified`, and keep looping until a
   clean full pass.

**Step C - the only exit condition.** You are DONE only when the progress file shows every section
`built` AND `verified`, and one clean full-screen re-verify pass found zero mismatches. Before you
would ever stop or write a closing summary, RE-READ `<scratchpad>/figma-to-code-progress.md`: if any
row is not `built`+`verified`, you are not done - continue with the next unfinished row. Report once,
at the very end.

**Never** pause for confirmation between sections or between build and verify - build the files, run
the sweep, fix, and keep going. The only allowed mid-run surface is a hard blocker you cannot resolve
(missing FIGMA_TOKEN, sim won't boot, an unlicensed-font decision): state it, and continue with
everything else you can. If context runs low, do not ask the user to re-run - the progress file lets
you resume; keep going until the final clean pass.

## Scaling: one sub-agent per section (use for large / multi-section screens)

For a screen with more than ~3 sections, run the BUILD WAVE as an orchestrator + fan-out so no single
context has to hold the whole screen (this is the hard fix for "stops after one section" - each
section is built in its own agent context). You (the invoking run) are the ORCHESTRATOR:

1. **Orchestrator does EXTRACT + shared setup first.** Produce the section inventory + spec sheet +
   worklist. Then build any SHARED prerequisites the sections need - the new theme entry/tokens and
   the shared Layer-A components (GlassCard, Tag, AppText, ...) - ONCE, yourself, and update the
   registry. Section agents must not each build these (that would race and duplicate).

2. **Fan out one sub-agent per section (parallel).** Use the Agent tool, one agent per named section,
   in a single message so they run concurrently. Give each agent ONLY: the section name + Figma node
   id, that section's spec-sheet rows, the token inventory + registry (read), and the file path it
   OWNS. Hard isolation rules in every sub-agent prompt:
   - **Write ONLY your own new section-component file** at `src/components/homeFeed/<Screen>/<Section>.js`
     (a prop-driven component). Never edit the screen file, theme globals, the registry, or another
     section's file - those are shared and the orchestrator owns them.
   - **Return** (do not apply): your component's export name + props, any new sub-components you had
     to create (with their files, which must also be under your section's own folder), proposed
     registry rows, and any deviations/blockers. Structured output.
   - Do NOT open the simulator. Build code only.
   Because each agent writes a disjoint file set, parallel writes never conflict.

3. **Orchestrator assembles.** Import each returned section component into the screen's
   render in inventory order, merge all proposed registry
   rows, and update the progress file (`built`) per section.

4. **Orchestrator runs the VERIFY WAVE alone.** The simulator is single-instance and cannot be
   parallelized, so the orchestrator runs the stateless section-by-section sweep itself. For each
   mismatch it may spawn a fresh fix sub-agent scoped to that one section's file (same isolation
   rules), then re-sweep. Loop to a clean full pass.

Keep sub-agent prompts short (inline the section's spec rows; do not tell them to read big files).
If the Agent tool is unavailable, fall back to the sequential two-wave build in the Execution
contract above.

## Workflow

### Phase 1 - EXTRACT (MCP-first, REST fallback)

Do this fresh for THIS screen every time - never reuse a prior screen's spec sheet, background, or
asset set (see "No assumptions" above). Pull the target frame. **Try the Figma MCP first**: `get_metadata` (structure / section tree),
`get_design_context` (composited node + reference code + a render), `get_variable_defs` (variables),
`get_screenshot` (frame PNG). When the account can read the file, prefer these - the render bakes in
effects (blur) and you get variable names REST can't provide. **On an access error** ("you don't have
edit access to this file") or a rate-limit, **fall back to the REST recipes** in
`references/figma-rest.md` for that call - do not stop; MCP access is file-dependent. `download_assets`
(asset/icon export) needs an editor seat, so for image/icon assets go straight to REST `/images` (or
a user-supplied export) when it is denied.

Whichever path, obtain:
- node JSON / design context with resolved values
- image-fill URLs (`imageRef` -> URL) for any IMAGE fills (background photos, thumbnails)
- **exported ASSETS: every icon/vector node as SVG, every raster illustration as PNG** - via MCP
  asset URLs / `download_assets` if the seat allows, else REST `/v1/images?format=svg` (icons) and
  `?format=png&scale=3` (raster), which work at view access. Collect all icon/vector node ids and
  export them batched + rate-limit-paced. Icons come FROM Figma, never hand-drawn. See
  `references/figma-rest.md` (Icons/vectors section).
- a rendered PNG of the frame (for orientation and the final visual compare ONLY)

Then produce TWO artifacts (both from `references/figma-rest.md` scripts):

**(a) The named-section inventory** - the ordered list of the screen's top-level sections, by NAME,
taken from the Figma node tree (the direct children of the screen's scroll container, e.g. the
`Wrapper` node). One row per section:

| # | section name (Figma node name) | node id | heading text | child components (types + count) | height |

This is the checklist the whole build and verify run against. Every section must exist in the built
screen, in order. Example (Home): `Today's practice`, `Aura Live`, `Celebrity Masterclass`,
`Programs For You`, `Series`, `A quiet mind`, `Parenting`, ... - each is a distinct named node, not
an arbitrary slice. Verification is section-by-section by NAME, never by blind viewport bands, so no
section is skipped or half-checked.

**(b) The per-element spec sheet** - for EVERY visible node, grouped UNDER its section, EVERY exact
numeric property (this is what makes it pixel-perfect - measure, never eyeball):

| section | node | type | w x h | pos (x,y in parent) | sizing (FIXED/HUG/FILL per axis) | font (family/size/weight/lh/tracking) | fill (hex+alpha) | stroke (color/weight/align) | radius (single or [tl,tr,br,bl]) | effects (shadow color/offset/radius; blur) | image fill (ref->url) | padding (l,t,r,b) | gap / rowgap |

The spec sheet - not the screenshot - is the source of truth for BUILD. The #1 pilot failure was
matching structure but approximating shadow / background image / font / font-size; it happens when
you build from the screenshot instead of the node properties. The screenshot conveys layout only.

Cache everything under the scratchpad. Respect the rate limit (single-frame calls, space retries).
If the MCP `get_variable_defs` works, use the variable names it returns; if you are on the REST
fallback, the Variables endpoint is Enterprise-gated (403), so map by resolved value instead. Either
way the final mapping target is this repo's theme tokens, not the Figma names.

### Phase 2 - DECOMPOSE

Go **section by section** through the named-section inventory (never a flat top-to-bottom element
dump). For each named section, list its elements and classify each:
- **Registry component (Layer A)** - a shared component (button, text, tag, card, icon, list row,
  tab bar, ...). Look it up in the registry.
- **Screen-generic (Layer B)** - used mostly on this screen; still built prop-driven.
- **One-off layout** - screen-specific arrangement / spacing / background.

Keep the decomposition grouped by section name, so BUILD and VERIFY can process and tick off one
named section at a time.

### Phase 3 - RESOLVE (reuse-or-build gate)

For each element, in this order:
1. **New-design component exists** (registry, new section) -> USE IT. Never re-implement.
2. **Only an old component exists** -> dependency-check it first:
   `grep -rl "reuseableUI/<Path>" src/ | wc -l` (the registry lists current counts).
   - Additive change + low deps -> extend the old component (new variant/prop). Old screens keep
     working; the new screen uses the new variant.
   - Not additive, or high deps (e.g. Icon 206, ButtonCenter 106) -> build the NEW component in the
     new-design section; old screens keep the old one; retire the old one later after migration is
     verified (a separate cleanup card, never preemptively).
3. **Nothing exists** -> build new, prop-driven, in the new-design section; add it to the registry.

Output a decomposition table: `element -> component -> reuse | extend | build -> props`, plus any
registry updates.

### Phase 4 - BUILD (the BUILD WAVE - repeat for EVERY section before any sim verify)

Run Phases 2-4 for each named section in the worklist, one after another, until every section's code
is built and marked `built` in the progress file. This is code-only; do NOT open the simulator until
all sections are built (that is the Phase 5 VERIFY WAVE). Do not stop between sections.

Assemble each section from the resolved components, driven by the **Phase 1 spec sheet, one row at a
time**. For each element apply its exact spec - do not skip a property because it "looks close" in
the screenshot. Every element must account for, from its spec row:
- font family + size + weight + lineHeight + letterSpacing (not just "some bold text")
- fill color AND its alpha; stroke if present
- every effect: each drop shadow (color, x/y offset, blur radius) and each background/layer blur
- corner radius (exact; capsules -> borderRadiusFull)
- image fills: render the actual image (download via the imageRef URL, or the app's data source) -
  never leave a background photo/thumbnail as a flat color or placeholder
- **icons / vectors: use the EXPORTED asset from Phase 1 (SVG via SvgXml / the repo Icon set), never
  redraw or approximate the paths by hand.** Icon color/stroke/size come from the node (fills /
  strokes / strokeWeight / box); theme-tint by stripping the SVG fill and applying the mapped
  `theme.colors.icons*` token, but keep the exported glyph. If an asset genuinely could not be
  exported, leave a clearly-marked TODO with its node id rather than a guessed drawing.
- **exact size, sizing mode, and position - do not eyeball:**
  - `w x h`: apply the exact width/height for FIXED elements (wrapped in `responsiveDimension`); for
    FILL use `flex:1` / full width, for HUG size to content - match the Figma `sizing` mode per axis,
    do not hardcode a width on something Figma set to FILL (or vice-versa).
  - `pos (x,y)`: for absolutely-positioned elements, apply the exact offset within the parent.
  - `stroke`: exact border color + `strokeWeight` (thickness) + align; a 1pt border is not a 2pt one.
- **spacing / gaps / margins - apply EXACTLY from Figma auto-layout, do not eyeball:**
  - the container's `itemSpacing` = the gap between its children (section-to-section gap on the
    scroll container; card-to-card gap inside a carousel; row-to-row gap in a list)
  - `counterAxisSpacing` = the row gap in a wrapped grid (2-col card grids)
  - `paddingLeft/Top/Right/Bottom` = the section's inner padding and the screen's horizontal margins
  - a margin between two siblings IS the parent's `itemSpacing` (or the child's x/y offset when
    absolute) - read it, do not guess a "looks right" 16
  - map every gap/padding/size to `responsiveDimension(N)` or a real `theme.spaces.space<N>` /
    `theme.dimensions.r<N>`; never a raw px, never a rounded-to-nice value, never an approximation.
    Common misses: the vertical gap between sections, the leading inset of a horizontal carousel, the
    gap between a section heading and its content, and exact card width/height.
- **radius**: apply the exact value; if `radius` is a 4-element array `[tl,tr,br,bl]`, the corners
  differ - set each corner, do not use one uniform radius.

Map every value to a REAL theme token (`references/token-inventory.md`) - never emit a raw hex,
raw px, or fontSize/fontWeight literal:
- color -> `theme.colors.<semanticToken>`
- font -> spread `...theme.fonts.font<NNN>Default<Weight>`
- spacing/size -> `responsiveDimension(N)` / `theme.dimensions.r<N>` / `theme.spaces.space<N>`
- radius -> `theme.borderRadius.borderRadius<N>` (capsules use `borderRadiusFull`, not measured pill radii)
- shadow -> `...theme.shadows.<token>` · gradient -> `theme.gradients.<token>`

There is no design gate: the new render is the screen's render, in both themes (see "One design").

Aura conventions: thread `theme` into every component; `propTypes` on every prop; functional
components for new code; `testID` on the actual touch target (see `.claude/rules/accessibility.md`);
lists -> FlashList, remote images -> FastImage; wire the screen into `Dashboard.js` navigateTo and
dispatch via `Actions.navigateTo(...)`.

### Phase 5 - VERIFY (all three gates required, before any PR)

1. **Per-element fidelity check** - go down the Phase 1 spec sheet row by row and confirm the built
   code applied each property: font family/size/weight/lineHeight, fill+alpha, EVERY shadow, blur,
   radius, image fill, and **every icon is the EXPORTED Figma asset (not a hand-drawn / approximated
   glyph)**. Any element that only got structure (no shadow, flat bg instead of the photo, default
   font size, a guessed icon) is a FAIL - fix before continuing. This gate exists because the pilot
   matched structure but skipped shadow/background-image/font/font-size and redrew icons by hand.
   Also confirm each value came from THIS screen's spec sheet, not carried over from a previously
   built screen: a background, card treatment, or color that matches another screen but not this
   screen's own Figma node is a FAIL. Verify the screen's background specifically - it is the most
   commonly assumed-instead-of-read value.
   **Minute-detail measurement (for pixel-perfect):** for every element check the EXACT numbers, not
   just presence - width/height, padding (l/t/r/b), gap between items, corner radius, stroke width,
   and position - against the spec sheet. A card that is 8pt too narrow, a 12pt gap built as 16, a
   1pt border built as 2, a 20pt radius built as 16 are all mismatches to FIX, not "close enough".
   Where possible measure from the iOS hierarchy dump (element frames give real rendered sizes) and
   compare the numbers to Figma; a >~1pt deviation on any measured value fails.
2. **Design-rules gate** - run `references/design-rules.md` as a checklist. Fix every miss.
2b. **Output-hygiene gate (run against your OWN diff, before finishing)** - mechanical checks for the
   hard rules above; each failure is fixed, not explained away:
   - `grep -rnE "auraDesignChange[0-9]?.*=.*'a'|LOCAL_VARIANT" src/` -> must be EMPTY (H2 merge blocker). Also scan for
     any other hardcoded variant / commented-out gate you added.
   - `git diff --stat origin/master...HEAD` -> every path serves this screen's UI (H1). Any
     `.circleci/`, `ios/fastlane/`, `scripts/`, `Gemfile*`, `.claude/`, `AuraSimTests/`, lockfile,
     payment/IAP or unrelated-bugfix path = remove it from this PR. If the branch is stale, rebase on
     `origin/master` so already-merged commits stop replaying in the diff.
   - Comment density on each new component: comment lines / added lines must be under ~10% (H3).
     Re-read each comment and delete every Figma-value / provenance / layer-name narration; keep only
     WHY-comments.
   - Naming pass on every new file/asset (H4): folder convention matched, variant suffix from the
     existing vocabulary, spelling checked, one version prefix, no overlapping folders, helpers out of
     component folders, test casing mirrors source, no `-TRIAL` assets.
3. **Theme check** - confirm the screen reads correctly in both 4.0 themes, or pins dark through
   `getCurrentThemeDarkMode()` when the frame is constant-dark. Verify both in the sim
   (`sim_test.sh --launch-arg -AuraTheme=light` / `dark`), and check no app code reads
   `auraDesignChange3`.
4. **iOS simulator SECTION-BY-SECTION scroll sweep** - anchored to the named-section inventory, not
   blind viewport bands (blind bands skip/half-check sections). Walk the inventory IN ORDER, and
   after finishing one section CONTINUE to the next automatically in the same run - do not stop or
   hand back until all sections are swept (see the Execution contract above):
   - For each named section: scroll it fully into view, screenshot, and verify it against that
     section's Figma node - heading text, every child component, spacing, and (via the spec sheet)
     fonts/shadows/radii/images.
   - **Horizontal sections are mandatory to scroll, not optional.** If a section's Figma node is
     wider than the viewport OR its content is a horizontally-scrolling row/carousel (its Figma
     child list is longer than what fits on screen), you MUST swipe left through it card by card to
     the LAST card, screenshotting each step, and verify every card against the Figma row's full
     list. A section is not verified until its horizontal content has been scrolled to the end.
   - MISMATCH -> fix. MISSING / unfinished / still-old-design -> BUILD it per Figma (Phases 1-4 for
     that section's nodes), add new components to the registry.
   See `references/sim-verify.md` for the exact swipe mechanics.

5. **Full-screen RE-VERIFY loop - stateless, re-checks EVERY section each pass (this is the fix for
   sections being left behind).** Do NOT carry "done" ticks across passes. Each pass starts fresh and
   re-inspects every named section again from the top - a section being fine last pass does not
   exempt it this pass (a fix elsewhere, especially to a shared component, can change it). One pass:
   - From the top, for every named section in order: scroll it into view (and scroll its horizontal
     content to the end), compare to Figma. Record match / mismatch for THIS pass only.
   - If ANY section mismatched this pass: auto-fix each mismatched section (Phases 1-4 for its nodes),
     then start a brand-new pass from the top - re-checking ALL sections, including ones that matched
     before.
   - Terminate only when a single uninterrupted top-to-bottom pass finds ZERO mismatches across every
     named section and every horizontal row. That clean full pass = done. There is no per-section
     "already checked, skip it" - correctness is proven by one clean sweep of the whole screen, not
     by accumulated ticks.
   Deviations RN genuinely cannot reproduce are raised to design, never shipped silently.

## Non-goals

- No Android (deferred).
- No autonomous shipping - the engineer runs the gates and opens the PR.
- No screens outside the designed Figma sections (registry coverage map decides).
- No HARD Figma MCP dependency - the MCP is used first when available, but the skill still completes
  via the REST fallback when MCP access is denied. No Figma variable-name dependency either.

## References

- `references/figma-rest.md` - REST extraction recipes, token, rate limits, spec sheet, image fills.
- `references/token-inventory.md` - the real Aura theme tokens.
- `references/component-registry.md` - component registry + coverage map (the point of reference).
- `references/design-rules.md` - the 10-rule design-QA self-review gate.
- `references/sim-verify.md` - the iOS simulator build-run-screenshot-compare loop.
