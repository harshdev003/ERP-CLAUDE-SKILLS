/**
 * @module redesignDark
 * @description Dark redesign theme, gated by `auraDesignChange`. Extends `newDark`
 *   rather than replacing it, so screens that aren't reskinned yet are unaffected —
 *   which is why the predicates in `models/theme.js` treat the two the same.
 */

import newDark from '../newDark'
import color from './color'
import redesignFonts from '../redesign/fonts'
import redesignGradients from '../redesign/gradients'
import redesignBorderRadius from '../redesign/borderRadius'
import redesignDimensions from '../redesign/dimensions'
import redesignShadows from '../redesign/shadows'
import redesignStyles from '../redesign/styleSheet'

export default {
  ...newDark,
  name: 'Redesign Dark',
  colors: {
    ...newDark.colors,
    ...color,
  },
  fonts: {
    ...newDark.fonts,
    ...redesignFonts,
  },
  gradients: {
    ...newDark.gradients,
    ...redesignGradients,
  },
  borderRadius: {
    ...newDark.borderRadius,
    ...redesignBorderRadius,
  },
  dimensions: {
    ...newDark.dimensions,
    ...redesignDimensions,
  },
  shadows: {
    ...newDark.shadows,
    ...redesignShadows,
  },
  styles: {
    ...newDark.styles,
    ...redesignStyles,
  },
}
