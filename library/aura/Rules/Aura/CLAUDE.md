# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Security review:** `.claude/rules/security-review.md` is the checklist every reviewer applies before approving a PR: Claude, Cursor (`.cursor/rules/security-review.mdc`), BugBot (`.cursor/BUGBOT.md`) and people. All three are copies of AI Knowledge → Compliance → Security → Code Review Checklist, identical in every mobile app repo. Change that page first, then every copy.

Project rules and standards are organized by topic in `.claude/rules/`. This file adds essential commands and architecture context.

## Essential Commands

```bash
# FIRST RUN in a new worktree or clone — do these before the install below.
# src/cask/ is gitignored, so a fresh checkout has no Firebase/secrets config and
# the iOS build dies on:
#   Build input file cannot be found: src/cask/GoogleService-Info.plist
bash scripts/sync-cask.sh seed --apply   # worktree only — copies src/cask/{dev,prod} from the parent checkout
bash scripts/sync-cask.sh switch dev     # point the cask symlinks at an env (dev|prod)
bundle install                           # CocoaPods + fastlane, into the Ruby pinned in .ruby-version (3.3.11)

# Install (first time or after dependency changes)
npm install --legacy-peer-deps
cd ios && bundle exec pod install --repo-update && cd ..

# Full clean reinstall (when things break)
npm run setup

# Start the Metro bundler (needed before iOS/Android run)
npm run start

# Run on a simulator
npm run ios
npm run android

# Lint (must pass before any commit/PR)
npm run lint
npm run lint:fix          # auto-fix what ESLint can

# Tests
npm test                  # all jest tests
npm run test:unit         # only /unit/ tests
npm run test:integration  # only /integration/ tests

# Localization — extract i18n keys after adding new t('...') usages
npx i18next-scanner --config i18next-scanner.config.js

# iOS archive
npm run archive

# Android release bundle
npm run android:bundleRelease

# Autonomous Claude-driven sim test on iOS Simulator (release-config Aura.app)
# See .claude/skills/sim-test/SKILL.md for full usage
bash AuraSimTests/Scripts/sim_start.sh --reset       # boot a clean sim
bash AuraSimTests/Scripts/sim_install.sh             # build + install release Aura.app
bash AuraSimTests/Scripts/sim_test.sh --goal "..."   # launch the XCUITest harness
```

Node version is pinned in `.nvmrc` (currently `22.20.0`) and that file is the single source of truth — CI reads it, `package.json` `engines` only bounds it, and `nvm use` before running any of the above is what keeps your shell in step. Xcode reads it too: `npm install`'s postinstall writes `ios/.xcode.env.local` (gitignored) with the Node that ran the install, and **fails the install** if that isn't the `.nvmrc` one — so `nvm use` before installing, or you get an error instead of a wrong-Node JS bundle. See [.claude/rules/native-ios.md](.claude/rules/native-ios.md). Node 22 is the floor because 18 and 20 are both past EOL; RN 0.77 needs `>=18` and Metro 0.81 needs `>=18.18`, so 22 satisfies the tree with room to spare. Ruby is pinned in `.ruby-version` (`3.3.11`) — `bundle install` puts CocoaPods and fastlane into *that* Ruby, which is why `pod` must be called as `bundle exec pod`.

`seed` only works in a worktree — it copies from the checkout the worktree was created from. In a fresh **clone** there is nothing to copy from, so get `src/cask/{dev,prod}` from a teammate, then run `switch`. See [.claude/skills/sync-cask/SKILL.md](.claude/skills/sync-cask/SKILL.md) for the full cask layout and the verification `switch` performs.

Work on a card in its own worktree (`.claude/worktrees/<card>`, branch named after the card), not in the main checkout. To run jest or eslint in a worktree without a full install, symlink the parent checkout's `node_modules` into the worktree for the run, and never commit it: stage explicit paths, never `git add -A`, which picks up the symlink. `prettier --write` on a whole file also reformats lines you didn't touch, so revert those hunks before committing.

## Architecture Overview

**Aura** is a React Native mobile app (RN 0.87.1, React 19, JavaScript-only — no TypeScript in `src/`). State is managed with **Reflux**. Navigation is a **custom Reflux-driven system** — `Actions.navigateTo(viewName, options, props)` → `NavigationController` (event bus) → `Dashboard.js` (resolver). React Navigation v6 is in deps but only used in isolated community sub-flows. See [.claude/rules/navigation.md](.claude/rules/navigation.md).

### Layer Map

```
Aura/
├── app.js                 # Root navigator — registers every screen
├── index.{ios,android}.js # RN entry points
├── src/
│   ├── screens/           # One file per screen (e.g. FamilyPlanOnboarding.js)
│   ├── components/        # Reusable UI components
│   ├── reuseableUI/       # Older shared UI primitives
│   ├── stores/            # Reflux stores (e.g. RNIapController, AppInfoStore)
│   ├── actions.js         # Reflux Actions (single global Actions object)
│   ├── logic/             # Helpers + side-effect modules (Trackings, APIManager, ...)
│   ├── api.js             # Network layer (single file, default-exports `Api`)
│   ├── requests/          # Request wrappers
│   ├── services/          # Cross-cutting service modules
│   ├── styles/            # Theme system (see UI section below)
│   ├── data/              # Static data + enums (enums.js)
│   ├── localization/      # i18next translations (en, de, es, fr, ja, ko, pt_BR)
│   ├── hooks/             # Custom React hooks
│   ├── context/           # React contexts
│   ├── providers/         # Context providers
│   ├── lib/               # Vendored / third-party adapters (firebase, etc.)
│   └── ...
├── ios/                   # Xcode project (workspace: Aura.xcworkspace)
├── android/               # Gradle project
└── apple/                 # APNs push certificates (.p12) — NOT widget code
```

### Key Architectural Patterns

- **Reflux state**: Stores live in `src/stores/`, declare `listenables: Actions`, mutate own fields, then call `this.trigger(EVENT, payload)`. Components subscribe with direct `this.FooStore = FooStore.listen((...args) => this.onFooStoreChange(...args))` and unsubscribe via `this.FooStore?.()` in `componentWillUnmount` — NOT `Reflux.connect` / `Reflux.listenTo` (neither is used in this codebase). Use `seamless-immutable` for collections that should not be mutated.
- **Actions**: All actions are declared centrally in `src/actions.js`. Don't create per-feature Actions modules — extend the central one.
- **Theme system**: A theme object (selected from `src/styles/{cleanDark,cleanLight,light,nature,newDark,newLight,rainbowDark}/index.js`) is passed via context/props. Use `theme.fonts.fontXXX`, `theme.colors.*`, `theme.dimensions.r{N}`, `theme.gradients`, `theme.shadows`, `theme.spaces`, `theme.borderRadius` — never hard-code colors, font sizes, or pixel values.
- **`responsiveDimension(N)`** from `src/styles/dimensions.js` is the single way to express pixel dimensions. It applies a width-based `moderateScale` (baseline 375pt, factor 0.3), implemented in that file — there is no `.env` and no `react-native-dotenv` in this repo (PROD-4975).
- **Error reporting**: All caught errors funnel through `bugsnag.report('Title', { ...context })` from `src/logic/Trackings`. Import as `import { bugsnag } from '<path>/logic/Trackings'` — never `Trackings.bugsnag.report`.
- **Logging**: `no-console` is an ESLint error in this repo. Use `bugsnag.report` for errors and `Trackings(eventName, data)` for analytics; do not commit `console.log`.
- **Localization**: All user-facing strings go through `react-i18next` (`t('key')`). After adding new keys, run the scanner to update language JSON files.
- **Cask config**: `src/cask/` is gitignored, so `src/cask/meals.sample.js` is the only record in the repo of what a cask must contain. **Any new key added to `meals.js` MUST be added to `meals.sample.js` in the same commit** — same key, alphabetical position, value redacted as `****`. Never the real value; the sample is committed. See [.claude/rules/security.md](.claude/rules/security.md).
- **API calls**: there is no single API client. Calls go through axios or call `fetch` directly (~50 sites), and each builds its own `Authorization` header. A behaviour that must apply to every request goes in both paths: an axios interceptor and the one-time wrapper around global `fetch`, both installed in `src/api.js`, with the shared logic in its own module (auth retry: `src/lib/authRetry.js`).
- **Service keys travel as `Authorization`**: `code.shield`, `code.apiAccessToken` and `code.aurieApiAccessToken` are sent in place of a user token, and two of them are JWT-shaped, so a token's shape can't tell a user token from a service key. Code that treats a Bearer token as the user's must exclude `SERVICE_KEYS` from `src/lib/authRetry.js`; `test/unit/authRetry.test.js` fails when a new key is sent without being listed there.
- **Deep links**: every source goes through `src/components/DeeplinkHandler.js`, rendered once by `app.js`. That covers universal links, `aurahealth://`, Branch, OneSignal, Iterable, local notifications and payment returns. Pure parsers in `src/models/deeplink.js` turn input into `{ destination, objectId, params }`, and one router, `handleDeeplinkIntent`, opens it. The router parks a link until a user is signed in and the dashboard is up. Never listen for, parse or navigate a link anywhere else. See [.claude/rules/deeplinks.md](.claude/rules/deeplinks.md).
- **Navigation**: Screens are registered in `Dashboard.js` (the central resolver — the `switch` in its `navigateTo` handler). Trigger navigation from anywhere via `Actions.navigateTo('viewName', null, props)`. Modals over the current screen via `Actions.superimposeViewControl(...)`; a card that appears on the Home feed by itself (launch IAM, invitation, promo) is a different, fixed pattern — see "Launch cards on Home". See [.claude/rules/navigation.md](.claude/rules/navigation.md).

### Native Layers

- iOS workspace is `ios/Aura.xcworkspace` (open this, **not** the `.xcodeproj`). Schemes: `Aura`, `Aura WidgetExtension`. Pods are managed via CocoaPods, pinned in the `Gemfile` (`bundle exec pod`).
- Android uses Gradle; `npx jetify` runs automatically on `postinstall`.

## Rules

Every file in `.claude/rules/` loads on its own; don't `@`-import them here, because an import loads a file into every session whatever its `paths:` scope. Keep this index in sync whenever a rule file is added, renamed, or removed.

Always loaded: `project-rules`, `architecture`, `coding`, `comments`, `naming`, `documentation`, `error-handling`, `error-handling-bugsnag`, `logging`, `performance`, `security`, `security-review`, `pr-process`, `ui-design-system`, `figma`, `state-management-reflux`, `navigation`, `localization`, `native-android`.

Loaded only when Claude reads a matching file (`paths:` frontmatter):

| Rule | Loads for |
|---|---|
| `accessibility` | `src/**`, `app.js`, `AuraSimTests/**` |
| `animations` | `src/**`, `app.js`, `index.ios.js`, `patches/**` |
| `deeplinks` | `app.js`, `src/components/DeeplinkHandler.js`, `src/models/deeplink.js`, the Iterable, referral-link and reminder-push modules, and their tests |
| `native-ios` | `ios/**`, `scripts/**`, `Gemfile`, `.nvmrc` |
| `sim-testing`, `sim-testing-conventions` | `AuraSimTests/**` |

Read a scoped rule directly when the task needs it before you've opened a matching file (e.g. a release, a sim run).

The instruction files that load at startup must stay under 150k characters in total.

## Skills

Skills in `.claude/skills/` are discovered automatically and load only when invoked; don't `@`-import their `SKILL.md` here. Keep this list in sync whenever a skill folder is added, renamed, or removed.

- `figma-to-code`: build a screen from a Figma node (read-only on Figma).
- `release-ios`: cut an iOS release from `master` (version bump, archive, TestFlight, tag, GitHub release).
- `release-feature-branch-ios`: build the current branch to TestFlight for QA, with no tag or release.
- `screenshot-harvest`: capture and store per-screen screenshots in every design and theme variant.
- `sim-test`: autonomous simulator testing through the `AuraSimTests` harness.
- `sync-cask`: switch the dev/prod cask config, or seed a worktree's cask.
