// Keep the key set in sync with `redesignLight/color.js` — a token defined on only
// one of the two silently renders `undefined` on the other.

import Color from '../redesign/palette'

export default {
  // Text
  textPrimary: Color.whiteMain,
  textEmphasis: Color.white85, // featured-card date + attendee count
  textSecondary: Color.white70,
  textTertiary: Color.white50,
  textSubdued: Color.white60, // the cancel-flow offer's subtitle and price period
  textMuted: Color.white40,
  textPlaceholder: Color.white20, // `Field` placeholder
  // ⚠️ Screenshot-sampled, not a Figma variable — see the note on the base token.
  textAccent: Color.accentPeriwinkle, // Made-For-You mix title
  textOnColorEmphasis: Color.white85, // shared — Settings hero eyebrow
  textOnColorMuted: Color.white70, // gift-card subtitle
  textOnInverse: Color.blackMain, // an unlocked badge's numeral on surfaceInverse
  textOnInverseMuted: Color.black70, // coach-profile CTA's "spots left" line
  textGlow: Color.white50, // shared — the Aurie field's placeholder glow on Home

  // Surfaces
  canvas: Color.canvasDark, // backdrop base #131313
  // shared — the Welcome screen renders constant-dark in both frames
  welcomeFlowBase: Color.welcomeFlowBase, // Welcome flow gradient's darkest stop
  canvasTransparent: Color.canvasDark0,
  canvasTranslucent: Color.canvasDark59, // bottom scrim's middle stop
  surfaceSubtle: Color.white05,
  surfaceTopicCard: Color.white05, // onboarding topic card, under its glow (= surfaceSubtle)
  surfaceSoft: Color.softNeutral04, // `Field` input + summary-card icon chip
  surfaceGlass: Color.white10,

  // The Settings CTA pill is white and the delete action red on BOTH themes.
  surfaceGlassOnCanvas: Color.white10, // shared — secondary pill on a dark page
  surfaceInverse: Color.whiteMain,
  surfaceOnColor: Color.white14, // shared — Settings hero chevron chip
  surfaceOverlay: Color.black40,
  surfaceModalScrim: Color.black60, // shared — dimmer behind a modal sheet
  surfaceSheet: Color.white10, // shared — confirm-sheet surface
  surfaceScrimSheet: Color.black55, // shared — confirm-sheet scrim
  surfaceScrim: Color.black30, // nav-control chip over hero imagery
  surfaceScrimStrong: Color.black45, // dims the screen behind a dropdown menu
  surfaceBadge: Color.black20, // premium-lock tag over card imagery
  // shared — the customize-tabs sheet is constant-dark, so its add/remove
  // badge reads the same white tones in both frames.
  surfaceControlBadge: Color.white16,
  iconControlBadge: Color.white95,
  surfacePlaceholder: Color.white20, // avatar placeholder on the introduce sheet
  coverScrim: Color.black20, // plan-preview card cover wash
  surfaceCard: Color.white05, // search coach/library card on the canvas
  surfaceTagOnLight: Color.white40, // shared — tag over a light card cover
  skeletonBase: Color.skeletonBaseDark,
  skeletonShimmer: Color.skeletonShimmerDark,
  surfaceNav: Color.surfaceNavDark, // pre-composited tab-bar surface
  navScrim: Color.navScrimDark, // dark scrim under the tab-bar blur
  navWash: Color.white10, // tab-bar pill wash over the blur

  // Borders
  borderSubtle: Color.white05,
  borderDefault: Color.white10,
  borderStrong: Color.white18, // the Aurie field on Home
  borderCardCta: Color.white18, // CoachCard "See details" CTA
  borderSelected: Color.white30, // selected topic card
  borderSelectedRow: Color.white35, // selected option row
  borderActive: Color.white40, // active category chip + Me-tab event filter chip
  borderOnColorSubtle: Color.white10, // shared — Settings hero hairline
  borderOnColorStrong: Color.white22, // shared — Settings hero chevron chip
  borderOnColor: Color.white12, // gift-card hairline
  borderOnImage: Color.white05, // chip hairline over card imagery

  // Icons
  iconMuted: Color.white70,
  iconGlow: Color.whiteMain, // the soft glow behind a topic chip's glyph

  // Accents
  accentLink: Color.linkBlue, // shared — inline text links
  accentDestructive: Color.destructiveRed, // shared — delete-account actions
  accentTeal: Color.accentTeal, // social-proof figure + icon
  accentLive: Color.liveRed, // live-badge dot + label
  accentMint: Color.accentMint, // discovery-session pill label + dot
  accentMintOnInverse: Color.accentMint, // shared — availability dot on the coach-profile CTA
  accentPeach: Color.accentPeach, // "Free Discovery Session Included"
  // shared — brand accent, reads over the dark canvas and over hero imagery alike
  accentSky: Color.accentSky, // program stat icons + "Show more" link
  accentLime: Color.accentLime, // coaching plan savings line
  // shared — the detail-hero glow is painted over the canvas in both frames
  heroWashPrimary: Color.washViolet, // media-detail hero wash, inner stop
  heroWashSecondary: Color.washSteel, // media-detail hero wash, outer stop
  mintSurface: Color.mintAlpha10, // discovery-session pill fill
  mintBorder: Color.mintAlpha30, // discovery-session pill border
  avatarRing: Color.avatarRingDark, // attendee avatar-stack ring
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
  accentOffer: Color.accentViolet,
  surfaceOfferCard: Color.white10,
  accentRating: Color.ratingGold, // review + App Store star fill
  iconStat: Color.statGrey, // social-proof stat glyphs

  // Sits on top of the aurora disc rather than the page, so it stays white in both
  // themes — reading `textPrimary` here turned the reminder bell black in light mode.
  onMediaPrimary: Color.whiteMain,

  // Stands in for the bell medallion's four inset white glows, which RN cannot draw.
  // Same value in both modes: the frame authors them white at 25% either way.
  onMediaRim: Color.white25,

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

  // On-light
  onLightPrimary: Color.blackMain,
  onLightSecondary: Color.black70,
  onLightBorder: Color.black05,
  onLightInk: Color.ink, // selected search chip + the library CTA

  // Controls
  progressTrack: Color.white25, // program hero progress track
  progressTrackSubtle: Color.white20, // badge topic progress on a card
  switchTrackOff: Color.switchTrackOff,
  switchTrackOn: Color.switchTrackOn,
}
