# Designing a stack

How to decide what goes in each layer. Read this before running `gh stack init`.

Adapted from [`github/gh-stack`](https://github.com/github/gh-stack) (MIT). the ERP team-specific
additions are marked **ERP:**.

## Contents

- [Plan the layers before writing code](#plan-the-layers-before-writing-code)
- [Branch naming](#branch-naming)
- [Mapping Linear sub-issues onto layers](#mapping-linear-sub-issues-onto-layers)
- [Staging changes deliberately](#staging-changes-deliberately)
- [When to add a layer](#when-to-add-a-layer)
- [One stack, one story](#one-stack-one-story)

## Plan the layers before writing code

A stack is a dependency chain. If code in one layer depends on code in another, the dependency must
live in the same branch or a lower one. That constraint is much cheaper to satisfy by planning than
by restructuring later, because there is no non-interactive in-place reorder — fixing the order
means `unstack` and `init` again.

Decide the layers first, then write code into them:

```
(main) <- todo-app/models <- todo-app/api <- todo-app/frontend <- todo-app/integration
```

- `todo-app/models` — shared types and schema
- `todo-app/api` — routes that use the models
- `todo-app/frontend` — components that call the routes
- `todo-app/integration` — tests exercising the whole feature

This is illustrative. Infer the stack topic and layer names from the actual task; do not reuse
`todo-app` or these layer names literally.

The failure mode to avoid is writing everything on one branch and trying to split it afterwards.
If a task is large enough to warrant a stack, create the stack at the start.

## Branch naming

Upstream prefers a shared topic prefix plus the layer's concern — `<topic>/<concern>`, for example
`billing/schema`, `billing/api`, `billing/ui` — and states that **user and repository branch naming
conventions take precedence**.

**ERP:** ours do. Use `<firstname>/<ticket-id>-<slug>` per `/CLAUDE.md` → "Naming
conventions" → "Branch names", with the slug kebab-cased from the issue title:

```
harsh/erp-124-user-preference-model
harsh/erp-125-user-preferences-endpoint
harsh/erp-126-preferences-tab
```

When there is no ticket (ad-hoc Mode B), fall back to `<firstname>/<topic>-<concern>` — this keeps
the owner prefix every other branch in our repos carries, while still clustering the stack.

Names are used exactly as given — nothing is prepended or transformed, and slashes are kept, so
`gh stack add harsh/erp-124-user-preference-model` creates that branch literally.

If you pass `-m` without a branch name, the name is generated from the commit message in
date-and-slug form (for example `03-24-add_api_routes`). **Never rely on this** — it produces a
branch that violates our convention. Always name the branch yourself.

## Mapping Linear sub-issues onto layers

**ERP:** the default mapping is **one sub-issue per layer**, but the mapping is a judgement
call, not a mechanical translation.

**Order by dependency, not by Linear order.** Linear returns sub-issues in creation order, which
reflects how someone thought of them, not how they must land. Derive the real order from the code:
schema and shared types at the bottom, then service/API code consuming them, then UI, then
integration tests. A layer must never reference code introduced above it.

**Merge sub-issues into one layer** when they are the same concern split for tracking reasons ("add
the field" + "add the migration" are one layer). Note which sub-issues a layer covers in the PR body
so the tracking stays honest.

**Split one sub-issue into two layers** when it spans a dependency boundary — a card that says
"add preferences end to end" is a model layer plus an API layer, and reviewing it as one PR is the
thing a stack exists to avoid.

**Leave a sub-issue out** when it is blocked, or so vague you'd be guessing at the interface. A
wrong layer near the bottom is the expensive mistake — everything above it has to be replayed, and
there is no non-interactive reorder. Say which sub-issue you left out and why.

**Stop if the sub-issues span repos.** A stack is strictly linear inside one repository. Cross-repo
fan-out is `linear-task` multi-agent mode, not a stack.

## Staging changes deliberately

Use `git add` and `git commit` directly rather than the `add -Am` shortcut. The point is control
over which changes land in which branch. With several modified files in the working tree, stage the
subset that belongs to the current layer, commit it, then create the next branch and stage the rest
there:

```bash
git add internal/models/user.go internal/models/session.go
git commit -m "Add user and session models [ERP-124]"

gh stack add harsh/erp-125-user-api
git add internal/api/routes.go internal/api/handlers.go
git commit -m "Add user API routes [ERP-125]"
```

Multiple commits per branch are fine. What matters is that every commit in a branch serves the same
concern, and that a change belonging to a different concern goes in a different branch.

Note that `gh stack add <branch>` without `-Am` does not touch the working tree, so uncommitted
changes carry over to the new branch. Commit or stash first if you want the new layer to start clean.

**ERP:** commit subjects follow `/CLAUDE.md` → "Commit messages" — short imperative subject
plus the ticket in brackets. In a stack the bracketed id is the **sub-issue's**, not the parent's,
because that layer is what completes it. Heredocs are fine for short ASCII commit messages, but
write the message to a file and use `git commit -F` the moment it contains an em-dash, arrow, or
curly quote — `git commit -m "$(cat <<'EOF' … )"` mis-parses on non-ASCII glyphs.

## When to add a layer

Add a branch when you start a **different concern that depends on what you have built so far**.
Signals:

- Moving from backend to frontend, or from core logic to tests or documentation
- The next changes have a different reviewer audience
- The current branch's diff is already large enough to review on its own

A layer that cannot be described in one sentence is usually two layers.

## One stack, one story

A stack should read as a coherent progression: a reviewer walks the PRs bottom to top and sees the
feature being built.

**Use a single stack** when every branch serves the same feature or project, even if the layers span
different concerns.

**Start a separate stack** for unrelated work — a different feature, an unrelated bug fix, an
independent refactor. Do not mix efforts into one stack just because you happened to work on both.
Use `gh stack init` for the new effort, or `gh stack checkout <target>` to move between existing
stacks.

A trivial incidental fix can ride along in the current stack. Once it grows into its own project, it
deserves its own stack.

**ERP:** a Linear parent issue is usually a good stack boundary — one parent, one stack. If
two parents' sub-issues genuinely interleave in dependency order, that is a signal the cards are
carved wrong; raise it rather than building a stack that spans both.
