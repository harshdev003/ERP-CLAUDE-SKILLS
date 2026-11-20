# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Project rules and standards are organized by topic in `.claude/rules/`. This file adds essential commands and architecture context.

## Essential Commands

```bash
# After cloning or changing project.yml / SPM packages
make proj                    # Regenerate Xcode project via xcodegen

# Lint (must pass with zero violations before any commit/PR)
swiftlint lint --strict

# Build (validate no compile errors)
xcodebuild -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp \
    -destination 'generic/platform=iOS Simulator' \
    build -quiet -allowProvisioningUpdates -destination-timeout 10

# Unit tests only (use DerivedData-CLI to avoid locking Xcode)
xcodebuild test \
    -project AuraConsumerApp.xcodeproj \
    -scheme AuraConsumerApp \
    -destination 'platform=iOS Simulator,name=iPhone 17 Pro,OS=latest' \
    -only-testing:AuraConsumerAppTests \
    -derivedDataPath DerivedData-CLI \
    -resultBundlePath TestResults/UnitTests

# Clean up after test runs
rm -rf DerivedData-CLI TestResults

# Generate a new feature screen (View + ViewModel + Service scaffolding)
make screen SCREEN=FeatureName [FOLDER=ParentFolder]
```

Git hooks run SwiftLint on staged Swift files at pre-commit, and SwiftLint + build at pre-push. Both skip the Swift steps when the commit/push touches only docs, sim-test knowledge, or CI/hook config, so those pushes do not need `--no-verify`. Set up once with `make install-hook`.

## Architecture Overview

**Aurie** is a voice-assistant therapy companion app (iOS 17+, Swift 5, SwiftUI).

### Layer Map

```
AuraConsumerApp/
├── App/                    # AppDelegate, SceneDelegate, bootstrap
├── DependencyInjection/    # ServiceContainer + lifecycle protocols
├── Domains/                # Shared Codable+Sendable structs — no business logic
├── Features/{Feature}/     # MVVM per feature: Views/, ViewModels/, Models/
├── Services/{Domain}/      # Business logic, API calls, persistence (actor-based)
└── Shared/                 # Reusable UI components, coordinators, extensions
```

### Key Architectural Patterns

- **MVVM + DI**: Views own ViewModels via `@StateObject`. ViewModels are `@MainActor ObservableObject` extending `BaseViewModel`. All dependencies are constructor-injected from `ServiceContainer` — ViewModels never reference `ServiceContainer` directly.
- **ServiceContainer**: Single DI container with lazily initialized, alphabetically ordered services. Has `rebuildUserServices()` for logout/account-switch. Lifecycle protocols (`AuthLifecycleProtocol`, `SceneLifecycleProtocol`, `UserLifecycleProtocol`) let services hook into app events.
- **Two-phase bootstrap**: Pre-consent (Logger only) → post-consent (Firebase, Mixpanel, Bugsnag, Iterable, AppsFlyer). No third-party SDK init before age gate.
- **Coordinators** in `Shared/Coordinators/` own navigation — Views never push routes directly.
- **Concurrency**: Services default to `actor`. ViewModels are `@MainActor`. Domain models are `Sendable`.

### Project Generation

The `.xcodeproj` is generated from `project.yml` via `xcodegen`. Run `make proj` after pulling changes that modify `project.yml` or after adding SPM packages.

## Rules

Keep this list in sync whenever `.claude/rules/` files are added, renamed, or removed.

@.claude/rules/project-rules.md
@.claude/rules/accessibility.md
@.claude/rules/architecture.md
@.claude/rules/coding.md
@.claude/rules/comments.md
@.claude/rules/naming.md
@.claude/rules/data-flow.md
@.claude/rules/service-patterns.md
@.claude/rules/concurrency-actors.md
@.claude/rules/error-handling.md
@.claude/rules/logging.md
@.claude/rules/performance.md
@.claude/rules/pr-process.md
@.claude/rules/security.md
@.claude/rules/documentation.md
@.claude/rules/config.md
@.claude/rules/testing.md
@.claude/rules/sim-testing.md
@.claude/rules/sim-testing-conventions.md

## Skills

Keep this list in sync whenever `.claude/skills/` folders are added, renamed, or removed.

@.claude/skills/create-iap-subscription/SKILL.md
@.claude/skills/sim-test/SKILL.md
@.claude/skills/parallel-agents/SKILL.md
@.claude/skills/release-ios/SKILL.md
@.claude/skills/release-feature-branch-ios/SKILL.md
@.claude/skills/aurie-screens-weekly/SKILL.md
@.claude/skills/screenshot-harvest/SKILL.md
@.claude/skills/voice-match/SKILL.md
