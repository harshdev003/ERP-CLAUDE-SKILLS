---
description: Logging standards and best practices for AuraConsumerApp
globs:
alwaysApply: true
---

# Logging Standards

## Logger Usage Requirements

- **ALWAYS use `Logger` instead of `print` statements** — no `print()` calls in production code.
- **Use the correct Logger category** for the context (see table below).
- **Follow the entry/exit pattern** for all meaningful methods.
- **Never log sensitive data** — no passwords, tokens, or PII.

## Log Level Guidelines

| Method | When to use |
|--------|-------------|
| `logger.entry("Op name")` | Start of a significant method (logs `🎯 Entry: ...`) |
| `logger.exit("Op name")` | End of a significant method (logs `✨ Exit: ...`) |
| `logger.debug("...")` | Step-by-step operational traces, variable values |
| `logger.info("...")` | Important business events, successful state changes |
| `logger.warn("...")` | Unexpected but handled states, missing optional data |
| `logger.error("...")` | Failures — call with `Error` object + context string |

## Entry/Exit Pattern

Use `entry`/`exit` to bracket significant async operations and ViewModel lifecycle methods:

```swift
func loadData() async {
    logger.entry("Load data")
    // ... implementation ...
    logger.exit("Load data")
}
```

For early exits due to errors, still call `logger.exit` or use `logger.error` before returning.

## Logger Categories

Declare the logger as a constant in `// MARK: - Dependencies`:

```swift
let logger = Logger.data   // pick the right category
```

| Category | Use for |
|----------|---------|
| `Logger.analytics` | Analytics events, tracking, user behavior |
| `Logger.app` | App lifecycle, init, DI, bootstrap |
| `Logger.audio` | Audio playback, TTS, audio processing |
| `Logger.auth` | Login/logout, user sessions, Firebase auth |
| `Logger.config` | Remote config, feature flags |
| `Logger.conversation` | Voice chat, WebSocket, VAD |
| `Logger.data` | Persistence, caching, SwiftData |
| `Logger.deeplink` | Deep link handling, Branch routing |
| `Logger.navigation` | Coordinator events, routing |
| `Logger.network` | API calls, HTTP requests |
| `Logger.permission` | System permission requests |
| `Logger.performance` | Timing, profiling, metrics |
| `Logger.preference` | User settings, preferences |
| `Logger.profile` | User profile, account operations |
| `Logger.reporting` | Bugsnag, crash handling |
| `Logger.ui` | View lifecycle, user interactions, theming |

## Adding a New Category

Add to the global extension in `Services/Log/Logger.swift`:

```swift
public extension Logger {
    /// Logger for [new feature description]
    static let newFeature: Logger = Logger(category: "NewFeature")
}
```

Use a descriptive, single-word category name. Add before submitting any PR that introduces a new logging domain.
