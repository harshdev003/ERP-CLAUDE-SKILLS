# The `{{metadata.contextInstructions}}` scaffold

## Two template shapes

Braintrust templates carry the context in one of two shapes, and the audit handles both.
`scaffold_shape()` in `scripts/audit.py` reports which (`prodScaffold` / `devScaffold` in
`--format json`; a prod↔dev mismatch shows as `scaffold` under `P↔D-settings`).

| Shape | Messages | Body |
|---|---|---|
| **delimited** — Coach, Quick Processing, QP Sentinel, QP Done, Done Prompt and Guardrails Handler `[Production]` / `[Development]` since 2026-10-20, matching IS 1.40's order | system body · system `---` · user `---` · system `{{metadata.contextInstructions}}` · system `## Conversation\n{{input}}` | `messages[0]` as is — nothing to strip |
| **appended** — old eval and experiment copies, and any template not yet migrated | system `body\n\n---\n\n{{metadata.contextInstructions}}` · system `## Conversation\n{{input}}` | `messages[0]` with the trailing scaffold stripped (below) |

In both shapes the comparable body is `messages[0]`; the remaining messages are scaffold.
A *delimited* body has no trailing tag, so the strip regex is a no-op on it.

## The appended shape

Appended-shape bodies end with a template scaffold:

```
… last line of the prompt …


{{metadata.contextInstructions}}
```

…or with an explicit horizontal-rule separator:

```
… last line of the prompt …


---


{{metadata.contextInstructions}}
```

This scaffold is **not part of the prompt content**. It's a Braintrust mustache placeholder that gets filled in at replay/eval time by the contents of `metadata.contextInstructions` (the dynamic per-call context — user info, recent memories, conversation summary, etc.) that IntelligenceService passes alongside the static prompt.

## Why the IS repo doesn't have it

The repo `.txt` file stores only `{prompt}`; the context is added at call time. Intelligence
Service 1.40 on sends it as its own developer message after a developer `---` + user `---`
cache-delimiter pair (the delimited shape mirrors that); older versions concatenated it onto
the system prompt (`${prompt}\n\n${contextInstructions || ''}`), which the appended shape mirrors.

## The regex

```python
import re

STRIP = re.compile(
    r"\s*"                                      # any whitespace
    r"(?:---\s*)?"                              # optional `---` separator + trailing space
    r"\{\{\s*metadata\.contextInstructions\s*\}\}"  # the mustache tag
    r"\s*$",                                    # trailing whitespace
    re.S,
)

stripped = STRIP.sub("", bt_body)
```

Both Braintrust [Production] and Braintrust [Development] should have the scaffold stripped before comparing against the repo's `.txt`.

## Pitfall: stripping only one side

If you strip the scaffold from BT Production but NOT from BT Development, comparing them shows a phantom 33–39 char drift on every Dev-vs-Prod-or-Repo body comparison. Counts to watch for:

- `\n\n{{metadata.contextInstructions}}` = 33 chars
- `\n\n---\n\n{{metadata.contextInstructions}}` = 39 chars (with `---` separator)

If you see a body drift of exactly 33, 35, 37, or 39 chars and the drift is only on the dev side, your strip is asymmetric — fix the comparison, not the prompt.

## When NOT to strip

If you're trying to release a *new* Braintrust prompt body from a Dev source, the scaffold needs to stay (you want the Production prompt to keep the template marker so contextInstructions interpolation continues to work). `eng:update-intelligence-prompt` handles this: it strips the scaffold when writing into the repo `.txt`, and promotes dev's whole `prompt` object (every message, in whichever shape) when POSTing a new BT Production version — never re-appending the old suffix to a delimited body. The audit skill never writes anything, so it only ever strips.

## History

This skill's strip regex was hardened over a 2026-06-04 prompt-drift audit. Initial version stripped Production but not Development, producing false drift on every prompter (the audit reported `prod↔dev body` ✗ on every row). Fixed by always stripping both sides — and the comparison surface dropped from ~20 false drifts to 4 real ones.
