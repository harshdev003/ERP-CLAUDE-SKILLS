# Prompt draft template

The shape of a first-draft `{configKey}.txt`. At run time the prompter reads the `.txt` and
appends its context instructions after it (`doBuildMessages` joins the prompt and the
`doBuildContextInstructions` output), usually under an `## Input Data` heading. The draft
shows both halves, so a reviewer sees exactly what the model will receive.

## Rules

- **Instruction sections are written out in full** from the plan: role, task, guidelines per
  output field, content and safety rules, output format. Keep the base prompt's section names
  where the plan extends one, so a diff against the base reads cleanly.
- **Context sections keep their real headings and use `[placeholder]` bodies.** Copy each
  heading exactly as its builder emits it, including the bracketed window
  (`### Past Conversation [last 10 conversations]`) with the plan's numbers filled in. Put
  one line under each placeholder saying what it will contain.
- **New builders** get a heading in the same style (`### Title [scope]`) and a
  `<!-- new builder: {methodName} -->` note so the implementer knows to write it.
- **Order** the context sections as the prompter's `doBuildContextInstructions` will join them.
- **The output format** section matches the plan's schema: field names, enums, min/max counts,
  and what an empty result looks like.

## Skeleton

```markdown
## Role

You are Aurie.
{Who Aurie is in this prompt and what this prompt is for.}

## Task

{What to produce, from which inputs, how many items, and when to produce none.}

## Guidelines

### {Output field or item type}
- {Rule}
- {Examples, tone only}

### Avoiding repetition
{How to use the previous outputs in the input data.}

### Safety
{Which topics may be resurfaced, how much detail a visible field may carry, what never becomes
an output.}

## Output Format

{Field-by-field description matching the schema, plus one example and the empty-result form.}

## Input Data

### User Information

[placeholder] — name, profile, streaks, current local date and time.

### Past Conversation [last {N} conversations]

[placeholder] — summaries of conversations since the last run.

### Relevant Running Memories

[placeholder] — up to {cap} active running memories, newest first, each with its recall question.

### {Previous outputs heading} [last {N}]
<!-- new builder: {methodName} -->

[placeholder] — the user's previous outputs, used to avoid repeats.
```
