# Coding style (ERP services)

Adapted from a production team's coding rules (`library/aura/Rules/BusinessService/claude/coding.md`).
The linter/formatter config in each repo wins on pure formatting.

## General
- Prefer `async/await`; no callback pyramids.
- **Guard clauses** over nesting: return early on invalid input.
- Use `Set`/`Map` for membership and lookups instead of repeated array scans.
- Keep destructured parameter keys in alphabetical order, like object literals.
- Build optional nested objects in one guarded block, not several spread-merging assignments.
- When an explicit `false` must override a default, check `typeof x === 'boolean'`, not `if (x)`.
- Functions whose return value nobody reads return nothing (`void`).
- Once a precondition is enforced with an early return, don't re-check it later.
- With 3+ logical arguments, take one object (`fn({a, b, c}, {context})`); keep the
  request context as the last parameter.
- Default a result holder to `undefined`, assign only on success — never default to
  `[]`/`{}` to hide whether work happened.

## Handlers / routes
- Handler names are CRUD verbs (`create`, `retrieve`, `list`, `update`, `remove`) or
  clear domain verbs (`post`, `approve`, `cancel`, `submit`, `reverse`). A new route
  takes the same form as its siblings.
- RESTful methods and status codes; structured error JSON (error-handling.md).
- Return dates as ISO 8601; money as `{amount: <minor units or decimal string>, currency}`.
- Non-trivial request parsing lives in its own module.

## Domain layer
- Validate every input; create specific errors, never generic failures.
- `try/catch` around I/O; translate low-level errors into domain errors.
- Name results `result` / `results`.

## Money and quantities
- Never use binary floating point for money, tax, rates or quantities that are
  summed — use integer minor units or a decimal library.
- One shared rounding function per concern (line, tax, document total), with the rule
  documented; don't round in multiple places.
- Store the currency and exchange rate with every monetary document.

## Data access
- Parameterised queries only (security.md B4).
- Tenant scoping is applied by the data layer, not repeated by hand in each query.
