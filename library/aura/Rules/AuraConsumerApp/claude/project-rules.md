---
description: Project-level orchestration rule for quality gates and rule hierarchy
alwaysApply: true
---

# AuraConsumerApp Project Rules

This file is the top-level orchestration rule. Detailed standards live in focused rule files under `.claude/rules/`.

## Rule Hierarchy

- Treat this file as the source for workflow gates and rule routing.
- Use specialized files as the source of truth for implementation details:
  - `accessibility.md`
  - `architecture.md`
  - `coding.md`
  - `comments.md`
  - `concurrency-actors.md`
  - `config.md`
  - `data-flow.md`
  - `documentation.md`
  - `error-handling.md`
  - `logging.md`
  - `naming.md`
  - `performance.md`
  - `pr-process.md`
  - `repo-specific.md`
  - `security.md`
  - `security-review.md`
  - `service-patterns.md`
  - `sim-testing.md`
  - `sim-testing-conventions.md`
  - `testing.md`
- Do not duplicate large sections across multiple rule files.

## Mandatory Validation Workflow After Code Changes

**CRITICAL: Run these checks BEFORE committing code.**

### 1. SwiftLint (Required)

```bash
swiftlint lint --strict
```

**Must have zero violations.** The `--strict` flag treats warnings as errors, matching CI behavior.

**Common violations to check manually if SwiftLint unavailable:**
- ✅ No trailing whitespace (including on empty lines)
- ✅ No print() statements (use Logger instead)
- ✅ File headers match required format
- ✅ Line length under 120 characters
- ✅ No force unwrapping (!)
- ✅ Proper alphabetical ordering of enum cases and switch statements

**If SwiftLint cannot run locally:**
1. Manually verify the common violations above
2. Check file headers match pattern:
   ```
   //
   //  FileName
   //  Brief description
   //
   //  Created by Author on MM/DD/YYYY.
   //  Copyright © YYYY Aura Health, Inc. All rights reserved.
   //
   ```
3. Run: `git diff --check` to find trailing whitespace
4. Rely on CI to catch other issues, then fix immediately

### 2. Build Validation

```bash
make proj
xcodebuild -project AuraConsumerApp.xcodeproj -scheme AuraConsumerApp \
    -destination 'generic/platform=iOS Simulator' \
    build -quiet -allowProvisioningUpdates -destination-timeout 10
```

## Swift-Focused Quality Gates

- **SwiftLint must pass with zero violations** - No exceptions, all warnings are errors in CI.
- Build must succeed for `AuraConsumerApp` before considering work done.
- Follow MVVM + DI + Coordinator standards from the specialized rules.
- Use `Logger` (not `print`) and avoid logging sensitive data.
- Prefer concise, reviewable changes without unrelated refactors.

## Scope Discipline

- For feature or bugfix work, avoid broad cleanup not required by the task.
- Keep changes aligned to one intent and one reviewable scope.
- If a structural refactor is needed, propose a dedicated follow-up.

## PR Submission Workflow

**See `pr-process.md` for complete PR workflow details.**

Critical PR gates:

1. **Code Quality** - SwiftLint passes, build succeeds, no warnings
2. **Git Operations** - All changes committed and pushed
3. **PR Created** - Descriptive title, complete description, Linear issue linked
4. **BugBot Review (on demand, and again after each fix)** - Trigger BugBot on every PR when otherwise ready; address all findings before requesting human review, then re-trigger it on the fix commit so BugBot verifies the fix. Not re-fired on pushes that do not address its findings. `eng:review-pr` is available on request. See `pr-process.md`.
5. **CI Green** - All checks passing
6. **Ready for Review** - Only then mark PR as ready

**When BugBot runs, address every finding.** BugBot catches logic errors, routing bugs, state issues, and code quality problems. Run it when the PR is implemented, and **again after each fix commit that addresses its findings** — a fix is not verified until BugBot has seen it, and a fix can introduce a problem the first pass never saw. Do not re-fire on pushes unrelated to its findings; that spams the PR with redundant reviews.

## Rule Maintenance Workflow

- When a task surfaces reusable guidance, ask the user at task completion (or when requested) whether they want to persist it in rules.
- Do not silently change rules for subjective preferences; confirm with the user first unless they explicitly requested rule updates.
- If `.claude/rules/` files are added/renamed/removed, update `CLAUDE.md` references in the same change.
