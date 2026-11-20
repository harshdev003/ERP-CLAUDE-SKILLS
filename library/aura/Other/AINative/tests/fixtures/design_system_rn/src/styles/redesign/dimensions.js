import { Dimensions } from 'react-native'

const { width } = Dimensions.get('window')

// The redesign frames are authored at 402pt; `responsiveDimension` scales from
// size-matters' 350pt base, so redesign values passed through it come out ~4.5% large.
const SCALE_FACTOR = 0.3
const BASE_WIDTH = 402

export const scaledDimension = (dimensionValue) => {
  const scale = width / BASE_WIDTH
  return dimensionValue + (scale - 1) * dimensionValue * SCALE_FACTOR
}

/**
 * Left offset that centres `size` on the live screen.
 *
 * The frames centre their hero art with a literal left offset, and that offset only
 * stays centred at the 402pt authoring width: `scaledDimension` grows the art by the
 * 0.3 moderate-scale factor (1.31x at an 820pt iPad) while the screen grows outright
 * (2.04x), so a baked-in offset strands the art in the left half — off centre by 286pt
 * on an iPad (A16) and 428pt on an iPad Pro 13". Deriving it from `width` centres at
 * every size, which is what `PaywallBellHero` already gets from `alignSelf: 'center'`.
 *
 * Use `alignSelf: 'center'` where the element is in normal flow; this is for the
 * absolutely-positioned art that cannot rely on the parent's alignment.
 *
 * @param {number} size Width of the element being centred, already scaled.
 * @returns {number} Left offset in points.
 */
export const centredLeft = (size) => (width - size) / 2

// The collapsed mini player is the tab-bar pill's height on purpose — the frames draw
// the two as one stack — so the one value is bound once and exported under both names.
const tabBarPillHeight = scaledDimension(64)

export default {
  d1: scaledDimension(1),
  d2: scaledDimension(2),
  d4: scaledDimension(4),
  d6: scaledDimension(6),
  d8: scaledDimension(8),
  d10: scaledDimension(10),
  d12: scaledDimension(12),
  d14: scaledDimension(14),
  d16: scaledDimension(16),
  d18: scaledDimension(18),
  d20: scaledDimension(20),
  d22: scaledDimension(22),
  d24: scaledDimension(24),
  d28: scaledDimension(28),
  d30: scaledDimension(30),
  d32: scaledDimension(32),
  d36: scaledDimension(36),
  d40: scaledDimension(40),
  d44: scaledDimension(44),
  d48: scaledDimension(48),
  d56: scaledDimension(56),
  d60: scaledDimension(60),
  d64: scaledDimension(64),
  d72: scaledDimension(72),
  d80: scaledDimension(80),
  d88: scaledDimension(88),
  d96: scaledDimension(96),
  d108: scaledDimension(108),
  d128: scaledDimension(128),

  // The bottom stack: the floating tab-bar pill, the collapsed mini player that sits
  // above it, and the scrim that fades feed content out beneath both. One set of
  // numbers because the three have to agree — separately tuned values in each view are
  // what put the mini player 9pt inside the pill (QA ap17): `HomeTabBar` pinned the
  // pill 104pt off the bottom while `SwippableMeditationPlayer` put the player's
  // bottom edge at 95pt. Only the constants live here; each view does its own
  // arithmetic from them. Frame values are in 402pt units.

  // The pill is 64 tall in a 355 row, with 20 above it and 40 below; it narrows to 276
  // when the search circle sits beside it. The mini player matches the row's width so
  // the two read as one stack.
  tabBarPillHeight,
  tabBarPillWidth: scaledDimension(276),
  tabBarRowWidth: scaledDimension(355),
  tabBarTopPadding: scaledDimension(20),
  tabBarBottomPadding: scaledDimension(40),
  // The pill's glass — backdrop-blur(17) in the node. Not `tabBarBlurAmount`: that is
  // the legacy bar's value on `newDark`, and `TabBar3` still reads it for that bar.
  tabBarPillBlurAmount: 17,
  // The gap between the collapsed player and the pill below it, and the horizontal
  // inset the frames give the collapsed bar's content from the row's ends.
  miniPlayerHeight: tabBarPillHeight,
  miniPlayerGap: scaledDimension(10),
  miniPlayerInset: scaledDimension(18),
}
