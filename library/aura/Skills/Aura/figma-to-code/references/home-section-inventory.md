# Home — named-section inventory (Phase 1a checklist)

> **The legacy design is gone (PROD-7647).** The "Gate used: `isRedesignTheme(...)`" notes below
> are build history; the helper no longer exists and Home has only its 4.0 render.

> ⚠️ **A newer frame exists: `3226:25791` "Home"** (402x5333, 13 content sections) — the expanded
> Home that supersedes `800:6687` below. It keeps every `800:6687` section and adds three:
> **Recommended Coaches** (`3238:31906`, Card/Coach x3 @220), **Made For You Weekly Playlists**
> (`3238:31790`, now a real Card/Playlist node), and a correctly-named **Light within Podcast by
> Aura** (`3238:32999`, PodcastCard x2 @290). The `Today` heading is renamed **"Today's practice"**
> and the `Filter` pill (`3226:25816`, "Filters") is now visible (was hidden). Every section heading
> gains a **"View all"** affordance. See `component-registry.md § E.3` for what was built against
> this frame. The `800:6687` table below is still valid for the shared sections.

Figma: file `dpxPlTNW722Y1Qr8JRnSIn` ("Aura-Health-4.0"), frame **`800:6687` "Home"**, 402x4026,
dark-only. Sections below are the direct children of the scroll container `Wrapper` (`800:6690`),
**in Figma order**. This is the checklist BUILD and VERIFY tick off section by section, by name.

> Derived from the node tree via Figma MCP `get_metadata`. Regenerate if the frame changes.

| # | Section node name | node id | Heading text | Children (type x count) | h |
|---|---|---|---|---|---|
| 0 | Frame 1193 (header) | 800:6691 | "Good Morning, {name}" | settings-btn x2, Logo, Home Carousel x5 | 207 |
| – | Filter | 800:6708 | "Today" | settings-btn, text | **hidden=true — do not build** |
| 1 | Frame 1195 | 800:6712 | **Today** | Card/Default x2 (290x340) | 398 |
| 2 | Aura Live | 800:6729 | **Aura Live** + View all | Card/AuraLive x2 (290x380) | 422 |
| 3 | Celebrity Masterclass | 800:6736 | **Celebrity Masterclass** | Card/CelebrityMasterclass x1 (366x420) | 478 |
| 4 | Celebrity Masterclass | 800:6741 | **Celebrity Masterclass** ⚠️ | PodcastCard x2 (290x290) | 348 |
| 5 | Programs For You | 800:6747 | **Programs For You** + View all | Card x2 (290x427) | 469 |
| 6 | Series | 800:6754 | **Series** + View all | Card/Default x2 (290x340) | 382 |
| 7 | Aura Live | 800:6761 | **A quiet mind** + View all | Card/Track x2 (180x329) | 371 |
| 8 | Aura Live | 800:6768 | **Parenting** + View all | Card/Track x2 (180x329 / 366) | 408 |

Outside `Wrapper`: `800:6688` backdrop (image 23), `800:6775` Status bar,
`800:6776` Tab Bar + Background (402x124).

## ⚠️ The frame is 402pt — and `responsiveDimension` is NOT

`responsiveDimension` is `moderateScale(v, 0.3)` against size-matters' **350pt** guideline base, but
every Aura 4.0 frame is authored at **402pt**. Feeding a 4.0 value through it inflates the result by
`(402/350 - 1) * 0.3` ≈ **4.5%** on the 402pt reference device — a 28pt icon renders at 29.25, a 9pt
label at 9.4 — and the error compounds through nested spacing. This is why the screen kept reading
"close but not the same" through several passes.

Use **`scaledDimension`** (`src/styles/dimensions.js`) for anything taken off a 4.0 node: same
moderate-scale behaviour, based on 402, so it is exact on the reference width and still proportional
elsewhere. The shared `theme.spaces.*` / `theme.borderRadius.*` tokens are ALSO 350-based and cannot
be retuned (the whole app uses them), so 4.0-only code calls `scaledDimension(N)` instead of the
token. Everything under `src/components/homeFeed/`, the `newDesign*` font tokens, the header and the chips
are converted; `responsiveDimension` stays for all pre-4.0 code.

Beware: the header container `800:6691` reports **width 428** and the card rows report **600 / 380**.
Those are horizontal scroll overflow, not a second device width — the screen frame, status bar and
tab bar are all 402, and `Wrapper` is 366 at x=18 (an **18pt** page margin).

Type ladder line heights are Figma's multiplier x size, unrounded: Display 30 Serif 1.2, Display 25
(both faces) **1.1**, Display 20 / Body 16 / Copy 14 / Caption 12 all **1.3**, Button 16 exactly 100%.

## Tab bar — use node 3094:29722, not 800:6776

`800:6776` (the Home instance) shows FOUR tabs plus a separate circular search button. The updated
component **`3094:29722`** keeps ALL FIVE tabs (Home / Sleep / Coaches / Live / Me) and has no search
circle — Live's icon there is `icon.earth`, not `tapbar.live`, which is why earlier attempts to filter
Live out never matched. `components/tabBar/HomeTabBar` implements 3094:29722.

Verified on-device by decoding a screenshot: the pill lands at y **770..834** on a 402x874 screen,
x=23.5, 355 wide, corner arc consistent with r=32 (RN clamps radius 53 to half of a 64pt height).
That is exactly the node — 402x124 = 20 top + 64 pill + 40 bottom.

⚠️ The gradient is **meant** to be translucent (`rgba(19,19,19,0)` -> `0.59` at 24.773% -> `#131313`),
so scroll content showing through above and behind the pill is correct, not a bug. It only reaches
solid `#131313` at the very bottom edge.

⚠️ `surface/nav` is white@10% over a **17pt backdrop blur**, not an opaque fill. Pre-compositing it to
one solid colour reads flat and too light; `HomeTabBar` stacks a real `CustomBlurView` under a
white@10% wash, and keeps the pre-composited colour only as the Android fallback (no backdrop blur
there). The node's `inset 0 0 11 white@5%` glow is Figma's topmost layer, so it is a sibling ABOVE the
items rather than a `boxShadow` on the container.

⚠️ Do NOT wrap the bar in the legacy `tabStyle` — it is `flexDirection: 'row'`, which collapses the
bar to content width and pins the pill left. It needs a full-width column wrapper plus
`width:'100%', alignSelf:'stretch'` on HomeTabBar's root.

⚠️ **Section 4 looks like a design copy-paste artifact**: the node AND its heading text node are both
named "Celebrity Masterclass" (duplicating section 3), but it contains `PodcastCard`s. The live app
renders this row as "Light Within Podcast by Aura". Raise with design; do not ship a second
"Celebrity Masterclass" heading.

⚠️ **Node names are reused and do NOT match headings.** Sections 7 and 8 are both node-named
"Aura Live" but head "A quiet mind" / "Parenting". Always key off the heading text node, never the
frame name.

## App reality vs this inventory

The agreed scope is **"restyle existing rows, keep data + order"**, so the app's live row lineup is
NOT forced to match this list. The app additionally renders `Live Sessions`, `Made For You Weekly
Playlists`, `New`, `Popular on Aura`, etc. Those are in-scope for *styling* (they must not stay
light-theme on the dark canvas) but their presence/order is not a mismatch against Figma.

Use this inventory to verify each Figma-specified section's **styling** against its app counterpart
(see the section -> component map in `component-registry.md`), not to enforce Figma's exact lineup.

## Frame `3226:25791` — the current Home (re-extracted, 13 sections)

Direct children of `Wrapper` (`3226:25794`, 366 @ x=18), in order. **Uniform 40pt gap between
every section; 16pt from section title to cards; 20pt card gap.**

| # | Section node | node id | Heading | Cards |
|---|---|---|---|---|
| 0 | header | 3226:25795 | greeting (2 text nodes) | 1 icon left / 2 right (12pt gap), 60pt ring, 5x chip 76x67 gap 12 |
| – | Filters | 3226:25816 | — | 78x36 pill (now visible) |
| 1 | Today's practice | 3226:25817 | + View all | Card 290x340 x2 |
| 2 | Aura Live | 3226:25824 | + View all | Card/AuraLive 290x380 x2 |
| 3 | Celebrity Masterclass | 3226:25831 | (no View all) | Card/CelebrityMasterclass 366x420 |
| 4 | ⚠️ dup-named "Celebrity Masterclass" | 3226:25836 | (no View all) | PodcastCard 290x289.59 x2 |
| 5 | Programs For You | 3226:25842 | + View all | Card 290x427 x2 |
| 6 | Recommended Coaches | 3238:31906 | + View all | Card/Coach 220x292 x3 |
| 7 | Made For You Weekly Playlists | 3238:31790 | + View all | Card/Playlist 290x290 x2 |
| 8 | Light within Podcast by Aura | 3238:32999 | + View all | PodcastCard 290x289.59 x2 |
| 9 | Series | 3226:25849 | + View all | Card 290x340 x2 |
| 10 | A quiet mind | 3226:25856 | + View all | **Card/Track 200x351** x2 |
| 11 | Parenting | 3226:25863 | + View all | **Card/Track 200x351/367** x2 |
| 12 | Tab Bar + Background | 3226:25871 | — | 402x124 |

⚠️ **Card/Track is 200x351 in this frame, not the 180x329 of `800:6687`.** Section 4 is still the
copy-paste artifact (node + heading both say "Celebrity Masterclass" but it holds PodcastCards);
section 8 is the correctly-named podcast row. Sections 10/11 are still node-named "Aura Live".

See `component-registry.md § E.7` for the nine drifts this frame's re-extraction exposed.

## Status (last swept: superseded 800:6687 pass)

| Section | Built? | Notes |
|---|---|---|
| 0 header | ✅ | glass circles + 60pt ring + Display 25 greeting; chips as 76x67 glass tiles |
| backdrop | ✅ | canvas + supplied pre-blurred backdrop asset (`newDesignHomeBackground.png`) |
| 1 Today | ✅* | `DefaultCard` wired + verified. *First slot for new users is `FirstMeditationMoreVisibleCard` — a bespoke onboarding card with a CTA and NO Figma counterpart; left as-is (see note) |
| 2 Aura Live | ✅ | `LiveCard` built + wired via `LiveMeditationRowItem` + verified |
| 3 Celebrity Masterclass | ✅ | `CelebrityCard` wired via `CelebrityMasterClassRow` |
| 4 Podcast | ✅ | `PodcastCard`. TWO surfaces — see "Podcast rows" below |
| 5 Programs For You | ✅ | `ProgramCard` wired via `ProgramBigCard` (opt-in `newDesignHomeCard`) |
| 6 Series | ✅ | `DefaultCard` wired via `SeriesTrackCard` + verified. Open: no content-type Tag (see below) |
| 7 A quiet mind | ✅ | Card/Track via `CustomTrackItem` gated branch |
| 8 Parenting | ✅ | same |
| tab bar | ✅ | `HomeTabBar` from node **3094:29722** (see "Tab bar" below) |

## Podcast rows — there are TWO, and they need different cards

Both render Figma's `PodcastCard`, at different widths. Missing the second one is what made
the Home Podcasts carousel look un-redesigned even though the card existed:

| App surface | Component | Figma | Width | Gate |
|---|---|---|---|---|
| "Light Within Podcast by Aura" (single episode, full-bleed) | `components/podcast/AuraPodcastRow` | 3094:29676 -> one PodcastCard @366 | 366 | plain `isRedesignTheme(theme)` |
| **"Podcasts" carousel (podcast series)** | `screens/podcasts/PodcastsRow` -> `series/SeriesTrackCard` | 800:6741 -> PodcastCard x2 @290 | 290 | `newDesignHomeCardVariant="podcast"` |

`SeriesTrackCard` now takes **`newDesignHomeCardVariant: 'default' | 'podcast'`** (replacing the old
boolean `newDesignHomeCard`) because BOTH Home rows route through it and it owns the
podcastsDetail/seriesDetails navigation split in `onPress`. `SeriesRow` passes `'default'`,
`PodcastsRow` passes `'podcast'`; the other 11 dependents pass nothing and are untouched.

⚠️ **`PodcastCard`'s root IS a `TouchableOpacity`; `DefaultCard`'s is a plain `View`.** So the
podcast branch passes `onPress`/`testID` straight to the card, while the default branch keeps the
wrapper. Wrapping PodcastCard would nest two touchables and the inner one (no `onPress`) claims the
responder and swallows the tap.

⚠️ **Provider label wording deviates.** Figma reads "with Steve & Daniel Lee"; the app has no
"with {{name}}" content key, only `content_card_by_author` ("by {{name}}"). Reused rather than
invented — needs design/loc sign-off if "with" is required.

Also styled by the Home-wide dark theme swap (legible, but cards not yet Figma): `Live Sessions`,
`New`, `Popular on Aura`.

## `Made For You` — built from a SCREENSHOT, not a node

Renders via `components/MadeForYouRowContainer` -> `screens/madeForYou/MadeForYou` ->
`MadeForYouItem` -> **`components/homeFeed/MixCard`** (opt-in prop `newDesignFeedCard`, set only by the
feed row so the other caller, `OnboardingYourPlanMadeForYou`, is untouched).

⚠️ **This is the one card in the 4.0 set not transcribed from Figma.** The section exists only in
file **`lwoV0lfkBSxNW2c2yWRl9C` ("Aura-Health-2.0"), node 1398:17049**. That file cannot be opened
by the Figma MCP under this account (<redacted-email>, "Ashish Kumar's team", Full seat) — it
returns **"you don't have edit access to this file"** for `get_metadata`, `get_screenshot` AND
`get_design_context`, on the section node and the frame alike. The MCP needs **edit** access, not
view. There is no MFY section anywhere in the 4.0 file, and `search_design_system` finds no playlist
card in any reachable library.

What the reconstruction is based on: a user-supplied screenshot plus the 4.0 system's own scale. The
**290 square footprint and 20pt gap are solid** — derived from the screenshot's card:gap ratio
(~0.068) cross-checked against a "View all" label measuring 45pt, which is exactly its width in
every other 4.0 section. Type sizes are inferred (Display 20 title / Caption 12 subtitle, matching
PodcastCard, the same 290-card family).

Known gaps, all deliberately left rather than fabricated:

| Mock shows | Status | Why |
|---|---|---|
| Periwinkle title colour | `accentPeriwinkle` **sampled by eye** | Only non-Figma value in the 4.0 palette. It is NOT in the file's variable set (which is entirely white-alpha + `#131313`). |
| Subtitle "Fresh picks · 6 tracks" | **omitted** | A topic carries no descriptor and no track count — the mix is generated from `topic.query` at open time, so the row never knows its length. Needs a data source AND new copy. |
| Tilted photo-tile collage | **omitted** | All three of a topic's images (`madeForYouMix.img`, `lightModeImage`, `darkModeImage`) are pre-rendered *text* treatments of the mix name — using one would duplicate the live title. Needs an exported asset or per-track thumbnails. `MixCard` takes an `artwork` node prop so it can be dropped in later. |
| Heading "Made For You" + "View all" | **not changed** | App renders `content_feed_type_mfy_weekly` ("Made For You Weekly Playlists") plus an Edit-topics action. Copy/IA decision, not a restyle. |

Provider label uses a new key **`content_card_provider_aura`** ("Aura"), added to all 7 locales
(brand name, identical in each).

⚠️ **`npx i18next-scanner` is broken in this repo** — `i18next-scanner.config.js` uses
`module.exports` but Node resolves `.js` as ESM, so it dies with "module is not defined in ES module
scope". Pre-existing (config untouched since #4640), unrelated to any 4.0 work. New keys must be
added to the six non-English locale files by hand, in alphabetical position; verify with
`npm run i18n:check`.

Also styled by the Home-wide dark theme swap only (legible, but not Figma cards): `Live Sessions`,
`New`, `Popular on Aura`.

## Uncovered app surfaces (Figma has no counterpart)

- **`src/screens/todayRow/FirstMeditationMoreVisibleCard.js`** — occupies the FIRST Today slot for
  users who have not listened yet. It carries its own conversion CTA
  (`first_meditation_listen_btn_text`), which Figma's Today section (2x Card/Default, no button) does
  not have. Deliberately NOT forced into Card/Default: doing so would drop the CTA. It reads correctly
  on the dark canvas via the Home-wide theme swap. **Raise with design** — Home's first-run state needs
  a 4.0 spec.

## Today-card wiring notes

Two distinct paths feed the Today row, both ending at `CustomTrackItem`:
- `TodayRow.renderCustomTrack` -> **`SingleTrack`** -> `CustomTrackItem` (spreads `{...this.props}`,
  so `todayCardUI` is forwarded)
- `TodayRow` -> `CustomTrackItem` directly

Gate used: `isRedesignTheme(theme) && this.props.todayCardUI`. `todayCardUI` is passed by
`TodayRow` ONLY (verified: 1 file repo-wide), so this cannot leak to another surface.

⚠️ **DefaultCard padding gotcha (cost a fix cycle):** absolutely-positioned children size against the
**padding box**, so putting the card's 20pt padding on the outer view inset the cover photo, scrim and
glow, leaving a visible dark gutter on the right/bottom. The padding must live on an INNER content
view so the overlays can fill the card. Any future card built on this pattern needs the same split.

## Series wiring notes

Chain: `SeriesRowContainer` -> `series/SeriesRow` -> **`series/SeriesTrackCard`** (which owns the
podcastsDetail/seriesDetails navigation split in its `onPress`, so do NOT bypass it).

`SeriesTrackCard` has **12 dependents** (`SearchSeriesList`, `PodcastsTab`, `PodcastsRow`, `SeriesTab`,
`FavoriteSeries`, `SeriesContinueRow`, `ProgramContinueRow`, ...), all outside the redesign. The gate is
therefore an **explicit opt-in prop, `newDesignHomeCard`, set only by `SeriesRow`** — not an inferred
context like `sentFrom`. Prefer this pattern whenever a shared card has many out-of-scope callers.

⚠️ **Open fidelity item:** the Series card renders with NO content-type Tag. The generic Card/Default
(800:6727) has one ("Story • 3 min"), but the Series instance is a different node (800:6759) whose tag
override was NOT extracted. Deliberately left absent rather than guessing a label — guessing is the
documented #1 fidelity failure. **Extract 800:6759 and confirm** whether the tag is shown and with what
text before ticking section 6 fully clean.

## Aura Live wiring notes

Chain: `AuraLiveRowContainer` -> `liveMeditation/liveMeditationList` ->
**`liveMeditation/LiveMeditationRowItem`** (3 dependents only, so a plain experiment gate is safe
here — no context prop needed).

`src/components/homeFeed/LiveCard.js` = Figma `Card/AuraLive` (800:6734). Reuses the row item's own
`openEventDetails` (card tap) and `reserveSpot` (CTA), so reservation + navigation are unchanged.

Data all came off the existing event model: `getTitle()`, `getStartDateFormatted()`, `getCoachID()`,
plus `CoachesStore.getCoachName / getCoachProfessionalTitle / getCoachPhotoWithNoBackground` — that
last one is the cut-out photo the Figma card needs.

Deviations: `backdrop-blur 15` omitted (no RN backdrop-filter; white@5% over a near-black canvas is
equivalent), and the Figma photo mask is a horizontal LinearGradient scrim rather than a MaskedView.
The "Live Now" badge only renders for `isPreview` events, mirroring the legacy row's own rule — the
Home row shows upcoming events, so it is correctly absent there.

⚠️ **i18n keys: do not invent them.** I guessed `live_meditation_reserve_spot_button` /
`live_meditation_reserved_label` and both were wrong. The real keys are
**`live_tab_card_reserve_cta`** and **`live_tab_card_reserved_cta`** (live.json). Always grep the
localization JSON for the English string before writing a `t()` call.

## LiveCard — three fidelity bugs found only by looking at the sim

All three passed lint and "looked structurally right"; only the on-device compare caught them.

1. **Photo anchored to the wrong edge.** I first built it bottom-right at 160x195 (copied from the
   legacy row item's measurements). The node says a **258x258 square at `top:0, left:58.66`** — i.e.
   TOP-anchored, overflowing the right edge by ~27pt. The wrong anchor left the card's top third
   empty. Read the geometry off the node; never inherit it from the legacy component.

2. **A scrim cannot replace a mask.** Figma masks the photo (Mask group I800:6734;586:6738) so it
   dissolves. I approximated with gradient scrims laid OVER it, which left the photo's **hard
   rectangular bottom edge visible as a line across the card** — a bright subject shows straight
   through 0.85 black. Fixed with **`@react-native-masked-view`** (already a dep, used by
   `series/SeriesTrackCard`) applying a real alpha ramp, with the scrim demoted to a legibility wash.
   **Rule: when the node has a Mask group, use MaskedView — do not substitute a scrim.**

3. **Collapsed spacer = wrong rhythm.** The node's column is `gap-12` and contains an explicit 4pt
   `sp-sm` child between the title block and host row, so title -> host is **12 + 4 + 12 = 28pt**. I
   had collapsed it to a single 12, compressing the lower half. Reproduce spacer children literally
   rather than folding them into one margin.

⚠️ **Known content difference (not a code bug):** Figma's mock uses a tight head-and-shoulders cut-out.
`CoachesStore.getCoachPhotoWithNoBackground` returns a fuller portrait, so `cover` fills more of the
card width than the mock. Raise with design if the tighter crop matters — it needs a different asset,
not different code.
