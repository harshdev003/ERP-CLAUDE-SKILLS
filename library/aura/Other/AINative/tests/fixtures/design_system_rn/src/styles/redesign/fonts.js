/**
 * @module redesignFonts
 * @description The redesign type ladder, shared by both redesign themes.
 *
 *   Charter is the serif and Nunito Sans the sans, as the 4.0 Figma's typography
 *   variables name them. Both are freely redistributable (Bitstream's Charter grant,
 *   SIL Open Font License for Nunito Sans), so the ladder bundles no trial-licensed
 *   face. No token sets a `fontWeight` — the bundled face carries it, and setting one
 *   makes iOS synthesize a faux-bold on top.
 */

import { Platform } from 'react-native'

import { scaledDimension } from './dimensions'

// Each family is swapped HERE and nowhere else in JS. Every redesign screen reads
// `theme.fonts.<token>`, and every token below derives its face from these two names,
// so moving to another family is the string plus the static files — named by PostScript
// name — dropped into assets/fonts/ and android/app/src/main/assets/fonts/ and
// registered in Info.plist `UIAppFonts` and the Xcode Resources phase. iOS resolves
// `fontFamily` by PostScript name and Android by filename, which is why the files keep
// those exact names: one string serves both platforms without a `getFontFamily` switch.
const SERIF_FAMILY = 'Charter'
// Nunito Sans, not the rounded Nunito the ladder shipped with: a different family that
// shares Nunito's vertical metrics (a 1.364em line box, caps inking to 0.714em), so
// every leading below was carried over unchanged. The statics are googlefonts/NunitoSans
// v3.101, whose PostScript names are `NunitoSans-<Weight>`.
const SANS_FAMILY = 'NunitoSans'

// Bitstream's Charter release names its regular cut `CharterRegular`, not `-Regular`.
// iOS ships its own Charter as `Charter-Roman`, a different PostScript name, so the
// bundled file registers beside it instead of colliding.
const SERIF_REGULAR = `${SERIF_FAMILY}Regular`
const SERIF_ITALIC = `${SERIF_FAMILY}Italic`

const SANS_LIGHT = `${SANS_FAMILY}-Light`
const SANS_REGULAR = `${SANS_FAMILY}-Regular`

// Nunito Sans ships a true Medium (500), which fills the rung the Season Sans trial bundle
// needed a synthesized weight-550 instance for.
const SANS_MEDIUM = `${SANS_FAMILY}-Medium`
const SANS_SEMIBOLD = `${SANS_FAMILY}-SemiBold`
const SANS_BOLD = `${SANS_FAMILY}-Bold`

// RN on iOS sets Charter's glyphs higher in their line box than Figma does — measured at
// about 0.155em, whatever the line height. Spread onto a serif Text (or a row aligned to
// its baseline) to drop the glyphs to where the frames draw them; a transform, so layout
// is untouched. Android has not been measured, so it is left alone there.
const SERIF_BASELINE_SHIFT = 0.155
export const serifBaselineShift = (font) => {
  if (Platform.OS !== 'ios') {
    return null
  }
  return { transform: [{ translateY: font.fontSize * SERIF_BASELINE_SHIFT }] }
}

export default {
  titleSerifRegular30: {
    fontFamily: SERIF_REGULAR,
    fontSize: scaledDimension(30),
    lineHeight: scaledDimension(36),
  },
  // The emphasised words inside a Display 30 title — the cancel-flow offer's "just for you".
  // Unlike a weight, the style has to be stated on iOS: it re-picks a named face within
  // its family by style, so without it `CharterItalic` resolves back to the upright cut.
  // Android must not get it — there `italic` looks for `CharterItalic_italic.ttf` and
  // falls back to the system italic, while the plain name loads the bundled file.
  titleSerifItalic30: {
    fontFamily: SERIF_ITALIC,
    ...(Platform.OS === 'ios' ? { fontStyle: 'italic' } : {}),
    fontSize: scaledDimension(30),
    lineHeight: scaledDimension(36),
  },
  // The welcome offer's headline figure ("50% off") — the serif two rungs above the
  // title, on the 1.3 leading the body rungs take.
  displaySerifRegular40: {
    fontFamily: SERIF_REGULAR,
    fontSize: scaledDimension(40),
    lineHeight: scaledDimension(52),
  },
  displaySerifRegular25: {
    fontFamily: SERIF_REGULAR,
    fontSize: scaledDimension(25),
    lineHeight: scaledDimension(27.5),
  },
  displaySerifRegular20: {
    fontFamily: SERIF_REGULAR,
    fontSize: scaledDimension(20),
    lineHeight: scaledDimension(26),
  },
  // The cancel-flow offer's plan name. Charter's "normal" leading is tight in the frame
  // (the card is 96pt), so this sits just above 1.0 — enough for the cap to clear.
  displaySerifRegular22: {
    fontFamily: SERIF_REGULAR,
    fontSize: scaledDimension(22),
    lineHeight: scaledDimension(23.5),
  },
  bodySerifRegular16: {
    fontFamily: SERIF_REGULAR,
    fontSize: scaledDimension(16),
    lineHeight: scaledDimension(20.8),
  },

  displaySansRegular25: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(25),
    lineHeight: scaledDimension(27.5),
  },
  displaySansRegular20: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(20),
    lineHeight: scaledDimension(26),
  },
  displaySansLight35: {
    fontFamily: SANS_LIGHT,
    fontSize: scaledDimension(35),
    lineHeight: scaledDimension(45.5),
  },

  fieldSansLight20: {
    fontFamily: SANS_LIGHT,
    fontSize: scaledDimension(20),
    lineHeight: scaledDimension(24.58),
  },
  labelSansLight18: {
    fontFamily: SANS_LIGHT,
    fontSize: scaledDimension(18),
    lineHeight: scaledDimension(23.4),
  },
  titleSansSemiBold18: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(18),
    lineHeight: scaledDimension(22.1),
  },
  titleSansSemiBold20: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(20),
    lineHeight: scaledDimension(26),
  },

  bodySansRegular16: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(16),
    lineHeight: scaledDimension(20.8),
  },
  copySansRegular14: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(14),
    lineHeight: scaledDimension(18.2),
  },
  captionSansRegular12: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(12),
    lineHeight: scaledDimension(15.6),
  },
  // The upper-case eyebrow and card captions of the cancel flow — `Caption 12 Strong`.
  captionSansSemiBold12: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(12),
    lineHeight: scaledDimension(15.6),
  },
  // The caption rung tracked out 8% for the upper-case eyebrow above a serif title —
  // the check-in and rating prompts' `eyebrow`.
  eyebrowSansRegular12: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(12),
    lineHeight: scaledDimension(15.6),
    letterSpacing: scaledDimension(0.96),
  },

  buttonSansSemiBold18: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(18),
    lineHeight: scaledDimension(22),
  },

  buttonSansMedium18: {
    fontFamily: SANS_MEDIUM,
    fontSize: scaledDimension(18),
    lineHeight: scaledDimension(22),
  },
  // 1.3 leading, not the 1.0 this shipped with. iOS clamps the line fragment to
  // `lineHeight` and seats the baseline a descender above its bottom, so a glyph
  // needs `inkAbove + descender` of leading to clear the top — for Nunito Sans that is
  // (0.714 + 0.353) = 1.067em. At 1.0 the cap of the "Share" pill's S was losing
  // ~1.07pt (3px at 3x), which is the cut-off button in the 3.21.1 QA build.
  buttonSansSemiBold16: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(16),
    lineHeight: scaledDimension(20.8),
  },

  // Same face and size as `buttonSansSemiBold16` but on the paywall CTA's taller
  // 1.229 leading, which the fixed 56pt pill needs to stay optically centred.
  ctaSansSemiBold16: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(16),
    lineHeight: scaledDimension(19.664),
  },

  // Off-ladder rungs: sizes and leadings no rung above covers. A label whose metrics
  // match an existing rung uses that rung (`displaySansRegular20` for 20/26,
  // `copySansRegular14` for 14/18.2) rather than a second definition of it.
  labelSansRegular16: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(16),
    lineHeight: scaledDimension(19.36),
  },
  // The cancel-flow offer's centred subtitle, on a looser 22pt leading than body.
  subtitleSansRegular16: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(16),
    lineHeight: scaledDimension(22),
  },
  // The offer's plan price row on Nunito Sans' "normal" 1.364em leading: the struck-through
  // and period figures at 15, the price itself at 17.
  priceSansRegular15: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(15),
    lineHeight: scaledDimension(20.46),
  },
  priceSansSemiBold17: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(17),
    lineHeight: scaledDimension(23.19),
  },
  // The cancel-flow offer's "continue to cancellation" link, on Nunito Sans' "normal"
  // 1.364em leading like the price row above it.
  linkSansRegular14: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(14),
    lineHeight: scaledDimension(19.1),
  },
  // Nunito Sans' own 1.364em line box, which is the design's "normal" leading.
  labelSansRegular18: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(18),
    lineHeight: scaledDimension(24.55),
  },
  // 1.3 leading, not the 1.0 this carried as `labelProximaRegular19`. Nunito Sans declares a
  // 1.364em line box (ascender 1011 + descender 353 per 1000upem), so a lineHeight equal
  // to the size leaves iOS centring a 25.9pt glyph box in a 19pt line and clipping ~3.5pt
  // off each end — enough to cut the descenders on the onboarding topic cards. The face
  // this was authored against boxed at ~1.2em and survived it; Nunito Sans does not.
  labelSansRegular19: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(19),
    lineHeight: scaledDimension(24.7),
  },
  // Figma's `Tab Bar 11`. Every label fits its fifth of the pill at this size: the
  // widest, "Community", is 58pt of 64, and only English users get five tabs.
  tabLabelSansSemiBold11: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(11),
    lineHeight: scaledDimension(14.3),
  },
  ctaLabelSansSemiBold13: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(13),
  },
  // The cancel-flow offer's "Save 40%" tag, on Nunito Sans' "normal" 1.364em leading.
  titleSansBold14: {
    fontFamily: SANS_BOLD,
    fontSize: scaledDimension(14),
    lineHeight: scaledDimension(19.1),
  },
  titleSansSemiBold14: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(14),
    lineHeight: scaledDimension(18.2),
  },
  // 1.3, for the reason on `buttonSansSemiBold16`. At 1.0 the Login CTA lost
  // ~1.21pt off the top of its cap and the descender of the "g".
  ctaLabelSansSemiBold18: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(18),
    lineHeight: scaledDimension(23.4),
  },
  authorNameSansSemiBold14: {
    fontFamily: SANS_SEMIBOLD,
    fontSize: scaledDimension(13.674),
    lineHeight: scaledDimension(15.04),
  },
  authorRoleSansRegular14: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(13.674),
    lineHeight: scaledDimension(15.04),
  },
  // 1.1, not the 1.3 the button rungs above take, because a Text box measures exactly
  // `lineHeight` — so on the wordmark, whose box IS the layout, every extra point of
  // leading pushes whatever is stacked under it down by the same amount. 1.1 clears
  // the 1.065em a cap needs with ~0.9pt to spare and costs QOD only 2.58pt, which its
  // quote gap gives back. (1.3 would have cost 7.75pt.) 1.1 is already a rung the
  // ladder uses — `displaySansRegular25`, `authorNameSansSemiBold14`.
  wordmarkSansRegular30: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(30.19),
    lineHeight: scaledDimension(33.209),
    letterSpacing: scaledDimension(8.3),
  },

  // The same wordmark instance at 85.5%, as the quote-of-the-day frame places it.
  wordmarkSansRegular26: {
    fontFamily: SANS_REGULAR,
    fontSize: scaledDimension(25.84),
    lineHeight: scaledDimension(28.424),
    letterSpacing: scaledDimension(7.11),
  },
}
