---
description: Top-level orchestration rule for Aura RN — quality gates and rule routing
alwaysApply: true
---

# Aura Project Rules

This file is the top-level orchestration rule. Detailed standards live in focused rule files under `.claude/rules/`. If you also maintain a `.cursor/rules/` mirror, keep the two in sync — but `.claude/rules/` is the source of truth.

## Rule Hierarchy

Treat this file as the source for workflow gates and rule routing. Use specialized files as the source of truth for implementation details:

- `architecture.md`
- `coding.md`
- `comments.md`
- `naming.md`
- `documentation.md`
- `error-handling.md`
- `error-handling-bugsnag.md`
- `logging.md`
- `performance.md`
- `security.md`
- `accessibility.md`
- `pr-process.md`
- `ui-design-system.md`
- `animations.md`
- `figma.md`
- `state-management-reflux.md`
- `navigation.md`
- `deeplinks.md`
- `localization.md`
- `native-ios.md`
- `native-android.md`

Do not duplicate large sections across multiple rule files.

## Mandatory Validation Workflow After Code Changes

**Run these checks BEFORE committing.**

### 1. ESLint (required)

```bash
npm run lint
```

Must show zero errors. The repo uses `airbnb-base` + `@react-native` + `prettier`. Key rules that bite:

- `no-console: error` — never commit `console.log` (use `bugsnag.report` or `Trackings`).
- `react/prop-types: error` — every component prop needs a `propTypes` declaration.
- `consistent-return: error` — all paths in a function must return or none.
- `no-mixed-operators`, `no-confusing-arrow`, `no-plusplus` — use parentheses, prefer `+= 1`.

If lint fails, run `npm run lint:fix` first, then fix the rest by hand.

### 2. Tests (required for changed code paths)

```bash
npm test                  # all
npm run test:unit         # only /unit/
```

If you change a store, helper in `src/logic/`, or `src/services/`, run the relevant suite.

### 3. Build sanity (required if you touched native or bundling)

For iOS: open `ios/Aura.xcworkspace` and build the `Aura` scheme on a simulator. For Android: `cd android && ./gradlew assembleDebug`.

## Quality Gates

- **ESLint must pass with zero errors** — no exceptions.
- **No new `console.log`** — use `bugsnag.report` for errors, `Trackings(eventName, data)` for analytics.
- **No hardcoded styles** — use the theme system (`ui-design-system.md`).
- **Imports of `bugsnag` must follow** the `error-handling-bugsnag.md` pattern.
- **All new screens** are wired into the `navigateTo` switch in `Dashboard.js` and dispatched via `Actions.navigateTo('viewName', null, props)` (see `navigation.md`).
- **All deep links** go through `DeeplinkHandler`: a parser in `src/models/deeplink.js`, a `case` in its router, and tests for both. No link handling in `app.js`, stores or screens (see `deeplinks.md`).

## Scope Discipline

- For feature or bugfix work, avoid broad cleanup not required by the task.
- Keep changes aligned to one intent and one reviewable scope.
- If a structural refactor is needed, propose a dedicated follow-up.
- Do **not** reformat or "tidy" unrelated files in the same PR — it makes review impossible and risks regressions in screens you didn't actually test.

## PR Submission Workflow

See `pr-process.md` for the full workflow. Critical gates:

1. Lint passes
2. Relevant tests pass
3. Manual smoke-test on at least one platform (iOS or Android) for any UI change
4. PR has a descriptive title and links the Linear issue
5. Wait for the automated review (cursor-bot, or the in-repo `code-reviewer` agent) and address ALL comments before requesting human review

## Rule Maintenance

- When a task surfaces reusable guidance, ask the user whether to persist it in rules.
- Don't silently change rules for subjective preferences — confirm first.
- Adding/renaming/removing a rule file requires updating `.claude/rules/` and the index in `CLAUDE.md`. If `.cursor/rules/` is being maintained as a mirror, update it too.
