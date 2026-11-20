---
description: Architecture conventions for the Aura React Native app
alwaysApply: true
---

# Architecture

## Related Rules

- `state-management-reflux.md` for store + actions patterns.
- `navigation.md` for screen registration and React Navigation usage.
- `ui-design-system.md` for theme/style ownership.

## Stack

- React Native 0.87.1, React 19, **JavaScript only** (no TypeScript in `src/`).
- State: **Reflux** (`reflux@6.4.1`) with `seamless-immutable` for collections.
- Navigation: **custom Reflux-driven system** — `Actions.navigateTo(viewName, options, props)` → `NavigationController` (`src/events/NavigationController.js`, the event bus) → `Dashboard.js` (the resolver). `@react-navigation` v6 is in deps but only used inside isolated community sub-flows. See `navigation.md`.
- Networking: `axios` + a single `src/api.js` file (default-exports `Api`) plus higher-level wrappers in `src/requests/`.
- Analytics & error reporting: Mixpanel, Bugsnag, AppsFlyer, Iterable, Firebase — all funnelled through `src/logic/Trackings.js`.
- i18n: `i18next` + `react-i18next`.

## Layer Map

```
Aura/
├── app.js                 # Root navigator — every screen is registered here
├── index.{ios,android}.js # RN entry points
├── src/
│   ├── screens/           # One file per screen (e.g. FamilyPlanOnboarding.js)
│   ├── components/        # Reusable UI components
│   ├── reuseableUI/       # Older shared UI primitives — prefer src/components/ for new work
│   ├── stores/            # Reflux stores ({Domain}Controller.js or {Domain}Store.js)
│   ├── events/            # Event-bus stores (e.g. NavigationController)
│   ├── actions.js         # Single global Reflux Actions object
│   ├── logic/             # Side-effect modules + helpers (Trackings, APIManager, ...)
│   ├── api.js             # Network layer (single file, axios + default-exports `Api`)
│   ├── requests/          # Higher-level request wrappers
│   ├── services/          # Cross-cutting service modules
│   ├── styles/            # Theme system (see ui-design-system.md)
│   ├── data/              # Static data + enums (enums.js is the canonical enum file)
│   ├── localization/      # i18next translations (en, de, es, fr, ja, ko, pt_BR)
│   ├── hooks/             # Custom React hooks
│   ├── context/           # React contexts
│   ├── providers/         # Context providers
│   ├── lib/               # Vendored / third-party adapters (firebase, etc.)
│   └── ...
├── ios/                   # Xcode workspace: Aura.xcworkspace
├── android/               # Gradle project
└── apple/                 # APNs push certificates (.p12) — NOT widget code
```

## Layer Responsibilities

| Layer | Location | Responsibility |
|-------|----------|----------------|
| Screens | `src/screens/` | Top-level UI containers, subscribe to stores, dispatch Actions, navigate |
| Components | `src/components/`, `src/reuseableUI/` | Pure(ish) UI; receive data via props |
| Stores | `src/stores/` | Reflux state holders; call APIs; trigger events |
| Event-bus stores | `src/events/` | Reflux stores that just relay Actions as events (e.g. `NavigationController`) |
| Actions | `src/actions.js` | Centralized Reflux Actions; **never create per-feature Actions modules** |
| Logic / Services | `src/logic/`, `src/services/` | Side-effect modules (analytics, audio, downloads, helpers) |
| API | `src/api.js`, `src/requests/` | HTTP layer (single `api.js` axios file) + higher-level request wrappers |
| Styles | `src/styles/` | Theme objects per palette + global tokens (fonts, colors, dimensions, spaces, gradients) |
| Localization | `src/localization/<locale>/` | i18next JSON resource bundles |

## Where to Put New Code

- **New screen** → `src/screens/<ScreenName>.js` (or sub-folder for grouped screens like `src/screens/familyPlan/`); wire into the `navigateTo` switch in `Dashboard.js` and dispatch via `Actions.navigateTo('yourScreen', null, props)`. See `navigation.md`.
- **New reusable UI** → `src/components/`. Don't add to `reuseableUI/` unless you're extending an existing primitive there.
- **New global state** → new Reflux store in `src/stores/`, listen to existing Actions or extend `src/actions.js`.
- **New API endpoint** → `src/api.js` (extend the default `Api` export) or `src/requests/`, called from a store (not directly from a screen).
- **New theme value** → `src/styles/globals/<token>.js`, then expose via every theme `index.js`.
- **New translation key** → add to `src/localization/en/<namespace>.json` first, then run `npx i18next-scanner --config i18next-scanner.config.js` to propagate.

## Experiments and Flags

- `src/lib/ExperimentHelper.js` is the experiment system. Experiments are defined in Firebase RTDB and assigned per user after login, so nothing before login is gated.
- An experiment whose `active` is a single variant is rolled out: every user gets that variant, version and traffic gating are skipped, and a per-user force-set changes nothing.
- Gate every surface through one helper for the feature (e.g. a `UserController.canShow*` or a feature module function), never a raw `experiments?.x === 'a'` at the call site.
- To hide a shipped feature for now and bring it back later, gate it on an experiment and roll the "off" variant out, rather than deleting the code.
- To reach a variant in a sim run, force it with `sim_test.sh --experiment <key>=<variant>` (applied on read, never saved).
- Removing an ended experiment: keep the winning branch, and grep `app.js` as well as `src/`.

## Don't

- Don't put business logic in screens — push it into stores or `src/logic/`.
- Don't import from `src/reuseableUI/` if a `src/components/` equivalent exists.
- Don't create per-feature `actions.js` files — extend the global one.
- Don't bypass `src/logic/Trackings` for analytics or error reporting.
