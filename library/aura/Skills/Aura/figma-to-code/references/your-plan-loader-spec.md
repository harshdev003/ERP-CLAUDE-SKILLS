# figma-to-code progress — "Your Plan" loader, 5 stages (EXTRACTED, NOT BUILT)

Branch: ashish/prod-1115-aura4-onboarding (PR #6776) · file dpxPlTNW722Y1Qr8JRnSIn
Full spec sheets: `planspec.txt` · renders: `p1_loading.png` … `p5_preview.png` · node JSON: `plan.json`

## Target files (existing, pre-4.0)
`src/screens/onboarding/onboardingYourPlanLoader/`
- `OnboardingYourPlanLoader.js` (243) — orchestrator, owns the progress/stage timing
- `OnboardingYourPlanPersonalized.js` (105) → stage 1
- `OnboardingYourPlanTopics.js` (151) → stage 2
- `OnboardingYourPlanContentType.js` (137) → stage 3
- `OnboardingYourPlanDuration.js` (177) → stage 4
- `OnboardingYourPlanMadeForYou.js` (118) → stage 5
- `OnboardingYourPlanStyle.js` (75) — shared styles
Mounted by `Onboarding6` at ~5700 (`yourPlanBeforeSignup`) and after registration.

| # | stage | node | fill w/314 | built | verified |
|---|-------|------|-----------|-------|----------|
| 1 | Personalization Loading | 1048:16546 | 51 (16%) | no | no |
| 2 | Personalized Topics | 1048:16581 | 117 (37%) | no | no |
| 3 | Content Type Highlight | 1048:16653 | 177 (56%) | no | no |
| 4 | Session Length Highlight | 1048:16693 | 246 (78%) | no | no |
| 5 | Personalized Plan Preview | 1048:16730 | 246 (78%) | no | no |

## Shared chrome (identical in all five — build ONCE)
- **Backdrop `55d645c1…` = FOREST** (`backdropVariant="forest"`, the default) — NOT dusk. Verified per frame.
- Content column x18 **y85** (no top chevron/progress), VERTICAL gap 48
- Heading block (366x235, gap 48): ring **115** + title "Let's personalize your Aura experience"
  SeasonSerif-Regular 30/36 LEFT, 366x72 → `OnboardingHeading` with `ringSize={115}` `ringGap={48}`
- **Bottom progress (NEW component, e.g. `OnboardingPlanProgressBar`)** at y829:
  - track `Button` 314x6, fill `newDesignSurfaceSoft` (#D9D9D9@0.04), r58, double inner shadow
  - fill `Rectangle 367` white, r29, width = pct × 314
  - **rainbow glow** `Rectangle 368` behind the fill: same width/height, LINEAR gradient
    #F3FF69@0 → #FF9473@.24 → #FFADF7@.51 → #84BCFF@.74 → #65FF3F@1, LAYER_BLUR 8.32
  - "%" label at (355, 820.5) `newDesignLabel18` white — the node says "15%" on every frame
    (static mock, same as the top bars); drive it from the real stage progress.

## Per-stage bodies
1. **Loading** — body gap 32. "Your personalized plan has" (`newDesignLabel18`) + "We analyzed all your
   responses…" (`newDesignSubtitle16`, white@0.7). Stats card 366x113, white@0.05, r18, double inner
   shadow, pad 20: two columns ("1000+" / "???" and "50+" / "???") split by a **vertical** divider
   (white@0.1). Big number = SeasonSans-Light **35**/45.5 (no token yet), caption `newDesignLabel18`.
2. **Topics** — "Based on your preferences" + "We have personalized your Aura plan…". **Wrapped 2-col
   grid**: `layoutWrap=WRAP`, gap 12, **counterAxisSpacing 15**. Four cards 176x115, white@0.05,
   **stroke white@0.3 (newDesignBorderSelected)**, r14, pad 20, label `newDesignLabel18` bottom-aligned
   (SPACE_BETWEEN). Two cards carry a Figma GLASS effect (no RN equivalent — note as a deviation).
3. **Content Type** — body is just "Content will be tailored to include more" (`newDesignLabel18`);
   container gap 80 with the rest hidden. Simplest stage.
4. **Session Length** — "To better accommodate your schedule…" (`newDesignLabel18` white@0.7) + a
   **200x200** card, white@0.05, r24, double inner shadow, containing the violet radial
   `Rectangle 366` (#3F3A6D→#6C5272 — reuse `gOnboardingRowSelectedGlowViolet`'s colours) and centred
   "Medium" SeasonSerif-**Light** 30/36 + "10 - 20 minutes" `newDesignSubtitle16` white@0.7 CENTER.
   Card centred in the column (x83). Title has DROP_SHADOW #000@0.25 y4.21.
5. **Plan Preview** — "Made for Alex" (`newDesignLabel18`, {{name}} — dynamic) + "Personalized plans
   for happiness…" (Light 18 white@0.7). Then `Frame 1183` **459x233 at x −46.5** — i.e. it
   deliberately overflows both edges — a HORIZONTAL row (gap 12) of track cards: title
   SeasonSerif-**Light** 30/36 + drop shadow, "by Aura \nMeditation Coach" `newDesignCopy14` white@0.7,
   and a "Soundscape" tag (`newDesignCopy14`, one instance has a #A6C40B drop shadow).

## Tokens/assets still needed
- A SeasonSans-Light **35**/45.5 font token (stage 1's big numbers) — none exists.
- SeasonSerif-**Light** 30/36 (stages 4 and 5) — existing serif tokens are Regular; check before adding.
- Everything else maps to existing tokens (`newDesignSurfaceSoft`, `newDesignBorderSelected`,
  `newDesignLabel18`, `newDesignSubtitle16`, `newDesignCopy14`, `newDesignTextTertiary`).

## Reuse
`OnboardingScreenLayout` (forest, no CTA, no scrim), `OnboardingHeading` (ringSize 115 / ringGap 48),
`OnboardingInsetGlow` for every card rim. Stage bodies are prop-driven components in
`src/components/onboarding/` or kept in the existing stage files behind the gate.
