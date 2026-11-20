# Pre-consent screens

A screen drawn **before the age-gate consent** cannot host an experiment. The consent is the
Get Started / Login tap on `welcome`; `ExperimentService` needs Firebase, Mixpanel starts after
consent, and `OnboardingExperimentBootstrapManager` resolves buckets strictly after it. So no
variant can be fetched and no exposure can fire on these screens.

Audited against `AuraConsumerApp` `origin/main` (`AppBootstrapView`, `AppBootstrapManager`,
`OnboardingExperimentBootstrapManager`) on 2026-10-05. The bootstrap states drawn before
consent are `awaitingConsent` / `initializing` (the welcome screen), the splash, and the
geographic-restriction block. Everything the onboarding flow shows after the tap (intro, value
props, quiz, notification permission, sign-in, sign-up) and every authenticated screen is
post-consent and reads a bucket resolved at the bootstrap boundary.

This list is the **canonical copy**. `design_idea.py` (`PRE_CONSENT_SCREENS`, plus
`Screen.pre_consent` on the measured model) and `build_idea.py` (`PRE_CONSENT_SCREENS`) carry
copies; `tests/test_pre_consent_screens.py` fails when any of the three disagree.

<!-- pre-consent-screens -->
- `welcome`
- `splash`
- `geographic_restriction`
<!-- /pre-consent-screens -->

## What an idea on one of these must do

- Name its **bucketing path** in `testPlan.instrumentation` (how a user is assigned and the
  exposure recorded without ExperimentService or Mixpanel), or
- move to the nearest **post-consent** screen (usually the first onboarding screen after the
  Get Started tap).

`product:build-idea plan` and `start` refuse a pre-consent screen unless the row's
Instrumentation carries `bucketing path: <how>`.
