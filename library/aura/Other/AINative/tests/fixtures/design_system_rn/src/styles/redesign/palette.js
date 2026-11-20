/**
 * @module redesignPalette
 * @description Literal colour values for the redesign. Screens use the semantic names
 *   in `redesign{Dark,Light}/color.js` instead; nothing outside `styles/redesign*`
 *   should import this file.
 */

export default {
  // Not folded into globals/colors.js' whiteAlpha ladder: that one is non-linear
  // (100 = 0.08) and lacks these steps, so its names would misreport the alpha.
  white05: 'rgba(255, 255, 255, 0.05)',
  white10: 'rgba(255, 255, 255, 0.1)',
  white12: 'rgba(255, 255, 255, 0.12)',
  white14: 'rgba(255, 255, 255, 0.14)',
  white16: 'rgba(255, 255, 255, 0.16)',
  white18: 'rgba(255, 255, 255, 0.18)',
  white20: 'rgba(255, 255, 255, 0.2)',
  white22: 'rgba(255, 255, 255, 0.22)',
  white25: 'rgba(255, 255, 255, 0.25)',
  white30: 'rgba(255, 255, 255, 0.3)',
  white35: 'rgba(255, 255, 255, 0.35)',
  white40: 'rgba(255, 255, 255, 0.4)',
  white50: 'rgba(255, 255, 255, 0.5)',
  white60: 'rgba(255, 255, 255, 0.6)',
  white70: 'rgba(255, 255, 255, 0.7)',
  white80: 'rgba(255, 255, 255, 0.8)',
  white85: 'rgba(255, 255, 255, 0.85)',
  white92: 'rgba(255, 255, 255, 0.92)',
  white95: 'rgba(255, 255, 255, 0.95)',
  black55: 'rgba(0, 0, 0, 0.55)',
  black05: 'rgba(0, 0, 0, 0.05)',
  black10: 'rgba(0, 0, 0, 0.1)',
  black20: 'rgba(0, 0, 0, 0.2)',
  black30: 'rgba(0, 0, 0, 0.3)',
  black40: 'rgba(0, 0, 0, 0.4)',
  black45: 'rgba(0, 0, 0, 0.45)',
  black50: 'rgba(0, 0, 0, 0.5)',
  black60: 'rgba(0, 0, 0, 0.6)',
  black70: 'rgba(0, 0, 0, 0.7)',
  blackMain: 'rgba(0, 0, 0, 1)',
  whiteMain: 'rgba(255, 255, 255, 1)',
  white0: 'rgba(255, 255, 255, 0)',

  // Not `blackMain`: the design draws content on white pills in this slightly
  // blue-shifted near-black, and does so in both frames.
  ink: 'rgba(26, 26, 31, 1)',

  // iOS' own switch palette, so the toggle matches the system control.
  switchTrackOff: 'rgba(235, 235, 245, 0.3)',
  switchTrackOn: 'rgba(52, 199, 89, 1)',

  softNeutral04: 'rgba(217, 217, 217, 0.04)',

  linkBlue: 'rgba(59, 130, 246, 1)',
  destructiveRed: 'rgba(242, 69, 79, 1)',
  canvasDark: 'rgba(19, 19, 19, 1)',
  canvasLight: 'rgba(248, 248, 248, 1)',
  // Darkest stop of the Welcome flow gradient, and so the fill the drifting washes are
  // painted over. Deliberately not `canvasDark` — the authored field bottoms out below
  // the page fill, and on that screen the difference is the whole bottom third.
  welcomeFlowBase: 'rgba(12, 14, 16, 1)',
  // Zero-alpha canvas — the transparent stop of a fade-into-the-page gradient.
  canvasDark0: 'rgba(19, 19, 19, 0)',
  canvasLight0: 'rgba(248, 248, 248, 0)',
  // The fade's middle stop. The bottom scrim is a three-stop ramp, not a straight
  // fade: it reaches this alpha a quarter of the way down and only goes fully opaque
  // at the very bottom, which is what keeps content legible behind the tab bar
  // without laying a hard band across the feed.
  canvasDark59: 'rgba(19, 19, 19, 0.59)',
  canvasLight59: 'rgba(248, 248, 248, 0.59)',

  // Skeleton tones are opaque because react-native-svg's gradient <Stop> drops the
  // alpha of an rgba stopColor — a translucent glass token renders as solid white.
  // These are white@5%/@10% over the dark canvas and black@5%/@10% over the light one,
  // pre-composited so the shimmer gradient honours them.
  skeletonBaseDark: 'rgba(31, 31, 31, 1)',
  skeletonShimmerDark: 'rgba(44, 44, 44, 1)',
  skeletonBaseLight: 'rgba(236, 236, 236, 1)',
  skeletonShimmerLight: 'rgba(223, 223, 223, 1)',

  // Pre-composited canvas + white@10%: RN cannot reproduce the mock's backdrop-blur.
  surfaceNavDark: 'rgba(32, 32, 34, 0.94)',
  surfaceNavLight: 'rgba(236, 236, 236, 0.94)',
  // Laid under the tab-bar blur; without it the blur samples colourful feed cards and
  // the glass pill washes out.
  navScrimDark: 'rgba(19, 19, 19, 0.72)',
  navScrimLight: 'rgba(248, 248, 248, 0.72)',

  accentTeal: 'rgba(1, 194, 161, 1)',
  liveRed: 'rgba(242, 69, 79, 1)',
  avatarRingDark: 'rgba(26, 26, 31, 1)',
  accentMint: 'rgba(140, 235, 178, 1)',
  // The light frames' mint and peach: the same accents, deep enough to read on a
  // light card where the dark frames' pastels would wash out.
  mintDeep: 'rgba(24, 130, 80, 1)',
  mintAlpha10: 'rgba(140, 235, 178, 0.1)',
  mintAlpha30: 'rgba(140, 235, 178, 0.3)',
  accentPeach: 'rgba(255, 209, 166, 1)',
  peachDeep: 'rgba(176, 98, 40, 1)',
  accentSky: 'rgba(105, 166, 200, 1)',
  accentLime: 'rgba(166, 196, 10, 1)',
  washViolet: 'rgba(143, 117, 245, 1)',
  washSteel: 'rgba(77, 128, 168, 1)',
  accentViolet: 'rgba(184, 158, 255, 1)',
  violetAlpha28: 'rgba(184, 158, 255, 0.28)',

  // The 4.0 primary CTA pill and the benefit check chip that shares its surface.
  // The chip takes this flat rose; the pill's two stops live in `redesign/gradients`.
  // Both are literal in the frame, so they do not change between light and dark.
  accentRose: 'rgba(247, 158, 172, 0.98)',
  ctaShadow: 'rgba(24, 21, 39, 0.34)',

  // The light cancel-flow offer's rose: the plan card's border and the two stops of
  // its save tag and CTA. The dark frame swaps all three to `accentViolet`.
  roseDeep: 'rgba(219, 123, 137, 1)',
  roseLight: 'rgba(246, 174, 188, 1)',
  ratingGold: 'rgba(255, 208, 43, 1)',

  // The paywall backdrop's conic sweep, sampled every 45 degrees around the ring.
  // RN has no conic gradient, so `PaywallAuroraBackdrop` rebuilds the sweep from
  // these; sampling only the 4 cardinals leaves visible gaps between the lobes.
  auroraNorth: 'rgba(132, 188, 255, 1)',
  auroraNorthEast: 'rgba(187, 222, 180, 1)',
  auroraEast: 'rgba(243, 255, 105, 1)',
  auroraSouthEast: 'rgba(249, 202, 110, 1)',
  auroraSouth: 'rgba(255, 148, 115, 1)',
  auroraSouthWest: 'rgba(255, 161, 181, 1)',
  auroraWest: 'rgba(255, 173, 247, 1)',
  auroraNorthWest: 'rgba(193, 181, 251, 1)',
  // Cool grey the paywall's App Store / downloads glyphs are drawn in — the one
  // place the redesign departs from the white-alpha icon ladder.
  statGrey: 'rgba(144, 146, 163, 1)',
  // ⚠️ Eyeballed off a screenshot — the source frame is in a Figma file the MCP cannot
  // open. Replace with the real token when it becomes readable.
  accentPeriwinkle: 'rgba(155, 164, 240, 1)',
}
