# PROD-8620 — `aiNativeAgeRange`: Apple's age range sheet after Get Started (Aurie)

## Context

Steve's card (PROD-8620, reassigned to Ashish by Rohan): as a possible extra safety measure, show Apple's native "Would Like to Know Your Age Range" sheet (Declared Age Range API) right after a new user taps **Get Started**, and A/B test whether it changes anything. Reference: Claude's iOS app shows the sheet over the welcome screen while the button spins.

Repo: AuraConsumerApp. Worktree `.claude/worktrees/prod-8620`, branch `ashish/prod-8620-abainativeagerange-show-native-ios-age` off `origin/main` (df5558894). Main checkout untouched (it has staged WIP on `feat/voice-emotion-speed`).

The card doesn't say what to do with the answer. **Default chosen: fail open.** Onboarding continues to ValueProps in every case (shared adult, shared under 18, declined, unavailable, error). The result goes to analytics only, nothing is stored on the profile. Flagged in the PR for Steve/Rohan.

## How Get Started works today (verified)

`WelcomeScreenContent` tap → `AppBootstrapManager.onGetStartedTapped()` → state `.initializing` (welcome stays up, button spins) → `AppBootstrapView.initializeFromButtonTap()` → `createAndInitializeCoordinator()` (Firebase, SDKs, AppCoordinator) → `AppCoordinator.checkAuthenticationAndNavigate()` → `.getStarted` case tracks `AI Sign Up Start` and calls `onboardingCoordinator?.navigateToValueProps()`. Only after that returns does bootstrap flip to `.ready`. So awaiting the sheet inside the `.getStarted` case shows it over the spinning welcome screen, exactly like the reference.

## Approach

1. **Experiment key** — `Experiments.Onboarding.aiNativeAgeRange` in `Services/Experiments/Models/Experiments.swift` (alphabetical, doc comment: "a" = sheet after Get Started, "b"/nil = control; resolved only at the Get Started tap on iOS 26+; fails closed to control).
   - Resolved with `experimentService.assignExperiments([key], context: .onboarding)` at the tap, the same way `OnboardingQuizResultsViewModel.checkArchetypeExperiment()` does. Not added to `OnboardingExperimentBootstrapManager`: that would bucket every visitor, including Login users, and resolve it in `.authenticated` too.
   - Assign only on iOS 26+ (`#available(iOS 26.0, *)`), so control and treatment are both users who could see the sheet.
   - Bounded wait: reuse `VoiceExperimentArmLookup.firstOf(_:orTimeout:)` (`Services/Experiments/VoiceExperimentArmLookup.swift`) with a 1.5 s cap; a late or missing answer = control, no sheet. The welcome screen is spinning during this, and `assignExperiments` has no timeout of its own.
   - Persisted to the new account by the existing `persistOnboardingExperiments` at sign-up; the `Experiment: aiNativeAgeRange` Mixpanel super property comes from the existing `updateAnalyticsSuperProperties`.

2. **OS adapter on `PermissionManager`** (security rule: system prompts go through the permissions service):
   - `PermissionManagerProtocol.requestAgeRange(ageGate: Int) async -> AgeRangeResponse`.
   - New `Services/Permissions/PermissionManager+AgeRange.swift`: `@available(iOS 26)` path calls `AgeRangeService.shared.requestAgeRange(ageGates: ageGate, in: topViewController)`; maps `.sharing(range)` → `.adult` when `lowerBound >= ageGate`, else `.minor`; `.declinedSharing` → `.declined`; `AgeRangeService.Error` / no presenter / pre-26 → `.unavailable`. Unexpected errors logged and reported (`reportService`), `notAvailable` just logged (expected on some accounts/regions).
   - New `Services/Permissions/Models/AgeRangeResponse.swift`: `enum AgeRangeResponse { adult, declined, minor, unavailable }` with its analytics value.
   - Presenter: top view controller of the foreground key window (same lookup as `AppStoreProductView.topViewController()`).
   - Update the other conformers: `PreviewPermissionManager`, `UITestPermissionManager`, and the four private test stubs (`EditPracticesViewModelTests`, `AttachmentsViewModelTests`, `LiveSessionCCTooltipTests`, `OnboardingNamelessCopyTests`) with a one-line `requestAgeRange` returning `.unavailable`.

3. **Orchestration** — new `Features/Onboarding/Coordinator/NativeAgeRangePrompt.swift`, an enum like `VoiceExperimentArmLookup`, with `static func presentIfEnrolled(experimentService:permissionManager:analyticsManager:logger:) async`. Testable without building an `AppCoordinator` (35 dependencies).
   - In `AppCoordinator.checkAuthenticationAndNavigate()` `.getStarted` case: `await NativeAgeRangePrompt.presentIfEnrolled(...)` before `navigateToValueProps()`. Login and authenticated paths untouched.

4. **Analytics** — one event, `AI Age Range Permission` (mirrors `AI ATT Permission`), property `Action`: `Shared Adult` / `Shared Under 18` / `Declined` / `Unavailable`. Sent only in arm "a" once the sheet was requested. Name added to `AnalyticsEventsNames` alphabetically. Only the 18+ yes/no from the gate goes out, no exact bounds.

5. **Entitlement** — add `com.apple.developer.declared-age-range` = `true` to `AuraConsumerApp/AuraConsumerApp.entitlements`. Signing is automatic (`signingStyle automatic` in fastlane), so the App ID capability should sync on the next archive; flagged in the PR to confirm.

## Files

- `AuraConsumerApp/Services/Experiments/Models/Experiments.swift` — key
- `AuraConsumerApp/Services/Permissions/PermissionManagerProtocol.swift`, `PermissionManager+AgeRange.swift` (new), `Models/AgeRangeResponse.swift` (new)
- `AuraConsumerApp/Features/Onboarding/Coordinator/NativeAgeRangePrompt.swift` (new)
- `AuraConsumerApp/Features/App/Coordinator/AppCoordinator.swift` — one call in `.getStarted`
- `AuraConsumerApp/Services/Analytics/Models/AnalyticsEventsNames.swift` — event name
- `AuraConsumerApp/AuraConsumerApp.entitlements`
- Conformers: `Preview Content/Services/Preview/PreviewPermissionManager.swift`, `Tests/Services/UITest/UITestPermissionManager.swift`, 4 test stubs

## Tests

New `AuraConsumerAppTests/Features/Onboarding/Coordinator/NativeAgeRangePromptTests.swift` (stub experiment service like `LiveKitVoiceServiceVoiceExperimentTests`, recording analytics like `PreviewAnalyticsManager` subclasses):
- arm "a" → sheet requested with gate 18, event sent with the mapped `Action` (one case per response)
- arm "b" / nil → no sheet, no event
- assignment slower than the cap → no sheet, no event, returns promptly
- asked only for `aiNativeAgeRange` in `.onboarding` context

`AgeRangeResponse` analytics values covered in the same file.

## Verification

- `make proj`; `swiftlint lint --strict <changed files>`; build command from CLAUDE.md
- Unit tests on this session's own sim (`sim_start.sh`), `-only-testing:AuraConsumerAppTests`, results under `TestResults/` in the worktree
- Sim check on an iOS 26+ simulator, signed out: force the arm with the DEBUG experiment override, tap Get Started, confirm the sheet appears over the welcome screen and both buttons continue to ValueProps; screenshot for the PR. Control arm: straight to ValueProps.
- Draft PR, then BugBot per `pr-process.md`.

## Out of scope

- Creating the `aiNativeAgeRange` experiment record (AuraBot, `targetApp ai`, new-user gates) — after merge, on your word.
- Any behaviour change for under-18 or declined answers, and storing the result on the profile — waiting on Steve.
- Known gap: a new account made from the sign-in screen doesn't persist pre-login picks (existing behaviour, not fixed here).
