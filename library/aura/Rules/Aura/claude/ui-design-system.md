---
description: Aura theme system — fonts, colors, dimensions, responsive sizing
globs: src/**/*.{js,jsx}
alwaysApply: false
---

# UI Design System

The app has a theme object passed via context/props. Every screen receives a `theme` (one of `cleanDark`, `cleanLight`, `light`, `nature`, `newDark`, `newLight`, `rainbowDark`). All visual values come from that theme — **never hardcode colors, font sizes, or pixel values**.

## Theme Shape

```js
theme = {
  colors,        // semantic color tokens (textPrimaryMain, backgroundMain, ...)
  fonts,         // font style objects (font900DefaultSemibold, ...)
  fontFamily,    // raw family name
  fontColors,
  styles,        // per-theme style sheet
  dimensions,    // r1, r2, r4, ... r64 — pre-scaled responsive dimensions
  icons,
  gradients,     // theme.gradients.<name>
  shadows,       // theme.shadows.<name>
  borderRadius,  // theme.borderRadius.<size>
  spaces,        // theme.spaces.<token>
  keyboardAppearance,
}
```

Tokens are defined in `src/styles/globals/` (cross-theme) and `src/styles/<theme>/` (per-theme overrides).

## Text — Always Use `theme.fonts` + `theme.colors`

```js
// ✅ GOOD
<Text
  style={{
    ...theme.fonts.font900DefaultSemibold,
    color: theme.colors.textPrimaryMain,
  }}
>
  Hello
</Text>

// ❌ BAD — hardcoded font + color
<Text style={{ fontSize: 24, fontWeight: 'bold', color: '#FFFFFF' }}>
  Hello
</Text>
```

The list of fonts is in `src/styles/globals/fonts.js` (and the per-theme `fontStyles.js`). The list of colors is in `src/styles/<theme>/color(s).js`.

**Before using a font or color key, verify it exists.** Don't invent new keys.

## Dimensions — Always Use `responsiveDimension()`

`responsiveDimension(N)` (from `src/styles/dimensions.js`) applies a width-based `moderateScale` (baseline 375pt, factor 0.3) so that pixel values scale across device sizes. The math lives in that file — it used to come from `react-native-size-matters/extend`, which read its baseline out of a gitignored `.env` via `react-native-dotenv` and made a fresh checkout unbuildable (PROD-4975).

```js
import { responsiveDimension } from '<path>/styles/dimensions'

// ✅ GOOD
<Text
  style={{
    ...theme.fonts.font900DefaultSemibold,
    color: theme.colors.textPrimaryMain,
    marginTop: responsiveDimension(20),
  }}
>
  Hello
</Text>

const styles = {
  thumbnail: {
    width: responsiveDimension(80),
    height: responsiveDimension(80),
    borderRadius: responsiveDimension(8),
  },
}

// ❌ BAD — raw pixel values
const styles = {
  thumbnail: {
    width: 80,
    height: 80,
    borderRadius: 8,
  },
}
```

If the same value is used many times, prefer the pre-baked `theme.dimensions.r{N}` constant (e.g. `theme.dimensions.r16`) over re-calling `responsiveDimension(16)`.

## Combined Anti-Pattern (DON'T)

```js
// ❌ BAD — hardcoded color, hardcoded font, raw pixel marginTop
name: {
  color: '#FFFFFF',
  fontSize: 24,
  fontWeight: 'bold',
  marginTop: 16,
},

// ✅ GOOD
name: {
  color: theme.colors.textPrimaryMain,
  ...theme.fonts.font300DefaultBold,
  marginTop: responsiveDimension(16),
},
```

## Other Theme Tokens

- **Gradients** → `theme.gradients.<name>` with `react-native-linear-gradient`.
- **Shadows** → spread `...theme.shadows.<name>`.
- **Border radius** → `theme.borderRadius.<size>` (or `responsiveDimension(N)` for one-offs).
- **Spaces** → `theme.spaces.<token>` for consistent gaps.

## Icons — Add the SVG to the Asset Folder, Never to a Component

Icons go through an asset pipeline. **Never paste SVG markup, a `<Path d="...">`, or a
hand-built `react-native-svg` tree into a component** — even when a designer hands you
the `.svg` directly. An icon pasted into a component is invisible to every other screen,
drifts from the file design ships, and has to be re-pasted the next time it changes.

Three steps, in order:

**1. Drop the `.svg` into `src/img/newDesign/svg/`.** Name it `<category>.<name>.svg`,
where the category is one of the ones already in that folder (`action`, `bullet`,
`community`, `control`, `feel`, `field`, `gear`, `header`, `home`, `icon`, `logos`,
`message`, `platform`, `play`, `player`, `profile`, `reels`, `specialty`, `tapbar`).
Use an existing category — don't invent one. A gradient-filled variant takes a
`.gradient` suffix, following `message.video.gradient.svg`.

**2. Regenerate the icon list, then format it.**

```bash
npm run bundleIcons && npx prettier --write src/generated/iconList.js
```

`npm run bundleIcons` runs `src/scripts/bundleIconsToObject.js`, which reads every file in
that folder and writes `src/generated/iconList.js` — keyed by filename minus `.svg`.
**Commit the regenerated file**; it is tracked, not gitignored.

**The prettier pass is not optional.** The script serialises with `JSON.stringify`, which
emits double quotes, and the repo's eslint enforces single quotes — so a freshly generated
file fails `npm run lint:cache` with one error per line (400+) and takes CI down with it.
The committed file is single-quoted because it has always been formatted after generating;
skipping that step silently reverts every line in the file.

**3. Render it through `Icon`.**

```js
import Icon from '<path>/reuseableUI/Icon/Icon'

// ✅ GOOD — the icon comes from the generated list
<Icon name={Icon.icons['message.heart.gradient']} size={responsiveDimension(24)} />

// ❌ BAD — SVG markup pasted into the component
<Svg viewBox="0 0 24 24">
  <Path d="M5.5 3C4.11929 3 3 4.11929 3 5.5V16.5..." fill="#FF38B9" />
</Svg>
```

`Icon` takes the SVG *content* as `name` (hence `Icon.icons[key]`, not the key itself),
and `fill` / `stroke` / `strokeWidth` rewrite the corresponding attributes in the markup
before rendering.

Two things that follow from how it rewrites:

- **Don't pass `fill` to a gradient icon.** The rewrite is a blanket regex over
  `fill="..."`, so it overwrites `fill="url(#someGradient)"` too and the icon turns flat.
  Leave a gradient icon's colour alone.
- **SVG filters are dropped.** `react-native-svg` has no filter support, so a `feDropShadow`
  or `feGaussianBlur` in the export renders as nothing. If the design needs that lift, give
  the icon's *container* a real RN shadow instead.

Drawing an SVG by hand in a component is justified only when no icon can serve — a shape
that must be recoloured per-instance beyond what `fill`/`stroke` can reach. Say why in a
comment when you do it (see `AurieValuePropsGlyph`).

## Checklist Before Submitting UI Code

- [ ] No hardcoded color literals (`'#...'`, `'rgb(...)'`, `'white'`, etc.) outside `src/styles/`
- [ ] No hardcoded `fontSize` / `fontWeight` outside `src/styles/`
- [ ] All pixel-style values pass through `responsiveDimension()` or `theme.dimensions.r{N}`
- [ ] All font keys and color keys you used actually exist in the theme files
- [ ] No SVG markup or `<Path d="...">` pasted into a component — new icons went into
      `src/img/newDesign/svg/`, `npm run bundleIcons` was run, the regenerated
      `src/generated/iconList.js` was **passed through prettier**, and it is part of
      the commit
- [ ] `npm run lint:cache` passes — the same command CI runs, and the only one that
      catches a generated file left unformatted
