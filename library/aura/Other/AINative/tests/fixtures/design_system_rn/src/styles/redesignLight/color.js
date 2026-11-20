// Every token is redefined here rather than inherited, so a white-alpha value can
// never leak onto a light canvas. "Shared" marks values identical in both frames.

import Color from '../redesign/palette'

export default {
  // Text
  textPrimary: Color.blackMain,
  textEmphasis: Color.black70,
  // Not textEmphasis: that is one step brighter on dark for the featured/podcast
  // cards, while the search chips, Clear pills and coach role are 70% in both frames.
  textSecondary: Color.black70,
  textTertiary: Color.black50,
  textSubdued: Color.black60, // the cancel-flow offer's subtitle and price period
  textMuted: Color.black40,
  textPlaceholder: Color.black20,
  textAccent: Color.accentPeriwinkle, // shared — brand accent
  textOnColorEmphasis: Color.white85, // shared — Settings hero eyebrow
  textOnColorMuted: Color.white70, // shared — sits on the gift-card gradient
  textOnInverse: Color.whiteMain, // an unlocked badge's numeral on surfaceInverse
  textOnInverseMuted: Color.white70, // coach-profile CTA's "spots left" line
  // Its consumer, the Aurie field on Home, renders constant-dark, so only the dark
  // value is ever drawn. Declared for the key parity redesignTokens asserts.
  textGlow: Color.white50, // shared — the Aurie field's placeholder glow on Home

  // Surfaces
  canvas: Color.canvasLight, // page fill #F8F8F8
  // shared — the Welcome screen renders constant-dark in both frames
  welcomeFlowBase: Color.welcomeFlowBase, // Welcome flow gradient's darkest stop
  canvasTransparent: Color.canvasLight0,
  canvasTranslucent: Color.canvasLight59, // bottom scrim's middle stop
  surfaceSubtle: Color.white50, // rows / stats / nav circles
  surfaceTopicCard: Color.white40, // onboarding topic card, under its glow
  surfaceSoft: Color.black05,
  surfaceGlass: Color.white80, // row icon-box
  // The Settings CTA pill is white and the delete action red on BOTH themes.
  surfaceGlassOnCanvas: Color.white10, // shared — secondary pill on a dark page
  surfaceInverse: Color.blackMain,
  surfaceOnColor: Color.white14, // shared — Settings hero chevron chip
  surfaceOverlay: Color.black40,
  surfaceSheet: Color.white10, // shared — confirm-sheet surface
  surfaceScrimSheet: Color.black55, // shared — confirm-sheet scrim
  // Its consumer, the paywall, renders constant-dark, so only the dark value is
  // ever drawn. Declared for the key parity redesignTokens asserts.
  surfaceModalScrim: Color.black60, // shared — dimmer behind a modal sheet
  // Its consumer, the live-details back chip, sits on a constant-dark hero, so only the
  // dark value is ever drawn. Declared for the key parity redesignTokens asserts.
  surfaceScrim: Color.black30,
  surfaceScrimStrong: Color.black20, // dims the booking screen behind its date menu
  surfaceBadge: Color.black20,
  // The customize-tabs sheet's add/remove badge: a grey disc with a black glyph on
  // the light sheet.
  surfaceControlBadge: Color.black20,
  iconControlBadge: Color.blackMain,
  surfacePlaceholder: Color.white20, // shared — the sheet is constant-dark // shared — sits on card imagery, not the frame
  coverScrim: Color.black20, // shared — plan-preview card cover wash
  // Opaque on light, glass on dark — the search coach/library card, which sits on
  // the canvas rather than on imagery, so it cannot use the translucent surfaces.
  surfaceCard: Color.whiteMain,
  surfaceTagOnLight: Color.white40, // shared — tag over a light card cover
  skeletonBase: Color.skeletonBaseLight,
  skeletonShimmer: Color.skeletonShimmerLight,
  surfaceNav: Color.surfaceNavLight,
  navScrim: Color.navScrimLight,
  navWash: Color.black05, // tab-bar pill wash

  // Borders
  borderSubtle: Color.black05, // row / stats hairline
  borderDefault: Color.black10, // stats divider
  borderStrong: Color.black20,
  // Lighter than borderStrong: the light frame outlines the pill as faintly as its fill.
  borderCardCta: Color.black10, // CoachCard "See details" CTA
  borderSelected: Color.black30,
  borderSelectedRow: Color.black40,
  borderActive: Color.black40,
  borderOnColorSubtle: Color.white10, // shared — Settings hero hairline
  borderOnColorStrong: Color.white22, // shared — Settings hero chevron chip
  borderOnColor: Color.white12, // shared — sits on the gift-card gradient
  borderOnImage: Color.white05, // shared — chip hairline over card imagery

  // Icons
  iconMuted: Color.black70, // row icon stroke
  // The light frame drops the soft glow the dark one draws behind a chip's glyph.
  iconGlow: Color.white0,

  // Accents — shared, brand hues carry across both frames
  accentLink: Color.linkBlue, // shared — inline text links
  accentDestructive: Color.destructiveRed, // shared — delete-account actions
  accentTeal: Color.accentTeal,
  accentLive: Color.liveRed,
  // Not shared: the coach card and the discovery-session pill sit on light surfaces
  // here, so the light frame deepens both.
  accentMint: Color.mintDeep, // discovery-session pill label + dot
  // shared — sits on the coach-profile CTA pill, not the page, so it keeps its brightness.
  accentMintOnInverse: Color.accentMint,
  accentPeach: Color.peachDeep, // "Free Discovery Session Included"
  accentSky: Color.accentSky, // shared
  accentLime: Color.accentLime, // shared
  heroWashPrimary: Color.washViolet, // shared
  heroWashSecondary: Color.washSteel, // shared
  mintSurface: Color.mintAlpha10,
  mintBorder: Color.mintAlpha30,
  avatarRing: Color.canvasLight, // ring reads as the page behind the avatars
  avatarRingOnCard: Color.whiteMain, // coach-card avatar sits on the card, not the page

  // Paywall accents
  accentViolet: Color.accentViolet, // promo-pill border
  accentVioletGlow: Color.violetAlpha28, // promo-pill outer glow
  // shared — the CTA pill and the benefit check chip are one Figma component, so the
  // fill, its shadow tint and the content drawn on it are literal in both frames.
  accentRose: Color.accentRose, // benefit check chip fill
  ctaShadow: Color.ctaShadow, // the CTA pill's drop shadow
  onAccentPrimary: Color.whiteMain, // CTA label + check glyph

  // Cancel-flow offer — the plan card's border, save tag and check. Rose on light,
  // violet on dark: the two frames swap hue, not just lightness.
  accentOffer: Color.roseDeep,
  surfaceOfferCard: Color.white92,
  accentRating: Color.ratingGold, // review + App Store star fill
  iconStat: Color.statGrey, // social-proof stat glyphs

  // On-light — the bottom CTA is a white pill in both frames.
  onLightPrimary: Color.blackMain,
  onLightSecondary: Color.black70,
  onLightBorder: Color.black05,
  onLightInk: Color.ink, // shared — selected search chip + the library CTA

  // Controls — shared, iOS system switch palette
  progressTrack: Color.white25, // shared — sits on hero imagery
  progressTrackSubtle: Color.black20, // badge topic progress on a card
  switchTrackOff: Color.switchTrackOff,
  switchTrackOn: Color.switchTrackOn,

  // Paywall aurora backdrop — shared: the sweep is literal in both frames, so the
  // wash reads the same and only the canvas under it changes.
  auroraNorth: Color.auroraNorth,
  auroraNorthEast: Color.auroraNorthEast,
  auroraEast: Color.auroraEast,
  auroraSouthEast: Color.auroraSouthEast,
  auroraSouth: Color.auroraSouth,
  auroraSouthWest: Color.auroraSouthWest,
  auroraWest: Color.auroraWest,
  auroraNorthWest: Color.auroraNorthWest,

  // Sits on top of the aurora disc rather than the page, so it stays white in both
  // themes — reading `textPrimary` here turned the reminder bell black in light mode.
  onMediaPrimary: Color.whiteMain,

  // Stands in for the bell medallion's four inset white glows, which RN cannot draw.
  // Same value in both modes: the frame authors them white at 25% either way.
  onMediaRim: Color.white25,
}
