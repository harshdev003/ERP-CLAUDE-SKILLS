---
description: Service implementation patterns based on IterableService conventions
alwaysApply: true
---

# Service Patterns

Use `IterableService` as the reference style for service quality, structure, and maintainability.

## Related Rules

- `comments.md` for comment preservation and when to add reasoning comments.
- `documentation.md` for SDK documentation links and "why" context comments.
- `concurrency-actors.md` for actor and main-actor service isolation decisions.
- `data-flow.md` for service output boundaries and AppStore scope.

## Service Structure

- Split a service into one primary type plus **focused extension files by concern**. A cohesive new concern goes in its **own `{Service}+{Concern}.swift` file** — NOT an inline `extension` block appended to the bottom of the primary file. Inline-extending the primary file bloats it and buries the concern; a separate file keeps each unit small and reviewable. xcodegen globs the folder, so a new file under `Services/{Domain}/` is picked up by `make proj` automatically — no `project.yml` edit needed.
  - `{Service}.swift` for core state and orchestration
  - `{Service}+AuthLifecycle.swift`
  - `{Service}+Events.swift`
  - `{Service}+Errors.swift`
  - `{Service}+Logging.swift`
- To let a concern's extension file reach the type's state and dependencies, declare those members **internal** (`let` / `var`), not `private` — Swift `private` is file-scoped and unreachable from another file (this is why the `IterableService` deps below are internal `let`). Keep helpers only one extension uses `private` within that file.
- Keep file names aligned to the primary type and concern.
- Group members using project MARK order from `coding.md`.

## Dependency Injection and Types

- Inject dependencies via initializer using protocol types.
- Avoid creating service dependencies inline in business methods.
- Prefer actor or other safe concurrency boundaries for mutable service state.

## Logging and Error Behavior

- Use `Logger` category appropriate to the service domain.
- Apply `logger.entry("method")` / `logger.exit("method")` for significant flows.
- Log expected guard exits at `debug` or `warn` with clear context.
- Use `logger.error` for failures and report unexpected errors via reporting service.

## Async and Cancellation

- Add explicit cancellation checks for multi-step async flows where stale work can corrupt state.
- Document cancellation-sensitive logic with short rationale comments near the guarded code.
- Ensure logout/account-switch paths cancel in-flight tasks that could affect the next user context.

## Third-Party SDK Integrations

- Include SDK documentation link in the file header for integration files.
- Add concise "why" comments for non-obvious sequencing, state guards, and edge cases.
- Keep SDK-specific adapter code isolated to the service and related extensions.

## Example Pattern

```swift
//
//  IterableService
//  Iterable SDK integration for push notification management and user engagement
//
//  Documentation: https://support.iterable.com/hc/en-us/articles/360035018152-Iterable-s-iOS-SDK
//

actor IterableService: IterableServiceProtocol {
    let apiService: APIServiceProtocol
    let logger = Logger.iterable

    /// Cancelled on login to avoid stale async cleanup clearing new user metadata.
    var unidentifyTask: Task<Void, Never>?
}
```
