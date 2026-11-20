# Aurie design tokens — generated

**Generated from `AuraConsumerApp/Services/Theme/Global` by `product:sync-design-system`.**
Do not edit: re-run the generator. Values here are what the app ships today.

Companion to `design-system-aurie.md`, which holds the renderer
mechanics and the real copy inventory. This file holds values only —
**cite tokens by name**, never by raw hex or px.

## Type

Captures from the iPhone 17 Pro simulator are **3x** — multiply every pt below by 3.

### Faces

| Token | PostScript name |
|---|---|
| `displayRegular` | `ABCOttoUnlicensedTrial-Regular` |
| `displayMedium` | `ABCOttoUnlicensedTrial-Medium` |
| `displayLight` | `ABCOttoUnlicensedTrial-Light` |
| `displayLightItalic` | `ABCOttoUnlicensedTrial-LightItalic` |

### Display scale (ABC Otto)

| Token | Size | Weight |
|---|---|---|
| `displayLightItalic32` | 32pt | Light italic |
| `displayLight32` | 32pt | Light |
| `displayLight28` | 28pt | Light |
| `displayLightItalic28` | 28pt | Light italic |
| `displayLight27` | 27pt | Light |
| `displayLight22` | 22pt | Light |
| `displayLightItalic22` | 22pt | Light italic |
| `displayLight21` | 21pt | Light |
| `displayLight20` | 20pt | Light |
| `display18` | 18pt | Regular |
| `display15` | 15pt | Regular |

### UI scale (SF Pro)

| Token | Text style | Size at default | Weight |
|---|---|---|---|
| `title2Regular` | `.title2` | 22pt | regular |
| `title3Regular` | `.title3` | 20pt | regular |
| `bodyRegular` | `.body` | 17pt | regular |
| `bodySemibold` | `.body` | 17pt | semibold |
| `calloutRegular` | `.callout` | 16pt | regular |
| `calloutSemibold` | `.callout` | 16pt | semibold |
| `calloutBold` | `.callout` | 16pt | bold |
| `subheadlineRegular` | `.subheadline` | 15pt | regular |
| `subheadlineSemibold` | `.subheadline` | 15pt | semibold |
| `footnoteRegular` | `.footnote` | 13pt | regular |
| `footnoteSemibold` | `.footnote` | 13pt | semibold |
| `captionRegular` | `.caption` | 12pt | regular |
| `captionSemibold` | `.caption` | 12pt | semibold |
| `caption2Regular` | `.caption2` | 11pt | regular |
| `caption2Semibold` | `.caption2` | 11pt | semibold |
| `caption2Decorative` | *fixed* | 10pt | medium |
| `caption3Decorative` | *fixed* | 8pt | medium |

SF Pro is the system font: not embedded here or in the app, because Apple's
licence forbids it. The app names a **Dynamic Type text style**, never a point
size, so the size above is the style's size at the default setting and scales
from there. The two *fixed* tokens are the decorative exception below 11 pt.

## Colour

30 opaque, 35 translucent. Names describe the **value**.
These are the palette the roles resolve to — on a sky surface name the
role (`skyState.textPrimary`, `textSecondary`, `figurePrimary`…), which
carries its own per-sky contrast measurement.

| Token | Value | Source |
|---|---|---|
| `blackPrimary` | `#222222` | `Colors.swift:13` |
| `blackSecondary` | `#1C1B1F` | `Colors.swift:14` |
| `grayBgLayout` | `#11191E` | `Colors.swift:17` |
| `grayMain` | `#2F3237` | `Colors.swift:18` |
| `gray600` | `#4E545F` | `Colors.swift:19` |
| `gray500` | `#5B657A` | `Colors.swift:20` |
| `gray400` | `#9092A3` | `Colors.swift:21` |
| `gray300` | `#D3D3DA` | `Colors.swift:22` |
| `gray200` | `#EDEDF0` | `Colors.swift:23` |
| `gray100` | `#F7F7F8` | `Colors.swift:24` |
| `whiteAlphaBgLayout` | `#F7FBFC` | `Colors.swift:38` |
| `whiteAlphaBgLayout2` | `#FDFDFD` | `Colors.swift:39` |
| `whiteAlphaMain` | `#FFFFFF` | `Colors.swift:40` |
| `additionalYellow` | `#FDC934` | `Colors.swift:51` |
| `additionalPink` | `#FA3FAF` | `Colors.swift:52` |
| `additionalBlue` | `#5AC8FA` | `Colors.swift:53` |
| `systemErrorMain` | `#FF3B30` | `Colors.swift:56` |
| `systemCautionMain` | `#FA8442` | `Colors.swift:59` |
| `systemSuccessMain` | `#79EF5D` | `Colors.swift:62` |
| `passwordRequirementMet` | `#03995A` | `Colors.swift:67` |
| `systemErrorPrimary` | `#E75050` | `Colors.swift:68` |
| `connectorGray` | `#898989` | `Colors.swift:92` |
| `lightGray` | `#BFC3DA` | `Colors.swift:93` |
| `bulletGray` | `#C5C6CE` | `Colors.swift:94` |
| `specialtyHoverInput` | `#5AC8FA` | `Colors.swift:97` |
| `specialtyBorderHover` | `#4CCAFF` | `Colors.swift:98` |
| `pmfIconShadow` | `#FFEBF0` | `Colors.swift:101` |
| `Black` | `#1C1C1F` | `Colors.swift:102` |
| `White` | `#FFFFFF` | `Colors.swift:107` |
| `appBgDefaultColor` | `#DCDBDF` | `Colors.swift:122` |
| `labelTertiary` | `rgba(61, 61, 66, 0.3)` | `Colors.swift:15` |
| `grayAlpha700` | `rgba(17, 25, 30, 0.9)` | `Colors.swift:27` |
| `grayAlpha600` | `rgba(17, 25, 30, 0.8)` | `Colors.swift:28` |
| `grayAlpha500` | `rgba(17, 25, 30, 0.7)` | `Colors.swift:29` |
| `grayAlpha400` | `rgba(17, 25, 30, 0.6)` | `Colors.swift:30` |
| `grayAlpha350` | `rgba(17, 25, 30, 0.32)` | `Colors.swift:31` |
| `grayAlpha300` | `rgba(17, 25, 30, 0.2)` | `Colors.swift:32` |
| `grayAlpha200` | `rgba(17, 25, 30, 0.12)` | `Colors.swift:33` |
| `grayAlpha150` | `rgba(17, 25, 30, 0.08)` | `Colors.swift:34` |
| `grayAlpha100` | `rgba(17, 25, 30, 0.04)` | `Colors.swift:35` |
| `whiteAlpha700` | `rgba(255, 255, 255, 0.9)` | `Colors.swift:41` |
| `whiteAlpha600` | `rgba(255, 255, 255, 0.8)` | `Colors.swift:42` |
| `whiteAlpha500` | `rgba(255, 255, 255, 0.7)` | `Colors.swift:43` |
| `whiteAlpha400` | `rgba(255, 255, 255, 0.6)` | `Colors.swift:44` |
| `whiteAlpha300` | `rgba(255, 255, 255, 0.24)` | `Colors.swift:45` |
| `whiteAlpha200` | `rgba(255, 255, 255, 0.16)` | `Colors.swift:46` |
| `whiteAlpha100` | `rgba(255, 255, 255, 0.08)` | `Colors.swift:47` |
| `whiteAlpha0` | `rgba(255, 255, 255, 0)` | `Colors.swift:48` |
| `systemError500` | `rgba(255, 59, 48, 0.7)` | `Colors.swift:57` |
| `systemError100` | `rgba(255, 59, 48, 0.1)` | `Colors.swift:58` |
| `systemCaution500` | `rgba(250, 132, 66, 0.7)` | `Colors.swift:60` |
| `systemCaution100` | `rgba(250, 132, 66, 0.1)` | `Colors.swift:61` |
| `systemSuccess500` | `rgba(121, 239, 93, 0.7)` | `Colors.swift:63` |
| `systemSuccess100` | `rgba(121, 239, 93, 0.1)` | `Colors.swift:64` |
| `blurDark64` | `rgba(0, 0, 0, 0.5)` | `Colors.swift:88` |
| `blurLight8` | `rgba(213, 213, 213, 0.1)` | `Colors.swift:89` |
| `Black75` | `rgba(28, 28, 31, 0.75)` | `Colors.swift:103` |
| `Black50` | `rgba(28, 28, 31, 0.5)` | `Colors.swift:104` |
| `Black20` | `rgba(28, 28, 31, 0.2)` | `Colors.swift:105` |
| `Black10` | `rgba(28, 28, 31, 0.1)` | `Colors.swift:106` |
| `White75` | `rgba(255, 255, 255, 0.75)` | `Colors.swift:108` |
| `White50` | `rgba(255, 255, 255, 0.5)` | `Colors.swift:109` |
| `White30` | `rgba(255, 255, 255, 0.3)` | `Colors.swift:110` |
| `White20` | `rgba(255, 255, 255, 0.2)` | `Colors.swift:111` |
| `White10` | `rgba(255, 255, 255, 0.1)` | `Colors.swift:112` |

### Fade pairs

| Token | Stops |
|---|---|
| `shadowGradientLight` | `rgba(185, 193, 197, 0.16), rgba(213, 221, 225, 0)` |
| `shadowGradientDark` | `rgba(0, 0, 0, 0.24), rgba(0, 0, 0, 0)` |
| `placeholderLight` | `rgba(255, 255, 255, 0.5), rgba(255, 255, 255, 0)` |
| `placeholderDark` | `rgba(47, 50, 55, 0.5), rgba(47, 50, 55, 0)` |

## Spacing

A linear px ladder — the name is the value.

`space0`=0  `space2`=2  `space4`=4  `space6`=6  `space8`=8  `space10`=10  `space12`=12  `space16`=16  `space20`=20  `space24`=24  `space32`=32  `space40`=40  `space48`=48  `space56`=56  `space64`=64  `space72`=72  `space80`=80  `space88`=88  `space96`=96  `space100`=100

## Radius

`borderRadiusFull` is 9999: a capsule.

`borderRadius8`=8  `borderRadius12`=12  `borderRadius16`=16  `borderRadius18`=18  `borderRadius20`=20  `borderRadius28`=28  `borderRadiusFull`=9999

## Gradients

| Token | CSS |
|---|---|
| `gBlue45` | `linear-gradient(45deg, #4CCAFF 0%, #1DF5ED 100%)` |
| `gBlue0` | `linear-gradient(90deg, #4CCAFF 0%, #1DF5ED 100%)` |
| `gGreen45` | `linear-gradient(45deg, #08F057 0%, #B1FF74 100%)` |
| `gGreen0` | `linear-gradient(90deg, #08F057 0%, #B1FF74 100%)` |
| `gGreenSubdued45` | `linear-gradient(45deg, #9DD500 0%, #5CE4B3 100%)` |
| `gGreenSubdued0` | `linear-gradient(90deg, #9DD500 0%, #5CE4B3 100%)` |
| `gGray45` | `linear-gradient(45deg, #BDD6D5 0%, #BDC7CD 100%)` |
| `gGray0` | `linear-gradient(90deg, #BDD6D5 0%, #BDC7CD 100%)` |
| `gPurpleGreen45` | `linear-gradient(45deg, #67F6CB 14%, #63C2FD 45%, #8D86FD 75%, #D270E9 100%)` |
| `gPurpleGreen0` | `linear-gradient(90deg, #67F6CB 14%, #63C2FD 45%, #8D86FD 75%, #D270E9 100%)` |
| `gTurquoiseGreen45` | `linear-gradient(45deg, #5FE3F2 0%, #7EF291 100%)` |
| `gTurquoiseGreen0` | `linear-gradient(90deg, #5FE3F2 0%, #7EF291 100%)` |
| `gPeach45` | `linear-gradient(45deg, #F6CB66 0%, #FB4975 100%)` |
| `gPeach0` | `linear-gradient(90deg, #F6CB66 0%, #FB4975 100%)` |
| `gBlueGreen45` | `linear-gradient(45deg, #76EC89 0%, #0D99FF 100%)` |
| `gBlueGreen0` | `linear-gradient(90deg, #76EC89 0%, #0D99FF 100%)` |
| `gOrange45` | `linear-gradient(45deg, #FF2B3A 0%, #FF9E34 100%)` |
| `gOrange0` | `linear-gradient(0deg, #FF2B3A 0%, #FF9E34 100%)` |
| `gPink45` | `linear-gradient(45deg, #FF4C4C 0%, #FF00B8 100%)` |
| `gPink0` | `linear-gradient(0deg, #FF4C4C 0%, #FF00B8 100%)` |
| `gPinkSubdued45` | `linear-gradient(45deg, #FC9192 0%, #FC64D2 100%)` |
| `gPinkSubdued0` | `linear-gradient(0deg, #FC9192 0%, #FC64D2 100%)` |
| `gRainbow0` | `linear-gradient(90deg, #F3FF69 0%, #FF9473 25%, #FFADF7 50%, #84BCFF 75%, #65FF3F 100%)` |
| `gRainbowBright0` | `linear-gradient(0deg, #F26749 0%, #F7D732 34%, #81E35E 67%, #73D3F1 100%)` |
| `gGold45` | `linear-gradient(45deg, #F9E066 0%, #DF7554 100%)` |
| `gSilver45` | `linear-gradient(45deg, #8AA1B6 0%, #D3E2EA 100%)` |
| `gBronze45` | `linear-gradient(45deg, #FFD3CE 0%, #C16F75 100%)` |
| `gMessage` | `linear-gradient(123deg, #03C9F4 0%, #0396D9 40%)` |
| `gMessageNew` | `linear-gradient(47deg, #00C1FC 2%, #5200D8 34%, #95008A 67%, #EB0062 87%)` |
| `gSubscriptionBadge` | `linear-gradient(85.63deg, #946EFF 16.586%, #FF38B9 48.735%, #FFC038 122.93%)` |
| `gSubscriptionButton` | `linear-gradient(90deg, rgba(255, 255, 255, 0.4) 0%, rgba(255, 255, 255, 0.4) 100%)` |
| `gSubscriptionGlow` | `linear-gradient(90deg, rgba(148, 110, 255, 0.15) 0%, rgba(255, 56, 185, 0.15) 50%, rgba(255, 192, 56, 0.15) 100%)` |

## Shadows

SwiftUI `radius` is a Gaussian sigma; the CSS blur below is ~2x it.

| Token | CSS |
|---|---|
| `textShadowHard` | `0px 2px 6px rgba(0, 0, 0, 0.15)` |
| `textShadowSoft` | `0px 4px 14px rgba(0, 0, 0, 0.15)` |
| `buttonShadowCtaBlueXl` | `0px 24px 56px rgba(56, 218, 247, 0.3)` |
| `buttonShadowCtaBlueL` | `0px 24px 40px rgba(56, 218, 247, 0.3)` |
| `buttonShadowCtaBlueM` | `0px 16px 32px rgba(56, 218, 247, 0.3)` |
| `buttonShadowCtaBlueS` | `0px 12px 24px rgba(56, 218, 247, 0.3)` |
| `buttonShadowCtaPinkXl` | `0px 24px 56px rgba(179, 69, 135, 0.3)` |
| `buttonShadowCtaPinkL` | `0px 24px 40px rgba(179, 69, 135, 0.3)` |
| `buttonShadowCtaPinkM` | `0px 16px 32px rgba(179, 69, 135, 0.3)` |
| `buttonShadowCtaPinkS` | `0px 12px 24px rgba(179, 69, 135, 0.3)` |
| `buttonShadowCtaGreenL` | `0px 24px 40px rgba(8, 240, 87, 0.5)` |
| `buttonShadowCtaGrayL` | `0px 24px 40px rgba(189, 214, 213, 0.3)` |
| `buttonShadowPrimaryXl` | `0px 24px 56px rgba(43, 42, 107, 0.1)` |
| `buttonShadowPrimaryL` | `0px 24px 40px rgba(43, 42, 107, 0.1)` |
| `buttonShadowPrimaryM` | `0px 16px 32px rgba(43, 42, 107, 0.08)` |
| `buttonShadowPrimaryS` | `0px 12px 24px rgba(43, 42, 107, 0.1)` |
| `checkboxRadioShadowM` | `0px 8px 20px rgba(56, 218, 247, 0.9)` |
| `checkboxRadioShadowS` | `0px 4px 16px rgba(56, 218, 247, 0.9)` |
| `colorShadowGreenXl` | `0px 24px 56px rgba(160, 231, 140, 0.3)` |
| `colorShadowGreenL` | `0px 24px 40px rgba(160, 231, 140, 0.3)` |
| `colorShadowGreenM` | `0px 16px 32px rgba(160, 231, 140, 0.3)` |
| `colorShadowGreenS` | `0px 12px 24px rgba(160, 231, 140, 0.3)` |
| `avatarDropInnerShadowM` | `6px 14px 22px rgba(48, 56, 72, 0.25)` |
| `buttonShadowSecondary` | `0px 6px 40px rgba(43, 42, 107, 0.12)` |
| `shadowSoftCardS` | `0px 4px 26px rgba(43, 42, 107, 0.08)` |
| `shadowSoftSInner` | `0px 2px 8px rgba(0, 0, 0, 0.1)` |
| `shadowMediumXs` | `0px 2px 8px rgba(43, 42, 107, 0.1)` |
| `shadowSubGreen` | `0px 12px 80px rgba(39, 229, 84, 0.25)` |
| `shadowSubPink` | `0px 12px 80px rgba(236, 38, 168, 0.25)` |
| `shadowCardBig` | `0px 12px 120px rgba(108, 112, 134, 0.1)` |
| `shadowMessage` | `0px 4px 80px rgba(43, 42, 107, 0.04)` |

## Brand rules that bind any copy

**Core promise:** Feel lighter today.

### Never use

*Journey*, *Unlock*, *Transform / transformation*, *Self-care*, *Holistic*, *Wellness journey*, *In today's busy world…*, *We all struggle with…*, *Therapist in your pocket*

### CTAs

| Do | Don't |
|---|---|
| Try it free | Change your life |
| Just listen | Transform how you feel forever |
| Start a 3-minute session | Begin your Aura journey today |

### Naming

- **Aura** — The meditation, sleep and CBT app — or the parent company.
- **Aurie** — The AI emotional companion. Always Aurie — never “Aura AI”, “Aura Companion” or “Aura+”.
- **Both** — Write “Aura and Aurie”, in that order.
- **Company** — “Aura Health” or “Aura Health, Inc.”

### Voice

Aurie speaks in **first person** — warm, grounded, invitational; never clinical, never preachy.

- **Warm and empathetic** — “I'm here to help you rediscover your light”
- **Non-judgmental** — accepts where you are without pushing
- **Grounded** — rooted in therapeutic expertise without being clinical
- **Invitational** — “Your light is worth tending. Shall we begin?”
- **Supportive** — “Let me send gentle support right when you need it”

### Approved lines

| Placement | Phrase |
|---|---|
| Hero | Feel lighter today |
| Desktop hero | Feel lighter … in minutes. |
| Subscription | Feel understood. Feel supported. Feel lighter. |
| Welcome | I'm here to help you rediscover your light |
| Closing | Your light is worth tending. Shall we begin? |

Source: AI Knowledge › Identity › Brand, AI Knowledge › Identity › Apps › Aurie.

## The bar every design must clear

These are **not** suggestions, and they bind token choice. A proposal that picks a legible-looking token which fails one of these is wrong.

| Rule | Minimum |
|---|---|
| Body text contrast | **4.5:1** |
| Large text (≥18 pt, or ≥14 pt bold) | **3:1** |
| Icons, borders, focus rings, control states | **3:1** |
| Tap target | **44 × 44 pt** |
| Minimum text size | **11 pt** — 8 pt for decoration only |
| Text scaling | **200%** without loss |
| Colour alone | never the only signal — pair it with an icon, text or shape |

**The decorative exception.** Decoration only — never running text, and never a label a person must read to use the app. Aurie: Caption 2 (10 pt), Caption 3 (8 pt). It is not a licence to shrink a label; if a person has to read it to use the app, the floor is 11 pt.

### Contrast is measured in every sky, not on white

Aurie is locked to light mode but reads the time-of-day sky, so **dusk and night are its dark mode**. A token that passes at midday and fails at night has failed. Measure against the worst case, and behind any blur or scrim that sits over it.

This is the single most repeated mistake in this app: eleven surfaces have been fixed one at a time for it. When a proposal puts text or a glyph over the sky, over glass, or over a gradient, say which sky you checked.

### Say so when nothing clears it

If no token in the list above meets the bar for what the idea needs, **say that** rather than picking the closest one. A missing token is a finding about the design system, and a more useful output than a sheet that cannot ship.

Source: AI Knowledge → Design/Accessibility Baseline; WCAG 2.2 AA, Apple HIG (target 44 × 44 pt, text floor 11 pt), Material (48 × 48 dp). WCAG AA alone asks only 24 × 24 px for a target — passing AA is not passing this bar.
