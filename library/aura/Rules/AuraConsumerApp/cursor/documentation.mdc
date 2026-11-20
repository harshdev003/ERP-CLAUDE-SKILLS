---
description: Documentation standards for AuraConsumerApp
globs:
alwaysApply: true
---

# Documentation

## Related Rules

- `comments.md` for comment preservation rules and when to add context comments.
- `service-patterns.md` for service file structure and integration boundaries.
- `coding.md` for MARK ordering and general Swift style requirements.
- `data-flow.md` for documenting data ownership and event-flow assumptions.

## File Header

Every Swift file must start with the standard header:

```swift
//
//  FileName
//  Brief description of the file's purpose
//
//  Created by [Author] on [MM/DD/YYYY].
//  Copyright © [Year] Aura Health, Inc. All rights reserved.
//
```

## DocC Comments

Use `///` triple-slash DocC comments for all public APIs, services, and non-obvious logic:

```swift
/// Loads the journal list for the current user, handling pagination.
///
/// - Parameter forceRefresh: When `true`, bypasses the cache and fetches from the network.
/// - Returns: An array of ``JournalItem`` sorted by creation date descending.
func loadJournals(forceRefresh: Bool = false) async -> [JournalItem]
```

- Include `- Parameter`, `- Returns`, and `- Throws` tags where applicable.
- Add usage examples for complex or non-obvious APIs.
- Document all enum cases with a brief `///` description.

## Service Context Documentation

Service files must include context that helps future readers understand integrations and tricky behavior.

- Add an external documentation link in the file header for third-party SDK integrations.
- Add short rationale comments for complex or non-obvious logic (state machines, cancellation behavior, sequencing, edge-case guards).
- Prefer "why" comments over "what" comments; code already shows what happens.
- Keep context comments close to the logic they explain and update them when behavior changes.

```swift
//
//  IterableService
//  Iterable SDK integration for push notification management and user engagement
//
//  Documentation: https://support.iterable.com/hc/en-us/articles/360035018152-Iterable-s-iOS-SDK
//

/// In-flight task for async logout cleanup. Cancelled on login to prevent it
/// from wiping metadata for the newly logged-in user.
var unidentifyTask: Task<Void, Never>?
```

## MARK Comments

Use `// MARK: -` to divide files into logical sections (see `coding.md` for the required order). Always include the dash and a space:

```swift
// MARK: - Dependencies   ✅
// MARK: Dependencies      ❌ (missing dash)
//MARK: - Dependencies     ❌ (missing space after //)
```

## README

`README.md` should cover:
- App purpose and overview
- Environment setup (Xcode version, Swift version, dependencies)
- How to run locally (`make proj`, fastlane commands)
- Key architectural decisions

Do NOT document individual API endpoints or business logic flows in README — that belongs in DocC or inline comments.

## UI Guidelines

The project has a `UI_Guidelines.md` — consult it before adding new UI components or modifying visual design patterns.
