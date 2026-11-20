// Duplicated from globals/gradients rather than imported, so this layer has no edge
// into the shared file and lifts out as a unit when the experiment resolves.
const gradient = (angle, colors = [], locations) => ({
  useAngle: true,
  angle,
  colors,
  locations,
})

const gradients = {
  gOnboardingDark: gradient(
    180,
    ['rgba(16,18,20,0.45)', 'rgba(16,18,20,0.05)', 'rgba(19,19,19,0.92)'],
    [0, 0.4, 1]
  ),
  gOnboardingCardScrim: gradient(
    180,
    ['rgba(0,0,0,0.05)', 'rgba(0,0,0,0.65)'],
    [0.3, 1]
  ),
  // Linear, not the radial the mock draws: react-native-svg 12's percentage viewport
  // does not track a content-height row and clipped the glow halfway down.
  gOnboardingRowSelectedGlow: gradient(
    90,
    ['rgba(82,114,112,1)', 'rgba(55,88,85,0)'],
    [0.174, 0.918]
  ),
  gOnboardingRowSelectedGlowViolet: gradient(
    90,
    ['rgba(63,58,109,1)', 'rgba(108,82,114,0)'],
    [0.174, 0.918]
  ),
  gOnboardingRowSelectedGlowBlue: gradient(
    90,
    ['rgba(108,123,159,1)', 'rgba(63,95,174,0)'],
    [0.174, 0.918]
  ),
  // Core alpha is the render's 0.40, not the mock's 0.54: that stop sits before the
  // row's left edge, so the raw value over-lightens by ~20%.
  gOnboardingRowSelectedGlowPeach: gradient(
    90,
    ['rgba(229,192,162,0.40)', 'rgba(20,16,14,0)'],
    [0.174, 0.918]
  ),
  // The light frames tint fewer hues than the dark ones: teal on the goals step, sky on
  // the steps the dark frames tint violet or peach, slate on the accent step.
  gOnboardingRowSelectedGlowLight: gradient(
    90,
    ['rgba(95,130,127,1)', 'rgba(90,132,128,0)'],
    [0.174, 0.918]
  ),
  gOnboardingRowSelectedGlowSkyLight: gradient(
    90,
    ['rgba(158,199,245,1)', 'rgba(126,146,169,0)'],
    [0.174, 0.918]
  ),
  gOnboardingRowSelectedGlowSlateLight: gradient(
    90,
    ['rgba(126,156,169,1)', 'rgba(126,156,169,0)'],
    [0.174, 0.918]
  ),
  // Unselected state only.
  gOnboardingTopicCardScrim: gradient(
    180,
    ['rgba(0,0,0,0)', 'rgba(0,0,0,0.2)'],
    [0, 0.92]
  ),
  // The mock's 4pt background blur is omitted — RN has no backdrop-filter.
  gOnboardingWelcomeScrim: gradient(
    180,
    ['rgba(2,3,3,0)', 'rgba(2,3,3,1)'],
    [0, 1]
  ),
  // Second, taller fade the quiz screens layer under the CTA scrim above.
  gOnboardingScreenScrim: gradient(
    180,
    ['rgba(0,0,0,0)', 'rgba(0,0,0,0.59)', 'rgba(0,0,0,1)'],
    [0, 0.248, 1]
  ),
  // The light frames fade the same two bands into their own canvas, not into black.
  gOnboardingWelcomeScrimLight: gradient(
    180,
    ['rgba(248,248,248,0)', 'rgba(248,248,248,1)'],
    [0, 1]
  ),
  gOnboardingScreenScrimLight: gradient(
    180,
    ['rgba(248,248,248,0)', 'rgba(248,248,248,0.59)', 'rgba(248,248,248,1)'],
    [0, 0.248, 1]
  ),
  // The plan loader's duration tile. Drawn as an SVG radial, which reads only the
  // colours: the session-length violet on dark, the goals step's teal on light.
  gPlanDurationTileGlow: gradient(0, ['#3F3A6D', '#6C5272']),
  gPlanDurationTileGlowLight: gradient(0, ['#5F827F', '#5F827F']),
  // The voice-preview card's `Rectangle 356` wash: near-black on dark, a whisper of
  // white on light. The blobs under it are the same in both frames.
  gVoicePreviewPanel: gradient(180, ['rgba(0, 0, 0, 0.2)', 'rgba(7, 7, 7, 0.2)']),
  gVoicePreviewPanelLight: gradient(180, [
    'rgba(255, 255, 255, 0.12)',
    'rgba(247, 247, 247, 0.08)',
  ]),
  gPlanProgressGlow: gradient(
    90,
    ['#F3FF69', '#FF9473', '#FFADF7', '#84BCFF', '#65FF3F'],
    [0, 0.24, 0.51, 0.74, 1]
  ),
  // Linear stands in for the mock's radial: the visible falloff is top-to-bottom, and
  // react-native-linear-gradient has no radial mode.
  gHomeBackdrop: gradient(
    180,
    ['rgba(19,19,19,0.25)', 'rgba(19,19,19,0.85)', 'rgba(19,19,19,1)'],
    [0, 0.35, 0.7]
  ),
  // The light program page keeps more of the blurred artwork at the top and
  // dissolves it into its own canvas.
  gProgramBackdropLight: gradient(
    180,
    ['rgba(248,248,248,0)', 'rgba(248,248,248,0.55)', 'rgba(248,248,248,1)'],
    [0, 0.5, 1]
  ),
  gCardScrim: gradient(
    180,
    ['rgba(0,0,0,0)', 'rgba(0,0,0,0)', 'rgba(0,0,0,0.65)'],
    [0, 0.45, 1]
  ),
  // The Today card on the light feed fades its cover into the glass, not black.
  gCardScrimLight: gradient(
    180,
    ['rgba(255,255,255,0)', 'rgba(255,255,255,0)', 'rgba(255,255,255,0.65)'],
    [0, 0.679, 1]
  ),
  gCelebrityScrim: gradient(
    180,
    ['rgba(0,0,0,0)', 'rgba(0,0,0,0.7)'],
    [0.42, 1]
  ),
  // Linear stands in for the node's radial on both playlist tiles: the falloff runs
  // top-to-bottom across a 180pt square, and react-native-linear-gradient has no
  // radial mode.
  gRecentTracksTile: gradient(
    180,
    ['rgba(82,114,112,1)', 'rgba(55,88,85,0)'],
    [0.074, 1]
  ),
  gSavedTracksTile: gradient(
    180,
    ['rgba(114,82,112,1)', 'rgba(114,82,112,0)'],
    [0.074, 1]
  ),
  // Downloaded tracks and Listen later have no frame of their own, so their tints
  // follow the two above — same ramp and value, hue-rotated so the four artwork-less
  // playlists read apart from one another. Design to confirm the hues.
  gDownloadTracksTile: gradient(
    180,
    ['rgba(82,98,114,1)', 'rgba(82,98,114,0)'],
    [0.074, 1]
  ),
  gListenLaterTile: gradient(
    180,
    ['rgba(114,104,82,1)', 'rgba(114,104,82,0)'],
    [0.074, 1]
  ),
  gSpecialtyMint: gradient(
    90,
    ['rgba(140,235,178,0.08)', 'rgba(77,166,140,0.05)'],
    [0, 1]
  ),
  gSpecialtyPink: gradient(
    90,
    ['rgba(242,166,217,0.08)', 'rgba(140,115,242,0.05)'],
    [0, 1]
  ),
  // The primary CTA pill. Its angle and stops are authored on the component itself
  // rather than through the CTA Color variables, so the fill is the same in both frames.
  gCtaPrimary: gradient(
    82.31,
    ['rgba(219,123,137,1)', 'rgba(246,174,188,1)'],
    [0.037, 1]
  ),
  // The pill's top sheen. Its first stop composites the node's 1pt inset white
  // highlight into the ramp: RN has no inset shadow, and a real border would draw the
  // highlight all the way round the pill instead of along its top edge.
  gCtaSheen: gradient(
    180,
    ['rgba(255,255,255,0.3)', 'rgba(255,255,255,0.14)', 'rgba(255,255,255,0)'],
    [0, 0.02, 0.6]
  ),
  gBottomCtaScrim: gradient(
    180,
    ['rgba(19,19,19,0)', 'rgba(19,19,19,0.59)', 'rgba(19,19,19,1)'],
    [0, 0.24773, 1]
  ),
  gBottomCtaScrimLight: gradient(
    180,
    ['rgba(248,248,248,0)', 'rgba(248,248,248,0.59)', 'rgba(248,248,248,1)'],
    [0, 0.24773, 1]
  ),
  // The isolated sheet surface: black@40% + a 200 background blur over the
  // profile. A Modal cannot sample that window, so this is the exported
  // composite (surfaceNavDark into canvas) — same rationale as the tab-bar fill.
  gCoachSheet: gradient(
    180,
    ['rgba(32,32,34,1)', 'rgba(19,19,19,1)'],
    [0, 1]
  ),
  // The light frame's sheet is the page canvas itself, with no lift.
  gCoachSheetLight: gradient(
    180,
    ['rgba(248,248,248,1)', 'rgba(248,248,248,1)'],
    [0, 1]
  ),
  // Starts at 70.76% of the hero — the coach cutout's head clears the fade, and the
  // photo's bottom edge is fully absorbed into the canvas.
  gLiveHeroScrim: gradient(180, ['rgba(19,19,19,0)', 'rgba(19,19,19,1)'], [0.7076, 1]),
  // The light frame dissolves the hero into its own canvas, not into black.
  gLiveHeroScrimLight: gradient(
    180,
    ['rgba(248,248,248,0)', 'rgba(248,248,248,1)'],
    [0.7076, 1]
  ),
  gLiveCountdownCard: gradient(
    180,
    ['rgba(255,255,255,0.12)', 'rgba(255,255,255,0.04)'],
    [0, 1]
  ),
  // Two linears stand in for the node's ANGULAR sweep — react-native-linear-gradient has
  // no angular mode. The axis is the bottom-left→top-right diagonal because that is the
  // one whose endpoints the sweep actually separates: sampling the node's own render puts
  // violet at the bottom-left corner and peach at the top-right, with the blue stop
  // crossing the middle. A 135deg axis instead lands the same colour on both ends and
  // inverts every corner.
  gLiveCountdownBorder: gradient(
    45,
    [
      'rgba(204,158,235,0.9)',
      'rgba(140,178,242,0.9)',
      'rgba(255,217,178,0.9)',
    ],
    [0, 0.5, 1]
  ),
  // The sweep tints the bottom-right corner pink, which the axis above cannot reach: that
  // corner shares the gradient's midpoint with the top-left, so a stop placed there would
  // move both. Laid over the base as a second pass, clear until past the midline, it
  // recolours only that corner.
  gLiveCountdownBorderCorner: gradient(
    135,
    ['rgba(230,188,207,0)', 'rgba(230,188,207,0)', 'rgba(230,188,207,0.9)'],
    [0, 0.55, 1]
  ),
  // The three share affordances on the reservation sheet; brand-coloured, so the same
  // values in both frames.
  gShareMessage: gradient(180, ['rgba(126,231,120,1)', 'rgba(103,205,90,1)'], [0, 1]),
  gShareStory: gradient(
    180,
    ['rgba(216,63,139,1)', 'rgba(248,80,75,1)', 'rgba(255,183,101,1)'],
    [0, 0.47, 1]
  ),
  gShareEvent: gradient(180, ['rgba(166,119,222,1)', 'rgba(115,133,236,1)'], [0, 1]),
  // Placeholder behind the coaching getting-started cover before its art loads.
  gCoachingMediaCover: gradient(
    180,
    ['rgba(43,87,82,1)', 'rgba(23,20,36,1)'],
    [0, 1]
  ),
  // The light cancel-flow offer's save tag and CTA. The frame draws the tag at 85deg and
  // the pill at 82deg; one angle serves both at this size.
  gOfferRose: gradient(
    85,
    ['rgba(219,123,137,1)', 'rgba(246,174,188,1)'],
    [0.037, 1]
  ),
  gGiftPromo: gradient(
    130,
    ['rgba(51,41,56,1)', 'rgba(89,64,77,1)', 'rgba(140,107,97,1)'],
    [0, 0.5, 1]
  ),
  gGiftOrb: gradient(
    90,
    ['rgba(182,102,223,0.8)', 'rgba(220,99,162,0.8)', 'rgba(249,85,35,0.8)'],
    [0, 0.317, 1]
  ),

  // Search topic tiles. The set is fixed but topics are server-driven, so a tile
  // picks its gradient by hashing the topic key — see `logic/searchTopicGradient`.
  // 135deg: the node's handles run right-and-down at 45deg off vertical.
  gTopicIndigo: gradient(135, ['rgba(26,31,71,1)', 'rgba(51,43,102,1)'], [0, 1]),
  gTopicTeal: gradient(135, ['rgba(26,74,79,1)', 'rgba(13,43,59,1)'], [0, 1]),
  gTopicBlue: gradient(135, ['rgba(31,51,102,1)', 'rgba(26,36,77,1)'], [0, 1]),
  gTopicPlum: gradient(135, ['rgba(112,61,87,1)', 'rgba(77,43,69,1)'], [0, 1]),

  // Start/end rather than an angle: these are the node's own gradient handles, so the
  // direction carries over exactly instead of being re-derived from a degree value
  // (which `useAngle` resolves against the box, not the design's 362x110).
  gSettingsHeroPremium: {
    colors: [
      'rgba(51, 41, 56, 1)',
      'rgba(89, 64, 77, 1)',
      'rgba(140, 107, 97, 1)',
    ],
    locations: [0, 0.5, 1],
    start: { x: 0, y: 0 },
    end: { x: 0.676, y: 0.946 },
  },
}

/**
 * Radial washes. `react-native-linear-gradient` has no radial mode, so these carry
 * SVG stops for `react-native-svg` rather than the linear prop bag above — see
 * `SettingsAuroraBackdrop` for how they are consumed. Reached as
 * `theme.gradients.radial.<name>`: namespaced because the shape differs, so a radial
 * can never be spread into a `LinearGradient` by mistake.
 */
const radialGradients = {
  gSettingsAurora: [
    { color: '#D9BFF2', opacity: 0.3, offset: 0 },
    { color: '#E5D9F2', opacity: 0.1, offset: 0.55 },
    { color: '#FFFFFF', opacity: 0, offset: 1 },
  ],
  // The node's badge has no fill: the card's bloom shows through it and a blurred core
  // plus inset highlights lift it. So the badge is this white core over the bloom, not an
  // opaque export — an export bakes in whatever sat behind it and blocks the bloom.
  gSettingsFamilyBadgeCore: [
    // Warm, not neutral: a white lift left the badge ~25 short on red against the node,
    // because the conic's warm quadrant is the half the export loses most.
    { color: '#FFD4DC', opacity: 0.34, offset: 0 },
    { color: '#FFD4DC', opacity: 0.27, offset: 0.62 },
    { color: '#FFD4DC', opacity: 0.14, offset: 1 },
  ],
  // The node is an opaque ellipse under a 170pt layer blur, which RN cannot render.
  // A wide radial falloff is the closest standing equivalent.
  gSettingsPremiumGlow: [
    { color: '#FFD9A6', opacity: 0.2, offset: 0 },
    { color: '#FFD9A6', opacity: 0, offset: 1 },
  ],
  // Ambient glows behind the longer Settings pages. The nodes sit under 100-120pt layer
  // blurs, which RN cannot render, so the falloff carries the softness instead.
  gSettingsGlowViolet: [
    { color: '#D9CCFA', opacity: 0.2, offset: 0 },
    { color: '#D9CCFA', opacity: 0.06, offset: 0.5 },
    { color: '#FFFFFF', opacity: 0, offset: 1 },
  ],
  gSettingsGlowRose: [
    { color: '#FAD1E5', opacity: 0.18, offset: 0 },
    { color: '#FAD1E5', opacity: 0.06, offset: 0.5 },
    { color: '#FFFFFF', opacity: 0, offset: 1 },
  ],
  // The Live card's glow behind the host photo, a 47pt blur in the design. One element
  // in both frames: its blue core reads through the dark card's scrims, while over the
  // light glass it reads as a slate wash, so the light stops are fitted to that frame.
  gLiveCardGlow: [
    { color: '#68ADFF', opacity: 0.85, offset: 0 },
    { color: '#354151', opacity: 0.5, offset: 0.6 },
    { color: '#354151', opacity: 0, offset: 1 },
  ],
  gLiveCardGlowLight: [
    { color: '#3C4656', opacity: 0.66, offset: 0 },
    { color: '#3E4E62', opacity: 0.5, offset: 0.45 },
    { color: '#40546E', opacity: 0.34, offset: 0.6 },
    { color: '#4C627C', opacity: 0.1, offset: 0.85 },
    { color: '#4C627C', opacity: 0, offset: 1 },
  ],
}

export default {
  ...gradients,
  radial: radialGradients,
}
