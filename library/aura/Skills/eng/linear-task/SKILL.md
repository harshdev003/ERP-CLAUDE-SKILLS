---
name: linear-task
description: >
  End-to-end driver for a Linear engineering issue: load the card, decide single-agent vs
  multi-sub-issue mode, then either drive one card to merge — right-repo gate, plan,
  implement, subagent review, commit and PR, wait for BugBot and human review, then close out
  — or fan out one implementation-agent per sub-issue, parallel across repos, delegating
  same-repo chains to `eng:stack-prs`. Trigger on "work on ENG-123", "work on this linear
  card", "drive ENGBACK-1097 end to end", "ship this card", "take this card to merge", "pick
  up this Linear ticket", "implement this Linear issue", "start on this task", or a pasted
  linear.app URL with an explicit request to work on it. Do NOT use for reviewing an existing
  PR (`eng:review-pr`), triggering BugBot alone (`eng:bugbot-review`), driving a PR stack
  (`eng:stack-prs`), or a bare Linear URL with no request to work on it. Also triggers on
  "let's do AUR-456". For a PR stack — "create stacked PRs", "stack these PRs", "rebase the
  stack" — use `eng:stack-prs`.

metadata:
  version: "3.2.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Linear                   # Linear MCP / GraphQL access
    - Tools/GitHub                   # gh CLI auth + PR conventions
    - Compliance/Security/Code Review Checklist  # security/privacy rules the plan and PR must satisfy
---

# Linear Task Workflow

End-to-end workflow for pulling a Linear issue and shipping it. Two modes:

- **Single-agent mode** — one card, opinionated lifecycle from load → merge → Linear close-out. Default for any card without sub-issues.
- **Multi-agent mode** — one card with sub-issues; fan out one `implementation-agent` per sub-issue, parallel across repos. A chain of two or more sub-issues in the *same* repo is a PR stack and is handed to [`eng:stack-prs`](../stack-prs/SKILL.md) rather than chained by hand.

## Invocation Guard

This is a high-side-effect workflow. Treat direct slash-command invocation of
`linear-task` as explicit. Otherwise, before Step 1, verify the user's latest message
explicitly asks to work, ship, implement, drive, or merge the Linear issue. A bare
Linear ID or URL is not enough; stop and ask what the user wants to do with it.

## Step 1: Fetch the Issue

Use `get_issue` with the issue identifier (e.g., `ENG-123`) or extract the ID from a `linear.app` URL.

```
get_issue(id: "ENG-123", includeRelations: true)
list_comments(issueId: "ENG-123")
list_issues(parentId: "ENG-123")     // fetch sub-issues if any
```

Read the full issue: title, description, labels, priority, state, comments, current `estimate`, `gitBranchName`, and any sub-issues.

Show the user a one-line summary before proceeding.

## Step 2: Decide Execution Mode

### Has sub-issues → **Multi-agent mode** (Step 3+)

Each sub-issue is executed exactly once — a solo sub-issue by its own subagent, a same-repo chain of two or more as one `eng:stack-prs` stack. Skip to **Step 3 (multi-agent)**.

### No sub-issues → **Single-agent mode** (Step 3 single-agent)

Assess complexity first:

- **Simple / well-scoped** (single repo, focused change): proceed to single-agent mode.
- **Complex / ambiguous / multi-repo**: prepare a breakdown — a short ordered list of subtasks with the repo and rough scope for each. Present it to the user and wait for confirmation. Once confirmed, optionally split into Linear sub-issues (ask the user first) and re-enter multi-agent mode; otherwise execute each subtask as its own subagent following the multi-agent flow.

A task warrants breakdown if it touches more than one repo, has multiple independent concerns, or would result in a PR too large to review meaningfully.

---

# Single-Agent Mode

The opinionated single-card lifecycle. Used when there are no sub-issues and the work fits in one PR.

## Single-Agent Step 3: Classify and verify the right repo

### 3a. Is this a code-change card?

Quick classification:

- **Code-change signals**: description mentions code paths, files, services, repos; labels include engineering/backend/frontend/iOS/etc.; parent is an engineering epic; title verbs like "fix", "implement", "add", "refactor", "handle".
- **Non-code signals**: research, ops, design, content, planning, "investigate", "decide", "write up".

If it does **not** look like a code change:

- Tell the user explicitly.
- Offer to (a) draft a plan and post it as a Linear comment, then stop, or (b) bail out.
- Do not enter a worktree.

### 3b. Resolve the target repo

Infer the repo from the card using these signals, in order:

1. Explicit `[repo=…]` tag in the description or comments.
2. Repo names mentioned in description (`IntelligenceService`, `UserWeb`, etc.).
3. Domain keywords mapped via the [Repo Mapping](#repo-mapping) table below.
4. Parent issue's project or any linked PRs.

### 3c. Verify the session is in the right repo

Compare the inferred repo to the current working directory:

- If the CWD is inside the inferred repo (anywhere in its tree) → continue.
- If the CWD does **not** match → **stop**. Tell the user the exact repo to switch to and ask them to start a new Claude session inside it. Do **not** attempt to `cd` across repos in-session; tool sessions get confused and worktree state leaks.

If the repo is ambiguous, ask the user once before stopping or proceeding.

## Single-Agent Step 4: Enter a worktree on the Linear-named branch

- Call `EnterWorktree` with a short name derived from the card identifier (e.g. `engback-1097`).
- Inside the worktree, create the branch named exactly `gitBranchName` from Linear (it already encodes the card identifier and a slug from the title — Linear will auto-attach this branch to the issue).
- If the branch already exists locally or remotely, treat it as resumption: check it out instead of creating a new one.

## Single-Agent Step 5: Plan (read-only)

Enter plan mode via `EnterPlanMode`.

- Launch up to 3 `Explore` subagents in parallel where useful (one per area of the codebase). Each gets a focused question, not "explore the repo".
- Read the repo's `CLAUDE.md` and any nested `CLAUDE.md` under the paths you'll touch, plus `.claude/rules/security.md` (or `.cursor/BUGBOT.md`). That is the security checklist reviewers will apply; name in the plan which of its rules the change touches.
- Write the plan to the plan file. Required sections:
  - **Context** — what's broken / what's being added, why; reference the card.
  - **Approach** — recommended approach only (not all alternatives considered).
  - **Files to modify** — full paths and the change at each.
  - **Tests** — what new tests, in what file, using which existing spec as a pattern.
  - **Verification** — exact commands (`nvm use && npm run lint`, `npx jest <path>`, manual steps).
  - **Out of scope** — anything related that won't ship in this PR.

Call `ExitPlanMode` for user approval before any side-effects.

## Single-Agent Step 6: Implement

- Apply the plan: edits + tests.
- Run the repo's mandatory pre-commit workflow. Most Node repos here use `nvm use && npm run lint` — but read the repo's `CLAUDE.md` and use whatever it specifies.
- Run the relevant test suite (`npx jest <new spec path>` first; then a wider scope to check for regressions).
- Fix any failures before moving on. Do not commit on red.

## Single-Agent Step 7: Code review by subagent

Spawn `eng:code-investigator` (or `general-purpose` if the eng investigator isn't available) with:

- The card identifier and a one-line summary of intent.
- The worktree path and branch name (so it reads the uncommitted diff via `git diff` from inside the worktree, not the main checkout).
- Explicit ask: report **Must fix / Should consider / Nits** under 500 words; skip preamble.

Then:

- Apply **Must fix** items inline.
- Document **Should consider** items in the commit message and PR body so the human reviewer has context.
- Leave **Nits** unless trivial.

Re-run lint + tests after applying review fixes.

## Single-Agent Step 8: Commit and create the PR

Commit message:

- Conventional-commits subject, Linear identifier in brackets at the end, e.g. `feat(elevenlabs): handle conversation restart [ENGBACK-1097]`.
- Body explains the *why*, the change summary, and any documented "known limitations" from review.
- Include the standard `Co-Authored-By: Claude` footer if applicable.

Use a heredoc-via-file pattern (`git commit -F /tmp/<card>-commit-msg.txt`) to avoid shell-quoting issues on multi-line bodies.

Push the branch (`git push -u origin <branch>`).

Open the PR via `gh pr create` from inside the worktree:

- Title: same as the commit subject.
- Body (also from a temp file): **Summary** with the linked Linear card, the **what changed** bullet list, **known limitations** copied from the commit body, and a **Test plan** checklist.
- Pass `--base <default-branch> --head <branch>` explicitly. `gh` is unreliable when run from a worktree without these flags.

Capture and show the PR URL.

## Single-Agent Step 9: Self-review pass via `eng:review-pr`

Before pinging BugBot or the user, spawn `eng:review-pr` as a subagent against the just-created PR. Fresh eyes catch things the implementer rationalised away — and `review-pr` brings context the implementing agent doesn't reach for by default (AI Knowledge `Procedures/Engineering/PR Process` rubric, brand-voice priors on user-facing strings).

**Default: on.** Skip only on explicit user signal ("skip self-review", "no self-review") or when the change is trivially small (single-line typo fix, one-test addition) — and even then, ask the user before skipping.

Invoke with the PR URL plus context that it's a self-review pass on a freshly-created PR. `eng:review-pr` runs its full pipeline (AI Knowledge priors, repo `CLAUDE.md`, surrounding code, drafted inline comments) and its own user-approval gate before posting. The user decides at that gate whether to (a) post all inline comments, (b) post a summary-only PR comment, or (c) skip posting entirely.

**Outcomes:**

- **No actionable findings** → record a one-line note ("self-review found nothing actionable"), proceed to Step 10.
- **Findings posted** → record a one-line summary ("self-review posted N inline comments + 1 summary comment"), proceed to Step 10. BugBot will see them on its run.
- **Must-fix findings the user wants applied immediately** → do NOT post the review yet. Loop back to Single-Agent Step 6 (implement) with the notes, then Step 7 (subagent code review on the new diff), then Step 8 (commit). Re-run this Step 9 against the updated PR so the posted review reflects the latest state.

Carry the self-review summary line into Step 11's user check-in so the user sees both passes (self-review + BugBot) side by side.

## Single-Agent Step 10: Wait for review (Cursor BugBot + human)

Don't poll-loop. Pick one longer delay and schedule a single wake-up:

- Use `ScheduleWakeup` with `delaySeconds` between 600 and 1200 (10–20 min). Long enough for the Cursor BugBot to complete; short enough to keep the user in the loop.
- The wake-up `prompt` must re-enter this skill with the PR URL so the next firing continues from Step 11.
- `reason`: "waiting for BugBot + first human pass on PR #<n>".

Optional: also trigger `eng:bugbot-review` on the PR if BugBot doesn't auto-run on the repo.

## Single-Agent Step 11: Surface review state, ask the user

On wake (or on user nudge), gather what's visible without re-fetching the full diff:

- `gh pr view <url> --json reviews,comments,statusCheckRollup,mergeStateStatus`
- `gh api repos/<owner>/<repo>/pulls/<num>/comments` — inline comments (this is where BugBot lands).

Summarize:

- Cursor BugBot's findings (top 3 if many; "no findings" if clean).
- Any human review comments.
- CI status.

Ask one focused question:

- If there are review notes: "Want me to address these in another commit?" → if yes, loop back to Step 6 with the notes folded in; otherwise stop and wait.
- If clean: "Looks clean — ready to merge?" → wait for the user to merge (this skill never merges).

## Single-Agent Step 12: Close out the card after merge

Detect or confirm merge:

- `gh pr view <url> --json state,mergedAt` → `"state": "MERGED"` means it's in.
- Or trust the user's "merged" signal.

Then, in order:

1. **Pull latest** on the main checkout's default branch (not from inside the worktree): `git fetch && git pull --ff-only`.
2. **Update the Linear card** via `save_issue`:
   - `description`: rewrite to reflect what shipped. Restructure into **Problem / Desired behaviour / Implementation / Out of scope / PR** and link the PR. Preserve the user's original intent; add an "Implementation" section that documents the final shape.
   - `links`: append the PR URL with a descriptive title.
   - `state: "Done"`.
3. **Re-check the estimate.** Compare original `estimate` to actual difficulty:
   - **Easier than estimated** (single file, no review fixes, no surprises) → propose dropping by 1–3 points.
   - **Harder than estimated** (multiple review rounds, hidden races, schema migrations, cross-file refactors discovered mid-flight) → propose raising by 1–3 points.
   - **Roughly accurate** → leave alone.

   Always ask the user to confirm the new estimate via `AskUserQuestion` before writing it.
4. **Clean up the worktree** only after explicit user confirmation that the PR is merged. Use `git worktree remove` from the main checkout, then `git branch -D <branch>`. Do not attempt removal from inside the worktree.

Return a one-paragraph summary: card identifier, PR URL + state, final estimate (old → new if changed), worktree disposition.

---

# Multi-Agent Mode

Used when the parent card has sub-issues. Each sub-issue gets exactly one `implementation-agent` subagent.

## Multi-Agent Step 3: Plan the Subagent Execution Order

Before spawning anything:

1. **Map each sub-issue to a repo** — see `[repo=]` tags on the Linear issue. If absent, use the [Repo Mapping](#repo-mapping) table below.
2. **Group by repo.** A repo with **two or more** sub-issues is a **stack group**; a repo with exactly one is a **solo sub-issue**.
3. **Determine parallelism**: different repos run in parallel. Within a repo the work is sequential either way — a stack group is driven bottom-to-top by `eng:stack-prs`, in one worktree.

**Do not hand-roll the branch chain.** Basing each same-repo branch on the previous one by hand is what `gh stack` does properly: it gives the group a real stack on GitHub, `sync`/`rebase --upstack` when a lower layer moves, and a bottom-first `gh stack merge`. Hand-rolled bases have none of that, and a fix low in the chain silently leaves every branch above it stale.

Write out the execution plan and share it with the user before spawning subagents or starting a stack.

### Execution plan format

```
UserService  [stack group → eng:stack-prs]
  bottom  ENG-124  Add user preference model     rohan/eng-124-user-preference-model
  top     ENG-126  Preferences tab in settings   rohan/eng-126-preferences-tab

UserWeb      [solo → implementation-agent]      [parallel with the UserService stack]
  ENG-125  Update the settings client            rohan/eng-125-settings-client  → base: main
```

Layer order inside a stack group is by **code dependency**, not Linear creation order — `eng:stack-prs` Step 2b owns that call, so hand it the group and let it order the layers.

## Multi-Agent Step 4: Execute the plan

Two execution paths, run concurrently where the plan says so.

### Stack groups → `eng:stack-prs`

Invoke [`eng:stack-prs`](../stack-prs/SKILL.md) as a **scoped hand-off**: pass the parent id for context, the group's repo, and the **explicit list of that group's sub-issue ids**. It owns everything from there: layer ordering, `init`/`add`, draft `submit --auto`, the PR body pass, per-PR BugBot, and the bottom-first merge.

```
eng:stack-prs — scoped hand-off
  parent: ENG-123 (context only)
  repo:   UserService
  scope:  ENG-124, ENG-126        ← exactly these; ENG-125 is executed here
```

**The explicit scope is what prevents a loop.** Handed only a parent id, `stack-prs` re-derives the sub-issue set with `list_issues(parentId)`, sees the other repos' cards, hits its cross-repo hard stop, and offers to hand back to this skill — which would send it straight back. Its scoped-hand-off contract skips both the re-query and that stop, so name the ids.

Do not also spawn `implementation-agent`s for a stack group's sub-issues — the stack is driven in a single worktree, so a second agent editing the same repo would collide.

### Solo sub-issues → `implementation-agent`

Spawn subagents according to the execution plan. Run parallel batches together in a single message; run sequential batches after the previous batch completes. Never take over the task — if a subagent errors, fix it for the subagent but don't take over the implementation. If the error cannot be resolved, surface it to the user.

### Spawning

Use `subagent_type: "implementation-agent"` for every Agent tool call. This invokes the named subagent defined in `.claude/agents/implementation-agent.md`, which makes it @-mentionable and resumable after completion.

Build the per-invocation prompt using the template defined in the `implementation-agent` system prompt. Supply every parameter:
`{{issue_id}}`, `{{issue_title}}`, `{{issue_description}}`, `{{issue_comments}}`, `{{additional_context}}`, `{{repo_name}}`, `{{base_branch}}`, `{{branch_name}}`

Substitute all `{{param}}` tokens. Do not leave any `{{...}}` in the final prompt.

Each subagent uses `EnterWorktree` within its target repo for branch isolation — parallel agents targeting the same repo won't conflict. Pass `{{repo_name}}` as the repo directory name — the agent resolves the path as `./AuraHealth/<RepoName>` relative to the working directory where the skill was invoked.

Print the fully-resolved prompt to console for logging before spawning.

### Branch naming convention

Solo sub-issues: `<issue-id-lowercase>-<short-slug>` — e.g. `eng-124-add-user-validation`.

Stack groups follow `eng:stack-prs`, which uses the repo-wide convention from `/CLAUDE.md` → "Naming conventions" → "Branch names": `<firstname>/<ticket-id>-<slug>` — e.g. `rohan/eng-124-add-user-validation`. Don't rename its branches to match the solo form; the stack tracks them verbatim.

## Multi-Agent Step 5: Collect Results

Once every subagent and stack completes, gather the PR URLs, summaries, and agent IDs. Present a consolidated summary to the user:

```
UserService  stack #7  (merge bottom-first)
  ENG-124  PR: <url>   ← bottom
  ENG-126  PR: <url>   ← top

UserWeb
  ENG-125  PR: <url>  agent: <id>
```

For a stack, say the stack number and that it merges bottom-first — a reviewer who merges the top PR first will strand the rest.

Include a note that the user can resume any agent by saying e.g. "continue the ENG-125 agent" or by @mentioning `@implementation-agent` with context about which one to continue. Claude will use `SendMessage` with the agent ID to resume it with full history intact. Stack groups have no agent to resume — re-enter `eng:stack-prs`, which reads live state from `gh stack view --json`.

## Multi-Agent Step 6: Update Linear (Optional)

Only if the user asks:
- Move sub-issues to "In Review" using `save_issue`.
- Add a comment to the parent issue with the PR links using `save_comment`.

---

## Repo Mapping

Map issues to repos using these signals. Repos are expected to live under `./AuraHealth/<RepoName>` relative to the working directory where the skill was invoked.

| Signal | Likely repo(s) |
|--------|---------------|
| "iOS", "Swift", "Aurie", "voice assistant" | `AuraConsumerApp` |
| "React Native", "mobile" (legacy) | `Aura` |
| "web", "dashboard", "portal" | `UserWeb`, `BusinessWeb`, `CoachWeb`, `CRMWeb` — pick by context |
| "design system", "component library", "shared UI" | `WebDesignSystem` |
| "auth", "user management", "profile" | `UserService` |
| "content", "meditation", "coach", "program" | `ContentService` |
| "AI", "conversation", "voice", "OpenAI", "ElevenLabs" | `IntelligenceService` |
| "feed", "campaign" | `FeedService` |
| "payment", "subscription", "Stripe", "IAP" | `PaymentService` |
| "notification", "push", "OneSignal" | `NotificationService` |
| "email", "Iterable" | `EmailService` or `IterableService` |
| "experiment", "A/B test" | `ExperimentService` |
| "infra", "GCP", "deployment" | `AuraInfra` |
| "Firebase", "event", "PubSub" | `FirebaseProducer`, `FirebaseConsumer`, or `FirebaseDenormalizer` |

If the repo isn't clear from the issue text, ask the user before proceeding.

---

## Edge Cases

**Card is already "Done" or "Cancelled"** (single-agent): stop. Don't re-open a worktree.

**Card already has an open PR with the same `gitBranchName`** (single-agent): resume on that branch instead of creating a new one.

**Sub-issue is vague** (multi-agent): read the parent issue and sibling sub-issues for context before starting the subagent. If still unclear, ask the user one focused question upfront rather than letting the subagent guess.

**Sub-issue is already in progress** (multi-agent): check for an existing branch (`git branch -a | grep <issue-id>`) before creating a new one. If found, check it out and continue from there.

**Blocked sub-issue** (multi-agent): surface the blocker immediately. Don't spawn a subagent for a blocked issue — hold off further execution.

**Two or more sub-issues in one repo** (multi-agent): that is a stack group — hand it to [`eng:stack-prs`](../stack-prs/SKILL.md), don't chain the branches by hand (Multi-Agent Step 3).

**A repo doesn't have stacked PRs enabled** (multi-agent): `eng:stack-prs` exits 9 and stops. Either get the setting enabled, or fall back to sequential solo sub-issues with explicit `--draft` bases — and tell the user the group lost its stack grouping.

**CI fails after push** (single-agent): before running self-review (Step 9) or scheduling the wake-up (Step 10), fix the failure first. Don't ask the user to review red CI.

**User says "skip the wait"** (single-agent): skip Step 10, go straight to Step 11 once the user signals they've reviewed.

**User says "skip self-review"** (single-agent): skip Step 9, go straight to Step 10. Note the skip in Step 11's check-in so the user knows BugBot is the only automated pass.

**Re-run on an already-merged card** (single-agent): no-op. The close-out steps (description / state / link) are all idempotent via `save_issue`. Do not create a second PR.

## Reference

The single-agent lifecycle codifies the workflow executed manually for ENGBACK-1097 → [AuraHealth/IntelligenceService#228](https://github.com/AuraHealth/IntelligenceService/pull/228). The Linear card there is the canonical example of the final description shape produced in Step 12.
