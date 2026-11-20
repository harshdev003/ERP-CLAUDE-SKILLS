# Aura 2.0 Component Registry (the point of reference)

Status: DRAFT v1. Evidence = 6 of 17 Figma sections machine-extracted (Home, Profile, Settings,
Live, Sleep, Coach - the densest). Dependency counts from live grep. Remaining 11 sections pending.
Update this file whenever you build/extend a component or design ships a new section.

> **The legacy design is gone (PROD-7647, design cleanup Parts 1–10).** The entries below were
> written while 4.0 shipped behind `auraDesignChange3`. Their `isRedesignTheme(...)` /
> `isAuraDesignChange(...)` gates, "legacy render / variant-`b` fallback" notes and "legacy path
> untouched" remarks are build history: those helpers no longer exist, and each screen keeps only its
> 4.0 render. Read an entry for its components, tokens and Figma mapping, not for how to gate. See
> `SKILL.md § One design`.

## Working agreement (how a screen uses this registry)

1. Doing a screen, you find a UI element -> look it up here FIRST.
2. Exists in the new-design section -> use it, never re-implement.
3. Only an old component exists -> dependency-check (`grep -rl "reuseableUI/<Path>" src/ | wc -l`):
   - additive change + low deps -> extend the old one (new variant/prop); old screens keep working.
   - not additive, or high deps -> build NEW in the new-design section; old screens keep the old one;
     retire it later after all its screens migrate (separate cleanup card, never preemptive).
4. Exists nowhere -> build new, prop-driven, in the new-design section; add it here.
5. Screen not in the redesign (coverage map below) -> keep old components untouched.

New-design components live in FEATURE folders, never a design-version folder: home-feed cards in `src/components/homeFeed/`, the tab bar in `src/components/tabBar/`, coach profile in the existing `src/components/coachProfile/`. The design-generation signal is carried by the **redesign themes** (`redesignDark` / `redesignLight`, which ThemeStore swaps in on `auraDesignChange3`) and `src/img/newDesign/`. Redesign tokens are unprefixed semantic names on those themes — see `token-inventory.md`; the old `newDesign*` token prefix is gone. Never introduce a version-numbered name (`aura2/`, `aura4/`); the repo has no precedent for one.

## A. Shared components (Layer A) - build/extend once, used by many screens

| Component | Figma evidence (6 sections) | Repo today | Deps | Verdict |
|---|---|---|---|---|
| Button | button/secondary 5 screens, 23x | reuseableUI/Button/ButtonCenter | 106 | EXTEND additive (new ButtonType: white pill). Legacy appReusable/Button (101) untouched |
| Text | Season type ladder everywhere | none (theme.fonts spreads) | n/a | BUILD NEW AppText (single Season-font enforcement point; QA Rule 1) |
| Icon | action.*, tapbar.*, specialty.* dozens/screen | reuseableUI/Icon | 206 | EXTEND additive (new icon set via npm run bundleIcons); never rename keys |
| Tag / pill chip | tag 4 screens, 43x | Pill (buggy placeholder) | ~0 | BUILD NEW Tag |
| Premium lock badge | field.locked 4 screens, 51x | none | n/a | BUILD NEW LockBadge (on every card type) |
| ThumbnailCard | Card/Track Home+Sleep 18x | CardContent (840-line switch) | 5 | BUILD NEW GlassCard surface + TrackCard. Do NOT add a 10th CardContent type |
| Tab bar | tapbar.* 4 screens | reuseableUI/TabBar | 9 | REBUILD (pill+blur+mini-player). Own card; routing risk |
| HomeCarouselTab | Home header tabs | HomeTabMenu | 0* | EXTEND/restyle |
| TextInputField | Login/Search (pending) | reuseableUI/Input | 8 | EXTEND (restyle via new theme; testID already forwarded) |
| CarouselTabCheckbox | Home quick-link chips | ChipBar | 12 | EXTEND or new (pending usage contract) |
| Dropdown | pending | Menu | 6 | EXTEND likely |
| ProgressBar | pending | appReusable/ProgressBar + SVG variants | 8 | EXTEND |
| LiveNowButton | Live badges | Badge/BadgeLive | 7 | EXTEND |
| Content List row card (incl Live) | LiveFeaturedCardV2 13x; rows | TextRow | 25 | NEW ListRowCard likely; TextRow untouched |
| Coach card | ProfileStatCard 45x; Coach section | CardContent coach type | 5 | NEW composition on GlassCard |
| Program card | Programs row + Program section | CardContent course type | 5 | NEW composition on GlassCard |
| Stats Card | ProfileStatCard 2 screens 45x | Streaks partial | low | BUILD NEW |
| Calendar / DayPicker | DayPickerCell 84x+14x (Profile, Live) | old calendar modal | low | BUILD NEW DayPicker |
| Coaching work Steps Card | Coach section | none | n/a | BUILD NEW |
| PromoCard | pending | none | n/a | BUILD NEW |
| DrawerTabCard | pending | none | n/a | BUILD NEW |
| Premium Banner | `components/settings/SettingsHeroBanner.js` | Banner | 2 | BUILT (Settings) — `variant` premium/family |
| Heading / section header | every content screen | Heading | 44 | EXTEND additive (new headingType) or new |

## B. Screen-generic (Layer B) - built with their page, still prop-driven

ProfileListRow (16x, Profile only); Settings row Action=Chevron (32x, Settings only); Search result
rows; Onboarding quiz option cards (Sign Up).

## C. Dependency risk table (live grep - files importing each)

| Component | Deps | Policy |
|---|---|---|
| reuseableUI/Icon | 206 | additive only |
| reuseableUI/Button/ButtonCenter | 106 | additive only (new enum values) |
| appReusable/Button (legacy) | 101 | DO NOT TOUCH |
| reuseableUI/Heading | 44 | additive only |
| reuseableUI/TextRow | 25 | prefer new component |
| reuseableUI/ChipBar | 12 | extend with care |
| reuseableUI/TabBar | 9 | being rebuilt |
| reuseableUI/Input | 8 | extend |
| appReusable/ProgressBar | 8 | extend |
| reuseableUI/Badge | 7 | extend |
| reuseableUI/Menu | 6 | extend |
| reuseableUI/CardContent | 5 | freeze - compose new cards on GlassCard |
| reuseableUI/Banner | 2 | either |

## D. Coverage map - screens in the redesign vs not

In the redesign (17 Figma sections): Profile x2, Home, Settings x2, Live, Sleep, Coach, Program,
Playlist/Podcast, Recent Tracks, Saved Tracks, Search, Sign Up, Login, Splash, Various.

NOT redesigned yet (keep old components until their section ships): audio/meditation player
(highest-traffic screen), paywall/subscription family, community, journals, challenges, chat/messages,
breather, timer, reels, quotes, track details, series, events, family plan, referral,
gift, coaching MVP/V1 flows (Coach section covers profile-card patterns only).

## E. Light/dark facts (verified)

Profile + Settings have BOTH light (lum 0.97) and dark (lum 0.07) frames. Search / inner-settings /
library screens: light-only mocks. Home / Sleep / Sign Up: dark-only. => two-theme model inheriting
`newLight`/`newDark`; if it ever needs its own palettes, name them for the design initiative
(the repo's existing pattern), never for a version number. Confirm with design whether Home/Sleep are
intentionally always-dark.

## E.1 Built so far (iOS onboarding, constant-dark)

Shared foundation used by both steps below (all in `Onboarding6.render()` gated on
`isConstantDarkView = ['name','valueProps']`):
- **Forest backdrop**: `src/img/onboardingNameBackground.jpg` (Figma image fill 55d645c1…, LAYER_BLUR
  100 baked in) rendered full-screen `cover` (explicit device W/H — NOT inset-0, else a small asset
  renders at intrinsic size) under the `gOnboardingDark` vignette scrim (globals/gradients.js).
- **Season fonts (TRIAL)** bundled `assets/fonts/` + Android + `ios/Aura/Info.plist` + main-app
  target in `project.pbxproj` (NOT via `react-native-asset` — phantom AuraWatch targets break it;
  add to the main app target only). Tokens in globals/fonts.js: `titleSerifRegular30` (Serif 30),
  `fieldSansLight20` (Sans Light 20), `buttonSansSemiBold18` (Sans SemiBold 18). ⚠️ TRIAL — swap for
  licensed before shipping (PostScript names lose `-TRIAL`; update the 3 tokens then).
- Screens render against the constant-dark palette (the step is dark in both app themes), taken
  from `getRedesignConstantDarkTheme(theme)` — never by naming a theme; `renderBack(iconColor)`
  extended additively for a white chevron.

- **Name step — node 1048-16912** (`renderName` in Onboarding6.js): serif heading, frosted field
  (`fieldSansLight20`), white "Continue" pill (`buttonSansSemiBold18`), 20pt margins.
- **Welcome step — node 1055-17752** (`src/screens/ValueProps.js`): aura ring + "AURA" (Proxima),
  `fieldSansLight20` tagline, white "Let's get started" pill + "Existing user? Log in"
  (`buttonSansSemiBold18`). Replaced the legacy `OnboardingSwiper` carousel (left as dead code — verify
  no other importers before deleting). JS-only; reuses the shared backdrop + fonts.
- **Content-interest step — node 1048-16823** (`src/screens/ContentTypeOnboarding.js`): 2-col glass
  card grid over image fills, `gOnboardingCardScrim` label scrim, `titleSerifRegular30` heading +
  `bodySansRegular16` subtitle, white "Continue" pill. Constant-dark `DARK_THEME` inside the file.
- **Topics step** (`src/screens/PersonalizedOnboarding.js`): constant-dark gated on
  `isDarkOnboarding()` (= not menu/me-tab/recommended-topics contexts) so only the onboarding entry
  is restyled; the reused-elsewhere contexts keep the app theme.
- **Goals step — node 1048-16080** (`src/screens/OnboardingMotivation.js`): `titleSerifRegular30`
  heading + `bodySansRegular16` subtitle + aura ring; single-column **frosted goal rows** (white
  @5% fill + double inset shadow, R18, 64pt, label `fieldSansLight20` 18) with a trailing checkbox
  (empty square / white-filled `✓`); selected rows get a faint teal wash (RN can't do the Figma
  white "Continue" pill always shown, dimmed until ≥1 selection. Constant-dark `DARK_THEME`;
  dropped the old `DynamicBackground`/`ThemeStore` listener for the shared forest backdrop +
  `gOnboardingDark`.
  - **Selected row** (from Figma `Component/Carousel/Tab` node 1048-16087, verified against cached
    node data): white @5% fill + **1px solid white border** + a **teal radial glow** (Figma
    `Rectangle 366`: `rgb(82,114,112)` opaque at ~10%x/50%y → `rgba(55,88,85,0)`, reproduced with a
    `react-native-svg` `RadialGradient` rect behind the content) + a **white-filled checkbox** with a
    dark check. Unselected checkbox = rounded-square white outline.
  - **Leading icons**: distinct white line-glyph per goal, mapped by motivation `unique` via
    `ICON_FOR_UNIQUE` (mindfulness→mind, happiness→sun, boostEnergy→bolt, emotions→sparkle,
    peace→wave; sparkle fallback for the other ~19 dynamic topics). Rendered as inline
    `react-native-svg` vectors in `renderGoalGlyph` (each wrapped in an iOS white shadow ≈ the Figma
    `LAYER_BLUR` glow). ⚠️ These SVGs were hand-reproduced from the design because the Figma asset
    export was unavailable at build time (REST `/images` + `/nodes` quota-capped 429; Figma MCP
    `download_assets` needs an editor seat — view access returns "don't have edit access"). The
    `sun`/`bolt`/`sparkle`/`wave` glyphs match closely; `mind` (head+spiral) is approximate. **To
    finalize: export the 5 icons (nodes 1048-16089/16100/16109/16115/16122) as SVG and replace the
    `renderGoalGlyph` cases** — that's the single swap point.

New tokens added since the Name/Welcome entries: `bodySansRegular16` (Sans Regular 16) in
globals/fonts.js; `gOnboardingCardScrim` in globals/gradients.js.

## E.2 Built so far (Home, Aura 4.0 — Stage 1 of 4)

⚠️ **Figma source changed.** Home came from **`dpxPlTNW722Y1Qr8JRnSIn` ("Aura-Health-4.0")**, not the
`lwoV0lfkBSxNW2c2yWRl9C` recorded in `figma-rest.md`. Every node id in §E.1 above is keyed to the OLD
file. Confirm which file is authoritative before trusting a node id.

⚠️ **MCP access is file-dependent** (see SKILL.md Phase 1 - MCP-first, REST fallback). For the 4.0
file `dpxPlTNW722Y1Qr8JRnSIn`, `get_metadata` / `get_design_context` / `get_variable_defs` all
returned under this account (Full seat on "Ashish Kumar's team") - so this Home build used the MCP
for design-context reads. For the 2.0 file `lwoV0lfkBSxNW2c2yWRl9C` the same calls are denied
("don't have edit access") - see the MFY note in `home-section-inventory.md` - and `download_assets`
needs an editor seat regardless. Use the MCP when it reads the file; fall back to REST (`figma-rest.md`)
when it does not.

**Home — node 800:6687** (402x4026, dark-only, 8 content sections + tab bar). Gated on
`isRedesignTheme(this.props.theme)`.

Stage 1 (done) — foundation + header + backdrop:
- **Tokens.** Full 4.0 variable set transcribed. `globals/fonts.js`: `titleSerifRegular30`,
  `displaySerifRegular25`, `displaySansRegular25`, `displaySansRegular20`, `bodySansRegular16`, `buttonSansSemiBold16`,
  `copySansRegular14`, `captionSansRegular12`. `globals/colors.js`: `white05/10/40/50`, `black40`,
  `canvas` (#131313). `newDark/colors.js`: `textTertiary`, `textMuted`,
  `surfaceSubtle`, `surfaceGlass`, `surfaceOverlay`, `canvas`.
  `redesign/borderRadius.js`: `borderRadius14`, `borderRadius24` (`radius-xl`).
  `redesign/gradients.js`: `gHomeBackdrop`.
  - The onboarding screens' four original Season tokens were folded into this ladder when the
    redesign became a theme: serif title -> `titleSerifRegular30`, field -> `fieldSansLight20`,
    button -> `buttonSansSemiBold18`, subtitle -> `bodySansRegular16`. Two line heights moved
    sub-point in the process (field 23.4 -> 24.58, button 21.1 -> 22) — the ladder's values are the
    Figma multipliers, so the ladder wins.
  - 4.0 `text/primary` -> existing `textPrimaryMain`; `text/secondary` -> existing `textSecondary500`
    (both exact matches — deliberately not duplicated).
- **Header — node 800:6691** (`src/components/AuraTabHeader.js`): 36pt glass circles on
  `surfaceSubtle` (icons 20pt, white stroke, top-aligned per Figma `items-start`), 60pt
  `ThemedAuraRing` centred, greeting at `displaySansRegular25` with the name in `textSecondary500`.
  `renderTopNav`'s icon arrays were extracted to **`buildNavIcons(strokeColor, iconSize)`** so both
  branches render identical icons/testIDs/order — the redesign restyles the header, it does not change
  its contents. Icon groups are absolutely pinned left/right (Figma's space-between only centres the
  ring because that mock has one icon per side; this screen can show two).
  - Greeting is split on a `\u0000` sentinel interpolated into the translated string, because the name
    position varies by locale — do NOT assume the name is last.
  - ⚠️ Figma shows no "!"; the translated copy (`content_greeting_morning_name`) has one. Left intact
    rather than stripping punctuation across 7 locales — a copy decision for design/localization.
- **Backdrop** (`src/screens/AuraTab.js`): `canvas` base + pre-blurred forest asset +
  `gHomeBackdrop`. Figma node 800:6689 is a photo under LAYER_BLUR 50 + a radial fade to #131313,
  which renders near-flat; RN cannot blur an image at runtime and neither ImageMagick nor PIL was
  available to bake it, so the already-bundled `onboardingNameBackground.jpg` stands in (7KB vs the
  1.4MB unblurred export). **To use the Home-specific photo, export it WITH the blur baked in** — then
  swapping the `AURA4_BACKDROP` require is the only change.
  - `renderThemedGradient` returns null under variant `a` in BOTH `AuraTabHeader` and `AuraTab`,
    else the theme gradient washes the dark canvas out.

Stages 2-4 (NOT built): category chips (76x67, R14, white@5%, 1px white@10% border, backdrop-blur 4,
icon + 1.2px blurred copy as glow, `captionSansRegular12` label @ white 50%); the 8 content sections and
their 7 card types (Card/Default, Card/AuraLive, Card/CelebrityMasterclass, PodcastCard, program Card,
Card/Track — all to be composed on a new GlassCard, NOT a 10th `CardContent` type); tab bar rebuild.

Stage 2 (done) — chips + dark feed:
- **The Home render chain**, for anyone hunting it again:
  `AuraTab` -> `Tab` -> `ScreenContainer` -> **`ScreenSelector`** (the category strip; each chip is a
  **`ScreenItem`**) + **`Screen`** (the content rows). Dead ends that cost time: `HomeTabMenu`
  (0 importers), `CollapsedFilterList` (Filter.js only), `ContentFilters` (0 importers),
  `TopicsStore.getAllButtonContentTypes` (0 importers). Chip labels come from
  `ContentTypeHelper.getHomeEmotionsList` via the CDN `type-<locale>.json`, so they are NOT greppable
  as i18n keys — that is why label-grepping fails.
- **Category chip — node 800:6703** (`renderNewDesignChip` in `src/screens/ScreenItem.js`): 76x67, R14,
  white@5% fill, 1px white@10% border, 20pt white icon, `captionSansRegular12` label @ white 50%, 12pt gap.
  Keeps the legacy `dashboard_categoryPill_<key>` testID so sim tests work in both variants.
  - Deviations: Figma's `backdrop-blur 4px` is omitted (RN has no backdrop-filter; ~24 native
    BlurViews in a horizontal ScrollView is not worth it, and over this near-black canvas a 5% white
    fill looks the same either way). The mock's double-icon glow (a 1.2px-blurred copy behind a sharp
    one) is an iOS white shadow instead — same substitution as the 2.0 goal glyphs.
  - Figma ships no *selected* variant of this component, so the active state (brighter glass fill +
    white border + full-strength label) is inferred. Confirm with design.
  - Label is `numberOfLines={2}`, not the mock's `nowrap`: at a fixed 76pt the longer CDN labels
    ("Life Coaching") would clip.
- **Dark feed via one theme swap** (`AuraTab.js`): under variant `a`, `<Tab theme={AURA4_THEME}>`
  instead of `this.props.theme`. This is the high-leverage move — `NEW_DARK` is a real, fully-populated
  theme every row/card component already renders correctly, so section titles, `Filters`, `Today`,
  `Live Sessions` and the card scrims all become legible on the dark canvas without touching ~20
  components. Do NOT restyle individual rows to fix dark legibility; swap the theme at this boundary.

Stage 3 (partial) — card foundation built, NOT yet wired:
- `src/components/homeFeed/GlassCard.js` — the shared cover surface every Figma card composes on
  (node I800:6766;252:172): radius 24, 1px white@10% border, drop shadow 0/6/9 black@15%. Owns the
  surface treatment ONLY — callers pass width/height, since each card type has its own cover size.
  Badge is a `badge` **node** prop, not a render prop (a render prop trips `react/display-name`).
- `src/components/homeFeed/LockBadge.js` — the Figma `tag` (node I800:6766;680:7225): black@20% fill,
  1px white@5% border, radius full, 5pt pad, 14.5pt glyph, inset 9pt. Uses the project's own
  `field.locked` from `generated/iconList` (verified present) rather than the exported Figma SVG.
- `src/components/homeFeed/TrackCard.js` — `Card/Track` (node 800:6766), spec-exact: 218pt cover,
  info block 16/12 padding, meta `captionSansRegular12` @ text/tertiary, title `bodySansRegular16` @ text/primary,
  26pt avatar + name at `bodySansRegular16`, 4pt `sp-sm` trailing spacer.

**TrackCard is WIRED and verified in the sim** — live on the "New", "Popular on Aura" and other
topic/track rows. Integration landed in `CustomTrackItem.render()` as an early-return branch placed
**after** the existing derivations, so it reuses the same data AND the same `this.onPress` (that handler
carries premium gating, the subscription modal and player navigation — never reimplement it).

Gate: `isRedesignTheme(this.props.theme) && this.props.sentFrom === Tabs.Home`. The
`sentFrom` half is load-bearing: `CustomTrackItem` has **22 dependents** including `SearchTrackList`,
`ListenLaterList`, `ConversationTrackRecs`, `PostHomework` and `MeditationTutorial`, none of which are
in the coverage map or have a Figma frame. Verified in the sim that variant `b` leaves all of them
untouched.

Field mapping (verified live):
- `cover` -> `{ uri: meditationPhotoURI, cache: 'force-cache' }` (`getPhotoUrl` returns a STRING, so it
  must be wrapped in `{uri}`; `meditationPhotoURI` uses the responsive portrait size, which fits
  Card/Track's 180x218 cover better than the landscape `photo400x225Url`)
- `meta` -> `[label, subText].join(' · ')` where `label` =
  `ContentTypeHelper.getLabelForContentType(...)` and `subText` = the duration string -> renders
  "Story · 62 mins", matching Figma's "Meditation · 10 mins"
- `title` -> `topic?.title` (confirmed — an earlier note wrongly flagged this as unknown)
- `authorName` / `authorAvatar` -> `CoachesStore.getCoachName/getCoachPhoto(topic?.userId, 'photo100Url')`
- `isLocked` -> **`!isPremium`**. `isPremium` in that render means "unlocked for THIS user" (premium
  account, inside the free-unlock allowance, or a podcast), so the Figma `field.locked` badge is its
  inverse. Getting this backwards shows locks on free content.

### Home section -> component map (fully traced; start here)

`Screen.js` -> `screenTypes/Feed.js` (a `ROW_TYPES` switch) -> one Row component per section:

| Section on screen | Row component |
|---|---|
| Today | `screens/TodayRow.js` |
| Live Sessions | `screens/AuraLiveStoriesRow.js` |
| Aura Live | `components/AuraLiveRowContainer.js` |
| Celebrity Masterclass | `screens/celebrity/CelebrityMasterClassRow.js` |
| Light Within Podcast | `components/podcast/AuraPodcastRow.js` / `PodcastRowContainer.js` |
| Programs For You | `screens/programs/ProgramsRow.js` |
| Series | `components/SeriesRowContainer.js` |
| Topic rows ("A quiet mind", "Parenting") | `components/RecommendationRowContainer.js` |

### Track-card integration recipe (the topic rows — highest leverage)

Full chain: `RecommendationRowContainer` -> `screens/rows/RecommendationRow.js` ->
`screens/explore/CustomTracks.js` -> **`CustomTrackItem`**.

**Integrate by wrapping at `CustomTracks.renderItem` (~line 746), NOT by restyling
`CustomTrackItem`.** `CustomTrackItem` has **22 dependents** — including `SearchTrackList`,
`ListenLaterList`, `ConversationTrackRecs`, `PostHomework`, `MeditationTutorial` — none of which are in
the redesign coverage map and none of which have a Figma reference. Restyling it leaks the 4.0 look
into all of them.

Gate on experiment AND context, mirroring `PersonalizedOnboarding.isDarkOnboarding()`:
`isRedesignTheme(theme) && sentFrom === Tabs.Home` (`CustomTracks` already receives
`sentFrom={selectedTab}`).

Field mapping for `<TrackCard>`, verified against `CustomTrackItem` (~line 730):
- `cover`       -> `AuraUtility.getPhotoUrl(topic, 'photo400x225Url')`
- `authorName`  -> `CoachesStore.getCoachName(topic?.userId)`
- `authorAvatar`-> `CoachesStore.getCoachPhoto(topic?.userId, 'photo100Url')`
- `isLocked`    -> the existing `isPremium` in that render
- `meta`        -> `ContentTypeHelper` label + duration
- `title`       -> **still to confirm** (not `topic.title`/`topic.name` — verify before wiring)

Keep the `dashboard_card_<trackId>` testID on the touch target, and preserve the existing press
handler / navigation — `TrackCard` is presentational and has no `onPress` yet, so either wrap it in the
existing touchable or add one.

⚠️ **Still NOT matching Figma (rest of Stage 3, and Stage 4):**
- **Tab bar** is still the light `TabBar3` — the largest remaining visual mismatch. Registry §A flags
  the rebuild as its own card (9 dependents, routing risk).
- **Card designs** are still legacy `CardContent`. Figma specifies 7: Card/Default 290x340,
  Card/AuraLive 290x380, Card/CelebrityMasterclass 366x420, PodcastCard 290x290, program Card 290x427,
  Card/Track 180x329 — all to compose on a new **GlassCard**, never a 10th `CardContent` type.
- **Section headers** lack the Figma "View all" affordance and serif treatment.
- Row list/order intentionally unchanged (the "restyle, keep data + order" decision), so the live feed
  keeps Today / Live Sessions / Aura Live rather than Figma's 8-section lineup.

## E.3 Built so far (Home, Aura 4.0 — expanded frame 3226:25791)

⚠️ **Newer Home frame.** The URL `?node-id=3226-25791` is an **expanded** Home (402x5333, 13
content sections) that supersedes the `800:6687` frame §E.1/§E.2 were keyed to. It keeps every
`800:6687` section and **adds three**: `Recommended Coaches` (Card/Coach), `Made For You Weekly
Playlists` (now a real `Card/Playlist` node), and a correctly-named `Light within Podcast by Aura`
row. All headings now carry a **"View all"** affordance (the live app already renders it on the rows
that support it — verified in the sim: "New", "Reduce Stress", "Relaxation", etc. show it).

**Recommended Coaches — node 3238:31906, Card/Coach 3238:31895** (`RecomendedCoachCard.renderNewDesignCard`,
gated on plain `isRedesignTheme(theme)` — the card has a single dependent,
`RecommendedCoachesRow`, so no context prop is needed). Glass card (white@10% fill, **white@5%
border/subtle** — not the white@10% surface fill), radius 24, pad 20, gap 12, w220: 72pt circular
FastImage avatar → name `labelSansLight18` (NEW token: Season Sans **Light** 18) → role `copySansRegular14`
text/secondary → meta-row (country flag image + `countryCode` • `track_follow_count_label_text`
followers) `captionSansRegular12` text/tertiary → 1pt spacer → white **"Follow"** pill (`borderRadiusFull`,
`buttonSansSemiBold16`, `canvas` #131313 label). Following state flips the pill to glass + white label.
Reuses the legacy `openAuthorDetails` (card tap) and `followPress` (CTA) so follow/unfollow +
profile nav are unchanged. Token-safe (all colors/fonts/radii resolve in NEW_DARK).
- ⚠️ **NOT visually confirmed on-device this session.** `RecommendedCoachesRow` is a feed ROW_TYPE
  whose placement is server/feed-config-driven; it did not surface in the test user's (Ashish+test)
  personalized Home feed within ~35 swipes (the feed is a long tail of dynamic topic rows). The
  branch is lint-clean and follows the exact verified pattern of `LiveCard`/`TrackCard`/
  `SeriesTrackCard`. Confirm on a user/feed-config where the coaches row renders.

**Made For You — `Card/Playlist` node 3238:31853 now real** (`components/homeFeed/MixCard.js`). The card
was previously reconstructed from a 2.0 screenshot (§E.2); the 4.0 frame now carries the real node, so
these three are now **spec-exact** (verified on-device):
- **Gradient title** — blue→periwinkle `linear-gradient(150.75deg, rgb(105,166,200), rgb(184,158,255))`
  via `@react-native-masked-view` + `LinearGradient` (was a flat `textAccent` periwinkle).
- **border/subtle white@5%** (`surfaceSubtle`), not the white@10% surface fill.
- **Subtitle** — now `copySansRegular14` (was Caption12) and populated with **"Updated weekly"** (new i18n
  key `content_card_updated_weekly`, added to all 7 locales; `MadeForYouItem` passes it). The mock's
  alternate "Wind down · 8 tracks" needs a per-mix track count the row has no data for — the
  always-true weekly descriptor is used rather than fabricating a count.
- ⚠️ **Collage still data-limited.** The real node draws six distinct 96pt covers rotated -45°; the
  row supplies only the single topic photo, so the existing 4-tile screenshot approximation stays
  (documented in MixCard's module note). A faithful 6-cover collage needs per-track thumbnails.

**Filters pill — node 3226:25816** (was `hidden` in 800:6687, now visible). The live app already
renders a `Filters` control; the Home dark-theme swap makes it legible on the canvas (verified). Minor
deviation: Figma wraps the slider icon in a 36pt glass circle; the app shows icon + label only.

New font token: `labelSansLight18` (Season Sans Light 18, lh 23.4) in globals/fonts.js — the only
Light-weight 4.0 token, for Card/Coach's name.

**Tab bar fidelity pass — node 3226:25871 tab pill** (`components/tabBar/HomeTabBar.js` +
`TabBar3.renderNewTabs`). Verified on-device:
- **ALL icons filled/solid**: the whole Figma bar uses filled glyphs, not the app's outline set —
  this was the main mismatch. Exported the clean filled glyph vectors from Figma via `download_assets`
  and composed each into a 28pt-frame SVG positioned per the node's insets: `tapbar.home.fill`
  (house), `tapbar.sleep.fill` (moon), `tapbar.coaches.fill` (2 people), `icon.earth` (globe),
  `tapbar.me.fill` (person). Added to `generated/iconList.js` + `img/newDesign/svg/`. All are
  fill-based; `HomeTabBar` recolours via `fill` (new item fields `iconFilled`/`activeIcon`/
  `activeIconFilled`) — white active, white@40% inactive. Swaps applied ONLY in `TabBar3`'s
  variant-`a` branch (keyed off per-tab testID), so the legacy bar keeps its outline icons.
  - ⚠️ Icon glyph SIZE matters: the first pass cropped `tapbar.home.fill`/`icon.earth` tight so they
    rendered oversized vs the outline set. Fixed by placing every glyph in a `0 0 28 28` viewBox at
    the exact Figma inset offset (`<g transform="translate(x y)">`), so all five match each other and
    the node. Don't tight-crop tab glyphs.
- **Dark glass**: new `navScrim` (rgba(19,19,19,0.72)) laid under the pill's blur + white@10%
  wash, so the pill reads as Figma's dark #2a2a2a glass even over bright feed cards instead of the
  washed-out translucent look the backdrop-blur produced over colourful content.
- **Clean dark backdrop**: HomeTabBar's background LinearGradient now reaches solid #131313 by ~62%
  (was 100%), so the region around/below the pill masks the feed to dark like the node's render
  instead of showing bright cards through the fade. The soft top fade is kept.
- **Label "Home"**: the newDesign branch overrides the home tab's DISPLAY text to
  `content_tab_home_label` ("Home", already localized in all 7 locales) to match Figma. Only the
  visible text changes — the analytics title captured in the tab's onPress stays "Aura", so tracking
  is unchanged, and the legacy bar still shows "Aura".
- ⚠️ **Not built**: the now-playing **mini-player** row Figma stacks above the pill in this node maps
  to the app's existing minimized `MeditationPlayer` (inside `SwippableMeditationPlayer`, shown only
  during playback) — a large shared component. Restyling its collapsed state to the Figma glass
  mini-player (thumb + title/subtitle + play-pause) is a separate card; not part of the always-visible
  tab pill.

## E.4 Built so far (Sleep tab, Aura 4.0 — node 3226:25163)

⚠️ Same file as Home 4.0: **`dpxPlTNW722Y1Qr8JRnSIn` ("Aura-Health-4.0")**. Sleep is the same
tabbed-feed architecture as Home, so this was a **reuse job, not a from-scratch build** — the Home
4.0 infrastructure (dark theme swap, newDesign cards, chips, tab bar) already applied; only two deltas
were needed.

**Render chain (same as Home):** `SleepTab` → `Tab` → `ScreenContainer` → `ScreenSelector` (chips) +
`Screen` → `Feed` (ROW_TYPES switch, `selectedTab = Tabs.Sleep = 'sleepHome'`). Gated on
`isRedesignTheme(this.props.theme)`.

Deltas built + sim-verified (variant a, iPhone 17 Pro):
- **`src/screens/SleepTab.js`** — added the `isRedesignTheme` branch (mirrors `AuraTab`):
  full-bleed 4.0 dark backdrop (`renderNewDesignBackdrop`, reuses `newDesignHomeBackground.png` +
  `canvas`), `theme={AURA4_THEME}` (NEW_DARK) into `<Tab>` (the one-point theme swap that makes
  the whole feed legible on dark), and `renderNewDesignHeader` = aura ring (`newDesignAuraRing.png`, 60pt) +
  14pt gap + "Sleep" title at `displaySansRegular25`, matching Figma node 3226:25169. The two flanking
  settings buttons in the mock are `opacity:0` placeholders → omitted. The 4.0 header drops the
  legacy subtitle (`content_sleep_header_title`). Legacy render path left intact as the variant-`b`
  fallback.
- **`src/screens/appReusable/CustomTrackItem.js`** — widened the TrackCard gate from
  `sentFrom === Tabs.Home` to `[Tabs.Home, Tabs.Sleep].includes(sentFrom)`. Sleep track rows forward
  `sentFrom: 'sleepHome'` via `RecommendationRow`/`CustomTracks`, so all Sleep track rows now render
  as the 4.0 `TrackCard`. The 22 non-redesign surfaces stay excluded (they send other `sentFrom`s).

Already 4.0 on Sleep for free (experiment-gated, no tab restriction — confirmed in the sweep):
`ScreenItem.renderNewDesignChip` (category chips + the pinned inset strip), `HomeTabBar` (dark glass tab
bar), `CelebrityCard` (the celebrity row renders on Sleep via `Feed.js` CELEBRITY_ROW's
`isShowCelebritySleepRow()` branch — verified "Aura X Greg Louganis").

Deviations (documented, raised to design — not blockers):
- **"Recently Played" (LISTEN_LATER_SLEEP)** renders as `TrackCard` (180pt) where the Figma section
  shows `Card/Default` (290pt). Consistent with the §E.2 "restyle, keep data + order" decision; the
  listen-later row is a horizontal track list and TrackCard is clean/on-brand. Forcing Card/Default
  would need a Sleep-specific gate on that row.
- **Backdrop** reuses Home's pre-blurred `newDesignHomeBackground.png` (RN can't runtime-blur the Figma
  Sleep night photo, node 3226:25166). Swap for a Sleep-specific baked-blur export when design ships
  one — single `AURA4_BACKDROP` require swap.
- Sleep "Made for you" correctly renders as `TrackCard` (Figma section 3226:25219 specifies
  Card/Track, NOT the playlist MixCard).

⚠️ There is no local override constant for the gate — exercising the redesign means setting the
variant on your own test user (`eng:set-user-experiment`), so you test the real bucketing path.

## E.5 Built so far (Coach tab, Aura 4.0 — node 846:6451)

Same file as Home/Sleep 4.0: **`dpxPlTNW722Y1Qr8JRnSIn` ("Aura-Health-4.0")**. Frame 402x2362,
dark-only, 5 content sections + tab bar. Gated on `isRedesignTheme(this.props.theme)`.
MCP `get_metadata` / `get_design_context` / `get_variable_defs` / `download_assets` all worked on
this file — no REST fallback needed.

**Render chain** (differs from Home/Sleep — this tab is NOT the `Tab`/`ScreenContainer` feed):
`TabBar3` (index 12) → `HomeExplore3.renderCoachesTab` → `screens/coachesTab/coachesTab.js` →
**`NewCoachingTabVideoCoaching`** → `CoachingTabFeed` (vertical FlatList) →
`CoachingByTopicsRow` (chips) + `CoachesRowForTopic` (each card row) → `CoachCardWithServices`.
The theme enters the subtree at ONE point — `NewCoachingTabVideoCoaching`'s `this.state.theme`
(it owns its own ThemeStore listener; the `theme` prop is not forwarded by `coachesTab.js`).

New components (`src/components/coachProfile/`):

| Component | Figma node | Notes |
|---|---|---|
| `CoachHero` | 846:6455 + cluster 846:6492 | 366x325: globe overlay (`marginBottom:-114`), Display-30-Serif headline, mint discovery pill w/ glowing dot, 7 absolutely-placed gradient-backed avatar circles. Props `theme/avatars/title/badgeLabel/testID` |
| `CoachTopicChip` | 846:6466 (`Long Carousel`) | Same Figma component as Home's chip, 100x67 instead of 76x67. Props `theme/label/iconName/onPress/style/testID`. `iconName` = resolved SVG from `Icon.icons[...]`, same shape LockBadge takes |
| `CoachServiceCard` | 846:6478 (`CoachCard`) | 300x439 glass card, full-bleed cover behind the content with a **real alpha mask** (`@react-native-masked-view` + LinearGradient), identity/services/plan blocks, "See details" chip + mint spots dot. Presentational; whole card is the touch target |

New tokens: `white18`, `accentMint`, `mintAlpha10`, `mintAlpha30`,
`accentPeach` (globals/colors) → `borderStrong`, `accentMint`, `mintSurface`,
`mintBorder`, `accentPeach` (newDark/colors); `ctaLabelSansSemiBold13` (redesign/fonts — Inter
SemiBold 13, same face as `tabLabelSansSemiBold11`).

New assets, both **baked** because RN cannot reproduce the effect at runtime:
- `src/img/newDesignCoachBackground.png` — node 846:6453 forest photo with LAYER_BLUR 50 **and** the
  radial fade-to-black composited in, 402x1080pt @2x. Verified numerically against the Figma render
  (±2/255 across the whole column). This is the Coach frame's OWN photo/fade geometry, not Home's.
  Pinned to the top at its own aspect (NOT stretched to device height like Home/Sleep) because the
  fade is baked at a fixed geometry; `canvas` fills below it.
- `src/img/newDesignCoachGlobe.png` — node 846:6456, `mix-blend-overlay @ 60%` pre-solved into a
  plain-alpha white overlay (RN has no blend modes). Derived from the transparent source, not the
  opaque composite export.

Wiring (all `isRedesignTheme`-gated, legacy paths untouched):
- `NewCoachingTabVideoCoaching.js` — backdrop, `theme={AURA4_THEME}` (NEW_DARK) swap,
  `renderNewDesignHeader` (hero + 48pt gap), hero coach fetch via
  `CoachesStore.getCoachingRecommendedCoaches`.
- `CoachingByTopicsRow.js` — 4.0 chip grid; the legacy inline `onPress` was extracted to a shared
  `onTopicPressed` so both variants dispatch identical navigation + analytics.
- `CoachesRowForTopic.js` — `renderNewDesignHeader` (Display 20 @ 18pt margin), 16pt title gap, 14pt
  card gap, 48pt section gap.
- `CoachCardWithServices.js` — `renderNewDesignCard`; reuses `openCoachDetails`.
- `CoachingTabFeed.js` — suppresses the two legacy decorative gradient PNGs, 48pt gap on the chip row.

⚠️ **The card branch is gated on tab AND experiment**: `isRedesignTheme(theme) &&
trackingData?.sentFrom === 'coachesTab'`. `CoachCardWithServices` is also rendered by coach search,
the per-topic list and the coaching onboarding flows — none of which have a 4.0 frame — so the
experiment gate alone would leak the new card into them. Same pattern as `CustomTrackItem`'s
`sentFrom` gate.

⚠️ **`Style.xPadding` is NOT the safe-area top** (it is `responsiveDimension(20)` ≈ 23pt, not the
62pt inset the 402x874 frame is authored against). The hero uses
`initialWindowMetrics?.insets?.top` from `react-native-safe-area-context` with `Style.xPadding` as
the fallback. Using `xPadding` put the whole screen ~39pt too high — worth knowing for the next
4.0 screen (Home/Sleep use `Style.xPadding` and may have the same drift).

Verified in the sim (iPhone 17 Pro, both variants). Measured against the Figma render: card
300x437 (spec 300x439), card gap 14 (spec 14), screen margin 18-19 (spec 18), section title glyphs
at y 686 and card top at 723 — **identical to the Figma frame**; pill width 191 (spec 192) and
section title width 152 (spec 155), which confirms the Season faces are loading at the right size.

Deviations (documented, not silent):
- **Headline wrap.** Figma hard-breaks after "with " (two paragraph nodes); the app wraps naturally
  ("…with 1-1" / "coaching"). Both are two centred lines at the same y. Hard-coding the break, or
  narrowing the box to force it, would wreck the other six locales — left natural. Design call.
- **Search affordance.** The mock shows no top-right control; the app keeps `CoachingTabFeed`'s
  search button. Removing it is a product decision, not a restyle.
- **Chip label** is `numberOfLines={2}` vs the mock's `nowrap` (same call Home's chip made); chip
  icons come from the project icon set mapped per coaching topic (`AURA4_TOPIC_ICONS`) since the
  mock's eight chips carry the Home rail's placeholder content topics, not the real coaching ones.
- **Avatar cluster** uses real recommended-coach photos at the node's exact positions/sizes/tints
  rather than the mock's three repeated stock portraits.
- **Card shadow** drops the Figma `-8` spread (RN has no spread); icon/dot glows are iOS shadows
  standing in for Figma's Gaussian blurs.
- Cards whose coach has no services list show the Figma `justify-between` gap between the plan
  block and the CTA row — that is the node's own layout behaviour with less content, not a bug.

## E.6 Built so far (Coach Profile, Aura 4.0 — node 846:6286)

Same file as Home/Sleep/Coach 4.0: **`dpxPlTNW722Y1Qr8JRnSIn`**. Frame 402x2616, dark-only, 14 content
sections + a pinned CTA. Gated on `isRedesignTheme(this.props.theme)`.

**Target screen: `src/screens/NewCoachDetails.js`** (route `newCoachDetails`, testID
`coachDetails_screen`). Not `CoachProfile.js` and not `NewCoachProfile.js` — the Coaches tab always
passes `canShowNewCoachProfile={true}`, and `CoachCardWithServices.openCoachDetails` sends that to
`newCoachDetails`. (`expertDetails` -> `NewCoachProfile` receives **no** `experiments` prop from
Dashboard, so gating there would need the prop added first.)

Render chain: `Dashboard` -> `NewCoachDetails` (Animated.SectionList: `header` + `info`/`coaching`/
`about`) -> `CoachDetailsHeader` / `CoachDetailsInfo` / `CoachDetailsCoachingDetails` ->
`CoachDetailsMessaging` -> `CoachAvailabilityDateSelector` + `CoachMessagingHowItWorks`.

New components — `src/components/coachProfile/` (16 files, all presentational + prop-driven):

| Component | Figma node | What |
|---|---|---|
| `CoachProfileSectionTitle` | 846:6331 et al | Display 20 heading; caller owns the gap below (20 or 16 per section) |
| `CoachProfileDivider` | 846:6348 et al | 1px `border/default` rule, drawn as a background not a border |
| `CoachProfileIconButton` | 846:6291 / 846:6293 | 36pt `surface/subtle` circle + 20pt glyph |
| `CoachProfileHero` | 846:6296 | 102pt avatar + Display-30-Serif name / country / role |
| `CoachProfileStats` | 846:6303 | R16 glass panel, equal-flex 110pt cells, 1x32 rules between |
| `CoachProfileTabNav` | 846:6309 | Info / Coaching / About + 28x2 underline (always mounted, opacity-toggled, so tabs never shift) |
| `CoachProfileIntro` | 846:6330 | 364.571x204.13 R16 intro-video card + centred play glyph |
| `CoachProfileChips` | 846:6337 | wrapped specialty pills, plain / mint / pink variants |
| `CoachProfileServices` | 846:6349 | icon + title + description rows |
| `CoachProfilePlans` | 846:6367 | bulleted title + price rows |
| `CoachProfileBio` | 846:6384 | Copy-14 paragraph, optional clamp + expand |
| `CoachProfileBookingHeader` | 846:6388 | mint premium pill + Display 25 title + subtitle |
| `CoachProfileDateSlots` | 846:6394 | 62pt equal-flex date slots, white fill when selected |
| `CoachProfileTimeSlots` | 846:6400 | 3-across wrapping time grid + "view more" button |
| `CoachProfileSteps` | 846:6430 | horizontal 228pt step cards with 48pt glass icon circles |
| `CoachProfileCta` | 846:6450 | pinned scrim + 355pt white pill + glowing mint spots dot |

New tokens: `onLightSecondary`, `onLightBorder` (globals + newDark colors — the CTA pill is
the only on-light surface in the 4.0 palette); gradients `gSpecialtyMint`,
`gSpecialtyPink`, `gBottomCtaScrim`.

**Backdrop is REUSED, and that was verified not assumed**: node 846:6287's source image hashes
byte-identical to the Coaches tab's (same photo, same LAYER_BLUR 50, same radial-fade matrix), so
`newDesignCoachBackground.png` serves both. Pinned to the top at its own aspect, canvas below.

New i18n keys (4, added to all 7 locales): `coaching_coach_details_intro_heading`,
`coaching_coach_details_plans_heading`, `coaching_coach_details_free_intro_badge`,
`coaching_coach_details_booking_subtitle`. Everything else reuses existing keys.

⚠️ **Gate style differs from the Coaches tab.** The shared coaching components
(`CoachDetailsInfo`, `CoachDetailsMessaging`, `CoachAvailabilityDateSelector`,
`CoachMessagingHowItWorks`, `CoachDetailsCoachingDetails`) each take an explicit **`isNewDesign` bool
prop** rather than reading the experiment themselves. `NewCoachDetails` decides once and threads it
down. They all have other callers with no 4.0 frame (`LiveMeditationDoneCoaching`,
`CoachDetailsForPlansModal`, `CoachDetailsVideoSubscription`, `SwitchCoachingDateSelector`,
`coachingV1/CoachBooking`) which never pass it, so the legacy path is untouched there. This is the
prop-gate variant of the `sentFrom` gate used on `CustomTrackItem` / `CoachCardWithServices`.

⚠️ **`Style.xPadding` is not the safe-area top** (see §E.5) — this screen also reads
`initialWindowMetrics?.insets?.top`.

Deviations (documented, raised to design):
- **Rating + reviews do not exist.** The mock's stats row is 4.9 ★★★★★ / 120 Reviews / 10+ Years,
  but there is no rating or review-count field anywhere in `CoachesStore` or the coach model. The
  row keeps the three stats the legacy header already shows (Followers / Favorites / Plays) in the
  node's card treatment rather than inventing numbers.
- **IA differs**: the mock puts booking + how-it-works on the same scroll as Info; the app has them
  under the **Coaching** tab. Sections were restyled in place — none were moved between tabs.
- **Specialty accent chips**: the node accents items 2 and 4 with two different gradients, but that
  is not tied to any data property, so the component applies it as a 4-item visual rhythm. Replace
  with a real signal if design defines one.
- **Step icons**: the node names `community.event` for step 2; the project icon set has no such key,
  so `bullet.calendar` stands in. The other three glyphs are exact.
- Copy: the mock says "Select a date" / "spots open" / "Book Your Discovery Call"; the app reuses the
  existing keys "Choose a Date" / "{{count}} spots left" / "Book Your Discovery Call Today" rather
  than adding near-duplicate strings across 7 locales.

## E.7 Re-verify pass against Home 3226:25791 (drift found + fixed)

A second `/figma-to-code` run on the SAME node re-extracted the frame and re-audited every
section. Nine real mismatches surfaced — **every one of them in a component still keyed to a
node id from the superseded `800:6687` frame**. That is the reusable lesson: when a frame is
superseded, the components built against the old node ids are exactly where drift hides, and a
"looks done" tick from the previous run is worthless. Grep for stale node ids first.

| # | Component | Was | Figma (3226:25791) |
|---|---|---|---|
| 1 | `TrackCard` cover | 218 | **240** (I3226:25861;252:172) |
| 2 | `CustomTrackItem` Card/Track width | 180 | **200** (3 of 4 instances; Parenting has both at 200) |
| 3 | `DefaultCard` cover | full-bleed photo | **184.139pt top band** (54.16%) with a MaskedView alpha ramp fading from 81.42% — the node has a Mask group, so a scrim is not a substitute |
| 4 | `DefaultCard` base | no fill | **`surface/subtle` white@5%** (visible below the cover band) |
| 5 | `DefaultCard` title | Display 30 Serif | **Display 25 Serif** |
| 6 | `DefaultCard` author | `font250Default*` (10pt) | **Inter 13.674 / lh 15.04** -> `authorNameSansSemiBold14` + `authorRoleSansRegular14` |
| 7 | `ProgramCard` hero glow | cx/cy .5, r .5 | **cx .4166 / cy .5 / r .8333** (node `gradientTransform`) — same correction applied to `DefaultCard` |
| 8 | `ProgramCard` gem | omitted ("no asset") | **rendered from the exported path** `M40 5L70.31 22.5V57.5L40 75L9.69 57.5V22.5L40 5Z`, white@95% + white@40% glow. The export WAS available via MCP; "asset unavailable" had gone stale |
| 9 | Header icon gap | 8 | **12** (node 3226:25802: buttons at x=0 / x=48 in an 84pt slot) |

**Card `Type=First Meditation`** (node `3226:25822`, PROD-6688): the Today row's first card for a
user with no session yet. Built as an additive `DefaultCard` variant — `ctaLabel` / `onCtaPress` /
`isCtaLoading` / `ctaTestID` — not a new card: same 258x258 shell, cover band 156pt (the pill takes
the difference), author + role on one Caption 12 line at a 20pt avatar, then a white
`button/secondary` pill (`surfaceInverse`, `onLightBorder`, `titleSansSemiBold14` on `onLightPrimary`).
Only the pill is tappable, as on the legacy card. `TodayRow` drops the legacy "Get Started" label row
above it in 4.0 — that row is what pushed the card below its siblings. The component description
asks for a play icon in the pill and no lock; the instance shows no icon and a lock. Built without
either: no icon (as drawn), no lock (the first meditation is free).

Also built this pass: **section titles**. `reuseableUI/Heading` gained an additive **`newDesign`** prop
(Display 20 heading + Copy 14 "View all" at text/tertiary) rather than being restyled — it has 44
dependents. This closes the "section headers not to spec" gap listed in §E.2.

⚠️ **Seven row components render that title, not one.** Wiring only `CustomTracks` left the feed
with MIXED typography (Season Sans on "New"/"Popular on Aura", legacy Proxima on "Series"/
"Podcasts"/"Programs For You") — caught in the sim, not in review. The override now lives in
**`src/logic/NewDesignSectionTitle.js`** and is spread onto `Heading` at all seven sites:
`CustomTracks`, `TodayRow`, `SeriesRow`, `PodcastsRow`, `ProgramsRow`, `AuraPodcastRow`,
`CelebrityMasterClassRow`. Spread it LAST so it overrides the row's legacy `theme`/`containerStyle`;
it returns `{}` when the experiment is off.

⚠️ **The redesign colour tokens exist only on `redesignDark` / `redesignLight`.** A shared component
given the 4.0 treatment must be handed a redesign theme or its colours resolve to `undefined` —
which RN ignores silently, so it renders unstyled with no error. Never name a theme to get there:
constant-dark surfaces use `getConstantDarkTheme(theme)`, and screens whose pre-redesign design
follows the user's theme use `getRedesignConstantDarkTheme(theme)` (both in `models/theme.js`).
`getIndividualStyle(appTheme.NEW_DARK)` pins the PRE-redesign palette and is exactly the bug this
warns about. `sectionTitleProps` pins this.

⚠️ **`getIndividualStyle` is exported from `src/models/theme`, NOT `src/stores/ThemeStore`**
(ThemeStore imports it without re-exporting). Importing it from ThemeStore is lint-clean, reads
correctly next to neighbouring code, and **crashes the app at launch** with
`TypeError: undefined is not a function` when called at module scope. Cost a full build cycle.

Frame geometry confirmed for the whole screen: **40pt** between every section, **16pt** section
title -> cards, **20pt** card gap, **18pt** page margin (Wrapper 366 @ x=18).

Verified MATCHING, no change needed: `LiveCard` (3226:25829), `CelebrityCard` (3226:25835),
`PodcastCard` (3226:25840), `Tag`, `GlassCard`, `LockBadge`.

## E.8 Built so far (Live tab, Aura 4.0 — nodes 800:7118 'Live' + 800:7173 'Live — My schedule')

Same file: **`dpxPlTNW722Y1Qr8JRnSIn`**. Both frames are segments of ONE app screen —
`src/screens/liveMeditation/liveMeditationTab.js` (mounted from `HomeExplore3`, tab index 15,
testID `liveMeditationTab_screen`), whose segments render `LiveMeditationAllList` (Live),
`LiveMeditationScheduledList` (My schedule), `LiveMeditationReplayList` (Replay). Extracted via
REST fallback (the Figma MCP needed re-auth in that session — MCP access itself is fine on this file).

Gate: `isRedesignTheme(this.props.theme)` decided ONCE in `liveMeditationTab`
(`isNewDesign()`), then threaded as an **`isNewDesign` bool prop** into the lists and the two
event-card components (`liveMeditationRow`, `LiveMeditationItemSmall`) — the prop-gate pattern of
§E.6, because both cards are also rendered by the community tab and coach-profile lists which have
no 4.0 frame. The community embed (`isFromCommunityTab`) never receives the prop, so it keeps the
legacy render.

New components (`src/components/liveMeditation/`, all presentational + prop-driven):

| Component | Figma node | What |
|---|---|---|
| `LiveDayPicker` | 800:7146 | 7-cell week strip, 44x68 cells R16, selected = white@10% fill + 1px white@10% border + 0/4/12 black@25% shadow; day `captionSansRegular12`, number `labelSansLight18`. Used by BOTH Live + My schedule segments (registry §A "DayPicker — BUILD NEW" now exists) |
| `LiveSectionHeader` | 800:7156 | Display-20 title + Caption-12 tertiary subtitle ("Friday, May 8 · 1 session"), gap 2 |
| `LiveFeaturedCard` | 800:7159 | 540pt glass card (white@5% fill+border, R24, pad 20): svg radial coach glow, radially-masked coach photo, white live-badge pill (red dot + Caption-12 red), meta line white@85%, Serif-25 title + coach name, 28pt avatar stack (-10 overlap, `avatarRing`), white reserve pill (Button-16, black label) |
| `LiveListRow` | 800:7164 | glass row (white@10% fill, white@5% border, R24, pad 12): 72pt gradient+cutout thumb R16, Caption-12 time / Body-16 title / Caption-12 byline, 40pt circular reserve toggle (`action.plus` / `control.checkmark`) |
| `LiveScheduleEmptyCard` | 800:7214 | glass card: 56pt ring glyph, `labelSansLight18` title, Copy-14 body, white "Browse Live sessions" pill (CTA = `setActiveTab('live')`) |

Extended additively: `homeFeed/LockBadge` gained a `style` prop (the Live featured card places the
lock in-flow in its top row instead of the default absolute top-right-9 pin).

New tokens: `white85`, `liveRed` (#F2454F), `avatarRing` (#1A1A1F),
`onLightPrimary` (black — white-pill CTA label) in globals/colors →
`textEmphasis`, `accentLive`, `avatarRing`, `onLightPrimary`
in newDark/colors.

New i18n keys (all 7 locales, `live.json`): `live_card_countdown_label` ("Live {{countdown}}" with
`moment().to(start)`), `live_section_session_count_one/_other`,
`live_schedule_empty_state_{title,body,cta}`, `live_section_no_sessions_label`.

Wiring notes:
- `liveMeditationTab` — 4.0 backdrop reuses `newDesign/homeBackground.png` (the frame's bg is the
  same 55d645c1 forest photo family, blur+fade baked) + `canvas`; NEW_DARK theme swap at
  `getContentTheme()` (one point, covers all three segments); header = 60pt `newDesign/auraRing.png`
  + "Live" at `displaySansRegular20`; TabNav = Season Copy-14 labels + 28x2 opacity-toggled underline.
  The inline segment-switch `onPress` was extracted to a shared `onTabPress` (both variants dispatch
  identically).
- Both lists — `onDatePress` extracted from the legacy inline handler (same tracking +
  scrollToLocation); 4.0 spacing = 32 header→picker→content, 12 within-section, section footer 20.
- CTA states reuse the exact legacy handlers: reserve / cancel / join / openEventDetails; badge =
  "Live" when `hasLiveStarted()` else the countdown key.

Deviations (documented, raised to design):
- Featured card's heart save-button (Figma `Action=Save` 313:143) OMITTED — no favorite-live-event
  action exists in the app and a UI PR can't invent one. The reserve pill fills the CTA row.
- `BACKGROUND_BLUR 30` on the featured card omitted (no RN backdrop-filter — same call as Home chips).
- Empty-card body copy is Inter in the node → `copySansRegular14` (Inter, same as
  `tabLabelSansSemiBold11`); its CTA label #121212 → `canvas` #131313 (1/255).
- Schedule/Replay segments keep their legacy layouts under the NEW_DARK theme (only Live +
  My-schedule-empty have 4.0 frames; whole-tab gate keeps the flow consistent).

## E.9 Built so far (Profile / Me tab, Aura 4.0 — nodes 3094:24800 light + 3094:24844 dark)

Same file: **`dpxPlTNW722Y1Qr8JRnSIn`**. Frame 402x1618 — **the first THEMED 4.0 screen** (both a
light and a dark frame; the dark frame is a pure palette inversion, geometry/fonts identical).
Extracted via REST fallback (Figma MCP unauthenticated in that session).

**Target: the Me tab** — `Dashboard.renderProfile` → `Profile2` → (under
`UserController.isMeTabAndPlaylistTopPart()`) **`Profile3`** → `ProfileList` →
`ProfileFilteredList` → `SavedIndividualItem` → 18 `ProfileList*` row components → ONE shared
`reuseableUI/Player/ResourceList` (23 importers). Gate decided ONCE in `Profile3`
(`isRedesignTheme(theme)`), threaded as an **`isNewDesign` bool prop** (§E.6/E.8 pattern).

**The themed-screen mechanic (new — copy this for Settings/Search):** `Profile3` follows the user's
theme, and the redesign semantic tokens exist on **both** redesign themes (`redesignLight/color.js`
carries the light values read
off the light frame: canvas #F8F8F8, surfaceSubtle white@50%, surfaceGlass white@80%, borderSubtle
black@5%, borderDefault black@10%, textPrimary #000, textTertiary black@50%, iconMuted black@70%).
Four tokens were added on BOTH themes because light/dark break the dark-only aliasing
(`textPrimary`, `borderSubtle`, `borderDefault`, `iconMuted`,
plus `navWash`, `borderOnColor`, `textOnColorMuted`). Keep the two
themes' 4.0 key sets in sync.

Components / extensions:

| What | Figma node | Where |
|---|---|---|
| ProfileListRow (4.0 glass row) | ProfileListRow instances | **`ResourceList` additive `isNewDesign` prop** — 72pt row, R16 surfaceSubtle + borderSubtle hairline, 44pt R12 surfaceGlass icon-box, `titleSansSemiBold18` title, Caption-12 tertiary subtitle joined " · ", 24pt `action.arrowRight` chevron. Threaded through `SavedIndividualItem` + all 18 `ProfileList*` rows |
| Stats row | 3094:24812 | NEW `components/profile/ProfileStatsRow.js` — R16 glass panel, 3 flex cells (24pt icon / Display-20 count / Caption-12 label, gaps 8), 1x32 borderDefault dividers. Registry §A "Stats Card — BUILD NEW" now exists |
| Profile header | 3094:24807 | `ProfileHeader` isNewDesign branch — 102pt avatar (AvatarDefault size override), Display-30-Serif name, "Member for {{days}} days" Caption-12 from **firebase auth `metadata.creationTime`** (hidden when unavailable — no profile field exists), stats row below |
| TabNav | 3094:24818 | `ProfileTabs` isNewDesign branch — Copy-14 labels (primary/tertiary), opacity-toggled 28x2 underline; same testIDs/handlers |
| GiftPromoCard | 3094:24831 | `GPGiftAuraFreeCard` isNewDesign branch — R24, `gGiftPromo` linear + #F2C78C radial glow (svg), white@12% border, gradient orb (`gGiftOrb` + white radial wash + #FFEBF0 iOS-shadow glow), Serif-30 title, Caption-12 white@70% subtitle, white Share pill. Same handlers (guestPassReferralPage + dismiss X kept) |
| Tools grouping | 3094:24838 | `ProfileFilteredList` — All tab partitions gratitudeJournal/badge rows under a Caption-12 "Tools" label (existing key `profile_section_tools_text`); rows mb 8 |
| Navbar circles | 3094:24801 | `ProfileList.navIcon` — 36pt surfaceSubtle circles, 20pt textPrimary glyphs; keeps search/gift/menu (mock shows only a settings gear — product decision to keep, documented) |
| **Light tab bar** | 3277:13754 (light frame) | `HomeTabBar` is now theme-driven (`textPrimary`/`navWash`/`borderSubtle` + isLight blur/backdrop switch); `TabBar3` picks `NEW_DESIGN_TAB_THEME_LIGHT` when the **Me tab (index 3)** is active and the app theme is light — every other tab stays constant-dark |

New tokens: fonts `titleSansSemiBold18` (Season Sans SemiBold 18/22.1); gradients `gGiftPromo`
(130°, #332938>#59404D>#8C6B61), `gGiftOrb`; globals `white12/70/80`,
`black10/50`, `canvasLight`, `surfaceNavLight`, `navScrimLight`.
New i18n: `profile_member_for_days_text_one/_other` (all 7 locales).

Deviations (documented, raised to design): navbar keeps 3 affordances; gift-card dismiss X kept;
row/tab sets are server-driven per-user (structure verified, not the mock's exact 5 rows — no Mood
Tracker category exists in the app); IMAGE4 playlist collage renders its first cover at 44pt; orb
inner-shadows omitted (RN); Events tab content keeps legacy layout under the swapped theme.

## E.10 Re-verify pass — Welcome/Login (4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, node 1055:17752)

The 4.0 file carries the SAME node id as the 2.0 file's Welcome step (§E.1 — the 4.0 file is a
descendant copy; the background imageRef 55d645c1… is also identical, so `onboardingNameBackground.jpg`
still serves). A fresh extraction + audit of `ValueProps.renderNewDesign` found six real drifts, all
fixed:

| # | Element | Was | Figma (1055:17752) |
|---|---|---|---|
| 1 | Aura ring | `ThemedAuraRing` plain white 2pt-border fallback (ThemedAuraRing only renders for the rainbow theme) | rainbow-glow ring = `newDesign/auraRing.png` at nd(136) (logo box 135.8) |
| 2 | AURA wordmark | `font700DefaultRegular` (28) + tracking rd(2), gap space16 | Inter 30.19/400/lh30.2, tracking **8.3**, gap 12.35 → `wordmarkSansRegular30` (redesign/fonts.js; includes letterSpacing + a `marginRight: -tracking` optical-centering pullback at the call site) |
| 3 | Tagline | `fieldSansLight20` (Light 20) full white, w rd(260), gap space56 | Season Sans Light **18**/lh23.4 → `labelSansLight18`; **white@0.7** → `textSecondary500`; w nd(253); gap nd(52) |
| 4 | CTA margins/shadow | 20pt margins + `buttonShadowPrimaryL` | **18pt** margins; the node has NO drop shadow — removed |
| 5 | Button label | `textPrimaryInverseMain` (= grayMain rgb(47,50,55)) | **#000000** → `onLightPrimary` |
| 6 | Bottom scrim | missing | `Rectangle 365` 133pt #020303 opaque(bottom)→transparent → new `gOnboardingWelcomeScrim` (globals/gradients.js) rendered in `renderGetStarted`; the node's 4pt background blur omitted (no RN backdrop-filter) |

Geometry: login-text bottom = 53pt off screen bottom on the 874 frame → `initialWindowMetrics.insets.bottom
+ nd(5)` container padding (Style.xPadding fallback); button→login visual gap 28 (nd(14) margin + nd(14) touch pad).

Deviations (documented): login label weight 650 → SemiBold 600 cut stands in; button-text INNER_SHADOW
white@5% omitted (invisible); copy "Existing user? Log in" kept vs mock's "Login" (grammatical, avoids a
7-locale churn); scrim backdrop-blur 4 omitted.

Three corrections came from on-device MEASUREMENT + a user callout, not the spec sheet — reusable:
- **`absoluteBoundingBox` LIES for boolean/effect nodes — use `absoluteRenderBounds`.** The white
  ring boolean's bb says 120.4 but its renderBounds (what actually draws) is **98.3**; sizing to the
  bb shipped a ring 22pt too big, and the first verify pass "confirmed" it because it measured
  against the same wrong number. Check renderBounds whenever a vector's drawn extent could differ
  from its box.
- **Export the SCREEN'S OWN instances instead of adapting shared assets.** `newDesign/auraRing.png`
  has a different glow-to-ring ratio than this node's logo, and the shared name-step backdrop has a
  different vignette than this frame's Background composite. Fixed with two node-exact exports:
  `newDesign/auraRingWelcome.png` (instance `I1055:17842;4339:93464` @3x; canvas = node box, so
  nd(136) renders the ring at exactly 98.3) and `newDesign/welcomeBackground.jpg` (node 1055:17756
  @1x jpg; export canvas = render bounds = bb inflated 100.5pt/side by the blur, so it's placed at
  `left -nd(830), top -nd(124), nd(1964) x nd(1189)`). Verified: background worst channel delta
  2/255 vs the frame render across a 24-point sample grid; ring 98.0pt.
- **RN line box ≠ Figma text box.** The wordmark's RN line box (lh 30.2) runs ~10pt taller than
  Figma's 20pt glyph box, so the next sibling's gap must be reduced (52 → 42) to keep the sibling at
  the node's absolute y. Verified by pixel measurement: all elements within ~1pt of the frame.

## E.11 Built — Login form (4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, node 1048:17458 'Login')

**Target: `src/screens/Login.js`** — `renderNewDesign` gated
`isRedesignTheme(this.props.theme) && !isComponent && !provider && !this.state.signup`
(Onboarding6's `login` view passes `experiments`; the provider / isComponent / signup contexts have
no 4.0 frame and keep legacy). All legacy handlers + testIDs reused (`login_emailField`,
`login_passwordField`, `login_passwordVisibilityButton`, `login_forgotPasswordButton`,
`login_submitButton`, `login_facebookButton`, `login_googleButton`, `login_backButton`;
`login_appleButton` new). Onboarding6's dark backdrop does NOT render for the `login` view, so the
branch renders its own (`welcomeBackground.jpg` — this frame's Background node is verbatim the
welcome step's) + `gOnboardingWelcomeScrim`.

Layout (sim-verified within ~2pt): column x18 w366 top = safeTop+60, gap 32 between the 5 blocks.
Ring = `auraRingWelcome.png` nd(68) image in a 60pt box (node ring RB 49.1 — bb says 60.1, render
bounds again). Title `titleSerifRegular30`. Social pills 60pt `borderRadiusFull` white,
`buttonSansSemiBold18` + `onLightPrimary` label, 24pt icon + 10 gap, 16 between pills; Apple
gated on `appleAuth.isSupported`. 'or' = `labelSansLight18` @ `textSecondary500`. Fields reuse the
name-step frosted treatment (D9D9D9@4%, R18, double inset boxShadow, h62, pad 25), placeholder
`fieldSansLight20` @ `textTertiary`; eye toggle = existing `field.visible`/`field.invisible`
icons. Login CTA 60pt: dimmed `surfaceGlass` + enabled (both fields non-empty) →
`bgPrimaryInverseMain`; label `font450DefaultSemibold` (the node uses Proxima 18/600, not Season)
@ `onLightPrimary` (#000 verified from render pixels). Wrapped in a ScrollView
(716pt column overflows small devices); KeyboardSpacer kept.

New assets: `newDesign/svg/platform.facebook.svg` + `platform.google.svg` (exported from the node;
`action.arrowLeft` already existed — the back chevron renders arrowLeft at
**nd(18)** because its glyph fills the viewBox while the mock's chevron is 16pt inside a 24 box).
`platform.apple` was also pre-existing but **mis-boxed** (25×25 viewBox, ink centered at 12.93 —
rendered ~0.5pt low/right and ~4% oversized next to the 24×24 FB/Google glyphs; user-reported as
"not aligned with logo"). Replaced 2026-08-14 with the node's own 24×24 export (ink centered at
x=12.0) — `iconList.js` updated surgically because `npm run bundleIcons` reformats all 278 entries
(quote-style churn); don't commit a full regeneration for a one-icon change.
New i18n (all 7 locales): `onboarding_login_email_field_placeholder` / `_password_field_placeholder`
("Enter your email"/"Enter your password" — existing keys say "Log in with Email"/"Password").

Deviations (documented): ~~"Welcome back!" keeps the existing key (mock: no "!")~~ **resolved
(user-driven, 2026-08-14): the new-design heading now uses `onboarding_login_welcome_back_headline_text`
("Welcome back", no "!", all 7 locales) — variant-`a` branch only, the legacy render keeps
`onboarding_login_welcome_back_text`**; "Forgot your password?" keeps its existing key (mock:
"Forgot password?"); node's Login button 362w@x2 = authoring artifact → 366 like siblings;
field inner text shadows white@5% omitted; enabled-CTA state inferred (node ships only dimmed);
CTA always pressable (legacy behavior — visual state only).

## E.12 Sign Up SECTION (4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, section node 1048:16079 — 20 frames)

The whole onboarding quiz flow. 15 of the 20 frames share ONE chrome, so this was a
**build-the-chrome-once** job, not 15 screen builds. The 5 personalization frames use a different
chrome (no progress bar, `Rectangle 368` + % label) and are NOT built yet.

**Shared chrome spec (verified on-device, all values exact):** backdrop = the same 55d645c1 forest
composite the welcome/login steps use (`welcomeBackground.jpg`); `Rectangle 362` black fade
(0 → .59@24.8% → 1) UNDER `Rectangle 365` #020303 fade; progress bar 162x3.5 @ **y88**, centred,
track white@10% / fill white R23; back chevron 24 @ (18,78); **Skip is TOP-RIGHT at (347.2, 78.3)**,
Season Sans Light 18 @ white@70% — NOT below the CTA; content column x18 w366 **top 122** gap 32;
CTA 366x54 R36 @ **y781.1**.

⚠️ **The original Season tokens were oversized ~4.5% (user-reported on Login, 2026-08-14).** They
were built on `responsiveDimension` (350pt guideline), inflating every size/lineHeight by ~4.5% on a
402pt device. Every consumer is a 4.0 screen authored at 402, so the whole ladder now scales on
`scaledDimension` (402pt base, `styles/redesign/dimensions.js`). Login's CTA moved off the legacy
`font450DefaultSemibold` (18.8/24 after inflation) onto `ctaLabelSansSemiBold18` (Inter
SemiBold 18/18). **Never size a redesign text token with `responsiveDimension`** —
`test/unit/redesignScale.test.js` enforces it.

⚠️ **Ring glow also clipped LEFT on the grid screens (user-reported, 2026-08-14).** Topics /
content-type / goals put their scroller inside the 18pt-padded container, so the scroller's clip
boundary sat at the column edge and cut the ring image's 4pt left overflow. Fix: full-bleed
scroller (`marginHorizontal: -18`) + `contentContainerStyle paddingHorizontal: 18` in the
new-design branch — geometry unchanged, clip boundary at the screen edge. `OnboardingScreenLayout`
screens were never affected (their ScrollView is full-width with padded content).

⚠️ **Two chrome fixes (user-reported, 2026-08-14), both apply to every screen on this chrome:**
- **`welcomeBackground.jpg` was flattened against WHITE.** `sips -s format jpeg` composites PNG
  alpha over white, so the blurred export's semi-transparent edge bleed became a milky haze in the
  top ~76pt of every green-backdrop screen (viewport top samples canvas row 124, well inside the
  100pt bleed). Regenerated: REST-export the Background node as PNG (alpha), composite over
  `bg/page` #131313 (pure-python PNG decode — no PIL/ImageMagick on the host), then JPEG. Same
  name/dims, zero code change. **Never `sips`-convert an alpha export straight to JPEG.**
- **The heading ring's glow was clipped at the scroll top.** The ring image intentionally overflows
  its 60pt box by 4pt; iOS ScrollViews clip at their content top, and the chrome gave content zero
  top padding. Fix everywhere: top bar band 60 → **54** + **6pt paddingTop** inside the scroll
  content (`OnboardingScreenLayout` contentContainer when `showHeader`; the grid screens'
  `renderHeader` wrapper) — content still lands at safeTop+60, the glow gets renderable headroom.
  Applied to `OnboardingScreenLayout`, `PersonalizedOnboarding`, `ContentTypeOnboarding`,
  `OnboardingMotivation`. Login already pads its ScrollView (safeTop+60) so it never clipped.

New shared components — `src/components/onboarding/`:

| Component | Figma | Notes |
|---|---|---|
| `OnboardingScreenLayout` | quiz frames | backdrop + progress + back + **top-right Skip** + scroll column + pinned CTA. `ctaLabel` omitted ⇒ no CTA (auto-advance steps); `ctaHidden` ⇒ CTA held off screen until the step can continue |
| `OnboardingHeading` | `Frame 1185` | 60pt ring (LEFT-aligned here, unlike the centred welcome ring) + Display-30-Serif + Subtitle-16 @ text/secondary |
| `OnboardingOptionRow` | `Component/Carousel/Tab` | 366x64 R18, white@5%, double inset shadow, teal radial glow + 1px white border + filled control when selected; `selectionStyle` checkbox/radio; optional icon + subLabel |
| `OnboardingProgressBar` | `Progress` | 162x3.5, `progress` 0..1 |
| `OnboardingContinueButton` | `Button` + both scrims | 54pt pill over the two bottom scrims |
| `OnboardingInsetGlow` | `Field` / `Tab` inset shadows | the frosted surfaces' white inner rim, as 4 concentric 2pt border rings (alphas .081/.051/.03/.013) — see the boxShadow note below |

New tokens: `bodySansRegular16` (fonts), `gOnboardingScreenScrim` (gradients).

**⚠️ `boxShadow` is a no-op in this app — never use it for an inset shadow.** `ios/Podfile:2` sets
`RCT_NEW_ARCH_ENABLED = '0'`, and RN only implements the `boxShadow` style prop on Fabric, so the
Figma `Field` / `Tab` inner shadows silently rendered flat (verified against the frame render:
+21/255 at the field edge in Figma, +0 on device). Use `OnboardingInsetGlow` instead. Still
outstanding on this pattern: `OnboardingOptionRow`, `OnboardingMotivation`, `HomeTabBar`.
**22 icons exported from Figma** (blur-duplicate glow layers stripped — these nodes ship a blurred
twin behind a sharp one; keep the sharp paths and use an iOS shadow for the glow). Installed as
`control.checkbox[.fill]`, `control.radio[.fill]`, `action.chevronDown`, `icon.*`. This retires the
§E.1 "hand-drawn goal glyphs" TODO — the exported originals now exist.

Built + **sim-verified** (5): Gender (1048:16312 → `OnboardingGender`), Age (1048:16367 →
`Onboarding6.renderAgeRange`), Session Length (1048:16206 → `OnboardingDuration`), Spiritual
(1048:16279 → `onboarding/OnboardingReligionPreference`), Accent (1048:16383 →
`OnboardingCoachAccentPreference`). Measured: progress 88.0–91.3 (spec 88–91.5), Skip x347.7–378.7
(spec 347.2–380.2), CTA 781.0–834.7 margins 18/18 (spec 781.1–835.1, 18/18).

**Name Entry (1048:16912 → `Onboarding6.renderNameNewDesign`) — rebuilt on this chrome.** It had
been hand-rolled and kept the LEGACY chrome, so a re-audit found three mismatches the shared
components already solve: the 75pt animated `ThemedAuraRing` (mock 48.7 drawn / 60pt box) at the
wrong offset, the legacy 100pt hairline progress bar, and `onboardingNameBackground.jpg` under a
`gOnboardingDark` approximation instead of this frame's backdrop (which is byte-identical to the
login/quiz one) — background was off by a mean 10/255. Adding `name` to `NEW_DESIGN_CHROME_VIEWS`
suppresses both legacy pieces; the ring now comes from `OnboardingHeading`. **The 5 steps above
were double-drawing the legacy ring too** (all pass `showRing` default true) — same fix.
Measured after: ring 23.5–72.5 (spec 23.5–72.2), progress track 161.8 wide @ y88 (exact), CTA
380.0–433.8 (exact), field rim within ±2.2/255.

Two traps here: this frame's CTA **scrolls with the content** (40 below the field), so it uses
`bottomScrim` + an inline pill rather than `ctaLabel`'s pinned one; and the gaps must be
`scaledDimension`, not `theme.spaces.space40` — `responsiveDimension(40)` is 40.86 on a 402pt
device and two of them drifted the CTA 1.8pt low.

**The progress bar is restyled, not re-timed.** `progressForView` (exported from
`OnboardingBarProgress`) is the legacy `position / 4` math, so the redesigned bar advances on the
existing schedule. The mock ships the same static 63.4-of-162 fill on every frame — do NOT read
that as a spec for the value. The 5 steps above still hardcode `NEW_DESIGN_PROGRESS = 0.392` and
should move to `progressForView`.

⚠️ **Onboarding6 owns the chrome gating — four traps found on-device, all fixed:**
1. `renderLegacyStepChrome()` gates the legacy `renderBack()`/`renderSkip()`; they duplicated the
   layout's chevron (same `onboarding_backButton` id) and floated over it.
2. `renderProgress()` (the GLOBAL legacy bar, rendered once for every view) stacks a second bar
   above the new one → gated via `NEW_DESIGN_CHROME_VIEWS`.
3. Duration's parent `ScrollView` → `React.Fragment` in variant a (nested same-axis scroll).
4. `onSkipPress` only TRACKS — the legacy `renderSkip` wrapper is what called `handleNext()`. Wiring
   it raw strands the user on the step; call sites now compose `handleNext()` + `onSkipPress()`.

⚠️ **BEHAVIOR DELTA awaiting product sign-off:** Gender / Age / Session Length auto-advance on tap
today; every frame pairs the rows with a Continue pill, which only works as select-then-continue.
Implemented as designed (same handlers + payloads, deferred to the CTA). Changes onboarding tap
counts / funnel timing — confirm or revert those three.

Deviations: mock repeats "3 minutes" under every age row (copy artifact) → no sub-label; every frame
shows progress 0.392 (mock artifact) → real per-step values TBD; Spiritual + Accent lists are
server-driven (app's real lists render); session-length sub-labels derived by splitting the existing
"(3 minutes)" parenthetical, "All durations" has none → single-line; accent flags dropped per mock;
Spiritual has NO subtitle in the node but the app renders an existing one.

NOT built (12): Content Type + full list (1048:16133 / 3228:30081 — already 4.0 from a prior run,
needs a re-audit), Voice Preview (1048:16430), Notification Time (1048:16466), Profile Summary
(1048:16499), Account Creation (1048:16802), and the 5 personalization frames (16546/16581/16653/
16693/16730).

⚠️ **Re-audit findings on the prior-run screens (Name / Goals / Topics / Content Type):** their
Continue pill floats over content with **no bottom scrims** (every frame has two), and Goals still
uses the §E.1 hand-drawn glyphs. Both are now fixable by composing `OnboardingContinueButton` and
the exported `icon.*` set. Name and Topics are done (§E.13 below); Goals and Content Type are not.

## E.13 Content Interest Selection (node 3234:30761 → `PersonalizedOnboarding`)

The topics grid — 2 columns, 29 cards, `GridView` scroller with the header inside it. Was already
gated but had kept the legacy chrome and a card that inverted the mock's states.

**The card is the screen** (`Component/Carousel/Tab`, 176x115 R14, pad 15, gap 14 / rowgap 15).
Its two states differ ONLY in scrim + hairline + the top-right control:

| | fill | hairline | control |
|---|---|---|---|
| selected | the artwork, untouched | white @30% | `control.radio.fill` |
| unselected | + `gOnboardingTopicCardScrim` | white @5% | `control.radio` |

**The mock's GLASS effect is a no-op — do not try to approximate it.** Differencing the frame render
against each card's own image fill (`/v1/files/<key>/images` → the `imageRef` URL) showed the
selected card is within 1/255 of its raw source, and the unselected delta fits `black 0 → 0.2 @
[0, 0.92]` at every sample (predicted .054/.109/.163 vs measured .057/.108/.172). One LinearGradient
reproduces it exactly. Verified again on-device after the build: tapping a card lightened it by
+4/+7/+13/+13/+22 down its height against a predicted +3.9/+7.3/+12.8/+12.4/+22.2.

**Icon exports must carry the node's padding, or they render oversized.** A re-run measured the card
icon at 23.8 against the node's 19.8: `icon.moon` and `icon.targetArrow` had been exported from tight
glyph nodes (viewBox 21 = the ink box), so scaling them into the 24pt `Icon` box drew them edge to
edge, while every other icon came from a 24 frame that already contains ~2pt of padding. Fix: pad the
tight ones to a 24 viewBox (`<g transform="translate(1.5 1.5)">`) — 20.7 after. **Check the viewBox of
every export against the others; a smaller one is a tight-bbox pick, not a smaller icon.**

**Per-topic icons: `src/logic/topicIcons.js`** maps the topic's `unique` → an `Icon.icons` key (the
artwork is server-driven, so the icon can't come from the API). 25 new SVGs exported from this node;
4 more were already in the repo (`icon.aura`, `icon.sun`, `icon.imagine`) — and the mock's
`IconCircleCheck`/`IconFormCircle` are byte-identical to the existing `control.radio.fill`/
`control.radio`. **Dedupe by path-data signature before adding an icon**; it caught all four.
Watch the blur-duplicate twins here too — two cards ship only a blurred wrapper, so the sharp child
has to be exported by id.

~~Deviations: the mock's artwork is pre-blurred and the app keeps the topic's own (sharper) remote
image — a content difference, not a style one.~~ **SUPERSEDED (user-driven, 2026-08-14): the cards'
artwork and order now match the frame exactly.** The 29 pre-blurred image fills were exported from
the node (MCP asset URLs, 1024², downscaled to 528w JPG q80, ~16KB each) into
`src/img/newDesign/topics/<unique>.jpg`, and **`src/logic/topicCards.js`** maps `unique` →
bundled artwork (`getTopicArtwork`) + the frame's card order (`orderTopicsForNewDesign`, applied to
the render list in the dark branch only; selection bookkeeping still uses the unsorted server list).
Aliases (anxiety/sleepAnxiety, relax/relaxation, emotions/dealEmotions, loveAndRelationships/
relationships, personalGrowthSuccess/personalGrowth) share their primary's artwork/rank; unmapped
server topics keep their remote artwork after the designed ones. Card labels were verified live:
all 29 server labels are byte-identical to the frame's copy, so no label overrides were needed.
The frame is a scroll canvas with no CTA, so the pinned `OnboardingContinueButton` and its scrims
come from the shared component. The dark branch's loading skeleton mirrors the card (DARK_THEME,
R14, 115pt) — fixed in the same pass.

Trap: `GridView` puts `rowStyle.marginTop` above the FIRST row too, so the header's own gap must be
`20 - rowGap` or the grid lands 15pt low (measured 343 vs the node's 327 before the fix; 328 after).

## E.13b/E.14b Grid update (user-driven, 2026-08-14): both selection screens share OnboardingTopicCard

Product direction changed: the content-type screen (choose 2) renders the SAME 2-column card grid
as the topics screen, per node **1048:16823** ("Content Interest Selection" — heading "What types
of content are you interested in?", subtitle "Choose 2 or more content types", pinned CTA). The
E.14 row-list build below is **superseded** for this screen (rows/`contentTypeRows` descriptors are
no longer rendered; the icon mapping is still used for the cards' top-left glyph).

- The topics card was extracted verbatim to **`src/components/onboarding/OnboardingTopicCard.js`**
  (accepts bundled `require()` artwork OR remote `{uri}`); `PersonalizedOnboarding` and
  `ContentTypeOnboarding` both compose it. Content-type artwork = the items' bundled `fullImage`.
- `ContentTypeOnboarding`: `itemsPerRow` 2 (new design), row gap 15, header gap `20 - gap`,
  testIDs unchanged (`onboarding_contentType_option_<key>`). Verified on-device: grid renders,
  min-2 selection flow works, selected = undimmed artwork + white@30% hairline + radio.fill.
- Topics heading updated to the node's copy — new key `onboarding_topic_concentrate_question_text`
  ("What do you want to concentrate on?", translated in all 7 locales) replaces the name-interpolated
  `onboarding_topic_what_text` in the variant-`a` branch only.
- ⚠️ `npx i18next-scanner` is BROKEN in this repo (ESM `i18next-scanner.config.js` vs CJS CLI
  loader) — new keys must be added to all 7 locales by hand, sorted; `npm run i18n:check` verifies.

## E.14 Content Type Selection — Full List (node 3228:30081 → `ContentTypeOnboarding`)

**Check the frame NAME before building — this section has two near-identical grids.** The screen
open in the sim is `Content Type Selection - Full List` (subtitle "Choose 2 or more content types");
its sibling `Content Interest Selection` (3234:30761, §E.13) is the TOPICS grid whose subtitle says
"content types" — a copy slip in the mock that will send you to the wrong screen.

The redesign **changes the layout**: an 11-row single-column list (366x90, gap 16), not the 2-column
image grid the screen used to render. Each row is `Component/Carousel/Tab` = `OnboardingOptionRow`,
so the screen is mostly composition: `OnboardingHeading` + rows + `OnboardingContinueButton`, column
gap 32.

**`OnboardingOptionRow` was wrong for every screen using it, and this frame proved it.** The row's
height is not fixed at 64 — every frame pads its row by 20, so the height follows the content:
gender (label only) measures 366x**64**, duration and this frame (label + subLabel) measure 366x**90**.
The component hardcoded 64, squashing the two-line steps. It also set the subLabel in Proxima where
all three frames specify Season Sans Regular 16 / lh 20.8, and its inner shadows were the dead
`boxShadow`. All three are fixed in place — those are corrections toward each screen's own node, not
a restyle. Measured after: rows within ~2pt, pitch 106 exact, checkbox x 343–360.5 exact.

**⚠️ `Icon`'s `stroke` prop CASCADES and fills in knock-out glyphs (user-reported, `icon.timeFlies`,
2026-08-14).** The duration frame's Short clock is a filled disc with the hands knocked out of a
compound path. `Icon.js` passes `stroke` to `SvgXml` as a **root prop**, which SVG inherits onto
every child path — so a ~1pt-wide knock-out gets a 1pt white stroke on both of its edges and closes
up. The clock rendered as a solid white ball. It is NOT a fill-rule problem alone, and NOT fixable
by repainting the hole.

Fix, all three needed: **`stroke-width="0"` on every fill-only path** (neutralises the cascade —
`Icon.js` only rewrites `stroke-width` when a `strokeWidth` prop is passed, which the row doesn't),
`fill-rule="evenodd"` so the inner subpath knocks out regardless of winding, and a re-box from the
export's non-square 28×24 render-bounds canvas to the folder's 24×24 convention (it was letterboxing
small in the square slot). `OnboardingOptionRow`'s white iOS-shadow glow was also removed — it is a
silhouette, so it shines through any true knock-out.

**Any filled glyph with an interior hole needs `stroke-width="0"`** before it will survive this
Icon component. iconList patched surgically (never regenerate — `bundleIcons` reformats all 278
entries into a different quote style).

**Icon export trap — pick the CONTAINER, then strip the blur.** Exporting "the deepest unblurred
leaf" silently produced a 2x2 viewBox for Hypnosis and a 3x3 for Life Coaching (single sub-paths of
a multi-path glyph) — they render as blobs, and only a device screenshot catches it. Export the icon
frame instead and delete the blurred twin from the SVG (`<g … filter="url(#filter…)">` + `<defs>`).
**Sanity-check every export's viewBox** — anything much under 24 is a wrong pick.

New: 11 `icon.*` glyphs, `src/logic/contentTypeRows.js` (key → icon + descriptor), and 11 i18n keys
for the row's second line, which is new copy the design introduces (the mock's "exercies" typo is
corrected). `therapy` maps to `icon.cbt` per the CBT branding in `.claude/rules/localization.md`.

Deviation: the mock's header reads "What makes these important for you?" — a placeholder pasted from
the motivation frame. The app's own "What type of content are you interested in?" is kept.

## E.14c Content Type Selection — the grid was the WRONG NODE (user-corrected, 2026-08-14)

§E.13b/E.14b re-pointed this screen at node **1048:16823** and rendered it as the 2-column
`OnboardingTopicCard` grid. That node is not the content-type screen: its 29 cards are the
**topics** set (Soothing, Restful Sleep, … Deal with Emotions), identical to 3234:30761. Only its
*heading* says "What types of content are you interested in?" — the same copy slip §E.14 warned
about, read in the opposite direction. The frames actually named "Content Type Selection" are
**1048:16133** (5 rows) and **3228:30081** (11 rows), and both are ROW LISTS.

**Three frames, near-identical names — check the CARD LABELS, not the frame name or the heading:**

| node | name | actual content |
|---|---|---|
| 1048:16133 | Content Type Selection | 5 content-type rows + descriptors |
| **3228:30081** | Content Type Selection - Full List | **11 content-type rows + descriptors** ← the screen |
| 1048:16823 | Content Interest Selection | 29 TOPIC cards (heading is a copy slip) |
| 3234:30761 | Content Interest Selection | 29 TOPIC cards (the topics grid, §E.13) |

Reverted to the row list per 3228:30081. `OnboardingOptionRow` already matched the node's row
(366x90, R18, white@5%, double inner shadow, pad 20, gap 16, icon 24 @x20, text gap 6, checkbox
24 @x322, pitch 106), so this was composition, not a rebuild.

Two per-frame values that the shared components had wrong, both now additive props:

- **Subtitle is Season Sans Light 18 / lh 23.4**, not the Regular 16/20.8 every other frame uses.
  Verified on BOTH content-type frames, so it is not an authoring artifact →
  `OnboardingHeading` gained `subtitleFont` (default unchanged).
- **The unselected checkbox is FULL white**, not the white@50% §E.17 established for gender /
  spiritual / duration. The node says `#FFFFFF` where those say `#FFFFFF@0.5`; confirmed off the
  render (stroke luminance **255**; white@0.5 over the row's 36 bg would be 145.5) →
  `OnboardingOptionRow` gained `unselectedControlOpacity` (default 0.5, so the other five steps
  are untouched). **§E.17's "every frame draws it at white@50%" is now false — check per frame.**

Other geometry: heading→rows gap is **32** (`Frame 1184` itemSpacing), not the topics grid's 20,
so the GridView header trap value is `32 - rowGap`. Row gap **16** (was the grid's 15).

**Row order comes from the frame, and the server list disagrees.** Frame order is Soundscapes,
Hypnosis, Mindfulness, Story, ASMR, Life Coaching, Breathwork, Prayer, Music, CBT, Work Wellness;
`getContentTypeFullList` returns mindfulness, lifeCoaching, story, soundscape, … →
`orderContentTypesForNewDesign` in `logic/contentTypeRows.js`, mirroring `topicCards.js`'s
`orderTopicsForNewDesign`. Applied to the **render list only** — `toggleCheckbox` writes its
argument back into state, so handing it the sorted list would silently re-sort stored preferences.
The app carries three types the design has no row for (`podcast`, `energyHealing`, `celebrity`);
they keep their relative order after the designed eleven rather than being dropped.

## E.15 Goals Selection (node 1048:16080 → `OnboardingMotivation`)

Rebuilt on the shared components; **99 lines of hand-drawn `react-native-svg` goal glyphs deleted**
(the §E.1 TODO), net −224 lines. Rows → `OnboardingOptionRow`, CTA → `OnboardingContinueButton`
(which brings the frame's two bottom scrims), header → `OnboardingHeading`, plus the 4.0 top bar and
the node's backdrop composite (the old asset + `gOnboardingDark` was off by a mean of 21/255).
Measured after: rows land within ~1pt on all five (tops 347/427/507/588, h 64.5, gap 16), ring 1pt,
progress 1pt, CTA exact, backdrop mean 2.9.

**The frame's copy and icons are fixed, not topic-derived.** The control branch builds its rows from
the user's selected topics; this frame lists five specific goals, each with its own icon, so the new
branch renders `NEW_DESIGN_GOALS` (5 new i18n keys). Every `unique` in that list is still a real
preference key, so what `onContinuePressed` hands downstream is unchanged in shape.

Two shared-component corrections fell out of this frame, both affecting all six screens using it:

- **Selected border is white@35%, not white.** Verified identical on the goals, gender and duration
  frames; was `textPrimaryMain` → `borderSelectedRow`.
- **The selection glow was clipped halfway down the row.** `Rectangle 366` is a radial whose
  y-radius is ~2× the row height, so inside the row it is a pure left-to-right fade with no vertical
  falloff. It was implemented as a react-native-svg radial sized `width/height="100%"`, and rn-svg 12's
  percentage viewport does not track a content-height row — on a two-line row the glow stopped at
  ~46 of 88pt, leaving a visible horizontal edge. Replaced with `gOnboardingRowSelectedGlow`
  (LinearGradient, stops at the radial's core and outer edge), which sizes reliably via absoluteFill
  and matches the mock's mid-row profile within ~3/255.

## E.16 Re-verify pass — Session Length Preference (node 1048:16206 → `OnboardingDuration`)

A second `/figma-to-code` run on this ONE frame. **The Figma file had been edited the same day**, and
two of the four drifts found were design changes, not build errors — the reusable lesson is that a
frame marked "built" last week is not "still correct" today. Check `lastModified` and re-extract.

| # | Element | Was | Figma (1048:16206) |
|---|---|---|---|
| 1 | Backdrop | `welcomeBackground.jpg` (the `55d645c1…` forest) at offset -830/-124 | **`85fb202d…` night sky**, `scaleMode: STRETCH`, + radial black vignette + **PROGRESSIVE LAYER_BLUR** (startRadius 80, 0.0→0.698) |
| 2 | Selected-row glow | shared teal token (the goals step's) | **violet `#3F3A6D → #6C5272@0`** |
| 3 | Progress value | hardcoded `0.392` | `progressForView('durationOnboardingRecs')` |
| 4 | Selected-row border | `borderWidth` on the touchable | INSIDE-aligned stroke that must NOT displace content |

**Read the `visible` flag on every fill.** This frame's `image 23` carries THREE fills: a *hidden*
`932f6f99…`, the visible `85fb202d…`, and the radial vignette. A scan that takes `fills[0]` reports
the hidden ref and sends you to bake the wrong photo — it cost a full round-trip here. Same for
`LAYER_BLUR`: `radius` is `0.0` because the blur is `blurType: PROGRESSIVE`, carried in
`startRadius`/`startOffset`/`endOffset`. Reading `radius` alone says "no blur".

**RN has neither a progressive blur nor a radial gradient fill, so let Figma composite: export the
background RECTANGLE node, not the image fill.** `/v1/images` on `I1048:16207;92:66` returns the
image + vignette + progressive blur already flattened. Two gotchas: the export is **effect-inflated**
(3847x2297 for a 1763x988.3pt rect at 2x = 80.25/80.10pt per side), so compute the frame's window as
`(bb - frame_origin) - inflation` → crop offset (1620, 207) size 804x1748; and the result is the
frame's visible window, so it renders full-bleed `cover`, NOT at the forest asset's offset geometry.
`src/img/newDesign/nightSkyBackground.jpg` (JPEG q80, 55KB — q90 measured no better, worst channel
delta 0.9 either way). Verified ≤0.9/255 vs the frame render across 8 background-only windows.

**Backdrops are per-STEP in this section — there are three, and they do not follow the flow order:**
`55d645c1…` forest (goals, content-type, name, topics, the 5 personalization frames);
`85fb202d…` night sky (session length, spiritual, gender, age); `7028d422…` dusk (accent, voice
preview, notification time, profile summary, account creation). `OnboardingScreenLayout` now takes an
additive `backdropVariant` (`forest` default | `nightSky` | `dusk`); the latter two resolve through a
`BAKED_BACKDROPS` map, so a fourth backdrop is one entry plus one asset.

**The selected-row glow is tinted per step.** `Rectangle 366`'s geometry is identical on every frame
(handles `[[0.0988,0.5],[0.9173,0.5],[0.0988,2.4935]]`, stops 0.0744/1.0 — hence the shared linear
approximation at `[0.174, 0.918]` stays right), but the colours are not: goals teal
`#527270→#375855`, session-length + gender violet `#3F3A6D→#6C5272`, spiritual peach
`#E5C0A2@0.54→#14100E`, accent blue `#6C7B9F→#3F5FAE`. `OnboardingOptionRow` takes a `selectedGlow`
prop defaulting to the teal, so one token per hue is added as each step is verified
(`gOnboardingRowSelectedGlowViolet` exists now).

**An INSIDE-aligned Figma stroke does not displace content; RN `borderWidth` does.** Both the
selected and unselected rows put their icon at x=20, so the 1pt stroke overlays the padding. With
`borderWidth` on the touchable, RN insets the children and the row visibly shifts on selection.
`OnboardingOptionRow` now draws the ring as an absolutely-positioned overlay — a shared fix for all
six steps using it.

**Two copy calls that §E.12 got wrong, both corrected by the product owner during this run — do not
"restore" either.** §E.12 read the frame's heading as a placeholder pasted from the motivation frame
and kept the app's legacy "What track length are you most interested in?". The frame's
**"What makes these important for you?" is the intended copy** for the redesigned step; it now has
its own key, `onboarding_duration_headline_text`, applied in the variant-`a` branch only so variant
`b` keeps the legacy question. Do NOT reuse `onboarding_motivation_why_text` for it — that string is
"Great choices! What makes these important **to** you?", a different step with a different prefix and
preposition.

**The row sub-labels are derived from the shipped copy, and one option has none.** §E.12 split the
existing "Short (3 minutes)" strings on their parenthetical to get the second line, which leaves
"All durations" single-line — but the frame gives it **"No preference"**. That is new copy, so it
needs a key rather than a derivation: `onboarding_duration_option_all_subtext_text`, applied via a
per-`itemKey` fallback map. Translation is pending in the 6 non-English locales; `Translator` sets
`fallbackLng`, so they render the English string rather than a raw key in the meantime.

⚠️ **`npx i18next-scanner --config i18next-scanner.config.js` (the propagation command in CLAUDE.md
and `localization.md`) is broken and cannot run.** `i18next-scanner.config.js` opens with
`import fs from 'fs'` and then assigns `module.exports`, so Node parses it as ESM and throws
`ReferenceError: module is not defined in ES module scope`. Not a Node-version issue — the `import`
forces ESM on any version. Add new keys to `en` by hand (in alphabetical position — `npm run
i18n:check` enforces sorting and passes) until someone fixes the config; that is a tooling PR, not a
UI one.

**The mock staggers the row labels by 2.6pt and it is an authoring artifact — do not reproduce it.**
Rows 1–2 wrap their 24pt glyph in a 26.6pt `Icons/ASMR` frame while rows 3–4 use a bare 24pt frame,
so the text block starts at x 62.6 on the first two rows and 60.0 on the last two. Measured on the
frame render (first bright column per label band): rows 1–2 at ~81pt, rows 3–4 at ~78pt. A uniform
24pt icon box aligns all four labels, which is plainly the intent; a staggered list would read as a
defect.

⚠️ **`sim_install.sh` does not install the test runner, so `sim_start.sh --reset` breaks the next
sim run.** The script's only `simctl install` is `Aura.app`; `AuraSimTests-Runner.app` is built but
never installed. On a freshly-erased device `sim_test.sh` then dies with `Simulator device failed to
launch com.AuraMobile.AuraSimTests.xctrunner … NotFound ("Unknown application display identifier")`,
which reads like a signing/scheme problem and isn't. Install the runner from the same products dir
after any erase. Note also that **`sim_install.sh` exits 0 even when the build fails** (`** TEST BUILD
FAILED **` in the log, exit code 0) — grep the log, never trust its exit status.

Still true from §E.12: the mock ships the same static 63.4-of-162 progress fill on EVERY frame, so
never read it as the step's value. Gender, Age, Spiritual and Accent still hardcode `0.392` and
still render the forest backdrop; each needs `backdropVariant="nightSky"` (Accent keeps `7028d422…`,
which has no asset yet) plus its own glow token.

## E.17 Re-verify pass — Spiritual Content Preference (node 1048:16279 → `OnboardingReligionPreference`)

A second `/figma-to-code` run on this ONE frame, built in §E.12. The file's `lastModified` was the
**same day** again (2026-08-11), so §E.16's rule held: re-extract, never trust a prior "verified".
Five drifts, three of them the exact items §E.16 predicted would still be outstanding here.

| # | Element | Was | Figma (1048:16279) |
|---|---|---|---|
| 1 | Backdrop | forest (`55d645c1…`) | **`85fb202d…` night sky** → `backdropVariant="nightSky"` |
| 2 | Selected-row glow | shared teal | **peach `#E5C0A2 → #14100E@0`** → new `gOnboardingRowSelectedGlowPeach` |
| 3 | Progress value | hardcoded `0.392` | `progressForView('religiousPreference')` (= 1.75/4 = **0.4375**) |
| 4 | Heading → rows gap | 24 | **32** (`Frame 1184` itemSpacing) |
| 5 | Unselected control stroke | white | **white@50%** — SHARED, see below |

**A node's gradient stop alpha does NOT transfer into the linear approximation.** `Rectangle 366`'s
peach core stop is `a: 0.54` at gradient position 0.074, but the row's left edge already maps *past*
that stop, so using 0.54 over-lightens the row by ~20% (worst 30/255 against the frame render).
Fitting the ramp to the render gives locations [0.185, 0.940] @ **0.40** — near-identical geometry to
the shared [0.174, 0.918], so the token keeps the shared locations and only takes the measured
alpha (worst 4/255, rmse 2.0). The teal and violet tokens never exposed this because their core
stop is `a: 1.0`, where the clamped plateau is opaque either way. **Fit the amplitude to the render;
copy only the geometry.**

**Unselected controls are white@50% on EVERY frame** (verified on spiritual, gender 1048:16312 and
session-length 1048:16206) — `OnboardingOptionRow` was drawing them full-white, so all six steps
using it were too bright. Measured off the render: 0.504/0.502/0.502. The bug hid for so long
because `control.radio.svg` bakes its own `stroke-opacity="0.5"` while `control.checkbox.svg` does
not, so the radio steps looked right and the checkbox steps did not. Fix travels as `Icon`'s
**`strokeOpacity`** prop, which is inherited and therefore *overridden* by an asset's own value —
an rgba stroke colour would instead **multiply** with it and take radio down to 25%.

**Read `visible` on element nodes, not just on fills.** §E.16's lesson was hidden *fills*; this
frame hides two whole nodes that a spec-sheet scan reports as real content:
- `IconGoogle` **inside the Continue button instance** (a leftover from the social-login component)
  — the CTA is label-only. Building it would have put a Google mark on a religion step.
- The heading's **subtitle**, whose copy is the session-length step's ("You will have access to all
  lengths in the app") and which collides with the 3-line title. The app keeps its own
  `onboarding_religion_subtext`; §E.12's "no subtitle in the node" now reads as design having
  switched it off. **Needs a design answer** — it is the one open item on this frame.

Backdrop reuse was verified, not assumed: this frame's `Background` instance matches the duration
frame's byte for byte (same rel offset -729.53/-23.15, same 1763.01x988.29 box, same hidden
`932f6f99…`, same visible `85fb202d…` STRETCH, same radial handles, same PROGRESSIVE blur
startRadius 80 / endOffset 0.698), so `nightSkyBackground.png` serves both. Re-measured against
this frame's render across 24 background-only windows: worst **4/255**, mean 0.54.

Row 5 ("None") has auto-layout gap 8 where rows 1-4 have 16 — an artifact, since the label sits at
x=20 and the control at x=322 on every row. Uniform is correct (same call as §E.16's 2.6pt stagger).

Confirmed matching, no change: row 366x64 pad20 R18 white@5% + double inner shadow, rows gap 16,
label `labelSansLight18`, selected hairline white@35%, `titleSerifRegular30` title, ring drawn
49.07 (render bounds, not the bb's 60.08), column x18 w366 top 122, top-right Skip, CTA 366x54 R36,
both bottom scrims.

Still outstanding from §E.16 for the sibling steps: Gender, Age and Accent continue to hardcode
`0.392` and render the forest backdrop (gender + age are night-sky frames; accent is `7028d422…`,
still with no baked asset), and each needs its own glow hue (gender = violet, already tokenised).
**All three are now done — Gender in §E.18, Age and Accent in §E.19.**

## E.18 Re-verify pass — Gender Identity (node 1048:16312 → `OnboardingGender`)

A `/figma-to-code` re-run on the frame §E.12 built first. Confirms §E.16/§E.17's standing prediction:
all three items they listed as still-outstanding for this step were still outstanding, and one more.

| # | Element | Was | Figma (1048:16312) |
|---|---------|-----|--------------------|
| 1 | Backdrop | forest (no `backdropVariant`) | `85fb202d…` night sky → `backdropVariant="nightSky"` |
| 2 | Selected-row glow | shared teal (no `selectedGlow`) | violet → `gOnboardingRowSelectedGlowViolet` |
| 3 | Progress value | hardcoded `0.392` | `progressForView('gender')` (EN 2.25/4 = **0.5625**) |
| 4 | `icon.people` stroke | `stroke-width="1.59354"` | **1.0** — exported off the blurred layer |

**An icon exported from the wrong one of Figma's two stacked copies is silently ~60% too heavy.**
Every glyph on these rows ships twice in the node: a sharp copy at `strokeWeight` 1.0 and, behind it,
a blurred copy at 1.59354 / opacity 0.4 that stands in for the glow (which `OnboardingOptionRow`
reproduces with an iOS shadow instead). `icon.people.svg` was exported from the blurred copy, so
"Other" drew heavier than its three siblings — none of which carry a `stroke-width` at all. **When a
node stacks a blur copy under a glyph, export the sharp one and check the emitted `stroke-width`
against its siblings.** `icon.imagine` and `icon.sun` carry the same 1.59354 and belong to other
screens' nodes — left alone here, worth checking when those frames are re-verified.

Confirmed matching, no change: row 366x64 pad20 R18 white@5% + double inner shadow, rows gap 16,
heading→rows gap 32, column x18 w366 top 122, ring 60, `titleSerifRegular30` title,
`bodySansRegular16` white@70% subtitle, `labelSansLight18` labels, selected hairline white@35%,
unselected controls white@50%, both bottom scrims (123 + 133, gradients within 1/255), CTA 366x54 R36
white/black 39pt off the bottom, chevron (`action.arrowLeft` at 18 renders 7.61x15.75 vs the node's
7.59x16), bar 162x3.54. The Continue button's `IconGoogle` is `visible: false` and correctly omitted —
§E.17's "read `visible` on element nodes" held again.

**The subtitle's line break is load-bearing.** The node's single TEXT carries an explicit `\n`
between its two sentences; the app joins the two legacy keys, and a space let "It" fit on line 1
(~324 of 366pt used). Joined on `\n` so the break survives any font-metric drift.

Open, not changed: the node's title reads "What**'s** your gender identity?" where
`onboarding_gender_identity_text` says "What **is**". The key is shared with the legacy render across
7 locales, so the contraction is a copy call for design/loc, not a build fix.

## E.19 Re-verify pass — Age (1048:16367) + Accent (1048:16383)

The last two steps §E.16/§E.17 listed as outstanding. Both carried the three predicted drifts
(forest backdrop, hardcoded `0.392`, missing glow hue); each also had a structural mismatch that only
a fresh extract caught. **This closes the section — all six option-row steps now match their frames.**

| Step | # | Element | Was | Figma |
|---|---|---|---|---|
| Age | 1 | Backdrop | forest | `85fb202d…` night sky → `backdropVariant="nightSky"` |
| Age | 2 | Progress | `0.392` | `progressForView('ageRange')` (EN 2.25/4 = **0.5625**) |
| Age | 3 | Subtitle | first sentence only | two sentences on the node's hard break |
| Age | 4 | Row style | radio control, left label | **no control, centred label** |
| Accent | 1 | Backdrop | forest | `7028d422…` → **new `duskBackground.png`**, `backdropVariant="dusk"` |
| Accent | 2 | Progress | `0.392` | `progressForView('countryOnboardingRecs')` (EN 3/4 = **0.75**) |
| Accent | 3 | Selected glow | shared teal | blue → new `gOnboardingRowSelectedGlowBlue` |
| Accent | 4 | Heading | one serif sentence | **sans eyebrow above** a serif question |
| Accent | 5 | Row icons | none (only `OT` mapped) | **five full-colour flags** |

**Compute the backdrop-export inflation from the export, never from a previous frame's constant.**
§E.12's night-sky rect inflated 80.25pt/side; this one inflates **100.25/100.10** (3927x2377 for the
same 1763.008x988.292pt rect) because its progressive blur has `radius: 100` where the other has 80.
Reusing 80.25 would have cropped 20pt off-centre. The robust form is
`inflation = (export_px - rect_pt*2) / 2`, then `crop = rel_offset*2 + inflation` → (1660, 247), size
804x1748. Verified ≤1/255 across 12 background-only windows; the 3 top-band windows read 3–10 because
that band is semi-transparent (α 219–245), and compositing it over `canvas` lands within
1/255. **So the asset must stay PNG** — a JPEG would drop the alpha the composite depends on.

**A multi-colour asset cannot go through the row's icon path unchanged.** `OnboardingOptionRow` tinted
every leading glyph and put a white shadow behind it, which is right for the monochrome glyphs (the
node stacks a blurred twin behind each) and wrong for the flags (single copy, no twin). `Icon` also
regex-replaces `fill=`/`stroke=` and forwards `stroke` to `SvgXml`, where unstroked flag paths would
**inherit** it and gain a 1px outline. Hence `iconTinted` (default true) — false skips both the tint
and the glow. Only `OT` on the accent step is tinted.

**Age's row style is deliberate, not an omission.** All five rows are `primaryAxis/counterAxis=CENTER`
with a single visible HUG label, no leading icon and no control, and their subLabel TEXT is explicitly
`visible: false` — while gender, accent, duration and spiritual all *do* draw controls. Added as
`selectionStyle="none"` + `labelAlign="center"`. Rows 2–5 measure 54 to row 1's 64: a HUG override
artifact (pad 20 + 23.4 label = 63.4), so uniform 64 is correct — same call as §E.17's gap-8 row.

Two open items for design, both consequences of the frames rather than build choices:
- **Age has no selected state anywhere in the node** (no `Rectangle 366`, no hairline on any row), so
  its glow hue is unspecified and falls back to the goals teal — which reads out of place on a
  night-sky frame where its neighbours are violet and peach. Needs a hue.
- With the control gone, an age selection is legible **only** from that row wash. Verified readable on
  device, but it is the one step in the flow with no explicit selection affordance.

Copy: the node's eyebrow is "Aura **coaches** are global." where `onboarding_coach_global_accents_text`
says "Aura**'s** coaches…". Split into `onboarding_coach_accents_eyebrow_text` +
`_headline_text` with the node's wording; the possessive is design's call. New English keys are low
risk here — `countryOnboardingRecs` is only reachable from `ageRange` when `isUserLocaleEnglish()`.

## E.20 Plan-loader personalization frames (nodes 1048:16546/16581/16653/16693/16730 → `OnboardingYourPlanLoader`)

The 5 personalization frames §E.12 listed as NOT built are now built (branch
`ashish/prod-1115-aura4-onboarding`, commits `20aec03be` + `0510773f4`). All five are stages of
ONE screen — `OnboardingYourPlanLoader.renderRedesign()`, gated on `isAuraDesignChange` with the
legacy render untouched. The stage bodies live in
`src/components/onboarding/OnboardingPlanStages.js` (`PlanStatsCard`, `PlanTopicGrid`,
`PlanLeadOnly`, `PlanDurationTile`, `PlanPreviewRow`); the animated bar + % label is
`OnboardingPlanProgressBar` (track 314x6 `surfaceSoft`, white fill, un-blurred
`gPlanProgressGlow` copy at 0.6 opacity standing in for the node's blurred rainbow glow —
RN cannot blur a sibling). Chrome reuses `OnboardingScreenLayout` (`showHeader={false}`) +
`OnboardingHeading` (ring 115, gap 48); the redesign steps stages on its own clock
(`runRedesignAnimation`) because the legacy `runAnimation` drives an `Animatable` ref the redesign
doesn't mount.

Re-verify pass on 1048:16730 "Personalized Plan Preview" (fresh MCP extraction) found + fixed 4
drifts in `PlanPreviewRow`:
- badge "Soundscape" was text/primary → node sets **Copy 14 @ text/secondary**
- StageLead body was `bodySansRegular16` → THIS frame sets **Label 18** (16546 confirmed Body 16,
  so it's per-stage: `StageLead` gained a `bodyFont` override, only the preview stage passes it)
- card drop shadow radius 24 → node is **0/4/12 black@25%**
- badge inset (16,16) → node is **(13.6, 17)**; border literal → `surfaceSubtle`

Second re-verify pass (user-driven, on-device loop): the preview cards were rebuilt to the node's
real form — cover = the topic's CLEAN photo (`topic.img`, PersonalizationTopics tile) under a
`coverScrim` (black@20%, new token) wash, serif title (`titleSerifRegular30` standing in
for the unbundled Season Serif Light, same substitution as the duration tile) with the node's text
shadow, 28pt ring glyph + byline capped at the node's 108pt box so it wraps into the mock's two-line
break, all bottom-anchored 21/19 per the node. The earlier opaque-footer-band deviation is GONE —
it existed only because the cover used the baked-title `madeForYouMix` art; switching the cover to
the clean photo dissolved the collision. Progress-bar glow: a same-size gradient copy is fully
hidden behind the opaque white fill — the halo is 3 enlarged copies (spread 2/5/9pt, opacity
.35/.18/.08) approximating the node's 4pt blur.

Standing deviations (documented in-code, intentional): badge sits on a flat black@19% fill (no
backdrop-blur in RN); halo is stepped rings, not a true blur; progress fill animates 0→100% (the
node's static 246/314 fill with a "15%" label is mock art, not a spec).

⚠️ **Incremental `xcodebuild` can SKIP the RN bundle phase** — a JS-only edit rebuilt+reinstalled
but shipped the previous `main.jsbundle` (timestamp check caught it). Before reinstalling after a
JS-only change, `rm DerivedData-CLI/Build/Products/Release-iphonesimulator/Aura.app/main.jsbundle`
to force the phase, and verify the bundle mtime afterwards.

## E.33 Coach Profile — "More availability" sheet (node 1006:13681 → `CoachDetailsMessaging`)

Frame `Coach Profile - View More Availibility` (sic): a bottom sheet over the dimmed profile replacing
the redesign's "View more availability" jump to `coachBooking`. Legacy (variant b / other callers)
keeps navigating; the gate is the existing `isNewDesign` prop already threaded into
`CoachDetailsMessaging` (§E.6 pattern).

New component: **`CoachProfileAvailabilitySheet`** (`src/components/coachProfile/`, presentational,
RN `Modal` slide-up). Chrome: R32-top sheet (new token `borderRadius32` on the redesign ladder),
`gCoachSheet` (surfaceNavDark → canvas) standing in for the isolated Sheet node's
black@40% + blur(200) composite (1006:14170) — a Modal cannot sample the screen behind
it, same rationale as `surfaceNav`. `borderSubtle` hairline, 40x5 `textMuted` drag handle
(visual only; dismissal = X / backdrop tap / Android back).
Composes §E.6 components: `CoachProfileIconButton` (close X), `CoachProfileDateSlots` (new additive
`scrollable` + `horizontalInset` props — fixed-width rail, 3 visible + 4th slot peeking, sub lines
kept), `CoachProfileTimeSlots` (selectable grid, no `moreLabel`). CTA is the node's white pill
(43pt, `buttonSansSemiBold16` on `onLightPrimary`), disabled at 0.5 until a time is picked
(`CoachProfileCta` convention).

Data + continue: no new fetch — `CoachDetailsMessaging.checkCoachAvailability` already pulls the
30-day availability; it now also buckets it by day (same YYYY-MM-DD bucketing as the selector).
Continue opens the confirm sheet (§E.21) instead of `coachBooking`. Tracking reuses
`Coaching Profile Messaging View All Selected` / `... Date Selected` — no new event names.

New i18n keys (added to all 7 locales): `coaching_availability_sheet_title`, `_meta`,
`_times_label`, `_times_label_no_timezone`, `_slot_count_one/_other`. Reused: `a11y_close`,
`continue_button`, `coaching_choose_date_label`, `date_today_label`, `date_tomorrow_label`.

Deviations:
- **Settings button omitted**: the node shows a settings glass circle top-left, but no availability
  setting exists in the app to open (same no-invented-actions rule as §E.8's heart button).
- "30 min · Free" is baked into `coaching_availability_sheet_meta` (the discovery call is uniformly
  30 min in existing copy) rather than computed from a duration that isn't in the data here.
- Sheet fill is `gCoachSheet` (the exported 1006:14170 composite), not a live backdrop-blur.

⚠️ **Branch repair (same PR)**: the coachProfile branch predated the `67dd5c7df` token-prefix
refactor and had drifted — 24 files still referenced `newDesignDimension` (deleted; →
`scaledDimension` from `styles/redesign/dimensions`), `newDesign*` fonts/colors (→ the semantic
ladder), `gNewDesign*` gradients, the deleted `logic/DesignExperiment` module, and
`getIndividualStyle(appTheme.NEW_DARK)` pins (→ `isRedesignTheme(theme)` +
`getCurrentThemeDarkMode(theme)`). Also restored `CoachAvailabilityDateSelector`'s redesign render
branch (§E.6's `renderNewDesign`), which existed only on the original mega-branch and was lost in
the branch split.

## E.34 Coach Profile — "Confirm your session" sheet (node 1006:14004 → `CoachDetailsMessaging`)

Frame `Coach Profile - View More Availibility` (same parent name as §E.20; this node is the
confirm step). Bottom sheet over the dimmed profile after Continue on the availability picker.
Legacy (inline time tap / variant b) still navigates to `coachBooking` step 2
(`BookingConfirmation`). Gate: existing `isNewDesign` prop.

Chrome is extracted into **`CoachProfileSheet`** (shared by §E.20) so the backdrop/scrim/handle/close
aren't duplicated. New **`CoachProfileConfirmSheet`**: Display-25-Serif title, 84pt FastImage
avatar + caption name + copy-14 `iconMuted` role + glass session-type chip, "Session Details"
(`displaySansRegular20`) with three space-between rows (`CoachProfileDivider` between them),
24pt R6 age checkbox (white when on, `control.check` glyph in `canvas`; `CheckBox` is not
reused — it is a cyan gradient used across booking/paywall), 64pt white Confirm pill (taller
than §E.20's 43pt Continue; no `onLightBorder`).

New icon **`control.check`** (`src/img/newDesign/svg/`, the node's own 14x14 export). The existing
`control.checkmark` could not be reused: its glyph sits in a padded 42x38 canvas with a baked
drop-shadow, so it only reads at ~40pt — which is why `reuseableUI/Checkbox` renders it at 40.
At this checkbox's 14pt the mark came out roughly a third of the box and off-centre.
⚠️ `npm run bundleIcons` rewrites `generated/iconList.js` with JSON double quotes, which the
committed file does not use — regenerate, then restore single quotes, or the diff is all 279 lines.

Both sheets' white-pill labels are `canvas` (#131313 = the node's `bg/page`), not `onLightPrimary`
(pure black) — matching `CoachProfileCta` / `CoachProfileDateSlots` / `CoachProfileTimeSlots`.

The node types the coach name as Inter 12 while every other string in the sheet is a Season face;
Inter is Figma's fallback, so this maps to `captionSansRegular12` (same size, right family). The
name really is a step smaller than the role beneath it — that part is not a slip.

Confirm calls `scheduleAppointment` with the same free-discovery flags `setDateChanged` used to
hand to `coachBooking`, then the existing `onSuccess`. Age check uses
`CoachingServicesController.isOver17AgeCheckMark` + the existing over-17 toast. Settings button
omitted (same as §E.20).

New i18n: `coaching_confirm_session_title`, `_details`, `_duration`. Reused:
`coaching_coach_booking_confirmation_{date,time,cta_button}`, `coaching_coach_booking_header_text`,
`coaching_age_confirmation_check`, `coach_availability_onboarding_duration`, `a11y_close`.
## E.22 Built — Live Event Details (node 1034:8076 'Live')

Same file **`dpxPlTNW722Y1Qr8JRnSIn`**. Frame 402x1014 (taller than the viewport -> scrolls).
Extracted via the Figma MCP (`get_metadata` + `get_design_context` + `get_variable_defs`), with the
REST `/nodes` dump for the numeric spec sheet.

**Target: `src/screens/liveMeditation/liveMeditationDetails.js`** — route `liveEventDetails`
(`Dashboard.js` navigateTo switch), NOT `components/liveMeditation/LiveEventDetails.js`, which is the
Reels bottom-sheet variant and stays legacy. Gate: `isRedesignTheme(this.props.theme)` -> a new
`renderRedesign()`; the legacy render is the untouched variant-`b` path. Constant-dark via
`getCurrentThemeDarkMode`.

Sections (root is VERTICAL auto-layout, **40pt between every section**): hero 430 · event-content
(pad h24, inner gap 16) · countdown · event-actions (pad h24, gap 24) · host (pad l20 t12 r20 b28).

| Component | Figma node | What |
|---|---|---|
| `LiveBadge` | 1034:8082 | White pill, 8pt `accentLive` dot + Caption-12 `accentLive`, pad 10/6/12/6. **Extracted from `LiveFeaturedCard.renderBadge`** (byte-identical), now used by both |
| `LiveActionButton` | 1034:8096 | Full-width white pill h52 R-full, `buttonSansSemiBold16` on `onLightPrimary`. The node's `specialty.play.fill` child is `visible: false` — label-only centring at x=125 confirms it; do NOT render it |
| `LiveDetailsHero` | 1034:8078 (+ glow 1034:8077) | Radial coach glow, background-free cutout, `gLiveHeroScrim`, 40pt back chip (`surfaceScrim` + `borderSubtle`), badge pinned right |
| `LiveDetailsCountdown` | 1034:8091 | Gradient-hairline card: outer `gLiveCountdownBorder` at 1.5pt padding, inner `gLiveCountdownCard` **over `canvas`** |
| `LiveDetailsHostCard` | 1034:8101 / 1034:8102 | Glass card R16, 44pt avatar, `titleSansSemiBold14` name + Caption-12 role, Follow pill (`surfaceGlass` + `borderActive`). Store-connected like `LiveEventCoachItem` (`useCoachFollowingHook`) since the screen is a class component |

New tokens: `surfaceScrim` (black30, both themes) · gradients `gLiveHeroScrim`,
`gLiveCountdownCard`, `gLiveCountdownBorder` · font `titleSansSemiBold14` (14/18.2 — the node's
Inter SemiBold 14; the existing `authorNameSansSemiBold14` is 13.674/15.04 and does NOT fit).
New icon `action.chevronLeft.svg` (`action.` namespace, matching `action.chevronDown`).
New i18n `live_details_starts_in_label` ("Starts in", uppercased by `textTransform` per repo
convention) in all 7 locales.

### Two traps this screen cost time on — read before the next glass/glow screen

1. **A gradient "border" needs an opaque inner layer.** `gLiveCountdownCard` is white 12%->3.5%,
   so with only the border gradient behind it the gradient reads through the *whole card face*
   instead of a hairline. Put `backgroundColor: theme.colors.canvas` on the inner gradient.
   Verified after fix: fill 46->29 vs the node's 47->27.
2. **Do not reproduce a blurred fill's geometry literally.** The glow node is a 393x309 rect with a
   sheared radial and `LAYER_BLUR 94`. Built as an unblurred rect it draws a bright seam across the
   top of the screen, because only the blur hides that edge. Fit the *rendered* alpha field instead:
   sampling 10k pixels of the frame render where the cutout is transparent gave
   `cx .64 cy .32 rx 1.06 ry .68`, stops `.62 / .24@0.4 / 0`, RMSE 0.043, spanning the whole hero.

3. **A full-bleed screen must negate `Dashboard`'s wrapper padding.** `renderSecondaryView` pads
   its navigated-view wrapper by `Style.xPadding` (~21pt), so a hero built to the screen edge still
   leaves a band of the tab behind showing above it — measured as 21 flat rows of the parent's
   background before the glow began. Negate it with `marginTop/marginBottom: -Style.xPadding` on the
   screen container, exactly as this file's legacy `container` style already does.
   **Then pass the same value back to the header controls as an explicit `topInset`:**
   `SafeAreaView` inside that wrapper under-reports (41pt, not the device's 59), and it does NOT
   re-report once the margin lands — so without the inset the back chip jumps from the node's y=77
   up to y=56. The screen bleeds; the controls do not.

**Inside-stroke rule (applies to every node with `strokeAlign=INSIDE`):** Figma's stroke overlaps
the padding; RN's `borderWidth` adds to it. Every padding must be `figmaPadding - borderWidth` or the
element renders 2pt oversized. Cost 2pt on the host card and the Follow pill before it was corrected.

Simulator verification (iPhone 17 Pro, release build) — every element matches the node exactly:
back chip x20 y77 40x40 · reserve pill x24 354x52 · share row x137.3 127.3x32 · host card x20 362x72
· follow pill x301 69x33.7 · section gaps 24 and 52 · card fill (31,31,31) = white05 over canvas ·
follow fill (54,54,54) = white10 over the card · countdown h110.

⚠️ **`.claude/rules/accessibility.md` is now stale on one point.** It states that overlay screens
rendered through `Dashboard.renderSecondaryView` never surface their testIDs and must be driven by
label. On this screen **all six surfaced** (`liveEventDetails_screen`, `_backButton`,
`_reserveButton`, `_shareButton`, `_hostCard`, `_hostCard_followButton`) and tap-by-id worked —
the `accessible={false}` the wrapper gained since that note was written fixed it. Worth a docs pass.

Deviations (documented, raise to design): the node's ANGULAR countdown border is a 135° linear sweep
(react-native-linear-gradient has no angular mode); the legacy "N Aura Members Attending" line is
dropped (absent from the frame); reserved/join keep the same white pill (the frame specifies only
that one state), matching `LiveFeaturedCard`.

## E.23 Fix — LiveFeaturedCard photo mask + glow (node 800:7159 'LiveFeaturedCardV2')

Built in §E.8; re-verified against the node and corrected. Two values were guessed rather than read,
and both showed as a harsh dark ellipse ringing the coach instead of a soft dissolve:

| | was | node says |
|---|---|---|
| Photo mask geometry | `cx .5 cy .5 r .5` (a circle half the box) | `cx .5 cy .387 rx 1.013 ry .569` — a **wide** ellipse centred above the middle, from handles `[[.5,.3866],[.5162,.9551],[-.5124,.4138]]` |
| Photo mask stops | `1 @0 · .13 @.73 · 0 @1` | `1 @0 · 1 @**.503** · .13 @.801 · 0 @1` — full opacity is *held* to half the radius before it falls |
| Photo box | mask-group box, `resizeMode="contain"`, `absoluteFill` | its own box: `x 9.41` inside the mask, `326.77²`, `cover` (the node's imageTransform x-scale .916 squares a 741x679 source — identical to `cover`) |
| Glow | rect-bounded `cx .5 cy .5 r .5` at the node's 239x313 rect | full-card `cx .65 cy .25 rx .70 ry .60`, stops `.62 · .20@.4 · 0` — fitted to the node's render (30k px, RMSE 0.035), because that rect carries a **110pt LAYER_BLUR** |

⚠️ **Read a Figma gradient stop as `{position, color, alpha}`.** `#D9D9D9@0.5027` in a spec dump is a
stop at *position* 0.5027 with alpha 1 — not alpha 0.5027. Misreading it inverts the ramp.

The mask box, not the photo box, is what clips: the node's photo (326.77 wide) deliberately overruns
its 314.67-wide mask, and `MaskedView` reproduces that clip for free.

Attendee stack + count are wired from reservations and render only when the event has any; the
node's heart save-button stays omitted per §E.8.

## E.24 Built — Host/Coach Profile (node 3094:29630 'Profile')

Same file: **`dpxPlTNW722Y1Qr8JRnSIn`**. Frame 402x3350, dark-only, 11 sections. Gated on
`isRedesignTheme(theme)`; MCP `get_metadata` / `get_design_context` / `get_variable_defs` all read
this file fine (no REST fallback needed).

⚠️ **This is NOT the §E.6 screen.** §E.6 built `NewCoachDetails.js` (route `newCoachDetails`, the
Info/Coaching/About booking screen, node 846:6286). This node is the **content-first host profile**:
route **`expertDetails`** → `src/screens/NewCoachProfile.js` → `CoachProfileContentSelector` →
`CoachProfileFeed` (Animated.FlatList) with `NewCoachProfileHeader` as `ListHeaderComponent` →
`screens/experts/CoachNewProfileHeader`. Both screens exist and are reachable; coach *search* and the
Coaches tab route coaching-enabled coaches to `newCoachDetails`, while a track's author link routes
to `expertDetails`.

`NewCoachProfile` receives no `experiments` prop, so the gate reads the theme (correct per the
skill anyway) and swaps to `getCurrentThemeDarkMode(theme)` for the whole subtree.

| What | Figma node | Where |
|---|---|---|
| Backdrop | 3094:29631 | NEW `components/coachProfile/CoachProfileBackdrop.js` — renders the pre-baked `newDesign/coachBackground.png` (blur 50 + radial fade already composited), pinned to the top at its 402x1080pt aspect with `canvas` below. **The node's image is the forest photo, NOT the coach's portrait** — a first pass blurred the coach photo at runtime, which gave every coach a different background; the source photo is the same one the Coaches tab and coach-details screens use (mean top-region RGB 31,47,50 vs the asset's 29,44,41) |
| Glass nav | 3094:29634 | `NewCoachProfile.renderNewDesignNav` — 36pt `surfaceSubtle` circles (back `action.chevronLeft` / share `action.shareMessage`), absolutely placed so the backdrop reaches the screen top. Replaces the legacy `NavigationBar` in this branch only |
| Identity | 3094:29640 | `CoachNewProfileHeader.renderNewDesign` — 102pt avatar, `titleSerifRegular30` name, `copySansRegular14` role, country + flag at `textTertiary` |
| Following pill | 3094:29646 | same — full-width outline pill, `borderActive` (border/strong = white@40%, NOT `borderStrong` which is white@18%), `buttonSansSemiBold16`. Reuses the existing `handleFollowCoach` + `following`/`loading` state |
| Stats panel | 3094:29647 | same — R16 `surfaceSubtle` + `borderSubtle`, 3 flex cells (`displaySansRegular20` count / `captionSansRegular12` label), 1x32 `borderDefault` dividers. Order is Followers / **Favorites** / Plays (the legacy header had Followers/Plays/Favorites — the node's order won) |
| Coaching banner | 3094:29653 | NEW `components/coachProfile/CoachProfileCoachingBanner.js` — R24 `surfaceGlass` + 1.5px `borderDefault`; eyebrow `live_details_coaching_button`; serif "Work with {name}" with the name faux-italic; price from `CoachingServicesController.getPlanStartingPriceForCoach`; white CTA pill + `accentMint` dot + spots-left. Falls back to the waitlist CTA when `availableSlots < 1`, reusing `useCoachCoachingHook` |
| About | 3094:29669 | `CoachNewProfileHeader.renderNewDesign` — `displaySansRegular20` heading, `copySansRegular14` bio, 4-line clamp + "Show more" at `textTertiary` |
| Listen on | 3094:29681 | `podcast/AuraPodcastPlatformsListenCard` gained an additive **`isNewDesign`** prop — one R24 glass panel with bare 24pt brand glyphs instead of white circles. Keeps the existing `AppInfoStore.auraPodcastConfig` links + `Listen On` analytics |
| Section titles | 3094:29694 et al | REUSES the existing `logic/NewDesignSectionTitle.js` (`newDesignSectionTitleProps`) + `Heading`'s `newDesign` prop — spread at the live-replay heading, and `CustomTracks`' own `isNewDesignSectionTitle` gate extended with the coach-profile `sentFrom` |
| Card/Track | 3094:29705 | REUSES the existing `components/homeFeed/TrackCard.js`. Both of `CustomTrackItem`'s TrackCard gates (carousel + 2-col grid) had their `[Tabs.Home, Tabs.Sleep]` list extended with the coach-profile `sentFrom`; no new card and no new wiring |

New token: `surfaceBadge` (= `black20`) on both redesign themes. **`LockBadge` had drifted** — it used
`surfaceOverlay` (black@40%), which renders as an opaque grey disc; the Figma tag component
(`680:7225`, the same instance `LiveFeaturedCard` uses) specifies black@**20**% + a white@5% hairline,
which is what §E.2 documented in the first place. Fixed at the component, so both cards get it.

New i18n: `coaching_coach_profile_work_with` ("Work with {{name}}"). Everything else reuses existing
keys — `coaching_coach_details_cta_book_button`, `coaching_coach_details_starting_price_text`,
`coaching_coach_details_spots_left_text`, `aura_podcast_platform_listen_text`,
`coaching_coach_details_tab_about`, `a11y_share`.

New assets: `platform.spotify` / `platform.youtube` / `platform.applePodcast` SVGs exported from
Figma (3094:29684/29686/29688) into `src/img/newDesign/svg/` — the `platform.` namespace already
existed (`platform.apple`, `.facebook`, `.google`). **Regenerating `iconList.js` emits double quotes;
run `npx eslint --fix src/generated/iconList.js` after `npm run bundleIcons`** or you add ~560 lint
errors and a whole-file diff instead of a 4-line one.

⚠️ **Pre-existing follow bug fixed here.** `CoachNewProfileHeader` reads `userId` from props, but
`NewCoachProfileHeader` never passed it — so `Api.isFollowing`/`follow`/`unFollow` all keyed on
`undefined`, hitting `userFollowing/undefined/<coachKey>`: **one bucket shared by every user.** Any
coach followed through this screen then read back as "Following" for everyone, which is what made the
pill look stuck. Now passes `getProfile()?.id`, and both functions bail when either id is missing so
nothing can write to that bucket again. Verified round-trip in the sim (Follow → Following →
persists across re-entry). Note the Me › Coaches list is populated from `CategoryController` saved
categories, a **different** store than the Firebase follow records, so the two can legitimately
disagree — don't use that list to judge follow state.

⚠️ **The `sentFrom` gate trap — this cost the most time.** `CustomTrackItem`/`CustomTracks` are shared
by 20+ surfaces with no 4.0 frame, so the card branch gates on experiment AND surface
(`COACH_PROFILE_SENT_FROM = 'expertDetailsNewUI'`, in NEW `src/logic/newDesignSentFrom.js`). But:
- **`RecommendationRow` forwards its `selectedTab` prop as the CustomTracks `sentFrom`** — it ignores
  its own `sentFrom` prop entirely. `CoachProfileFeed` was passing only `sentFrom`, so the gate saw
  `undefined` and silently never fired (the section titles restyled, the cards didn't). Fixed by also
  passing `selectedTab={'expertDetailsNewUI'}`, which additionally fixes the row's analytics `Tab`.
- **`theme` was never passed to `CustomTracks` at all** (it keeps its own ThemeStore state). Both
  gates read `this.state.theme`, which is already the swapped redesign theme, so no prop threading is
  needed — `RecommendationRow` forwards `theme` only for the row's own use.
- **Don't pass the caller's `containerStyle` into `TrackCard`** — the legacy container styles carry
  the old card's geometry and stretched it to ~455pt. `TrackCard` owns its own width and row gap.

⚠️ **This screen was first built against a 41-commit-stale checkout, and three pieces of it were
already solved upstream: `homeFeed/TrackCard`, `logic/NewDesignSectionTitle` + `Heading`'s `newDesign`
prop, and `homeFeed/PodcastCard`. All three were duplicated and then discarded on rebase. Fetch before
surveying what exists — the reuse check in Phase 3 is only as good as the base it runs against.** On a
case-insensitive filesystem the duplicate also collided: `newDesignSectionTitle.js` and upstream's
`NewDesignSectionTitle.js` are the same path, so git tracked both casings until one was force-removed
from the index.

Verified in the sim (iPhone 17 Pro, variant `a` — note the branch already carries a pre-existing
`FORCE_AURA_DESIGN_CHANGE = true` in `ExperimentHelper.js`, marked "Do not commit", which is what
forces variant `a` locally). Confirmed on two coaches (one with coaching enabled, one without) that
the identity block, stats, Following pill, coaching banner, About, section titles and Card/Track all
match the node — and that Home/Sleep track rows still render the legacy card, so the `sentFrom` gate
holds.

Deviations (documented, raised to design):
- **Rating "★ 4.9 (120)" omitted** from the banner — same finding as §E.6: no rating or review-count
  field exists anywhere in `CoachesStore` or the coach model. The eyebrow row renders without it
  rather than inventing numbers.
- **Serif italic is synthesized.** The node uses `Season Serif Regular_Italic` for the name in "Work
  with *Lauren*"; only the upright face is bundled, so iOS obliques it. Bundle the italic face to
  match exactly.
- **The "1:1 COACHING" eyebrow renders in Season Sans, not Inter.** Node 3094:29655 is the only text
  node in the frame that specifies Inter; every other 12pt label in it resolves to the frame's own
  `Caption 12` (Season Sans) variable, so this reads as a stray override rather than intent. The
  ladder would need a 12pt token that duplicates `captionSansRegular12` for one uppercase
  label. Kept on `captionSansRegular12` with the node's 0.72 tracking; confirm with design.
- **The screen bleeds to both edges.** `Dashboard.renderSecondaryView` pads every navigated view by
  `Style.xPadding` top and bottom; `isFullScreenView` now also covers `expertDetails` under
  `isRedesignTheme`, so the backdrop reaches the status bar and the feed reaches the home indicator.
  Legacy keeps the padding it relies on. **This padding also silently offsets safe-area math** — the
  nav and identity offsets measured off the node (17pt / 99pt) were landing ~23pt low until the
  bleed went in, so check it before trusting any inset you compute on an overlay route.
- **Section list is server-driven.** The mock shows Live sessions / New / Stress Relief for Sleep /
  Sound Bath; the live feed renders whatever `useFeedForCoachHook` returns for that coach
  (Breathwork, Affirmations, Mindfulness, …). Structure verified, not the mock's exact rows —
  consistent with the "restyle, keep data + order" decision.
- **Podcast + Listen-on only render for coaches with an Aura Podcast episode** (`PodcastController`
  gate, unchanged). The `Card/Podcast` node itself was left on the legacy `AuraPodcastCard` — only
  the row heading and the Listen-on panel were restyled. Building the 4.0 PodcastCard is the one
  section of this frame not yet matched.
- **No tab bar.** The mock draws the app tab bar; `expertDetails` is an overlay route rendered above
  it, so it is app chrome here, not part of this screen.
- **Filters row** (sticky, legacy `CoachProfileFilters`) has no counterpart in the node; left as-is.

## E.25 Built — Guest Pass share sheet (4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, frame `Guest Pass`)

Target: `GuestPassShareModal` + `GuestPassReferralPage` `isRedesignTheme` branch → presentational
`GuestPassSheet` (constant-dark via `getCurrentThemeDarkMode`). Legacy modal/page render paths stay
as the variant-`b` fallback. Same share handlers / testIDs (`guestPassReferral_sendTextButton` etc.).

| What | Figma layer | Where |
|---|---|---|
| Sheet chrome | `Guest Pass · Sheet` | NEW `components/guestPass/GuestPassSheet.js` — canvas, R32 top corners, 40×5 handle (`textMuted`), 36pt glass close, 20/12/20 content pad, 16 gap |
| Ticket card | `guest-pass-card` | NEW `components/guestPass/GuestPassTicketCard.js` — 332pt, R32 `surfaceSubtle`, SVG radial plum wash, exported ring PNG, 30-DAY badge, Serif-25 title, canvas-colored notches, dashed SVG perforation |
| Share pills | `Button` ×3 | Sheet-local 60pt white pills, `titleSansSemiBold18`, exported 24pt glyphs in `GuestPassSheetIcons.js` (`header.message` / `profile.email` / `community.link` instances — not the shared Icon set, whose glyphs differ) |

Deviations (documented, raised to design):
- **Header title `Guest Pass` is in the node with fill hidden** — not rendered.
- **Button face is Season Sans Medium 18**, which is not bundled; `titleSansSemiBold18` is the nearest cut (18/22.1).
- **Inner shadows on the ticket omitted** (RN has no inset shadow).
- **Notches are canvas-colored punches**, not a clip of the sheet through the card.

## E.26 Built — Notifications / Message Center (4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, frame `Notifications`)

Target: `CfMessagesScreen` (`cfMessagesScreen`, inbox button `dashboard_inboxButton`). `isRedesignTheme` branch; legacy Header + `AuraPodcastCard` stay as the variant-`b` fallback. Same fetch / `inbox_message_*` / `messagesScreen_screen` / tap → `webViewer`.

| What | Figma layer | Where |
|---|---|---|
| Canvas + navbar | `Notifications` / `navbar` | Screen-local `renderRedesign` — `canvas`, 62pt bar, 36pt `surfaceSubtle` back (`action.arrowLeft` 24pt), title `bodySansRegular16`. Dashboard overlay back hidden + `isFullScreenView` so the canvas reaches the status bar. |
| Section heading | `New Podcast from Aura` | Message `item.title`, `displaySansRegular20` / `textPrimary`, 24pt gap to the card |
| Podcast card | `PodcastCard` FILL × 264 | `homeFeed/PodcastCard` `layout="feed"` via `MCAuraPodcastCard` — `borderDefault`, shadow `#190C26@0.06`, light-theme title/provider. Tag glyphs use `textPrimaryOnColorMain` (always on a dark overlay). |

Deviations (documented, raised to design):
- **`Plans` block omitted** — leftover coaching copy in the frame (`1hr, 1-4 sessions/mo` / `$149`); inbox data has no matching row.
- **Tag icon is `home.podcast`**, not the frame's `home.story` instance swap on a Podcast chip.
- **Duration copy is `{{count}} min` / `mins`** from the existing plural key; the mock is always `55 min`.
- **Lock follows `isUserPremium`**, not always-on as in the mock.
- **Other message types** (live, coach, track, …) keep their existing cards with the new screen chrome.

## E.27 Built — Program detail (4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, frames `Program` / `Program/Locked`)

Target: `screens/programs/ProgramDetails.js` (`programDetails`). `isRedesignTheme` branch; the legacy
`Header` + `ProgramDescription` + `ProgramSections` render path stays as the variant-`b` fallback, and
every handler is reused (`handleExitNav`, `handleCtaForPlayNext`, `handlePlayTrack`, `getCtaState`).
The two frames are one screen: Locked only drops the progress track, puts `bullet.lock.s` in the pill,
and changes the CTA label — all already driven by `isUserPremium` / `programProgress`.

| What | Figma layer | Where |
|---|---|---|
| Backdrop | `Frame 1193` / `image 23` | Screen-local — the cover through RN `Image` `blurRadius`, `gHomeBackdrop` over it. Inside the scroll content, so it travels with the page. `programDetails` added to Dashboard's `isFullScreenView` so the canvas reaches both edges. |
| Header row | `Header row` | `components/programs/ProgramDetailHeaderRow` — 36pt `surfaceSubtle` chips, `action.chevronLeft` + the caller's favourite slot |
| Favourite chip | `close-btn` (a bookmark, despite the layer name) | `FavoriteProgramsSeries newDesign` — additive branch, store logic untouched |
| Hero | `hero` | `components/programs/ProgramDetailHero` — same cover treatment as `homeFeed/ProgramCard` (gem path, radial glow, `gCardScrim`) plus the length pill and progress track |
| CTA | `ActionButton` | `liveMeditation/LiveActionButton` reused verbatim — same Figma component, `specialty.play.fill` is `visible: false` on both screens |
| Learn | `learn` | `components/programs/ProgramLearnSection` — clamped intro + Show more, two-column stat grid on `accentSky` glyphs |
| Section heading | `SectionHeader` | `components/programs/ProgramSectionHeading` — title only; `view-all` is `visible: false` on every instance |
| Coaches | `featured-coaches` | `components/programs/ProgramCoachAvatars` — 64pt avatars, gap 20 |
| Content sections | `cultivating` ×2 | `components/programs/ProgramContentSection` — drives the existing store-connected `Tracks` (`newDesignProgramRow`), so track loading is unchanged |
| List row | `ContentListRow` | `components/homeFeed/ContentListRow` — registry §A "Content List row card → NEW ListRowCard". Trailing slot takes the caller's overflow control |
| Overflow | `action.more.fill` | `ListenLaterMenuIcon newDesign` — additive branch swapping `ThreeDot` for the exported glyph |

New tokens: `accentSky` (#69A6C8 — stat glyphs + the Show more link) and `progressTrack` (white25),
both in `redesign/palette.js` + both `color.js` files. No new icons — all eleven already shipped with
the foundation icon set. No new i18n keys.

Deviations (documented, raised to design):
- **The pill is uppercased by `textTransform`**, per repo convention: the mock's copy is literally
  `25-DAY PROGRAM` while `program_details_program_length_text2` is sentence case.
- **The backdrop blur is `Image blurRadius`, not the mock's 100pt layer blur** on a 4.4×-scaled copy —
  RN has no layer-blur filter; the radial black mask becomes `gHomeBackdrop`.
- **The pill fill is `surfaceScrim` (black30), the node is black@0.28.** Semantically the same
  chip-over-hero-imagery token the Live hero uses; a `black28` step for one pill is not worth it.
- **Section heading is 2 lines and the section description clamps to 3 with tap-to-expand.** The mock's
  copy is short enough to need neither; real programs run to 10-line descriptions and 60-char titles.
  The clamp mirrors the legacy `ProgramSections` behavior.
- **The overflow "…" only renders for premium users**, since `Tracks` builds it behind `premium`. The
  mock shows it on the Locked frame too — showing it to free users would be a behavior change, not a
  restyle, so it is left alone.

Shared with the legacy path: `logic/ProgramStats.js` now owns the content-type → label switch, so the
two designs cannot drift on the stat copy; `ProgramDescription` keeps only its own PNG icon map.

## E.28 Built — Celebrity Masterclass detail (adapted from `Playlist/Podcast`)

**No 4.0 frame exists for this screen.** Searched the whole file — Current page to depth 3, To be
done, Archive — zero `celeb` / `masterclass` hits. At the user's direction it is built against the
`Playlist/Podcast` section's `Podcast` frame (878:6831) as the reference pattern, since both are
media-detail screens. Treat every row below as an ADAPTATION, not a spec match, and re-verify if a
real frame ever lands.

Target: `screens/celebrity/CelebrityMasterClassDetails.js` (`celebrityMasterClassDetails`).
`isRedesignTheme` branch; the legacy `CelebrityMasterClassDetailsHeader` + `VideoTrackCard` path stays
as the variant-`b` fallback. The `FlatList` is kept (not swapped for a ScrollView) so the existing
`itemsToRender` / `onEndReached` pagination is untouched.

| What | Figma layer (Podcast frame) | Where |
|---|---|---|
| Canvas + wash | `hero-wash` ellipse | `components/celebrity/CelebrityDetailHero` — SVG radial on `canvas` |
| Header row | `Header row` | `programs/ProgramDetailHeaderRow` reused, no right slot |
| Cover / eyebrow / title / description | `meta` | `CelebrityDetailHero` — 180pt R32 cover, uppercased eyebrow, serif title, centred body |
| Primary CTA | `actions` / `ActionButton` | `LiveActionButton` |
| Watch Trailer | `actions` / `secondary` | `LiveActionButton variant="secondary"` — new additive variant + `iconName` |
| Host row | `host` / `PersonRow` | `CoachFollowCard newDesign` — additive branch, follow state untouched |
| Episode rows | `ContentListRow` | `homeFeed/ContentListRow` via `Tracks newDesignListRow` |
| Section headings | `ep-header` | `programs/ProgramSectionHeading` reused |

New tokens: `heroWashPrimary` / `heroWashSecondary` (violet + steel, the wash's radial stops). No new
icons, no new i18n keys — `celebrity_master_class_card_tag`, `aura_podcast_page_episodes_header`,
`masterclass_details_about_title` and `masterclass_details_watch_trailer_button` all existed.

Deviations (documented, raised to design):
- **The 180pt square cover replaces the legacy 504pt full-bleed portrait.** That is what the podcast
  frame prescribes, but podcast/playlist artwork is square by nature and a celebrity portrait is not —
  worth confirming before this ships.
- **`Filter` / `Sort` in `ep-header` are omitted** — the masterclass screen has no such affordance and
  adding one would be a behavior change.
- **Row meta is `contentType · duration`**, matching the Program screen, where the podcast frame shows
  `duration · date`; masterclass episodes carry no publish date.
- **`Save` / `Download` become a single `Watch Trailer`** — the masterclass screen's real secondary
  action; it renders only when a trailer track exists.
- **The wash has no layer blur** (RN has no layer-blur filter); the radial falloff carries it.
- **Bio and About clamp to 4 lines with tap-to-expand**, mirroring the legacy behavior; the frame has
  neither block.

Promote-to-shared follow-up: `LiveActionButton`, `ProgramDetailHeaderRow` and `ProgramSectionHeading`
now each serve two or three screens from a feature folder they no longer belong to. Worth one rename
PR once the next screen needs them, rather than churning three call sites per screen.

## E.29 Built — Podcast + Playlist detail (4.0 section `Playlist/Podcast` 878:6783)

Both frames are one layout: `hero-wash` + `meta` + `actions` + `episodes`, differing only in which
optional parts appear. Built as `isRedesignTheme` branches on `podcastsDetail`
(`screens/podcasts/PodcastsDetail.js`) and `playlist` (`screens/playlists/playlist.js`); both keep
their existing `FlatList` so pagination and drag state are untouched.

Three components were promoted out of feature folders now that three screens share them:
`programs/ProgramDetailHeaderRow` → `homeFeed/DetailHeaderRow`, `programs/ProgramSectionHeading` →
`homeFeed/SectionHeading`, and `celebrity/CelebrityDetailHero` → `homeFeed/MediaDetailHero` (which
gained the Playlist frame's optional `subtitle`). This is the rename E.28 deferred.

`Tracks` gained ONE shared `renderNewDesignListRow`, replacing the celebrity-only branch from E.28 —
every 4.0 list screen now routes through it before the per-screen card branches. Meta reads
`duration · date` when the caller passes `customContentType` (podcast) and `contentType · duration`
otherwise, which matches all three frames exactly.

| Frame part | Podcast | Playlist |
|---|---|---|
| eyebrow | `content_card_type_podcast` | — (no equivalent; see deviations) |
| subtitle | — | `playlist_detail_author_suffix` → "Made for Alex" |
| primary | Play | Play |
| secondary | — | Shuffle + Add track |
| host row | `CoachFollowCard newDesign` | — |
| `Episodes` heading | yes | — |

Deviations (documented, raised to design):
- **Podcast has no Save / Download**, which the frame shows: `PodcastsDetail` has no such actions and
  adding them is behavior, not styling.
- **Playlist's secondary pills are Shuffle + Add track, not the frame's Save / Download** — you cannot
  "save" your own playlist; those are the screen's real actions.
- **Playlist has no eyebrow row.** The frame's "MADE FOR YOU · WEEKLY" belongs to a generated mix; a
  user playlist has no equivalent field.
- **`Filter` / `Sort` in the podcast `ep-header` are omitted** — same reasoning as E.28.
- **`TrackMenu` is not reused for the playlist row's overflow**: it is `position: absolute`, so it
  cannot sit in the row's flex slot. The row renders `action.more.fill` and calls the same handler.

⚠️ **Unresolved: which screen the `Playlist` frame targets.** Its copy reads as the Made-For-You mix
("MADE FOR YOU · WEEKLY", "Your Discover Aura"), which in the app is `madeForYouMix`, NOT `playlist`.
But "Made for Alex" is `playlist_detail_author_suffix`, a user-playlist string. Built against
`playlist` on the section's name; confirm with design before shipping, and re-point to
`MadeForYouMix.js` if the mix was intended — the branch moves as-is.

⚠️ **The Playlist branch is unverified on device** — the test account has no playlists, so only the
Podcast screen was driven end-to-end in the simulator.

The section's third frame, `Track Sheet` (882:7197), is the track options modal and is NOT built.

## E.30 Built — Live Replay "Continue watching" (4.0 `Live — Replay` 800:7223 / `ContinueWatching` 800:7260)

The section did not exist in any form — searched every locale, every Live screen/component, and
`git log --all -S` (the string had never appeared in any commit on any branch). So this is a new
feature, not a restyle, and it needed a data source as well as a card.

| Piece | Where |
|---|---|
| Card | `components/liveMeditation/LiveContinueWatchingCard` — `surfaceGlass` + `borderSubtle`, R24; 180pt cover with a centred 24pt `specialty.play.fill`; 3pt progress hairline (`surfaceSubtle` track, white fill); meta block pad 16/14/16/16, gap 4 |
| Heading | `LiveSectionHeader` reused |
| Section | `LiveMeditationReplayList.renderContinueWatching`, between the filter chips and the first date section — redesign branch only |
| Progress store | `logic/LiveReplayProgress.js` — AsyncStorage, single most-recent entry |
| Capture | `LiveEventReplay.onProgress` (throttled to 5s) + `componentWillUnmount`; `onEnd` clears it |
| Resume | the card passes `startAt`; `LiveEventReplay.onLoad` seeks once duration is known |

**This adds behavior, not just UI** — playback position is now persisted. Only one entry is kept
(all the card shows) and it is filtered out when: under 15s watched, under 30s remaining, older than
30 days, or the replay is no longer in the tab's loaded list. Those thresholds keep the card from
offering a session the user did not meaningfully start, already finished, or forgot about.

New i18n `live_replay_continue_watching_title` and `live_replay_minutes_left_{one,other}` in
`live.json`. ⚠️ **The six non-English locales carry the English string** — `i18next-scanner` fails to
load its config on this checkout, so the keys were propagated by hand; they need real translations
before release.

Deviations: the cover is the coach photo (a live event has no separate artwork), and the mock's
gradient behind it is the photo itself.

## E.31 Built — "Introduce yourself" sheet (4.0 `Introduce Yourself (Profile Picture) - Dark` 3184:15492)

Target: `screens/liveMeditation/LiveMeditationProfileUpdate.js`, opened from
`LiveMeditationStore.checkForProfilePicture` when a user reserves a live spot with no profile photo.
`isRedesignTheme` branch; the legacy sheet stays as the variant-`b` fallback and every handler
(`close`, `uploadPhoto`, the ProfileStore listeners) is reused.

The screen previously hardcoded `getIndividualStyle('Clean Light')` into state, so it rendered light
on every theme. The redesign branch reads `getCurrentThemeDarkMode(this.props.theme)` instead; the
legacy path keeps its own `state.theme` untouched.

| Piece | Spec |
|---|---|
| Overlay | `surfaceOverlay` (black40) |
| Sheet | radius [32,32,0,0], `borderSubtle` top hairline, surface = `gCoachSheet` |
| Drag handle | 40x5 R3 `borderActive`, pad 10/6 |
| Close chip | 36pt `surfaceSubtle` circle, `action.close` 20pt, pad h20 v8 |
| Content | pad h20 / t8 / b40, gap 24 |
| Avatar | 102pt circle `surfacePlaceholder` + the repo's dummy silhouette, `action.addDefault` badge |
| Title | `displaySansRegular25`, centred |
| Body | `bodySansRegular16` on `textOnColorMuted`, centred, pad h20 |
| CTA | `LiveActionButton variant="secondary"` with `borderWidth: 0` |

New token `surfacePlaceholder` (white20). No new icons, no new i18n keys.

Deviations (documented, raised to design):
- **The frame's left `settings-btn` is not rendered** — that node carries `opacity: 0`, and the
  frame's own render shows only the close chip.
- **`gCoachSheet` stands in for the mock's 200pt background blur** — RN cannot sample the window
  behind a sheet; same substitution, and same rationale, as the coach sheet.
- **The secondary pill is borderless here.** This instance carries no stroke, unlike the Live
  secondary pill, so the caller passes `borderWidth: 0`.
- **The avatar silhouette is the repo's `dummyProfileIcon` (which includes a "?")**; the mock draws a
  plain person. Kept for consistency with the placeholder used everywhere else in the app.
- **Sheet height renders ~30pt shorter than the mock's 394pt.** Partly the avatar group (the mock's
  badge overhangs its box), partly the negative bottom margin on the legacy container the branch
  still shares. Not chased down — flag if design wants the exact inset.

⚠️ Dismissing this sheet chains straight into the **Reservation Confirmed** modal, which is still
legacy light. That frame IS designed (`Various` 3184:14945 / 3184:15044) and is not built.

## E.32 Built — "Reservation Confirmed" sheet (4.0 `Reservation Confirmed` 3184:14945)

Target: `screens/liveMeditation/LiveMeditationCalendarShareModal.js`, opened by
`Actions.openLiveCalendarShareModal` — including straight after the E.31 introduce sheet is
dismissed, which is how the legacy light sheet was surfacing right after a redesigned one.
`isRedesignTheme` branch; the legacy sheet and the separate `useComponentDesign` path both stay
untouched, and every handler is reused (`sendMessage`, `shareToStory`, `shareEvent`,
`addToCalender`, `closeModal`).

Same sheet chrome as E.31 — handle, close chip, `gCoachSheet` surface, R32 top, `borderSubtle`
hairline. Content pad h20 / t8 / b40, gap 24 throughout:

| Block | Spec |
|---|---|
| Title | `displaySansRegular25`, centred |
| Invite | `labelSansLight18` + the existing `inviteFriends.png` avatar trio |
| Share row | three 60pt gradient circles, 28pt glyph, `copySansRegular14` labels, cells 95 wide |
| Divider | `borderDefault` hairline |
| CTA | `LiveActionButton variant="secondary"` + `bullet.calendar`, `borderWidth: 0` |

New gradients `gShareMessage`, `gShareStory`, `gShareEvent` (brand colours, so identical in both
frames). Icons `header.message`, `action.shareMessage`, `bullet.calendar` already existed; the
Instagram glyph stays the repo's existing PNG, since it is a brand mark rather than part of the 4.0
icon set.

Note the screen's handler is `addToCalender` and its flag `addToCaleder` — both pre-existing
misspellings. The branch matches them rather than renaming, which would be churn in a UI change.

Deviations: as E.31 — the frame's left `settings-btn` (opacity 0) is not rendered, and `gCoachSheet`
stands in for the 200pt background blur.
## E.36 Built — Channel / music-topic detail (adapted from `Playlist/Podcast` 878:6783)

Target: `screens/ChannelDetails3.js` (route `channelDetails3`) — **this is the screen behind QA
item 12 "Single playlist view still old design"**, not `MelodiousMusicDetails`. The earlier
ap2 attempt (reverted in `57e5d53e6`) patched `MelodiousMusicDetails`, which nothing navigates
to: `musicChannelDetails` has zero call sites in `src/`. The live screen is reached from
`MelodiousMusicTopics.handleOpenTopic` (a music topic tile such as "Jazz", `source: 'music'`),
and from `MyStations` / `Station` / the new-coach `InAppModal` for the channel variant.

`isRedesignTheme` branch; `play()` and every legacy renderer stay untouched, and every handler is
reused (`handlePlayTrackOnPress`, `handleShufflePlay`, `handleSubscribeToChannel`, `onEndReached`,
`onMomentumScrollBegin`). No new component — the branch is assembled from E.27/E.28/E.29's
`DetailHeaderRow`, `MediaDetailHero`, `LiveActionButton` and `Tracks newDesignListRow`.

The screen has two variants and one layout, differing only in which hero parts appear:

| Frame part | Music topic (`source: 'music'`) | Channel |
|---|---|---|
| eyebrow | — (a topic has no counts) | `track_follow_count_label_text` · `track_play_count_label_text` |
| title | `channel.channelName` | `channel.channelName` |
| subtitle | — | `channel.authorName` |
| description | — | `channel.channelDescriptionLong` |
| primary | Shuffle Play (`player.repeat`) | Follow (`specialty.plus`) / Following (`control.check`, secondary) |

What QA's three sub-items were, and where each came from:

- **Cyan Shuffle Play pill** — `styles.subscribeButtonContainer` under a `LinearGradient` of
  `this.buttonActive`. Now `LiveActionButton`, the same white pill the other three detail screens use.
- **Small square thumbnails** — `Tracks` fell through to `renderListWithReuseableComponents` →
  `ListSmall`. Now `ContentListRow` via `newDesignListRow`.
- **Rows flush to the bezel** — the screen *does* pass
  `containerStyle={{ paddingHorizontal: 20 }}`, but `ListSmall` is rendered with a hard-coded
  `contentContainerStyle={{ marginTop: 24 }}` (`Tracks.js:1100`) and never forwards it. Worth
  knowing before anyone tries to fix a legacy row's padding from the call site again — it cannot
  work. The 4.0 branch pads the row wrapper instead.

Two Dashboard changes, both redesign-gated: `channelDetails3` joins the full-bleed list (the branch
paints its own canvas and the 62pt status-bar inset), and `renderBack` returns null for it, since
the header row carries its own back chip. `viewsWithOverlayingBackgrounds` keeps `channelDetails3`
for the legacy branch's white arrow. `channelDetails3` is also added to
`StatusBarAppearance.CONSTANT_DARK_VIEWS`, where every sibling 4.0 detail screen already is — the
list is the declaration that a view renders dark whatever the theme, and this one now does. Note
this is a declaration fix, not an observed defect: on a light theme the glyphs came out white on
device even without the entry, though `barStyleForSurface` reads as if they should not have.

Verified on device (iPhone 17 Pro, 2026-09-03), dark and light theme: the hero cover renders from
`topic.imageUrl`; one back chip, returning to the topic grid with the Music pill still selected; no
white strip top or bottom; pagination past the first 10 rows; and the row inset measured at 20.3pt
against 0pt before (`scaledDimension(20)` on a 402pt device).

Deviations:
- **Shuffle uses `player.repeat`, which reads as an infinity loop.** The icon set has no shuffle
  glyph; E.29's playlist Shuffle pill uses the same icon, so the two stay consistent. Design to
  supply `player.shuffle`, or drop the glyph on both.
- **The description's read-more toggle is dropped.** The frame's `meta` block is a centred 3-line
  cap, which is what `MediaDetailHero` renders; `channel_details_more_text` /
  `channel_details_less_text` are unused on this branch (still used by the legacy one).
- **`newCoachDesign` is dead** — hardcoded `false`, never set, so the legacy screen only ever
  rendered the flush-row variant and the branch has no equivalent to port.
- **`handleSubscribeToChannel` only toggles local state**; it does not call `subscribeToChannel`.
  Pre-existing on the legacy button, reused unchanged rather than fixed inside a UI change.

⚠️ **`MelodiousMusicDetails.js` is left alone deliberately.** It is unreachable (no `navigateTo`
for `musicChannelDetails`) and predates the theme system. Restyling dead code is the trap the ap2
revert already fell into once.

⚠️ **E.29's Playlist branch is unreachable in the 4.0 build — the frame is on the wrong route.**
It was built onto `playlist` (`screens/playlists/playlist.js`), but every entry point a current
account can reach navigates to `newPlaylistDetails` (`screens/NewPlaylistDetails.js`, no
`isRedesignTheme` branch): `ProfileListPlaylistItem`, `PlaylistPlayButton`, `UserPlaylistItem`,
`CLPlaylistRowItem`, `PlaylistCreate` and `PlaylistStore`. `playlist` is reached only from
`playlistItem` inside route `playlists`, which is reached only from `Profile2.openPlaylists` — and
`Profile2` hands off to `Profile3` whenever `UserController.isMeTabAndPlaylistTopPart()` is true,
i.e. for every account whose `joinedVersion >= 3.18.112`. (`MyPlayListHomeTabCard`'s call site is
dead: nothing imports `MyPlayListHomeTabs`.) This is why E.29 recorded the Playlist branch as
"unverified on device". Resolving it needs the design answer E.29 already flagged — whether the
`Playlist` frame targets the user playlist, `madeForYouMix`, or both — so it is reported rather
than guessed; porting the branch to `NewPlaylistDetails` would also have to keep that screen's
rearrange/delete edit mode, which `TrackListAndControls` owns.

## E.37 Built — The remaining playlist screens (QA follow-up to item 12)

A route-by-route audit rather than a single frame. Every Dashboard route touching a
playlist, podcast or track list was mapped to its component, checked for an
`isRedesignTheme` branch, and traced to its `navigateTo` call sites to decide whether a
**current** account can reach it — `Profile2` hands off to `Profile3` for every account
with `joinedVersion >= 3.18.112`, so anything only `Profile2` links to is legacy-only.

| Route | Screen | Reachable in 4.0 from | Result |
|---|---|---|---|
| `newDownloadTracks` | `NewDownloadTracks` | Me tab row, Categories | **Built** — `RedesignTrackPlaylist`, `showDownload={false}` |
| `newListenLater` | `NewListenLater` | Me tab row, Categories | **Built** — `RedesignTrackPlaylist` |
| `newPlaylistDetails` | `NewPlaylistDetails` | Me tab Playlists rows, create flow, coach playlists, Categories | **Built** — inline, the `Playlist` frame |
| `mfyDetails` | `MFYdetails` | Home Made-For-You card (`MadeForYouItem`), Me tab MFY row | **Built** — inline, the `Playlist` frame |
| `viewAllUserPlaylist` | `ViewAllUserPlaylist` | coach profile → Playlists → View all | **Built** — the view-all pattern |
| `podcastsDetail`, `allAuraPodcasts`, `newRecentTracks`, `newSavedTracks`, `listenLaterList` | — | — | already redesigned |
| `playlist`, `playlists`, `playlistDetail`, `createPlaylist`, `playlistAddTracks`, `savedMeditations`, `recentlyPlayedSelector`, `madeForYouMix` | — | `Profile2` only | legacy-only, untouched |
| `newCreatePlaylist` / `playlistCreate` | `NewCreatePlaylist` | Me tab "Create playlist" | not a list — a create-form sheet; not built |
| `homeTabsPodcasts` pill | `PodcastsTab` (via `Screen.js`) | Home category pill row, English only | not built — one of ten category-pill screens with no branch (only Feed, Grid, Programs, Music have one); a category-screen pass, not a playlist gap |

**`MFYdetails` resolves E.29's open question.** The `Playlist` frame's copy — "MADE FOR YOU ·
WEEKLY", "Your Discover Aura", "Made for Alex" — is this screen: `made_for_you_weekly_playlist_text`
as the eyebrow, `topic.madeForYouMix.title`, `made_for_you_made_for_you_subtitle_text`, the weekly
description, the mix artwork as the cover, and Save / Download as the secondary pills — exactly the
frame's, and exactly the options `TrackListAndControls` offers for `CATEGORIES.MFY`. `MadeForYouItem`
sends current accounts here; the 813-line `MadeForYouMix` it also names is legacy-only.

`NewPlaylistDetails` carries the user-playlist reading of the same frame, ported from E.29's
unreachable `playlist` branch: cover `playlist.cover || trackImages[0]`, eyebrow
"Playlist · N tracks" (the Me-tab row's meta — E.29 had none, and this is its equivalent), byline
`playlist_detail_author_suffix` ("by {name}", a coach's name from `CoachesStore`, the user's own
from `UserController.getCurrentUserName()`, none for another member's), Play + Shuffle, then
Add track for the owner or Save for anyone else, and `ContentListRow` rows. The `...` chip opens
the existing info sheet. Playback mirrors `TrackListAndControls.onPlayTracks` for this screen —
same queue, same player options — with shuffle only reordering the queue.

Both `MFYdetails`' Save / Download pills live in a child component (`MfyRedesignActions`) so
`useCategoryDetailsHook` / `useCategoryDownloadHook` mount with the mix id resolved; at the
screen's top they would seed on `mf_undefined` and misreport the mix as unsaved until the next
profile change. `NewPlaylistDetails` has the id from its route props, so its hooks sit at the top.

New assets: `tile.downloadTracks` and `tile.listenLater` are the `specialty.*` glyphs' geometry
scaled ×3 onto the 72 viewBox with the stroke held at 2 on-canvas (`0.667` inside `scale(3)`) —
the weight the two existing tiles carry; the unscaled specialty icons are 3× heavier
(a9af64b55). `gDownloadTracksTile` / `gListenLaterTile` follow the two existing tile tints —
same ramp and value, hue-rotated so the four artwork-less playlists read apart. Design to confirm
the hues. `RedesignTrackPlaylist` gains `showDownload` (default `true`; the two existing callers
are unchanged) because a Download pill on the Downloaded-tracks list is circular, which the
legacy list also excludes.

Dashboard: the five routes join the redesign full-bleed list; all five already returned `null`
from `renderBack`. `StatusBarAppearance.CONSTANT_DARK_VIEWS` gains them plus `newRecentTracks`
and `newSavedTracks`, which E.29's siblings had missed.

Deviations:
- **Rearrange mode stays on the legacy list.** `DraggableFlatList` owns the reorder / delete
  gestures and the frame has no equivalent, so `NewPlaylistDetails` hands back to the legacy
  render while `isEditMode` and returns once the changes save. A visible jump, documented rather
  than hidden. The private-playlist and influencer-preview variants have no frame and stay
  legacy too, as do the info / edit / add-tracks sheets the `...` chip opens.
- **Shuffle uses `player.repeat`** — as E.29 and E.36; there is still no shuffle glyph.
- **Save always draws `action.bookmark`** and toggles its label; `action.bookmark.fill` carries
  its own fill, which `LiveActionButton`'s stroke-only recolour would not reach.
- **`ViewAllUserPlaylist` keeps `NavigationBar`** with the glass-chevron swap, matching
  `ViewAllRecentlyPlayed` rather than the detail screens' `DetailHeaderRow`. It also drops a
  hardcoded `'Playlists'` title for `me_tab_header_playlist`.

Verified on device 2026-09-03 (iPhone 17 Pro, dark theme): `MFYdetails` ("Your Discover Aura",
20 tracks — hero, eyebrow, "Made for Test", Play / Saved / Download, rows), `NewListenLater` and
`NewDownloadTracks` (empty states, the new tiles at the right weight), and `NewPlaylistDetails`
end to end — created "QA 4.0 Playlist" through the app's own Create flow, saw the empty variant
(Add Tracks alone), added two tracks through the legacy sheet, saw the 2-track variant (cover,
"PLAYLIST · 2 TRACKS", Play / Shuffle / Add Tracks, rows), opened the `...` sheet, and took
Rearrange → Save changes round-trip back to the 4.0 layout. The light theme was checked
statically: every theme reference in the five branches resolves through
`getCurrentThemeDarkMode` or a `theme={darkTheme}` prop. **Not verified on device:**
`ViewAllUserPlaylist` — no coach with two or more playlists was to hand.

Three fixes came out of the pass, each confirmed on the next build (f5e48810b, 2026-09-03):
- **Download pill glyph** — `specialty.downloadTracks` carries a gradient stroke, which `Icon`'s
  recolour deliberately skips, so it drew *orange* on the white-glyph pill — on Saved and
  Recent tracks too, since `RedesignTrackPlaylist` had used it all along. Now `action.download`,
  the same geometry with a flat stroke, in both `RedesignTrackPlaylist` and `MFYdetails`.
- **Downloaded Tracks title** — `download_tracks_title` ("Download tracks") read as an
  instruction and disagreed with the Me-tab row ("Downloaded Tracks"); the 4.0 branch now uses
  the row's `profile_section_item_download`.
- **Legacy roots under the full-bleed route** — `NewPlaylistDetails`' rearrange-mode and
  private-playlist renders cancel the Dashboard wrapper's xPadding with negative margins, and
  the full-bleed route no longer applies that padding, so the legacy nav bar rode up under the
  status bar. Both roots now zero those margins (and paint the canvas) under the redesign.

One observation, since fixed as PROD-5510: twice in six back presses from these details the app
landed on a different tab (Sleep once, Home once) instead of the Me tab it left from. The chip
calls `handleExitNav`, exactly what `actions.navigateBack()` on the legacy screens calls, and
`navigateTo` pushes `main` onto `lastView` normally — so `activeTab` was being moved by something
else while the detail was open. No single writer reproduced on demand (the tab pager
`this.refs.swiper` turned out to be dead code; `handleNightMode` only fires 23:00–04:00 or for
`premiumCircadin`). The fix is structural: `Dashboard.lastViewTabs` records the tab each
`lastView` entry was opened over and `handleExitNav` restores it on pop, so back returns to the
tab the user left regardless of which writer moved it. A deliberate switch goes through
`setActiveTab`, which empties both stacks, so it cannot be undone. `handleNightMode` — the one
writer known to run under an open detail, from `callInAppModal`'s delayed waterfall — now bails
when `view !== 'main'`.

## E.38 Built — Customize Tabs sheet (4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, frame `Customize Tabs` 4168:29797)

The category strip's `Customize` chip opens `CustomizeHomeTabs2` (Dashboard-mounted modal,
`Actions.openCustomizeHomeTabsModal2`). Its `render` now branches on `isRedesignTheme` into a
bottom sheet; the legacy centred card is untouched as the variant-`b` fallback. All state and
handlers (screen lists, drag order, remove/add, minimum-sections guard, save + tracking) stay in
the container — the sheet is presentational.

| What | Figma | Where |
|---|---|---|
| Sheet chrome | `sheet` + `SheetHeader` + `SheetFooter` | NEW `components/homeFeed/CustomizeTabsSheet.js` — canvas, R32 top, `borderDefault` hairline, 0/-12/40 black@50% shadow, blur(4) + `surfaceOverlay` backdrop; 36×4 `borderActive` grabber; Reset (Copy-14 `textTertiary`) / Body-16 title / 32pt `surfaceSubtle` close; white Save pill (`buttonSansSemiBold16` on `canvas`, py 17, pb = max(inset, 32)). **Max height = screen − 240pt**, the frame's sheet top; longer lists scroll inside. **Owns its motion**: the Modal is `animationType="none"` and one Animated progress fades the scrim while the panel slides up (320ms ease-out in, 240ms ease-in out, close/backdrop/Save all go through `dismiss`) — RN's `slide` moved the scrim up with the panel, which is what QA flagged as "opening behaviour". The scrim fades at once but the panel slide is **held until the lists have loaded** (400ms cap, then it slides with the skeleton) |
| Tile | `tile-*` | 102×88 R16 `surfaceSubtle`, 22pt `home.*` glyph (`Icon`, stroke+fill `textPrimary`), Caption-12 `textSecondary` label, gap 8. Hidden: opacity .6, `textMuted` label |
| Add / remove badge | `Frame` (18×18) | top/right 6, `surfaceControlBadge` (NEW token white@16%), 1px `borderSubtle`, glyph bars `iconControlBadge` (NEW token white@95%); 10pt hitSlop |
| Grid | `tile-row` ×N | `react-native-draggable-grid` kept for drag-reorder; `itemHeight` = 88+12 and per-column `alignItems` (start / center / end) so tiles sit flush to the 362pt row like the node's `justify-between`. **The grid hugs each cell to its content**, so the cell must be sized explicitly (`(deviceWidth − 40) / 3` × 100) — a `width: '100%'` wrapper collapses and every tile centres (first on-device pass rendered the grid 9pt inset each side) |
| Hidden row | `hidden-row` | static wrap row on the same three columns (no drag — matches legacy, which never persisted a hidden-list order) |
| Glyph map | `home.meditation` … | `REDESIGN_TAB_ICONS` in `CustomizeHomeTabs2` (tab key → `home.*`); `healthCoaching` / `soundHealing` go through `ContentTypeHelper.getRedesignIconNameForContentType` so the Cyrillic asset name is typed once |

New palette: `white16`, `white95`. New colour tokens (both themes, shared): `surfaceControlBadge`,
`iconControlBadge`. New i18n (7 locales): `content_customize_tabs_sheet_title` ("Customize your
Tabs"), `content_customize_tabs_hidden_description` ("Hidden"), `a11y_add_tab`, `a11y_remove_tab`.
Reuses `filter_sheet_reset_button`, `content_customize_tabs_description/_minimum_sections/_save_button`.

testIDs: `customizeTabs_screen`, `_resetButton`, `_closeButton`, `_saveButton`, `_tile_<key>`,
`_removeButton_<key>`, `_addButton_<key>`.

The grid is held on the skeleton until `TopicsStore`'s `ON_TOPIC_ORDER_CHANGED` has fired for this
open (`orderLoaded`), not just until the screen list arrives — otherwise the tiles paint in default
order and the draggable grid animates them into the saved order a beat later.

**The draggable grid mounts at height 0 with no tiles, measures itself, then grows.** Left to size the
sheet, that read as a stutter during the slide (QA: "jitters then opens"). The grid sits in a wrapper
sized from the row count (`rows × 100 − 12`), so the sheet's footprint is fixed from the first frame.

Behaviour notes (the two places the sheet adds a control the legacy card lacks):
- **Reset** = every tab visible in default order, staged (Save still required). Legacy had no reset.
- **Save is always shown** (legacy showed it only after a change); with nothing staged it dismisses
  without writing.

Deviations: the mock's tile labels are `nowrap`; ours wrap to `numberOfLines={2}` (centred, 6pt side
padding) so "Alternative Healing" reads whole instead of ellipsising inside the 102pt tile. The legacy "New" gradient
badge is a `Tag` (label `content_feed_button_new_label`) at the tile's top-left — the frame has no
new-tab state. Following uses `profile.people` (no `home.*` glyph exists). Tabs with no matching glyph (Kids,
Thought Provoking, Soundbath, Relax Before Bed, Spirituality) fall back to the legacy tinted PNG the
category strip already shows for them, so no tile renders label-only.

Verified on device 2026-09-03 (debug build, iPhone 17 Pro): tile 102×88, 28pt column gap, 12pt row
gap, 50pt Save pill, badge 18pt at 6/6; remove → Hidden row, add, drag-reorder, Reset, Save (persists to
the strip), the header close and the backdrop tap all work, on both the Home and Sleep lists.

## E.35 Notification Time (1048:16466) + Profile Summary (1048:16499)

Two frames built onto the existing onboarding chrome, both on the `7028d422…` dusk backdrop
(`backdropVariant="dusk"`). Neither needed a new locale key — every string already existed and was
translated, including the exact "87% of members notice a difference in 3 days".

| Frame | New/changed | Detail |
|---|---|---|
| Notification Time | `OnboardingSelectField` | label + chevron on the glass surface; first field FIXED 200, second FILLs |
| Notification Time | `OnboardingOptionRow` `selectionStyle="switch"` | + `OnboardingSwitch` |
| Profile Summary | `OnboardingSummaryCard` | 3 divided rows, 40pt circular icon chips |
| Profile Summary | `OnboardingHeading` `ringSize`/`ringGap`/`subtitleGap` | this frame draws a 115pt ring, gaps 48 / 20 |

**A switch row's surface stays constant.** The weekend row carries no `Rectangle 366` and no
hairline even when on — its state lives in the control — so the `switch` variant suppresses the
selected wash that every other variant draws. Passing `isSelected` for the glow would have lit the
whole row on toggle.

**The frame only draws a toggle OFF, so the on colour is not in it.** Reading it off the component
set (`121:4403`, variants `Off | On`) gives iOS' own palette: system green `#34C759` on, system fill
off — the instance resolves that fill to `#EBEBF5@0.3` in dark where the set renders `#3C3C43@0.3` on
its light canvas. **When a frame shows one state of a stateful component, render the component set to
get the others** — `/v1/images` on the set id returns every variant side by side.

**A per-run gradient does not survive into RN.** Profile Summary's "87% " is a horizontal gradient
(`#00C9A7`→`#006353`) on a character range of one TEXT node. RN cannot gradient-fill an inline run,
and the run spans ~14% of the node's width, so the ramp only moves ~8/255 across it — the flat accent
`#01C2A1` (which its row icon already uses) stands in. Read `characterStyleOverrides` +
`styleOverrideTable`: a spec-sheet scan that only prints the node's own `fills` reports this text as
plain white and loses the accent entirely.

**Two more `NEW_DESIGN_CHROME_VIEWS` omissions.** `push` and `onboardingPlan` were missing, so the
legacy 100pt progress bar drew over the new chrome on both — visible as a stray dark bar under the
Dynamic Island. Every new-design step must be in that list AND have the legacy back gated
(`{!isAuraDesignChange(this.experiments) && this.renderBack()}`) or both chromes render at once.

**`icon.calendarClock` shipped in a 28-wide viewBox for a 24pt frame** and rendered ~14% small.
Figma inflates an icon export by its blur twins, and whoever stripped the twins kept the inflated
box. Corrected to `viewBox="1.67 0 24 24"` — the offset is the inflation, measurable by differencing
the exported path extents against the node's own vector positions. **Check an exported icon's
viewBox against the node's frame size before registering it.**

Rows in the summary card measure 340 wide inside a 326 content box (icon 40 + gap 16 + a FIXED 284
text frame). That overflows by 14; built as flex so the text takes the real remainder.

## E.21 Account Creation (1048:16802)

The registration step (`Onboarding6.renderPersonalization`), on the dusk backdrop. New:
`OnboardingTextField` (the `Field` glass input) and `OnboardingSocialButton`; `OnboardingHeading`
gains `align="center"` — this is the only frame in the flow that centres the whole heading block.
Every string already existed, including the agreement line with its `<0>`-tagged link runs.

**The backdrop was the one thing I got wrong, exactly as this file warns.** Built without
`backdropVariant`, the step inherited the forest default while the node specifies `7028d422…` dusk.
Nothing else in the frame hinted at it — the sim sweep is what caught it. **Pass `backdropVariant` in
the first draft of every new step, before anything else.**

**`Rectangle 365` is hidden on this frame**, so there is no bottom scrim and the CTA scrolls with the
content rather than being pinned. Read that rectangle's `visible` flag per frame instead of assuming
the pinned-CTA layout every other step uses.

**The `#D9D9D9@4%` well is shared, so its token is not chip-specific.** `Login.js` hardcodes that rgba
with a comment saying no token exists; the summary card's icon chip and this `Field` are the same
value, so `newDesignSurfaceChip` was renamed `newDesignSurfaceSoft`. Login can adopt it when that PR
next moves — not changed here, since it lives on a different branch.

Placeholder text is white@**0.2** (`newDesignPlaceholder`), not the white@0.5 tertiary that Login's
field uses — read the placeholder fill per frame.

`action.eyeOpen.svg` exported for the password reveal. Named as a compound (like `icon.calendarClock`)
rather than coining an `.open`/`.off` variant suffix the folder has no precedent for; a future closed
state is `action.eyeClosed`.

The inline CTA is drawn only in its inactive state (white@10%, node 1048:16818). Active falls back to
the solid white pill every other step uses — flagged rather than invented, since the frame does not
show it.

**The inactive state is not built** (PROD-6719). Every dimmed version tried — `opacity: 0.5`, a 70%
white tint, then this white@10% glass — still read to QA as ready to tap. Onboarding CTAs now have no
inactive state: a step holds its CTA off screen until it can continue (`ctaHidden`, or not rendering
`OnboardingContinueButton`), and a single-select step (age, gender, session length) advances on the
row tap and has no CTA at all.

## F. To finalize

Remaining 11 sections' counts; usage contracts (diff each Layer-A component's instance overrides
across screens -> exact prop list); design answers (Season fonts, always-dark screens, missing-
sections roadmap); ADR (new folder + theme entry names).

## E.11 Settings (SideBar) — 4.0 file `dpxPlTNW722Y1Qr8JRnSIn`, nodes `800:6777` + `3334:13564`

**Second THEMED 4.0 screen** (light `800:6839` + dark `800:6778`; geometry identical, palette
inverted) — follows the Profile3 mechanic in §E.9. Family-plan hero state is authored DARK-ONLY.

**Target: `src/screens/SideBar.js`** (the drawer, `Dashboard.renderNavigationView` → route
`sideBar`). Gate decided once in `render()` via `isRedesignTheme(theme)`; `renderRedesign()` is
purely additive (+133/-0) so the legacy path stays as the variant-`b` fallback. The row data comes
from the EXISTING `getNewSideBarSideBarMenuItems()` — same handlers, same
`sidebar_menuItem_<key>` testIDs, same guest-pass suppression.

| What | Where |
|---|---|
| Chevron row (56pt) | NEW `components/settings/SettingsRow.js` — 44pt R12 icon-box with **NO fill** (unlike ProfileListRow's surfaceGlass box), `bodySansRegular16` label, 24pt `action.arrowRightSmall`. Registry §A "Settings row Action=Chevron" now exists |
| Titled group | NEW `components/settings/SettingsSection.js` — Caption-12 `textTertiary` uppercase caption, R16 `surfaceSubtle` card + `borderSubtle` hairline, 1pt dividers between rows only |
| Hero banner | NEW `components/settings/SettingsHeroBanner.js` — one 110pt R24 shell, `variant` premium/family. Premium: `gGiftPromo` + `auraRingCard.png` at 1.25x box. Family: transparent base + purple radial + 56pt `gSettingsFamilyBadgeCore` badge |
| Family hero wiring | `FamilyPlanMenuCard` **`isNewDesign` branch** — reuses its own title/discount (real pricing via `getPerPersonMonthPrice` + `formatCurrency`), `familyPlanUpSell` handler and mount tracking |
| Aurora backdrop | NEW `components/settings/SettingsAuroraBackdrop.js` + `SettingsRadialWash.js` — SVG radial (RN has no radial linear-gradient); box sized as a ratio of device width because the node bleeds off both frame edges |
| Header circle | NEW `components/settings/SettingsNavButton.js` — 36pt `surfaceSubtle` circle, 20pt glyph, strokeWidth 1.2 |

**Integration bug found by the sim sweep (would have shipped):** `Dashboard.renderSecondaryView`
wraps overlay routes in `paddingTop/Bottom: Style.xPadding` unless the route is in its
`isFullScreenView` list. That pushed the header 20pt below the node's 72pt and added a bottom strip.
Fixed by adding `sideBar` to that redesign-gated list, as `pushSettings` / `allAuraPodcasts` already are.

New tokens: `redesign/shadows.js` (NEW FILE — `shadowSettingsHeroPremium/Family`; the helper is
duplicated from globals so the layer keeps no edge into shared files, per `redesignScale.test.js`);
gradients `gSettingsHeroPremium` + the radial washes on `theme.gradients.radial` (`gSettingsAurora`,
`gSettingsFamilyBadgeCore`, `gSettingsPremiumGlow`, `gSettingsGlowViolet`, `gSettingsGlowRose`); palette `white14/white22`; colors
`textOnColorEmphasis`, `surfaceOnColor`, `borderOnColorSubtle`, `borderOnColorStrong` (identical on
BOTH themes — the hero is dark in light mode too) and `surfaceScrimSheet` (the confirm-sheet scrim;
distinct from the base branch's `surfaceScrimStrong`, which is a lighter scrim with its own
consumer in `BookingConfirmation`). New icons: `profile.language`, `bullet.people`.
New i18n: `sidebar_premium_award_text` (7 locales).

**`borderRadius16`/`borderRadius12` still resolve through globals** (`responsiveDimension`), so they
land +0.71/+0.53pt at 402 — inside the ~1pt tolerance but not exact. Adding both to
`redesign/borderRadius.js` would fix every 4.0 screen at once; deliberately left as a follow-up
rather than changing a shared token inside a per-screen PR.

Deviations (raised, not silent): "Aura Premium" is SeasonSerif-**Light** + "Premium" in
**RegularItalic** in the node and NEITHER face is bundled → one `displaySerifRegular25` run, no
italic (faking a weight is barred by design-rule 1; splitting a translated string by word index
breaks the other 6 locales). Family glow is a 170pt LAYER_BLUR → SVG radial falloff. Premium base
reuses `gGiftPromo` (130° vs the node's ~144°, same stops). Hero border uses `borderOnColor`
(white@12% vs node 10%). Light-frame premium chevron is #000 in the node — treated as a mock slip,
rendered white. Header close-circle is opacity-0 → omitted. `family_plan_menu_card_discount` ships
"-" where the mock shows "·". "Best of Apple" uses the `platform.apple` icon, not the SF Pro glyph.

