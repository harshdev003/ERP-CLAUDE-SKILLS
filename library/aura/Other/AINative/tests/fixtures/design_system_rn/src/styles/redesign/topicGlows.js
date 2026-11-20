/**
 * @module topicGlows
 * @description The onboarding topic cards' glow colours, keyed by topic `unique`.
 *   Each pair is the glow's core and the colour it fades out through.
 */

const glow = (core, edge) => [`rgb(${core})`, `rgb(${edge})`]

export const TOPIC_GLOWS = {
  musicSoothing: glow('82, 114, 112', '59, 86, 85'),
  sleep: glow('62, 74, 112', '44, 56, 85'),
  mindfulness: glow('96, 115, 90', '69, 86, 68'),
  morning: glow('138, 108, 69', '99, 81, 52'),
  advanced: glow('98, 73, 107', '71, 55, 81'),
  gratitude: glow('114, 117, 75', '82, 88, 57'),
  happiness: glow('138, 90, 83', '99, 68, 63'),
  focus: glow('75, 100, 117', '54, 75, 89'),
  relaxation: glow('122, 91, 104', '88, 68, 79'),
  relax: glow('122, 91, 104', '88, 68, 79'),
  sleepAnxiety: glow('102, 95, 122', '74, 72, 93'),
  anxiety: glow('102, 95, 122', '74, 72, 93'),
  boostEnergy: glow('138, 89, 61', '99, 67, 46'),
  healing: glow('71, 102, 82', '51, 77, 62'),
  work: glow('102, 90, 78', '73, 67, 59'),
  personalGrowth: glow('94, 107, 75', '68, 80, 57'),
  personalGrowthSuccess: glow('94, 107, 75', '68, 80, 57'),
  kids: glow('143, 111, 117', '103, 84, 89'),
  beginner: glow('112, 90, 110', '81, 67, 84'),
  pain: glow('46, 72, 102', '33, 54, 78'),
  parenting: glow('143, 119, 100', '103, 89, 76'),
  anger: glow('107, 48, 51', '77, 36, 39'),
  resilience: glow('103, 111, 112', '74, 83, 85'),
  chakras: glow('93, 75, 117', '67, 56, 89'),
  relationships: glow('128, 79, 91', '92, 59, 69'),
  loveAndRelationships: glow('128, 79, 91', '92, 59, 69'),
  stress: glow('92, 97, 128', '66, 72, 97'),
  depression: glow('68, 84, 92', '49, 63, 70'),
  yoga: glow('117, 80, 68', '84, 60, 52'),
  burnout: glow('73, 102, 93', '53, 77, 70'),
  selfEsteem: glow('148, 129, 71', '106, 97, 54'),
  healthCoaching: glow('67, 108, 112', '48, 81, 85'),
  dealEmotions: glow('117, 101, 112', '84, 76, 85'),
  emotions: glow('117, 101, 112', '84, 76, 85'),
}

// Topics the design has no card for are server-driven additions; they take the most
// neutral of the designed glows rather than rendering bare.
export const DEFAULT_TOPIC_GLOW = TOPIC_GLOWS.resilience

// The light frame's glows (Content Interest Selection – All Glow – Light), keyed as
// above. Each starts from its topic's dark core but fades out through lighter tints of
// it, with its own stops, so there is no rule to derive them by — they are copied from
// the frame card by card. The frame renders only their alpha ramp, in the last stop's
// tint (see OnboardingTopicCard). Each stop is [offset, rgb, opacity].
const lightGlow = (stops) => ({ stops })

export const TOPIC_GLOWS_LIGHT = {
  musicSoothing: lightGlow([
    [0.074369, '82, 114, 112', 1],
    [0.53718, '112, 159, 156', 0.5],
    [1, '142, 204, 199', 0],
  ]),
  sleep: lightGlow([
    [0.074369, '62, 74, 112', 1],
    [0.53718, '99, 114, 162', 0.5],
    [1, '136, 154, 211', 0],
  ]),
  mindfulness: lightGlow([
    [0.074369, '96, 115, 90', 1],
    [0.53718, '128, 157, 118', 0.5],
    [1, '160, 200, 147', 0],
  ]),
  morning: lightGlow([
    [0.074369, '138, 108, 69', 1],
    [0.53718, '174, 143, 102', 0.5],
    [1, '211, 178, 136', 0],
  ]),
  advanced: lightGlow([
    [0.074369, '98, 73, 107', 1],
    [0.53718, '144, 106, 158', 0.5],
    [1, '190, 139, 208', 0],
  ]),
  gratitude: lightGlow([
    [0.074369, '114, 117, 75', 1],
    [0.53718, '160, 164, 105', 0.5],
    [1, '206, 211, 136', 0],
  ]),
  happiness: lightGlow([
    [0.074369, '138, 90, 83', 1],
    [1, '211, 146, 136', 0],
  ]),
  focus: lightGlow([
    [0.074369, '75, 100, 117', 1],
    [0.53718, '105, 140, 164', 0.5],
    [1, '136, 180, 211', 0],
  ]),
  relaxation: lightGlow([
    [0.074369, '122, 91, 104', 1],
    [1, '203, 144, 168', 0],
  ]),
  relax: lightGlow([
    [0.074369, '122, 91, 104', 1],
    [1, '203, 144, 168', 0],
  ]),
  sleepAnxiety: lightGlow([
    [0.074369, '102, 95, 122', 1],
    [1, '161, 147, 200', 0],
  ]),
  anxiety: lightGlow([
    [0.074369, '102, 95, 122', 1],
    [1, '161, 147, 200', 0],
  ]),
  boostEnergy: lightGlow([
    [0.074369, '138, 89, 61', 1],
    [0.53718, '174, 126, 98', 0.5],
    [1, '211, 163, 136', 0],
  ]),
  healing: lightGlow([
    [0.074369, '71, 102, 82', 1],
    [0.53718, '106, 154, 122', 0.5],
    [1, '141, 206, 162', 0],
  ]),
  work: lightGlow([
    [0.074369, '102, 90, 78', 1],
    [0.53718, '151, 132, 112', 0.5],
    [1, '201, 173, 146', 0],
  ]),
  personalGrowth: lightGlow([
    [0.074369, '94, 107, 75', 1],
    [0.53718, '137, 157, 108', 0.5],
    [1, '180, 206, 141', 0],
  ]),
  personalGrowthSuccess: lightGlow([
    [0.074369, '94, 107, 75', 1],
    [0.53718, '137, 157, 108', 0.5],
    [1, '180, 206, 141', 0],
  ]),
  kids: lightGlow([
    [0.074369, '143, 111, 117', 1],
    [1, '200, 147, 156', 0],
  ]),
  beginner: lightGlow([
    [0.074369, '112, 90, 110', 1],
    [0.53718, '156, 119, 153', 0.5],
    [1, '200, 147, 195', 0],
  ]),
  pain: lightGlow([
    [0.074369, '46, 72, 102', 1],
    [0.30578, '68, 97, 129', 0.75],
    [0.53718, '91, 122, 156', 0.5],
    [1, '136, 171, 211', 0],
  ]),
  parenting: lightGlow([
    [0.074369, '143, 119, 100', 1],
    [1, '206, 169, 141', 0],
  ]),
  anger: lightGlow([
    [0.074369, '107, 48, 51', 1],
    [0.30578, '133, 70, 73', 0.75],
    [0.53718, '159, 92, 96', 0.5],
    [1, '211, 136, 140', 0],
  ]),
  resilience: lightGlow([
    [0.074369, '103, 111, 112', 1],
    [0.53718, '125, 151, 156', 0.5],
    [1, '147, 191, 200', 0],
  ]),
  chakras: lightGlow([
    [0.074369, '93, 75, 117', 1],
    [0.53718, '130, 105, 164', 0.5],
    [1, '167, 136, 211', 0],
  ]),
  relationships: lightGlow([
    [0.074369, '128, 79, 91', 1],
    [0.53718, '169, 107, 123', 0.5],
    [1, '211, 136, 154', 0],
  ]),
  loveAndRelationships: lightGlow([
    [0.074369, '128, 79, 91', 1],
    [0.53718, '169, 107, 123', 0.5],
    [1, '211, 136, 154', 0],
  ]),
  stress: lightGlow([
    [0.074369, '92, 97, 128', 1],
    [1, '142, 151, 204', 0],
  ]),
  depression: lightGlow([
    [0.074369, '68, 84, 92', 1],
    [0.53718, '106, 134, 147', 0.5],
    [1, '144, 183, 203', 0],
  ]),
  yoga: lightGlow([
    [0.074369, '117, 80, 68', 1],
    [0.53718, '164, 117, 102', 0.5],
    [1, '211, 155, 136', 0],
  ]),
  burnout: lightGlow([
    [0.074369, '73, 102, 93', 1],
    [0.53718, '108, 153, 138', 0.5],
    [1, '142, 204, 184', 0],
  ]),
  selfEsteem: lightGlow([
    [0.074369, '148, 129, 71', 1],
    [1, '211, 192, 136', 0],
  ]),
  healthCoaching: lightGlow([
    [0.074369, '67, 108, 112', 1],
    [0.53718, '102, 155, 162', 0.5],
    [1, '136, 203, 211', 0],
  ]),
  dealEmotions: lightGlow([
    [0.074369, '117, 101, 112', 1],
    [1, '200, 147, 182', 0],
  ]),
  emotions: lightGlow([
    [0.074369, '117, 101, 112', 1],
    [1, '200, 147, 182', 0],
  ]),
}

// The light twin of DEFAULT_TOPIC_GLOW.
export const DEFAULT_TOPIC_GLOW_LIGHT = TOPIC_GLOWS_LIGHT.resilience
