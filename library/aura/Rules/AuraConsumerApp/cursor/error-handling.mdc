---
description: Error handling patterns for AuraConsumerApp
globs:
alwaysApply: true
---

# Error Handling Patterns

## LocalizedError

Define domain-specific errors as enums conforming to `LocalizedError` and `Equatable`:

```swift
// ✅ GOOD - Domain error enum
enum ConversationError: LocalizedError, Equatable {
    case authenticationFailed(String)
    case connectionFailed(String)
    case unknown(String)

    var errorDescription: String? {
        switch self {
        case .authenticationFailed(let message):
            return "Authentication failed: \(message)"
        case .connectionFailed(let message):
            return "Connection failed: \(message)"
        case .unknown(let message):
            return message
        }
    }
}
```

## ViewModel Error Handling

Catch errors in ViewModels, log them, report to Bugsnag, and update UI state:

```swift
// ✅ GOOD - Error handling in a ViewModel
func loadJournals() async {
    logger.entry("Load journals")
    loadingState = .loading

    do {
        journals = try await journalManager.fetchJournals()
        loadingState = .loaded
        logger.exit("Load journals")
    } catch {
        logger.error(error, context: "Failed to load journals")
        reportService.report(error)
        loadingState = .error
    }
}
```

## Bugsnag Reporting

- Use `reportService.report(_ error: Error)` for unexpected errors in production.
- Do NOT report expected user-facing errors (e.g., validation failures, no-network).
- Always log with `logger.error()` before reporting.

## Error Propagation

- Services `throw` errors upward — they do not catch and swallow them.
- ViewModels catch errors from services and translate to UI state.
- Views never handle errors directly — they observe ViewModel state.

## No Silent Failures

- Never use `try?` on operations that could fail in non-obvious ways.
- Prefer `try` with `do/catch` so failures are logged and surfaced.

```swift
// ❌ BAD - Silent failure
let result = try? service.fetchData()

// ✅ GOOD - Explicit handling
do {
    let result = try await service.fetchData()
} catch {
    logger.error(error, context: "fetchData failed")
}
```
