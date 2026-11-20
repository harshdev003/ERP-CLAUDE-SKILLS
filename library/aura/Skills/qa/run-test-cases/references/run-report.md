# Run report shape

Read by `run-test-cases` in Step 6. This is the body of the Test Runs row.

The test of a good report: **someone who cannot open Linear, and was not in the room, still
knows what broke and how to see it for themselves.**

```markdown
## What ran

<Scope in one sentence.> Build <version> (<build>) on <environment>, driven by <driver>.
Accounts: <which account shape each group used>.

| | Count |
|---|---|
| Ran | n |
| Passed | n |
| Failed | n |
| Blocked | n |
| Not run | n |

## Failures

### <CASE-ID> — <case title>
**Step n.** Expected: <what the case says>. Saw: <what actually happened>.
<Screenshot.> <Log lines, if they say something the screenshot does not.>
<One line on blast radius: who hits this, how often, and whether there is a way round it.>

## Blocked

### <CASE-ID> — <case title>
Could not reach step 1 because <what failed upstream>. <The case this depends on.>

## Not run

| Case | Why |
|---|---|
| NOT-001 | `device` — needs real push |
| BKM-001 | gate not live: bookmarkChat is on control |
| SUB-009 | needs purchase permission, not granted for this run |

## What this pass did not cover

<The honest paragraph. Which features have only automatable coverage, which have none,
and what a reader should not conclude from a green result.>
```

## Rules

- **Failures first.** A reader who stops after the first screen should already know the
  worst of it.
- **One failure, one section.** Merging two failures into one paragraph loses one of them.
- **No Linear links.** The report stands alone; the bug card points back to it.
- **"Not run" always carries a reason**, and the reason is specific: which tag, which gate,
  which missing permission. "Skipped" is not a reason.
- **The last section is not optional.** A green report with no coverage statement invites
  exactly the wrong conclusion.
