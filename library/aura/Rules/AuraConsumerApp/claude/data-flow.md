---
description: Data flow conventions for Views, ViewModels, services, AppStore, and event streams
alwaysApply: true
---

# Data Flow

Use a layered flow: services produce data/events, ViewModels map to UI state, Views render.

## Related Rules

- `concurrency-actors.md` for actor and main-actor isolation rules.
- `architecture.md` for MVVM+DI boundaries and coordinator responsibilities.
- `service-patterns.md` for practical service conventions based on `IterableService`.

## Core Principles

- Keep UI-facing state in `@Published` on `@MainActor` ViewModels.
- Keep business/domain work in services, not in Views.
- Keep global state minimal; only elevate to `AppStore` when multiple features depend on it.
- Prefer typed, testable flows over stringly-typed notification pipelines.

## SwiftUI Ownership Rules

- Use `@StateObject` when the View creates and owns the ViewModel.
- Use `@ObservedObject` when the ViewModel is injected by a parent/coordinator.
- Use `@EnvironmentObject` for truly shared app-level state only (theme/session/app store), not as a generic service locator.

## ViewModel State Rules

- Use `@Published` for values the View renders.
- Keep mutations on the main actor by making ViewModels `@MainActor`.
- Subscribe to service outputs in ViewModel and map to UI state.

## Service Output Patterns

- Services may expose:
  - async APIs for request/response operations, and
  - stream APIs (`AnyPublisher` or async stream) for updates/events.
- Keep subject instances private; expose read-only publisher/stream interfaces.
- Do not let Views subscribe directly to services for feature state; route through ViewModel.

## AppStore Scope

- `AppStore` should hold global cross-feature state (session, consent, app lifecycle, global flags).
- Do not place screen-local form/editing state in `AppStore`.
- Promote state into `AppStore` only when at least two independent features need shared reactive access.

## NotificationCenter Guidance

- Use `NotificationCenter` for OS/framework callbacks and legacy bridges.
- Convert notifications into typed service events/state as close to the source as possible.
- Avoid feature-level business flow that depends directly on raw notification names and `userInfo` dictionaries.

## Combine and Thread Safety

- Treat `CurrentValueSubject` as stateful and isolate mutations/reads behind `actor` or `@MainActor`.
- `PassthroughSubject.send` can be called from multiple contexts, but ordering/race semantics still require isolation discipline.
- Avoid relying on unsynchronized cross-thread `.value` access patterns.

## Recommended Architecture Choice

- Default: service stream/publisher + ViewModel `@Published` state.
- Add `AppStore` only for truly global state.
- Keep NotificationCenter as a boundary integration mechanism, not primary feature data flow.
