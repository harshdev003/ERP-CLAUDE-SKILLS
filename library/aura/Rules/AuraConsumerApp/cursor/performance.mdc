---
description: Performance guidelines for AuraConsumerApp
globs:
alwaysApply: true
---

# Performance Guidelines

## Related Rules

- `concurrency-actors.md` for actor-isolation and main-thread-bound API guidance.
- `data-flow.md` for wrapper ownership and ViewModel state placement.
- `service-patterns.md` for cancellation-aware service execution patterns.

## SwiftUI View Performance

- Use `LazyVStack` / `LazyHStack` for lists that render many items.
- Avoid complex computations or heavy allocations inside `body` — move to ViewModel computed properties.
- Use `@State` for local UI state (toggle, text input); use `@Published` in ViewModel for business state.
- Prefer `.task {}` modifier over `onAppear` + `Task {}` for async data loading in views.

## Memory Management

- Follow `data-flow.md` for `@StateObject` / `@ObservedObject` / `@EnvironmentObject` ownership rules.
- Use `[weak self]` in escaping closures and long-lived tasks when needed to prevent retain cycles.
- Clean up resources in `onDisappear` or `deinit` (cancel tasks, remove listeners).

```swift
// ✅ GOOD - Owned ViewModel
@StateObject private var viewModel = JournalViewModel(...)

// ✅ GOOD - Injected ViewModel
@ObservedObject var viewModel: JournalViewModel
```

## Async & Concurrency

- Use structured concurrency (`async/await`, `TaskGroup`) — avoid unstructured `DispatchQueue` calls.
- Follow `concurrency-actors.md` for actor-isolation and main-thread-bound API rules.
- Cancel long-running Tasks when a View disappears:

```swift
.task {
    await viewModel.loadData()
}
// .task modifier auto-cancels when the view disappears
```

- Use `@MainActor` ViewModels and avoid manual `DispatchQueue.main.async` for UI state mutation.

## Image & Media

- Use `AsyncImage` or a caching layer for remote images — never load raw `Data` synchronously.
- Avoid keeping large `UIImage` / `Data` objects in `@Published` properties longer than necessary.

## Caching

- Use the `CacheService` for network response caching where appropriate.
- Prefer in-memory caching for session-scoped data; use SwiftData for persistent data.

## Performance Profiling

Use `Logger.performance` with `logger.startTime()` / `PerformanceTimer` to measure slow operations:

```swift
let timer = logger.startTime("Load feed")
// ... heavy operation ...
// timer logs duration on deinit or explicit stop
```
