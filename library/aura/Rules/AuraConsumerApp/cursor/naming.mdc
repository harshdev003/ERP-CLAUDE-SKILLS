---
description: Naming conventions for AuraConsumerApp
globs:
alwaysApply: true
---

# Naming Conventions

## Type Naming

| Type | Convention | Example |
|------|-----------|---------|
| Views | `{Feature}View` | `JournalView`, `HomeDashboardView` |
| ViewModels | `{Feature}ViewModel` | `JournalViewModel`, `HomeDashboardViewModel` |
| Services | `{Domain}Service` | `JournalService`, `AnalyticsManager` |
| Protocols | `{Name}Protocol` | `APIServiceProtocol`, `JournalManagerProtocol` |
| Coordinators | `{Feature}Coordinator` | `AuthCoordinator`, `HomeCoordinator` |
| Models/Structs | `{EntityName}` | `JournalItem`, `FeedItem`, `User` |
| Errors | `{Domain}Error` | `ConversationError`, `AuthError` |
| Enums (state) | `{Name}State` or `{Name}Status` | `LoadingState`, `AuthStatus` |

## Property Naming

- Use descriptive, self-documenting names — prefer clarity over brevity.
- Boolean properties: use `is`, `has`, `can`, `should` prefix (e.g., `isLoading`, `hasMorePages`).
- Collections: use plural nouns (e.g., `journals`, `feedItems`).
- Avoid abbreviations unless widely understood (e.g., `id`, `url`, `api` are fine).

## Method Naming

- Use Swift API Design Guidelines: verbs for mutating, nouns for non-mutating.
- Async methods should be named as if they return synchronously (e.g., `loadJournals()`, not `fetchJournalsAsync()`).
- Boolean-returning methods: `isValid()`, `canPerform()`, `hasFeature()`.

## File Naming

- File name must match the primary type it contains: `JournalViewModel.swift` contains `JournalViewModel`.
- Extensions in separate files: `{Type}+{Concern}.swift` (e.g., `LiveKitVoiceService+AudioSession.swift`).

## Constants

- Use `static let` inside enums or structs for namespaced constants.
- Use `UPPER_SNAKE_CASE` only for Objective-C bridging — prefer `static let camelCase` in Swift.

```swift
// ✅ GOOD
enum AnalyticsEventsNames {
    static let aiJournalDeleted = "ai_journal_deleted"
    static let journalCreated = "journal_created"
}
```

## SwiftUI View Identifiers

- Use `accessibilityIdentifier` for UI testing — follow `{feature}_{component}` format (e.g., `"journal_deleteButton"`).
- Screen identifiers **must** end with literal `_screen` (snake_case): `"onboarding_quiz_screen"`, NOT `"onboarding_quizScreen"`. The sim test parser uses `hasSuffix("_screen")` for detection.
