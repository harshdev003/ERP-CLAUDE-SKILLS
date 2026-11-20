---
description: Comment preservation and context comment standards for AI-assisted development
globs:
alwaysApply: true
---

# Comment Preservation & Context Comments

Existing comments are a critical part of the codebase's institutional knowledge. They capture intent, constraints, and hard-won lessons that are not visible in the code itself.

## Related Rules

- `documentation.md` for file headers, DocC, and MARK comments.
- `coding.md` for general Swift style and file structure.
- `service-patterns.md` for service-level context documentation.

## Comment Preservation (MANDATORY)

**NEVER delete, remove, or omit existing comments when editing code.** This is the single most important rule in this file.

### Rules

- When modifying a function or block that has comments, **keep all surrounding comments intact** — even if they seem unrelated to your change.
- When moving code, **move its comments with it**.
- When refactoring, **carry forward all comments** to the new location or structure. If a comment applies to code that was split across multiple locations, duplicate the comment or add a cross-reference.
- When replacing a block of code, **preserve the original comments** and update them only if the new code changes the behavior they describe.
- **Only remove a comment** when the code it describes has been entirely deleted and the comment has no remaining relevance.
- **Update a comment** when you change the behavior it documents — stale comments are worse than no comments, but deleting without replacing is never acceptable.

### What Counts as a Comment

All of the following must be preserved:

- `//` inline comments
- `///` DocC documentation comments
- `/* */` block comments
- `// MARK:` section dividers
- `// TODO:`, `// FIXME:`, `// HACK:`, `// NOTE:` annotations
- Comments inside closures, guards, and switch cases
- Commented-out code with an explanatory note (e.g., `// Disabled until backend supports v2 — see ENGMOB-1234`)

## When to Add Comments (MANDATORY)

High-quality code explains its **reasoning**, not just its behavior. Add comments that answer **"why"**, not **"what"**.

### Always Add a Comment For

1. **Business rules and domain constraints** — why a value is checked, why a threshold exists, why an order matters.

```swift
// ✅ GOOD — explains the business rule
// Users under 13 require parental consent per COPPA; skip onboarding analytics
// until consent is confirmed to avoid collecting data prematurely.
guard user.age >= 13 || user.hasParentalConsent else { return }
```

2. **Non-obvious control flow** — early returns, guard conditions, fallthrough cases that would surprise a reader.

```swift
// ✅ GOOD — explains why we bail out
// Token refresh is handled by AuthService; if we reach here without a token
// the session was invalidated server-side and we must force re-login.
guard let token = authStore.currentToken else {
    await forceLogout()
    return
}
```

3. **Workarounds and known issues** — bugs in dependencies, OS quirks, temporary hacks.

```swift
// ✅ GOOD — documents a workaround
// iOS 17.2 has a SwiftUI bug where .task modifiers on sheets fire twice.
// Debounce with a flag until Apple fixes this (FB13456789).
guard !hasAlreadyLoaded else { return }
```

4. **Performance decisions** — why a particular data structure, algorithm, or caching strategy was chosen.

```swift
// ✅ GOOD — explains the performance trade-off
// Using Dictionary for O(1) lookup instead of Array.filter — feed items can
// exceed 500 entries and this runs on every scroll frame callback.
private var feedItemIndex: [String: FeedItem] = [:]
```

5. **Architectural decisions and constraints** — why a class is structured a certain way, why a dependency exists.

```swift
// ✅ GOOD — explains an architectural constraint
// This must remain a class (not struct) because ObservableObject conformance
// requires reference semantics for @Published property observation.
class JournalViewModel: BaseViewModel { ... }
```

6. **Integration and API contracts** — expected formats, required sequences, SDK limitations.

```swift
// ✅ GOOD — documents an API contract
// The Iterable SDK requires setEmail before any event tracking calls.
// Calling track() before setEmail silently drops events with no error.
await iterableService.setEmail(user.email)
```

7. **Edge cases and boundary conditions** — what happens at limits, empty states, nil cases.

```swift
// ✅ GOOD — documents edge case handling
// Backend returns an empty array (not nil) when the user has no journals,
// but returns nil when the endpoint is unavailable. We treat both as empty.
let journals = try await apiService.fetchJournals() ?? []
```

## What NOT to Comment

Do not add comments that merely narrate what the code does — the code already says that.

```swift
// ❌ BAD — restates the code
// Set loading state to true
isLoading = true

// ❌ BAD — restates the code
// Call the API to fetch journals
let journals = try await apiService.fetchJournals()

// ❌ BAD — restates the code
// Return the result
return result

// ❌ BAD — restates the code
// Initialize the view model
let viewModel = JournalViewModel(apiService: apiService)
```

## No Ticket / PR References in Code Comments (MANDATORY)

**Never put Linear ticket IDs, GitHub PR numbers, or internal task identifiers in source-code comments.** Ticket numbers belong in the commit message and the PR description — they rot in code (tickets close, get renumbered, merged into epics) and add noise without explaining the _why_.

```swift
// ❌ BAD — ticket refs in code comments
/// Aurie WebView paywall v2 (ENG-649 re-run of ENG-443): ...
//  The v2 rerun (ENG-649) intentionally drops two v1 gates ...
// LimitedFree exclusion (PROD-2055 / ENG-589) — exposure gated ...

// ✅ GOOD — explain the constraint or behavior; let git history hold the ticket trail
/// Aurie WebView paywall: "a" = treatment, "b"/nil = control.
// LimitedFree treatment users are excluded — exposure is gated on the
// LimitedFree assignment to suppress the WebView paywall exposure event
// for users who'd be blocked anyway.
```

Also do not add migration prose in comments — phrases like `"v1 vs v2"`, `"re-run of"`, `"previously this required X"`, `"the old gate did Y"`. That story belongs in the commit message and PR description, not in long-lived source comments. State the _current_ behavior cleanly; readers can ask `git blame` for the rest.

Applies to: `.swift` files, sim-test `knowledge/` markdown, all in-repo source. Does not apply to: commit messages, PR descriptions, top-level `CHANGELOG.md` entries.

## Comment Style

- Use `//` for inline reasoning comments (one or two lines).
- Use `///` DocC for public API documentation (parameters, returns, throws).
- Use `// MARK: -` for section organization (see `coding.md`).
- Keep comments up to date — when you change logic, update or extend the comment above it.
- Place comments on the line **above** the code they describe, not on the same line (except for very short annotations like `// swiftlint:disable`).

## Ticket References

**Do NOT put internal Linear / ENG / PROD / AUR ticket IDs in code comments, file headers, or sim-test knowledge files.** The ticket lives in the commit message and PR description — that's where it remains traceable. Embedding it in code is noise that rots: the ticket gets archived, the link breaks, the context drifts, and the reader still has to ask "what did that ticket actually decide?". Write the decision in the comment itself instead.

```swift
// ❌ BAD — ticket ID with no carried-over context
/// Paywall CTA position (ENG-650): controls the Subscribe CTA placement.
static let paywallCTAPosition = "paywallCTAPosition"

// ✅ GOOD — the rationale lives in the comment, not behind a ticket link
/// Controls the Subscribe CTA placement on the shared final pricing screen.
/// "a" = sticky bottom bar; "b" or nil = control (CTA scrolls); "c" = dual
/// CTA (one after plan cards, one at end).
static let paywallCTAPosition = "paywallCTAPosition"
```

**Exception**: external traceable bug IDs that point to a real issue tracker outside this codebase are fine — Apple Feedback (`FB13456789`), open-source SDK issues (`#1234` on a vendor repo), CVE numbers. These reference systems the author of this code does not own and where the ID remains the canonical lookup forever.

**Preservation override**: do not strip existing ticket refs from comments you are otherwise leaving alone — that violates the "Comment Preservation" rule at the top of this file. The "no internal tickets" rule applies to comments you author or substantively rewrite, not to drive-by cleanups of unrelated code.

## AI Agent Checklist

When making any code change, verify:

- [ ] No existing comments were deleted or lost
- [ ] Comments adjacent to modified code were updated if the behavior changed
- [ ] Non-obvious logic has a "why" comment
- [ ] Workarounds reference the issue or radar number (external trackers only — see "Ticket References")
- [ ] No internal Linear / ENG / PROD / AUR ticket IDs in comments you authored
- [ ] Business rules are documented near the guard/condition that enforces them
- [ ] No new Linear/Jira ticket IDs (ENG-XXX, PROD-XXXX, etc.) or PR numbers appear in code comments — those belong in the commit message and PR description only
- [ ] No "v1 vs v2", "re-run", "previously this did X" migration prose in comments — describe the current behavior, let `git blame` carry the history
