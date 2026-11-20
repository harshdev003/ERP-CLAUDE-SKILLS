# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security-review.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security-review.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every mobile app repo. Change that page first, then every copy.

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

# Unit tests only, on this session's own simulator (use DerivedData-CLI to avoid locking Xcode).
# Never a name-based destination: it can fall through to a connected iPhone.
# Parallel testing off, or xcodebuild boots up to 5 simulator clones.
SID=$(bash AuraConsumerAppSimTests/Scripts/sim_start.sh | tail -1)
xcodebuild test \
    -project AuraConsumerApp.xcodeproj \
    -scheme AuraConsumerApp \
    -destination "platform=iOS Simulator,id=$SID" \
    -parallel-testing-enabled NO \
    -only-testing:AuraConsumerAppTests \
    -derivedDataPath DerivedData-CLI \
    -resultBundlePath TestResults/UnitTests

# Clean up after test runs
rm -rf DerivedData-CLI TestResults

# Generate a new feature screen (View + ViewModel + Service scaffolding)
make screen SCREEN=FeatureName [FOLDER=ParentFolder]
```

Git hooks run SwiftLint on staged Swift files at pre-commit, and SwiftLint + build at pre-push. Both skip the Swift steps when the commit/push touches only docs, sim-test knowledge, or CI/hook config, so those pushes do not need `--no-verify`. Set up once with `make install-hook`.

If the pre-push build fails with `xcodebuild` exit 74, "Could not resolve package dependencies" or "Couldn't check out revision …", the local Swift package checkout is broken, not the code. This happens most often on a fresh worktree after a session restart. Run `xcodebuild -resolvePackageDependencies -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp` from the worktree, then push again. Never skip the hook with `--no-verify`.

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

Every file in `.claude/rules/` loads on its own, so this list is not `@`-imported: an import would load it a second time. Files with `paths:` frontmatter load only when Claude reads a matching file. Keep this list in sync when rule files are added, renamed or removed, and keep everything loaded at startup under 150k characters.

`.claude/rules/*.md` is the source of truth. `.cursor/rules/*.mdc` are copies for Cursor: their own front matter plus the same body. After editing a rule, run `python3 Scripts/sync_cursor_rules.py sync` and commit both; the pre-commit hook and CircleCI's lint job fail when they differ.

- Always loaded: `project-rules.md` (workflow gates), `repo-specific.md` (rules for this repo only), `architecture.md`, `coding.md`, `comments.md`, `naming.md`, `data-flow.md`, `service-patterns.md`, `concurrency-actors.md`, `error-handling.md`, `logging.md`, `performance.md`, `pr-process.md`, `security.md`, `security-review.md`, `documentation.md`, `config.md`, `testing.md`
- Path-scoped: `accessibility.md` (views, components, shared UI, theme, UI and sim tests), `sim-testing.md` and `sim-testing-conventions.md` (`AuraConsumerAppSimTests/` and the sim-test, screenshot-harvest and aurie-screens-weekly skills)

## Skills

Skills in `.claude/skills/` are listed by their descriptions and load when invoked, so they are not `@`-imported either. Keep this list in sync when skill folders are added, renamed or removed.

- `sim-test`: autonomous simulator testing against real services
- `screenshot-harvest`: capture, review and store screenshots in every sky
- `aurie-screens-weekly`: the Monday screens routine (harvest, PR, Notion and Claude Design sync)
- `parallel-agents`: Linear ticket fan-out into sub-card PRs on a shared base branch
- `release-ios`: a real release: TestFlight upload, bump PR, tag, GitHub release
- `release-feature-branch-ios`: a TestFlight build of the current branch for QA, with no tag
- `create-iap-subscription`: create an App Store subscription product and register it with the backend
