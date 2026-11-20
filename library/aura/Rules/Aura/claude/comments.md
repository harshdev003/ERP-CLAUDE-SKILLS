---
description: Comment preservation and "why" comments for Aura
alwaysApply: true
---

# Comment Preservation & Context Comments

Existing comments are part of the codebase's institutional knowledge — they capture intent, constraints, and hard-won lessons that the code itself doesn't show.

## Related Rules

- `documentation.md` for JSDoc + file headers.
- `coding.md` for general code style.

## Comment Preservation (MANDATORY)

**Never delete, remove, or omit existing comments when editing code.** This is the single most important rule in this file.

- When modifying a function or block that has comments, **keep all surrounding comments intact** — even if they seem unrelated to your change.
- When moving code, **move its comments with it**.
- When refactoring, **carry forward all comments** to the new location. If a comment applies to code now split across multiple locations, duplicate the comment or add a cross-reference.
- When replacing a block, **preserve the original comments** and update them only if the new code changes the behavior they describe.
- **Only remove a comment** when the code it describes has been entirely deleted and the comment has no remaining relevance.
- **Update a comment** when you change the behavior it documents — stale comments are worse than no comments, but deleting without replacing is never acceptable.

### What Counts as a Comment

All of the following must be preserved:

- `//` inline comments
- `/* */` block comments
- `/** */` JSDoc blocks
- `// TODO:`, `// FIXME:`, `// HACK:`, `// NOTE:` annotations
- Comments inside callbacks, conditionals, and switch cases
- Commented-out code with an explanatory note (e.g., `// Disabled until backend ships v2 — see PROD-1234`)
- `// eslint-disable-next-line ...` comments — these are intentional and must stay (or the ESLint error returns)

## When to Add Comments

**Default: don't add a comment. Add one only when the code is not obvious on its own.**

High-quality code explains its **reasoning** ("why"), not its behavior ("what"). If the next developer reading the line can tell what's happening from the code alone — function names, variable names, control flow — a comment adds noise without adding signal.

### The bar

Before writing a comment, ask: **would a competent React Native engineer reading just this code understand WHY it's like this?**

- **Yes** → no comment (the code speaks for itself)
- **No, but the reason is in the file already** (e.g., earlier comment, JSDoc) → no comment
- **No, and the reason is genuinely non-obvious** (workaround, business rule, SDK quirk, performance trade-off) → write a comment that explains the reason

A test for "is this comment doing work?": delete it. If the code still tells the whole story, the comment was noise.

### Common over-commenting patterns to AVOID

- **Ticket-attribution boilerplate** on routine changes — `// ENGMOB-1234 — adds testID for sim test` is noise. The git blame / commit message already says this. Reserve ticket references for **workarounds** or **non-obvious decisions** that someone might revisit.
- **Restating what a self-explanatory prop does** — `// testID for sim tests` next to `testID="foo_bar"` adds nothing.
- **Re-introducing context the reader can see** — `// We use TouchableOpacity here` next to a `<TouchableOpacity>`.
- **Multi-line preambles on tiny changes** — a 6-line block comment for adding one prop is almost always a sign of over-commenting.

### Always Add a Comment For

1. **Business rules and domain constraints** — why a value is checked, why a threshold exists.

```js
// ✅ GOOD — explains the business rule
// IAP pricing is set per region in App Store Connect; we trust the
// productId localization rather than computing a price client-side.
const localizedPrice = product.localizedPrice
```

2. **Non-obvious control flow** — early returns, guards that would surprise a reader.

```js
// ✅ GOOD — explains why we bail
// Free trial eligibility is server-evaluated; if the receipt validation
// hasn't returned yet, treat the user as non-eligible to avoid showing
// "Start free trial" and then yanking it after the receipt check.
if (!receipt?.validated) return false
```

3. **Workarounds and known issues** — bugs in dependencies, OS quirks, temporary hacks.

```js
// ✅ GOOD — documents a workaround
// react-native-iap@12 throws an InvalidObject error if you pass a non-string
// productId on Android. Coerce before calling. (See AURA-1234.)
const id = String(productId)
```

4. **Performance decisions** — why a particular data structure or caching approach was chosen.

```js
// ✅ GOOD
// Indexing tracks by id once on store init avoids an O(N) scan every
// time the player queries for the next track during playback.
this.tracksById = keyBy(tracks, 'id')
```

5. **Integration / API contracts** — expected formats, required sequences, SDK quirks.

```js
// ✅ GOOD
// Iterable's setEmail must be called before any track event or the events
// silently drop with no error from the SDK.
await Iterable.setEmail(user.email)
```

6. **Edge cases and boundary conditions**.

```js
// ✅ GOOD
// Backend returns [] for "user has no journals" but null for "endpoint down".
// We treat both as empty so the empty-state UI renders either way.
const journals = response?.data ?? []
```

## What NOT to Comment

Don't restate what the code already says.

```js
// ❌ BAD
// Set loading to true
this.loading = true

// ❌ BAD
// Call the API
const result = await api.fetch()

// ❌ BAD
// Return the result
return result
```

## Style

- Use `//` for inline reasoning comments (one or two lines).
- Use `/** */` JSDoc for exported functions whose contract isn't obvious from the name and arg list (params, returns, side effects).
- Place comments on the line **above** the code they describe, not on the same line (except for short annotations like `// eslint-disable-next-line`).

## AI Agent Checklist

Before submitting a change, verify:

- [ ] No existing comments were deleted or lost
- [ ] Comments adjacent to modified code were updated if behavior changed
- [ ] Non-obvious logic has a "why" comment
- [ ] Workarounds reference an issue ID or external link
- [ ] `// eslint-disable-*` comments are still attached to the line they apply to
