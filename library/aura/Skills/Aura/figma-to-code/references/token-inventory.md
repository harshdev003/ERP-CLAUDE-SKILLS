# Aura theme token inventory (real names, verified in src/styles/)

Theme is a Reflux store (`src/stores/ThemeStore.js`) that triggers a resolved `theme` object,
passed as a `theme` prop into components. Every reuseableUI primitive takes `theme` as a required
prop. The app paints two themes, **redesignDark** and **redesignLight**, which extend the `newDark`
/ `newLight` token layers. Auto follows the phone's appearance.

Never hardcode a color, font size/weight, or px. Always map to a token below.

## The redesign themes

`redesignDark` / `redesignLight` are the 4.0 themes, the only ones the app paints. They **extend**
`newDark` / `newLight` rather than replacing them; the redesign layer only adds tokens. A user's
preference is still stored as the base name (`NEW_LIGHT` / `NEW_DARK`), and `getIndividualStyle`
resolves it to the 4.0 theme of the same brightness, so a screen never sees a base theme.

Consequences when writing a redesign screen:

- The redesign tokens below (`§ Redesign colors`, `§ Redesign type ladder`, `§ Redesign gradients`,
  `d<N>` dimensions) exist ONLY on these two themes. Reading one from a non-redesign theme yields
  `undefined`, which RN silently ignores — so it surfaces as an unstyled element, not an error.
- The base tokens (`textPrimaryMain`, `font400DefaultRegular`, `r16`, …) still work and should be
  used for anything the redesign does not respecify.
- **A screen that renders constant-dark calls `getCurrentThemeDarkMode()`** rather than naming a
  theme. There is no constant-*light* equivalent because no screen needs one; add
  `getCurrentThemeLightMode` alongside it the day one does.
- `redesignDark/color.js` and `redesignLight/color.js` declare the **same key set**. Adding a token
  to one means adding it to the other — `test/unit/redesignTokens.test.js` enforces this.

## Colors — semantic map (per-theme color.js/colors.js, e.g. src/styles/newDark/)

Use the camelCase semantic tokens (NOT the legacy `c1..c11`, `fc1`, or snake_case sets).

- Text: `textPrimaryMain`, `textPrimaryOnWhiteMain`, `textPrimaryInverseMain`, `textPrimaryOnColorMain`,
  `textSecondary600`, `textSecondaryStrong500Main`, `textSecondary500`, `textTertiaryStrong400Main`,
  `textTertiary400`, `textError`, `textCaution`, `textSuccess`, `textRate`.
- Icons: `iconsPrimaryMain`, `iconsSecondary500`, `iconsTertiary400`, `iconsError`, ... (mirrors text).
- Background: `bgPrimaryMain`, `bgPrimaryInverseMain`, `bgLayout`, `bgLayout2`,
  `bgPrimaryTransparentSoftWhite400100`, `bgPrimaryTransparentWhite600100`, `bgError`, `bgCaution`,
  `bgSuccess`, `bgAvatarLightGray`.
- Border: `borderPrimarySubdued300`, `borderPrimaryTransparent300400`, `borderSecondaryTransparent200`,
  `borderError500`, `borderLive`, `borderGradient400`, `borderTertiaryTransparent140100`.
- Specialty/blur/modal: `specialtyPrimarySubdued300200`, `specialtyPrimaryDisabled300`,
  `specialtyHoverInput`, `bgBlurDark64`, `bgBlurLight64`, `bgBlurLight8`, `bgBlurDark8`,
  `bgModalAlpha600`, `rectangleShadowGradientPrimary`, `placeholderGradientPrimary`.

Primitive palette (globals/colors.js, imported as `Color`): grays (`grayMain`, `gray100..600`,
`grayAlpha100..700`), whites (`whiteAlpha0..700`, `whiteAlphaMain`), system (`systemError*`,
`systemCaution*`, `systemSuccess*`). **Unchanged by the redesign** — the redesign's raw values live
in `redesign/palette.js` instead (below).

## Redesign palette — src/styles/redesign/palette.js

Raw values, named for the colour, consumed only by `redesign{Dark,Light}/color.js`. Literal alpha
steps `white05/10/12/18/20/30/35/40/50/70/80/85` + `whiteMain`, `black05/10/20/30/40/50/70` +
`blackMain`; canvases `canvasDark` / `canvasLight`; nav `surfaceNavDark/Light`, `navScrimDark/Light`;
accents `accentTeal`, `accentMint`, `mintAlpha10/30`, `accentPeach`, `accentPeriwinkle`, `liveRed`,
`avatarRingDark`; plus `softNeutral04` and `switchTrackOff/On`.

The literal-alpha ladder is deliberately not folded into `whiteAlpha100..700` in globals: that ladder
is non-linear (100 = 0.08, 300 = 0.24) and has no 0.05 / 0.1 / 0.4 / 0.5 step, so reusing its names
would misreport the actual alpha.

## Redesign colors — src/styles/redesign{Dark,Light}/color.js

Semantic, unprefixed, grouped by category. Read as `theme.colors.<name>`:

- Text: `textPrimary`, `textEmphasis`, `textTertiary`, `textMuted`, `textPlaceholder`, `textAccent`,
  `textOnColorMuted`.
- Surfaces: `canvas`, `surfaceSubtle`, `surfaceSoft`, `surfaceGlass`, `surfaceOverlay`, `surfaceNav`,
  `navScrim`, `navWash`.
- Borders: `borderSubtle`, `borderDefault`, `borderStrong`, `borderSelected`, `borderSelectedRow`,
  `borderOnColor`.
- Icons: `iconMuted`.
- Accents: `accentTeal`, `accentLive`, `accentMint`, `accentPeach`, `mintSurface`, `mintBorder`,
  `avatarRing`.
- On-light (the white bottom-CTA pill): `onLightPrimary`, `onLightSecondary`, `onLightBorder`.
- Controls: `switchTrackOff`, `switchTrackOn`.

`textPrimary` / `textSecondary` equivalents already exist on the base themes as `textPrimaryMain` /
`textSecondary500`; the redesign redefines `textPrimary` because it diverges between light and dark.

## Fonts — src/styles/globals/fonts.js (family: Proxima Nova; weights 400/600/700)

Spread the whole object: `...theme.fonts.font<NNN>Default<Weight>`. Size / lineHeight:

- font900DefaultSemibold 36/40
- font800Default{Regular,Semibold,Bold} 32/40
- font700Default{...} 28/35 · font600Default{...} 24/30 · font550Default{...} 22/27
- font500Default{...} 20/25 · font450Default{...} 18/23 · font400Default{...} 16/22 (+font400DefaultRegular1 lh25)
- font350Default{...} 14/18 · font325Default{...} 13/17 · font300Default{...} 12/15
- font275Default{...} 11/14 · font250Default{...} 10/13

## Redesign type ladder — src/styles/redesign/fonts.js

Serif is **Charter** (`CharterRegular`, Bitstream's freely redistributable release); sans is **Nunito Sans**
(`NunitoSans-{Light,Regular,Medium,SemiBold,Bold}` statics, SIL Open Font License) — not the rounded
Nunito, which the ladder used until the 4.0 Figma moved to Nunito Sans. No trial-licensed face
remains in the ladder. Names are `{role}{Face}{Weight}{Size}`:

- Serif: `titleSerifRegular30` (30/36), `displaySerifRegular25` (25/27.5)
- Sans display: `displaySansRegular25` (25/27.5), `displaySansRegular20` (20/26),
  `displaySansLight35` (35/45.5)
- Labels: `fieldSansLight20` (20/24.58), `labelSansLight18` (18/23.4), `titleSansSemiBold18` (18/22.1)
- Body: `bodySansRegular16` (16/20.8), `copySansRegular14` (14/18.2), `captionSansRegular12` (12/15.6)
- CTAs: `buttonSansSemiBold18` (18/22), `buttonSansSemiBold16` (16/16)
- Tab bar: `tabLabelSansSemiBold11` (11/14.3, Figma `Tab Bar 11`)
- Off-ladder Inter (where the frame drops out of the ladder's rungs; these were the `*Proxima*`
  tokens that stood in for Inter before it was bundled): `labelSansRegular19`,
  `ctaLabelSansSemiBold13`, `authorNameSansSemiBold14`,
  `authorRoleSansRegular14`, `wordmarkSansRegular30`

Never set `fontWeight` alongside a redesign face — the face carries the weight and iOS will synthesize
a faux-bold on top of it.

## Dimensions — src/styles/dimensions.js

`responsiveDimension(n)` wraps moderateScale(n, 0.3), scaling from the 350pt size-matters base.
Pre-baked `theme.dimensions.r<N>`:
r1,r2,r4,r6,r8,r10,r12,r13,r14,r16,r18,r20,r22,r24,r28,r30,r32,r34,r36,r40,r44,r46,r48,r56,r60,
r64,r72,r74,r76,r80,r88,r90,r92,r96,r108,r128,r132,r154.

## Redesign dimensions — src/styles/redesign/dimensions.js

`scaledDimension(n)` is the same idea from a **402pt** base — use it for values read off a 402pt
frame, where `responsiveDimension` would render ~4.5% large and compound through nested spacing.
Pre-baked on the redesign themes as `theme.dimensions.d<N>`:
d1,d2,d4,d6,d8,d10,d12,d14,d16,d18,d20,d22,d24,d28,d30,d32,d36,d40,d44,d48,d56,d60,d64,d72,d80,
d88,d96,d108,d128. Values off the ladder (a 13.674pt size, a 24.58pt line height) call
`scaledDimension` directly — the same relationship `r<N>` has with `responsiveDimension`.

## Spaces — globals/space.js

space0,2,4,6,8,10,12,16,20,24,32,40,48,56,64,72,80,88,96,100 (all responsiveDimension).

## Border radius — globals/borderRadius.js

borderRadius2,4,6,8,10,12,16, borderRadiusFull (9999) — all `responsiveDimension` (350pt base).
Capsule/pill elements use `borderRadiusFull`, NOT measured radii like 45/53 (those are height/2 of
specific pills and will distort under responsiveDimension).

The redesign adds `borderRadius14` (Home category chip) and `borderRadius24` (`radius-xl` — glass
cards, service cards, live rows) in `src/styles/redesign/borderRadius.js`, on `scaledDimension`.
They exist only on the redesign themes, like every other redesign token.

**Every value in `src/styles/redesign/` uses `scaledDimension`, never `responsiveDimension`** —
mixing the two bases inside one component (a 402-scaled height against a 350-scaled corner) is what
makes a pill stop reading as a pill. `test/unit/redesignScale.test.js` enforces it.

## Shadows / gradients — globals/shadows.js, globals/gradients.js

- Shadows: helpers `shadow(...)`, `textShadow(...)`; tokens `buttonShadowCtaBlue{Xl..S}`,
  `buttonShadowPrimary{Xl..S}`, `shadowSoftCardS`, `shadowCardBig`, `shadowMessage`, ...
- Gradients: helper `gradient(angle, colors, locations)`; tokens `gBlue45/0`, `gGreen45/0`,
  `gGreenSubdued45/0`, `gPink45/0`, `gPeach45/0`, `gRainbow0`, `gGold45`, `gMessage`, ...

## Redesign gradients — src/styles/redesign/gradients.js

Merged over the shared set on the redesign themes only. Onboarding: `gOnboardingDark`,
`gOnboardingCardScrim`, `gOnboardingTopicCardScrim`, `gOnboardingWelcomeScrim`,
`gOnboardingScreenScrim`, and the selected-row glows `gOnboardingRowSelectedGlow{,Violet,Blue,Peach}`.
Feed / detail: `gHomeBackdrop`, `gCardScrim`, `gCelebrityScrim`, `gBottomCtaScrim`, `gPlanProgressGlow`,
`gSpecialtyMint`, `gSpecialtyPink`, `gGiftPromo`, `gGiftOrb`.

## Which theme slots the redesign owns

The redesign themes **extend** their base (`redesignDark` = `newDark` + a redesign layer), so a
screen that has not been reskinned renders identically on either. Only these slots carry redesign
values; the rest are inherited verbatim.

| Slot | Redesign source | Why |
|---|---|---|
| `colors` | `redesign{Dark,Light}/color.js` | diverge between light and dark |
| `fonts` | `redesign/fonts.js` | Season ladder, identical in both frames |
| `gradients` | `redesign/gradients.js` | painted over imagery or the dark canvas |
| `borderRadius` | `redesign/borderRadius.js` | 402pt-scaled radii |
| `dimensions` | `redesign/dimensions.js` | 402pt-scaled `d*` ladder beside `r*`, plus the named geometry several surfaces must agree on (the bottom stack: `tabBarPillHeight`, `tabBarRowWidth`, `miniPlayerGap`, …) |
| `spaces` | **none — use `theme.dimensions.d<N>`** | `space<N>` is already `responsiveDimension(N)`, i.e. a duplicate of `r<N>` on the 350pt base. A redesign `spaces` ladder would be a third copy of the same numbers. Reskinned components use `d<N>` for padding and gaps. |
| `shadows` | `redesign/shadows.js` | drop shadows on `scaledDimension`. Do not add 4.0 shadows to `globals/shadows.js`. |
| `styles` | `redesign/styleSheet.js` | app-level constants several 4.0 surfaces must agree on — card press delay and opacity, sheet entrance, scrim stops — read as `theme.styles.<name>`. Not component styles. |
| `icons` | **none** | zero reads from 4.0 components; they use the SVG icon list. |

## The redesign won: what is left of the gate

`auraDesignChange3` ended with 4.0 the winner, and the design cleanup (PROD-7647) removed the
legacy design: every legacy screen, the design checks, the Nature theme and the Clean Light /
Clean Dark palettes. `getIndividualStyle` returns only the two 4.0 themes, and `ThemeStore` has no
design gate.

Still in the code, and why:

| What | Where | Goes when |
|---|---|---|
| The `auraDesignChange3` arm assignment | `AURA_DESIGN_CHANGE` in `src/data/enums.js`, `Onboarding6.chooseDesignExperiment` (with the splash wait), the Dashboard bucketing list, the `designChanged` re-trigger in `ExperimentHelper.cacheExperiments` | UserWeb's WebView paywall and Aurie upsell pick their design from the arm, and fall back to legacy without one. Remove once they default to 4.0 (AuraHealth/UserWeb#2518). |
| `newDark` / `newLight` | `src/styles/newDark/`, `src/styles/newLight/` | The 4.0 themes extend them. |

Optional later cleanup, with no call site changes: fold `redesign{Dark,Light}/color.js` into
`newDark` / `newLight` and move `redesign/*.js` into `globals/`, keeping `scaledDimension` (the
frames are still 402pt). Screens read the same token names either way.

## Adding a redesign token

1. Raw colour value -> `redesign/palette.js`, named for the colour, never for the design version
   (`white05`, `accentMint`). Do NOT add it to `globals/colors.js` — that file is shared by every
   theme and must stay free of redesign values so the layer deletes cleanly.
2. Semantic name -> BOTH `redesignDark/color.js` and `redesignLight/color.js`, in the right category
   block. The light value is read off the light Figma frame; only genuinely frame-independent values
   (brand accents, anything sitting on a gradient or photo) repeat the dark value, and those are
   marked `// shared`.
3. Type -> `redesign/fonts.js` using `{role}{Face}{Weight}{Size}` and `scaledDimension`.
4. Gradient -> `redesign/gradients.js`, `g<Role>` with no design-version prefix. A radial wash
   (SVG stops) goes under `radial:` there and is read as `theme.gradients.radial.<name>`.
5. A value several surfaces must agree on -> the theme, never a loose named export. Geometry
   goes in `redesign/dimensions.js` (`theme.dimensions.<name>`); a duration, opacity,
   animation preset or gradient-stop list goes in `redesign/styleSheet.js`
   (`theme.styles.<name>`). **Components read every visual value off the theme object.**
   Importing one straight from `src/styles/` bypasses the theme contract and was rejected
   in review on both #6775 and #6895. Arithmetic on those values (a height that depends on
   what is on screen) belongs in the view that renders it, not in the style file.

`npm test` fails if the light and dark key sets drift apart.

## Values seen in Home that are NOT yet tokens

- Frosted card surface: `#FFFFFF` @ 0.05 fill + BACKGROUND_BLUR r=8 (the fill is `surfaceSubtle`;
  the blur has no token — use `BlurView`).
- Radial white glow `@0.18 -> 0` — react-native-linear-gradient has no radial mode.
- Card drop shadow: `#000 @0.25, offset y4, radius 24` (verified in Home node JSON).
- Blur radii 8/30.
