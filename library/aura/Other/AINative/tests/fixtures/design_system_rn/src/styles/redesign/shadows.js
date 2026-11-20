/**
 * @module redesignShadows
 * @description Drop shadows for the redesign, shared by both redesign themes.
 *
 *   `shadow` is duplicated from `globals/shadows` rather than imported, so this layer
 *   keeps no edge into the shared files and lifts out as a unit when the experiment
 *   resolves — same reasoning as `redesign/gradients`.
 *
 *   Figma blur maps to half its value on iOS, so `shadowRadius = blur / 2`. A node's
 *   `spread` has no RN equivalent and is NOT folded in here — the caster view is inset by
 *   it instead (see `SettingsHeroBanner`), which reproduces the geometry exactly rather
 *   than approximating it with a smaller blur.
 */

import { scaledDimension } from './dimensions'

const shadow = (height, radius, color, elevation) => ({
  shadowColor: color,
  shadowOffset: { width: 0, height: scaledDimension(height) },
  shadowOpacity: 1,
  shadowRadius: scaledDimension(radius),
  elevation,
})

export default {
  shadowSettingsHeroPremium: shadow(12, 12, 'rgba(46, 26, 77, 0.45)', 12),
  shadowSettingsHeroFamily: shadow(12, 12, 'rgba(39, 31, 34, 1)', 12),
  // The welcome offer card's lift off the canvas — blur 18, so radius 9.
  shadowOfferCard: shadow(6, 9, 'rgba(0, 0, 0, 0.15)', 6),
  // A sheet panel's lift off the dimmed canvas — the FilterBackdrop shadow the filter
  // and customize-tabs sheets draw, kept identical so the three panels lift alike.
  shadowSheetPanel: shadow(-12, 40, 'rgba(0, 0, 0, 0.5)', 12),
  // The pre-session dialog's lift off the dimmed player.
  shadowPlayerDialog: shadow(24, 16, 'rgba(0, 0, 0, 0.25)', 12),
  // The gratitude entry's ••• menu, dropped over the list.
  shadowEntryMenu: shadow(12, 20, 'rgba(0, 0, 0, 0.16)', 8),

  // Cancel flow. The cards' lift is a whisper (3-4%), the pill's a real drop; the pill's
  // -4 spread is the inset of its caster view (`UnsubscribePill`).
  shadowUnsubscribeCard: shadow(4, 10, 'rgba(0, 0, 0, 0.03)', 2),
  shadowUnsubscribePlanCard: shadow(8, 12, 'rgba(0, 0, 0, 0.04)', 4),
  shadowUnsubscribeCta: shadow(8, 12, 'rgba(0, 0, 0, 0.12)', 6),
  shadowUnsubscribeSheet: shadow(-8, 20, 'rgba(0, 0, 0, 0.12)', 12),
  // The offer screen lifts its plan card further on dark, where a faint shadow vanishes
  // into the canvas, and glows its save tag violet in both frames — a touch stronger on
  // dark for the same reason.
  shadowOfferPlanCardLight: shadow(12, 16, 'rgba(0, 0, 0, 0.08)', 8),
  shadowOfferPlanCardDark: shadow(16, 20, 'rgba(0, 0, 0, 0.45)', 12),
  shadowOfferCta: shadow(10, 12, 'rgba(0, 0, 0, 0.28)', 8),
  shadowOfferSaveTagLight: shadow(0, 7, 'rgba(184, 158, 255, 0.35)', 4),
  shadowOfferSaveTagDark: shadow(0, 7, 'rgba(184, 158, 255, 0.45)', 4),

  // Badges. An unlocked medal's drop carries a -4 spread, so `BadgeMedal` casts it from
  // an inset view; the topics card's lift is a whisper.
  shadowBadgeMedal: shadow(8, 12, 'rgba(0, 0, 0, 0.12)', 6),
  shadowBadgeCard: shadow(4, 10, 'rgba(0, 0, 0, 0.03)', 2),

  // Mood tracker. The cards and the selected range pill share the whisper; the Add mood
  // pill's -4 spread is not reproduced (it would need an inset caster view), so its
  // drop reads a touch wider than the frame's.
  shadowMoodCard: shadow(4, 10, 'rgba(0, 0, 0, 0.03)', 2),
  shadowMoodCta: shadow(8, 12, 'rgba(0, 0, 0, 0.12)', 6),
  shadowMoodKnob: shadow(2, 4, 'rgba(0, 0, 0, 0.35)', 4),

  // Onboarding. The light frames lift each glass row, field and button off the
  // backdrop; the dark ones draw them flat. The frame's second, 1pt contact shadow
  // (4%) is not reproduced — RN casts one shadow per view.
  shadowOnboardingSurfaceLight: shadow(6, 10, 'rgba(0, 0, 0, 0.08)', 4),
}
