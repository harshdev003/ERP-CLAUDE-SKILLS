import { scaledDimension } from './dimensions'

export default {
  // 12 and 16 also exist in globals, but through `responsiveDimension` they land ~4.5%
  // over (16 -> 16.71 at 402pt). Redefining them here puts every 4.0 surface on the
  // exact authored value.
  borderRadius12: scaledDimension(12),
  borderRadius14: scaledDimension(14),
  borderRadius16: scaledDimension(16),
  borderRadius24: scaledDimension(24),
  borderRadius32: scaledDimension(32),
}
