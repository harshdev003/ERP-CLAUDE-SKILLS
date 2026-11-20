---
description: Swift concurrency and actor-isolation guidelines for Views, ViewModels, and Services
alwaysApply: true
---

# Concurrency and Actor Isolation

Use actor isolation to keep state safe and predictable across the app.

## Related Rules

- `data-flow.md` for ownership wrappers, AppStore scope, and stream boundaries.
- `service-patterns.md` for actor-based service implementation style.
- `performance.md` for runtime-performance-oriented async guidance.

## General

- Prefer value types (`struct`, `enum`) for models and immutable data.
- Prefer compiler-enforced isolation (`actor`, `@MainActor`) over manual thread management.

## Views

- Do not annotate SwiftUI `View` structs with `@MainActor`.
- Keep view state in SwiftUI property wrappers (`@State`, `@Binding`, `@ObservedObject`, `@StateObject`, `@EnvironmentObject`).
- Trigger async work from `.task`, `onAppear`, or explicit `Task {}` and write results back to SwiftUI state.
- Keep heavy/non-UI work out of `body`.

```swift
struct ItemsView: View {
    @State private var items: [String] = []

    var body: some View {
        List(items, id: \.self) { Text($0) }
            .task {
                let data = await fetchItems()
                items = data
            }
    }
}
```

## ViewModels

- Annotate ViewModels with `@MainActor` because they own UI-facing state.
- Expose UI state via `@Published` and mutate it on the main actor.
- Inject services that are not main-actor isolated by default; the ViewModel orchestrates between UI and services.
- If storing callbacks, declare them `@Sendable`.
- For data ownership and wrapper selection rules (`@StateObject` vs `@ObservedObject` vs `@EnvironmentObject`), follow `data-flow.md`.

```swift
@MainActor
final class ProfileViewModel: ObservableObject {
    @Published private(set) var name = ""
    private let service: ProfileServiceProtocol

    var onFinished: (@Sendable () -> Void)?

    init(service: ProfileServiceProtocol) {
        self.service = service
    }
}
```

## Managers and Services

- Default to `actor` for stateful services/managers that coordinate async work.
- Use `@MainActor` for services that must run on main-thread-bound OS/UI APIs.
- Use a regular class for stateless utilities or simple helpers where isolation is unnecessary.
- Keep network/data operations off main-actor services unless API constraints require main-thread calls.
- For stream/publisher and AppStore scope guidance, follow `data-flow.md`.
- For dependencies captured/stored by actors and sent across isolation boundaries, use `Sendable` protocol constraints where appropriate.

## Third-Party SDK Interop

- For SDKs that are not fully annotated for Swift Concurrency, prefer `@preconcurrency import <SDK>` as a targeted interoperability fix.
- Do NOT add `@preconcurrency` unless warnings indicate missing concurrency annotations from that SDK.
- For actor services, avoid calling actor-isolated setup methods from `init` when compiler diagnostics flag isolation issues.
- Prefer explicit async `initialize()` methods for SDK setup side effects, then call `initialize()` from app/service bootstrap before tracking/usage.

## Main-Actor-Critical iOS APIs

- `AVAudioSession` configuration and activation calls should execute on the main actor.
- `AVAudioSession` interruption and route-change notification handling should be wired on the main thread/main actor.
- `AVAudioEngine` setup/start/stop/connect operations should run on the main actor to avoid session race conditions.
- Notification listeners for these flows should be registered on the main actor.
