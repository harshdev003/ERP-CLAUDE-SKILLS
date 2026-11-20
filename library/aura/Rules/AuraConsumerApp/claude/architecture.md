---
description: Architecture conventions for AuraConsumerApp (MVVM + Dependency Injection)
globs:
alwaysApply: true
---

# Architecture

## Related Rules

- `concurrency-actors.md` for actor and main-actor isolation.
- `data-flow.md` for state ownership and ViewModel/service data flow.
- `service-patterns.md` for concrete service implementation conventions.

## MVVM + Dependency Injection

The app uses **MVVM** with a centralized **ServiceContainer** for dependency injection.

### Layer Responsibilities

| Layer | Location | Responsibility |
|-------|----------|----------------|
| **Views** | `Features/{Feature}/Views/` | SwiftUI rendering only — no business logic |
| **ViewModels** | `Features/{Feature}/ViewModels/` | State management, user intent, service orchestration |
| **Services** | `Services/{Domain}/` | Business logic, API calls, data persistence |
| **Domains** | `Domains/` | Shared models — `Codable`, `Sendable`, protocol-free structs |
| **Shared** | `Shared/` | Reusable components, extensions, coordinators, utilities |

### Service Ownership — auth vs. user data

`FirebaseAuthService` owns **auth/session only** — sign-in, tokens, `currentUser`, onboarding completion. User **profile-data** mutations (e.g. `PATCH /users` for name, settings, privacy) belong in **`UserService`**, alongside `updatePrivacy` — **not** in `FirebaseAuthService`. The pattern: `UserService` PATCHes, re-fetches the full profile via `firebaseAuthService.fetchUserProfileFromAPI`, caches it (`updateCachedUser`), and updates `AppStore.state.userSession.user`; ViewModels read the name/profile from the **store** (`currentUser` is get-only on the protocol).

### Feature Structure

Each feature lives in `Features/{Feature}/` and contains:

```
Features/Journal/
├── Views/
│   ├── JournalView.swift
│   └── JournalRowView.swift
├── ViewModels/
│   └── JournalViewModel.swift
└── Models/          (feature-local models, if not shared)
```

## ViewModel Pattern

- All ViewModels extend `BaseViewModel` (which is `@MainActor ObservableObject`).
- Use `@Published` for all properties that drive UI state.
- Inject all dependencies via `init` — never access `ServiceContainer` directly from a ViewModel.
- ViewModels are instantiated and owned by the View via `@StateObject`.

```swift
// ✅ GOOD - ViewModel with injected dependencies
@MainActor
class JournalViewModel: BaseViewModel {

    // MARK: - Dependencies
    let analyticsManager: AnalyticsManagerProtocol
    let apiService: APIServiceProtocol
    let logger = Logger.data

    init(analyticsManager: AnalyticsManagerProtocol, apiService: APIServiceProtocol) {
        self.analyticsManager = analyticsManager
        self.apiService = apiService
    }
}
```

### Managers vs. ViewModels (MANDATORY)

A `Manager` or `Service` is only for:

- an app-wide service with side effects — network, persistence, OS APIs; or
- app-wide UI state that unrelated screens share — `ThemeManager`, `ToastManager`.

State and actions that belong to a screen, or to a component on it (what a row shows, a confirmation pill, one reply's playback), belong in a **ViewModel**.

- **Never add a new `static let shared` singleton** for feature state, and never resolve dependencies from `ServiceContainer.shared` inside a type to get around initializer injection.
- **A component used on several screens gets a child ViewModel per screen.** The coordinator builds it with injected services and passes it into the screen's ViewModel. `AttachmentsViewModel` is the reference:
  - `HomeCoordinator` builds one per chat surface;
  - the screen ViewModels take it as an optional init parameter (`chatAttachments:`);
  - `ConversationViewProtocol` exposes it with a `nil` default;
  - views observe it with `if let` plus `@ObservedObject` (`.attachmentPanel(_:)`).
  - `MessageActionsViewModel` follows the same shape.
- **Per-user cleanup on logout** goes in the ViewModel's `clearState()` override (`BaseViewModel` listens for `.clearViewModelState`), not in a `ServiceContainer` call.
- **Legacy singletons are not precedent.** `MessageBookmarkManager.shared` and `MessageRatingManager.shared` predate this rule; don't copy them.

```swift
// ❌ BAD — a singleton manager for a component's state, deps pulled from the container
final class MessageActionsManager: ObservableObject {
    static let shared = MessageActionsManager()
    var store: AppStore { ServiceContainer.shared.appStore }
}

// ✅ GOOD — a child ViewModel the coordinator builds and the screen's ViewModel holds
let liveSessionViewModel = LiveSessionViewModel(
    ...,
    messageActions: MessageActionsViewModel(store: appStore, ttsService: ttsService),
    ...
)
```

## ServiceContainer

`ServiceContainer` is the single source of truth for all service instances:
- Services are **lazily initialized** — alphabetically ordered.
- Two-phase initialization: pre-consent (logging only) and post-consent (full SDK init).
- On logout, services that hold user state are rebuilt via `rebuildUserServices()`.

## Coordinators

Navigation is handled by **Coordinators** in `Shared/Coordinators/`. Views never push routes directly — they call coordinator methods or emit navigation events to the coordinator.

## Two-Phase Bootstrap

1. **Pre-consent** (before age gate): Only `Logger` and minimal services — no third-party SDK init.
2. **Post-consent** (after age gate accepted): Full SDK initialization (Firebase, Mixpanel, Bugsnag, Iterable, AppsFlyer).
