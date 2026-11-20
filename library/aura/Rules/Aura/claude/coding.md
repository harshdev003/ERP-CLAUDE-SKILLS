---
description: JavaScript / React Native code style for Aura
globs: src/**/*.{js,jsx}
alwaysApply: false
---

# Coding Style

## Related Rules

- `comments.md` for comment preservation and "why" comments.
- `naming.md` for naming conventions.
- `ui-design-system.md` for theme + style usage.
- `error-handling-bugsnag.md` for the Bugsnag import + report pattern.

## Formatting (enforced by ESLint + Prettier)

- 2-space indentation, single quotes, no semicolons (per repo Prettier config).
- Trailing commas in multi-line collections.
- Keep lines reasonable; `max-len` is `off` in ESLint, but reviewers will ask you to wrap > ~120 chars.

## Hard Rules from `.eslintrc.json`

These are **errors** in this repo — your PR will fail CI if you break them:

- `no-console: error` — never commit `console.log`. Use `bugsnag.report` or `Trackings`. (Existing `console.log` calls behind `if (__DEV__)` and `// eslint-disable-next-line no-console` are tolerated; new code should follow the same pattern only when truly needed.)
- `react/prop-types: error` — every component prop is declared in `propTypes`.
- `consistent-return: error` — a function either always returns a value or never does.
- `no-mixed-operators: error` — wrap mixed `&&`/`||` and arithmetic groups in parens.
- `no-confusing-arrow: error` (with `allowParens: true`).
- `no-plusplus: error` — use `i += 1`, not `i++`.
- `no-sequences: error`.
- `quotes: ['error', 'single']` — single quotes, template literals allowed.
- `no-restricted-globals: Translator` — never reference the global `Translator`; import from `src/stores/Translator`.
- `lines-around-comment: error` — leave a blank line before block comments. ESLint flags a comment glued to the line above it.

## React Components

- Functional components with hooks for new code. The codebase has many class components (especially in `src/screens/`) — leave them alone unless your task involves rewriting one.
- Always declare `propTypes`:

```js
import PropTypes from 'prop-types'

const Foo = ({ title, onPress }) => (
  <TouchableOpacity onPress={onPress}>
    <Text>{title}</Text>
  </TouchableOpacity>
)

Foo.propTypes = {
  title: PropTypes.string.isRequired,
  onPress: PropTypes.func.isRequired,
}

export default Foo
```

- Default exports are the convention here. Don't add named exports unless multiple things are exported.

## Lists — Never Render an Item Inline in the Return

A `.map()` in a component's return must call a **named renderer**, not carry the item's
JSX inline. Inline maps bury the item's markup inside an already-long return, make the
item impossible to read or reason about on its own, and are what every list in this
codebase already avoids — see `{list.map(this.renderItem)}` in `TodayRow`,
`RecHomeFilter`, `ConversationTrackRecs`, `OnboardingGender` and many more.

```js
// ✅ GOOD
const AurieOnboardingDidYouKnow = ({ progress }) => {
  // One paragraph of the card's body.
  const renderDidYouKnowParagraph = (key, index) => {
    if (!key) {
      return null
    }

    const paragraph = Translator.t(key)

    return (
      <Text
        accessibilityLabel={paragraph}
        key={`${key}-${index}`}
        maxFontSizeMultiplier={1.4}
        style={styles.paragraphStyle}>
        {paragraph}
      </Text>
    )
  }

  return <View>{AURIE_DID_YOU_KNOW_KEYS?.map(renderDidYouKnowParagraph)}</View>
}

// ❌ BAD — the item's markup inlined in the return
return (
  <View>
    {AURIE_DID_YOU_KNOW_KEYS.map((key) => {
      const paragraph = Translator.t(key)

      return <Text style={styles.paragraphStyle}>{paragraph}</Text>
    })}
  </View>
)
```

Five rules, all of them load-bearing:

1. **The renderer goes inside the component**, not at module scope — a function component's
   renderer is a `const` in its body, a class component's is a class method called as
   `this.renderItem`. Keeping it inside is what lets it reach props and state without
   threading them through, and keeps one consistent place to look for it.
2. **Take `(item, index)`** and build the React key from both:
   ``key={`${item.id}-${index}`}``. The index alone re-keys every row when the list
   reorders; the id alone breaks if the backend ever repeats one.
3. **Optional-chain the map** — `list?.map(renderItem)`. These lists come from props and
   API responses, and `undefined.map` is a render crash.
4. **Guard inside the renderer** — `if (!item) { return null }` before touching any field,
   so one null entry drops a row instead of taking the screen down.
5. **Comment it with `//`, not a `/** */` block**, when the renderer is the first thing in
   a function body. This repo sets `lines-around-comment` to error, and it rejects a block
   comment at the start of a block. Class methods are fine with JSDoc.

Only extract renderers that produce **JSX**. A `.map()` that transforms data — building an
options array, translating a list of keys to strings — is not a list render and stays where
it is.

## Imports

- Sort: built-ins → third-party → absolute project → relative. ESLint `import` plugin enforces ordering issues.
- Use relative paths (`../../logic/Trackings`) — there is no path alias configured.
- `import { bugsnag } from '<path>/logic/Trackings'` is the **only** correct way to use Bugsnag. See `error-handling-bugsnag.md`.

## Async

- Prefer `async`/`await` over chained `.then()` for new code.
- Always wrap `await` in `try/catch` and report errors via `bugsnag.report` — see `error-handling.md`.
- Reflux store methods that perform async work return nothing meaningful; they `trigger(...)` on completion.

## Avoid

- `var` — use `const` by default, `let` only when reassignment is required.
- Force-coercions (`!!x`, `+x`) when intent is non-obvious — use `Boolean(x)`, `Number(x)`.
- Mutating `seamless-immutable` collections — use `.set`, `.merge`, `.asMutable()` if needed.
- Creating new files in `reuseableUI/` for new UI — use `src/components/` instead.
- Adding new top-level files at the repo root (use `src/`).
