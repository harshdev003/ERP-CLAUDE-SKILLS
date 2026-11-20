# Pull Request Process

## Related Rules

- `project-rules.md` for overall workflow gates
- `documentation.md` for PR description requirements

## PR Submission Workflow

Before marking a PR as ready for review, complete ALL of the following steps in order:

### 1. Code Completion
- ✅ All requested features implemented
- ✅ All acceptance criteria met
- ✅ Error handling in place
- ✅ Logging added for key operations

### 2. Code Quality Checks (BEFORE Committing)

**Run SwiftLint first:**
```bash
swiftlint lint --strict
```

Must show zero violations. If SwiftLint unavailable, manually verify:
- ✅ No trailing whitespace: `git diff --check`
- ✅ No print() statements (use Logger)
- ✅ File headers match required format
- ✅ Enum cases alphabetically sorted
- ✅ Switch cases match enum order

**Then validate build:**
- ✅ SwiftLint passes with zero violations
- ✅ Build succeeds for all targets
- ✅ No compiler warnings introduced
- ✅ Code follows project conventions

**If lint fails in CI:**
- DO NOT ignore - fix immediately
- Common issue: trailing whitespace on blank lines
- Check: `git show HEAD:path/to/file.swift | cat -A` to see invisible characters
- Fix and push another commit

### 3. Git Operations
- ✅ All changes staged and committed with clear messages
- ✅ Commits pushed to feature branch
- ✅ Branch synced with base branch (if needed)

### 4. PR Creation (as a DRAFT)

- ✅ **PR created with `gh pr create --draft`** — never ready on creation
- ✅ PR created with descriptive title
- ✅ PR description follows template (if exists)
- ✅ Linear issue linked in description
- ✅ Changes section clearly documents what was done

**Create it as a draft, rather than creating it ready and intending to hold it.**
`bugbot run` is asynchronous. A PR that is ready the moment it exists can be
merged before its review lands — and the fixes are then stranded on a branch
that is already in `main`, needing a second PR to re-land.

That is not hypothetical. AINative [#979](https://github.com/AuraHealth/AINative/pull/979)
merged with six open BugBot findings — one of them High, and the High one *was*
"PR opened without draft flag". Step 7 below already said to convert **from**
draft, but nothing anywhere said to create one, so the gate had nothing to gate.

A PR with unaddressed review findings is not ready for review. That is what the
word means.

### 5. BugBot Review (on demand, and again after each fix)

Run a BugBot pass on **every** PR (stacked sub-PRs included) before requesting human review, and address all findings first.

**BugBot (Cursor's review bot)** catches logic errors, routing bugs, state issues, and code-quality problems. Trigger it when the PR is implemented (lint + build + unit tests green) — as the last gate before human review / QA. To trigger, post one top-level `bugbot run` comment on the PR.

> **Frequency rule:** trigger BugBot when the PR is implemented, and **re-trigger it after any fix commit that addresses BugBot findings** — the point is for BugBot to verify the fix rather than for the author to assert it, and a fix can introduce a new problem the first pass never saw. Do **not** re-fire on pushes that do not address BugBot findings (rebases, description edits, unrelated follow-up work) — that spams the PR with redundant reviews.

> **Corollary:** a PR is only BugBot-clean when the **most recent** BugBot review, on the **current** head commit, reports no findings. A clean run on an older commit does not carry forward.

`eng:review-pr` (Claude adversarial review) is available on request — invoke it when you want a deeper multi-dimension fan-out review. It is not a required gate on every PR.

After triggering BugBot:

1. **WAIT for BugBot to complete its review** (typically 2-5 minutes, sometimes longer for complex PRs)
2. **CHECK for BugBot comments** on the PR
3. **ADDRESS ALL BugBot issues** before requesting human review — fix, reply, push, then **re-trigger BugBot on the fix commit** and wait for it to come back clean. Repeat until a run on the current head reports no findings; cap the cycle at ~5 rounds and hand back to the engineer rather than looping indefinitely.

#### How to Handle Cursor Bot Comments

**High Severity Issues:**
- MUST be fixed immediately
- Do not request human review until resolved
- Add commits addressing each issue
- Document the fix in a reply comment

**Medium Severity Issues:**
- Should be fixed before human review
- If fix is non-trivial, document reason for deferring
- Add to tech debt if deferring

**Low Severity/Suggestions:**
- Fix if straightforward
- Otherwise document decision in reply

#### Cursor Bot Comment Categories

Common issues cursor bot catches:

1. **Logic Errors** - Incorrect conditions, missing null checks, wrong comparisons
2. **Metadata/State Issues** - Lost context, incorrect merging, missing parameters
3. **Routing Problems** - Incorrect navigation, missing route handlers
4. **Code Quality** - Complexity violations, unused code, poor patterns
5. **Security** - Exposed secrets, unsafe operations, missing validation

### 6. Final Checks

After addressing cursor bot comments:

- ✅ All cursor bot issues resolved
- ✅ New commits pushed addressing feedback
- ✅ CI checks passing (lint, build, tests)
- ✅ PR description updated if scope changed

### 7. Ready for Human Review

Run `gh pr ready` to convert from draft — and only when:

- ✅ All above steps completed
- ✅ No outstanding cursor bot comments
- ✅ CI is green
- ✅ Self-review completed

## PR Description Template

```markdown
## Description
[Brief summary of what this PR does]

## Changes
[Detailed list of changes made]

## Testing
- [ ] SwiftLint passes
- [ ] Build succeeds
- [ ] Cursor bot issues addressed
- [ ] Manual testing completed

## Related
- Linear Issue: [ISSUE-ID](link)
```

## Example: Addressing Cursor Bot Issues

**Bad Response:**
```
// Ignoring cursor bot comment without explanation
```

**Good Response:**
```markdown
✅ Fixed in commit abc123

**Issue**: Metadata merge causing incorrect routing
**Root Cause**: feedId from metadata overriding path-based conversationId
**Fix**: Check conversationId presence first, prioritize over feedId
**Testing**: Verified existing conversations now route correctly
```

## When to Request Help

If a cursor bot issue is:
- Unclear or seems incorrect
- Requires architectural changes beyond PR scope
- Conflicts with product requirements

Then:
1. Reply to cursor bot comment explaining the concern
2. Tag the PR reviewer
3. Document the decision made

## Automation

Cloud agents should:
- Monitor for cursor bot comments automatically
- Address issues proactively
- Update PR description with fixes
- Only notify user when stuck or needing direction

## AI Agent PR Workflow — BugBot policy (MANDATORY)

**BugBot runs on demand, and again after each fix that addresses its findings — never automatically on every push.** Do not post `bugbot run` on your own initiative, and do not re-fire it after a rebase, a description edit, or unrelated follow-up work; that spams the PR with redundant reviews. Do re-fire it after a commit that addresses BugBot findings, so the fix is verified rather than assumed. The `bugbot-review` skill posts a single trigger per call — driving the fix / re-trigger / wait cycle, and stopping it, is the agent's job.

When the user asks for a BugBot pass (or you reach the ready-for-review gate on a PR):

1. Make sure the PR is otherwise ready — SwiftLint + build + unit tests green, fix commits pushed.
2. **Trigger BugBot**: post a top-level `bugbot run` comment on the PR.
3. **Wait for BugBot** to complete its review (~2-5 minutes)
   - Poll with: `gh api repos/{owner}/{repo}/pulls/{pr}/reviews` until the bot review appears
   - Also check: `gh api repos/{owner}/{repo}/pulls/{pr}/comments` for inline comments
4. **Read all BugBot comments** and categorize by severity
5. **Fix all issues** — high and medium severity must be fixed; low severity should be fixed if straightforward
6. **Reply to every BugBot comment** with a brief fix summary, referencing the commit SHA:
   - `gh api repos/{owner}/{repo}/pulls/{pr}/comments/{id}/replies -f body="Fixed in {sha}. {description}"`
7. **Push the fix commit**, verify CI passes, then **re-trigger BugBot** so it reviews the fix. Loop back to step 3 and repeat until a run on the current head is clean; cap at ~5 rounds and hand back to the engineer if findings persist.

**BugBot runs on every PR — sub-PRs included.** Run a BugBot pass on every PR before requesting human review, and re-run it after each fix commit that addresses its findings. The constraint is on *unrelated* pushes, not on scope: a rebase or a description edit is not a reason to re-run it, but a fix answering a finding always is. `eng:review-pr` is available on request for a deeper adversarial pass, but is not a required gate on every PR.
