/**
 * @module redesignLight
 * @description Light redesign theme. Extends `newLight` on the same terms
 *   `redesignDark` extends `newDark`.
 */

import newLight from '../newLight'
import color from './color'
import redesignFonts from '../redesign/fonts'
import redesignGradients from '../redesign/gradients'
import redesignBorderRadius from '../redesign/borderRadius'
import redesignDimensions from '../redesign/dimensions'
import redesignShadows from '../redesign/shadows'
import redesignStyles from '../redesign/styleSheet'

export default {
  ...newLight,
  name: 'Redesign Light',
  colors: {
    ...newLight.colors,
    ...color,
  },
  fonts: {
    ...newLight.fonts,
    ...redesignFonts,
  },
  gradients: {
    ...newLight.gradients,
    ...redesignGradients,
  },
  borderRadius: {
    ...newLight.borderRadius,
    ...redesignBorderRadius,
  },
  dimensions: {
    ...newLight.dimensions,
    ...redesignDimensions,
  },
  shadows: {
    ...newLight.shadows,
    ...redesignShadows,
  },
  styles: {
    ...newLight.styles,
    ...redesignStyles,
  },
}
