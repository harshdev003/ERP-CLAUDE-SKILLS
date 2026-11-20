/**
 * @module redesignStyleSheet
 * @description App-level style constants for the redesign, shared by both redesign
 *   themes and read as `theme.styles.<name>` — the 4.0 counterpart of
 *   `newDark/styleSheet.js`. Not component styles: only values several 4.0 surfaces
 *   have to agree on belong here, and they are reached through the theme rather than
 *   imported into each component, so a surface can never disagree with its siblings.
 */

import dimensions from './dimensions'

export default {
  // How long a card waits before showing its pressed state, in ms.
  //
  // A `TouchableOpacity` at the default `delayPressIn` of 0 dims the moment a finger
  // lands, so dragging a card to scroll flashed its pressed state before the ScrollView
  // stole the responder (QA ap10). Delaying past the point a scroll gesture declares
  // itself means the highlight never appears for a drag, while a real tap — which lifts
  // well after this — still gets its acknowledgement.
  //
  // 130ms is React Native's own `Touchable` highlight delay (`HIGHLIGHT_DELAY_MS`),
  // the value `TouchableHighlight` uses for the same reason.
  cardPressInDelay: 130,

  // Pressed-state opacity for a card. `TouchableOpacity` defaults to 0.2, which on a
  // full-bleed hero reads as the screen going dark rather than as a tap.
  cardActiveOpacity: 0.9,

  // Entrance for a centred sheet — the reminder picker, the habit picker, the menus.
  //
  // Sliding the whole modal up from the bottom edge (RN's `animationType="slide"`, and
  // the equivalent `LayoutAnimation` offset trick) carries the overlay along with the
  // sheet, so the scrim arrives travelling rather than fading (QA ob2). Pairing this
  // with a fade on the overlay gives the two their own motion: the scrim fades, the
  // sheet rises a little way from just below centre and scales in.
  sheetEnter: {
    from: { opacity: 0, scale: 0.94, translateY: dimensions.d28 },
    to: { opacity: 1, scale: 1, translateY: 0 },
  },
  sheetEnterDuration: 260,

  // Overlay fade, shorter than the sheet so the scrim is already there behind it.
  overlayFadeDuration: 200,

  // The bottom-stack scrim's gradient stops, as fractions of its own height.
  //
  // A three-stop ramp: transparent, then `canvasTranslucent` a quarter of the way
  // down, then the canvas itself only at the very bottom. Reaching full opacity early
  // — the previous 62% — laid a visible hard band across the feed above the bar. The
  // colours are the surface's own canvas tokens, resolved where the scrim is drawn.
  bottomScrimLocations: [0, 0.24773, 1],
}
