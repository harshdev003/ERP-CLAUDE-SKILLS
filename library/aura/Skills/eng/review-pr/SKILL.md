---
name: review-pr
description: >
  Review a GitHub pull request against AuraHealth's engineering standards and post per-line
  inline comments back to the PR. Gathers context in layers before reading the diff: (1) the
  canonical Engineering PR process and brand-voice priors from AI Knowledge, (2) the repo's
  own guidelines (`CLAUDE.md`, `README.md`, any nested `CLAUDE.md`s under changed paths), (3)
  the surrounding code patterns (sibling files, callers/callees of touched code), and (4) the
  linked Linear ticket and the PR description. Then reviews the diff file by file, drafts an
  inline comment for every issue, shows the user the full set, and on approval submits a
  single GitHub review with all comments anchored to the right lines. Trigger when the user
  says "review this PR", "code review on {PR URL}", "review PR #1234", "do a PR review", "look
  over this pull request", "leave inline comments on {PR}", "give me feedback on this PR", or
  pastes a GitHub PR URL and asks for a review.

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Products/Aurie       # brand voice + user-facing-string priors when reviewing Aurie copy
    - Products/Aura        # brand voice + user-facing-string priors when reviewing Aura copy
    - Procedures/Engineering/PR Process     # canonical Eng PR review checklist — required reading before any review
    - Compliance/Security/Code Review Checklist  # security/privacy rules every Aura code PR is checked against
    - Tools/Slack                    # Slack post/read conventions + footer format
    - Tools/Linear                   # Linear MCP / GraphQL access
    - Tools/GitHub                   # gh CLI auth + PR conventions
---

# review-pr

Review a GitHub pull request against AuraHealth's engineering standards and post per-line inline comments back to the PR.

The flow is **context first, code second** — every layer of context narrows what counts as a real issue, and skipping a layer almost always produces noisy or wrong comments. Don't shortcut.

## When to use

Trigger when the user says any of:

- "review this PR" / "review PR #1234"
- "code review on `<PR URL>`"
- "do a PR review on `<branch>`"
- "leave inline comments on `<PR>`"
- "give me feedback on this PR"
- pastes a GitHub PR URL (or a GitHub PR comment URL) and asks for a review

Also trigger when the user opens a PR via `gh pr create` and immediately asks for a review pass before requesting human review.

## Inputs

Accept any of:

- **PR URL** — `https://github.com/<owner>/<repo>/pull/<num>` — the most common form.
- **PR number** — `#1234` or just `1234` — works only if the current working directory is inside the matching repo (the skill resolves owner/repo via `gh repo view --json nameWithOwner`).
- **Branch name** — `jerome/eng-381-add-braintrust-topics` — resolved to its open PR via `gh pr list --head <branch>`. If no PR exists yet, ask the user whether to review the branch as a *pre-PR* dry-run (no inline posting; just a chat readout).
- **No argument** — review the PR for the current branch (`gh pr view`).

Optional flags from the user message:

- `event: comment | request_changes | approve` — the review event for `gh api`. **Default `comment`** (non-blocking). Only escalate to `request_changes` when the user explicitly says so or when at least one **Critical** issue is found and the user agrees on Step 7 prompt.
- `scope: changed | full` — `changed` (default) reviews only lines in the diff; `full` lets the skill comment on adjacent un-touched lines (still attached to a hunk line on GitHub) when an issue spans context.

## Workflow

### Step 0 — Pull AI Knowledge priors

Before reading the PR or the diff, invoke `shared:retrieve-knowledge` to pre-load the paths declared in this skill's `knowledge.reads` frontmatter:

- `Procedures/Engineering/PR Process` — the **canonical review checklist** maintained by Engineering. Treat its sections (e.g. correctness, tests, observability, rollout safety, naming, security) as the rubric. If the page lists explicit categories, organize Step 6 findings under those exact headings so the comments map cleanly to the team's expectations.
- `Compliance/Security/Code Review Checklist` — the **security and privacy rules** for every Aura code repo. Section A applies to all repos; B is for backend services, C web, D Firebase rules and Functions, E mobile, F release. The repo copy (Step 2) is the same text; if they differ, flag the drift and apply the stricter rule.
- `Products/Aurie` and `Products/Aura` — pre-load **both**. Used in Step 6 when reviewing user-facing strings (alerts, push copy, voice prompts, paywall headlines). Select the relevant one based on which app the touched code ships to:
  - iOS Swift / `IntelligenceService` / voice-assistant code → Aurie voice
  - React Native / web / library/coaching content → Aura voice

If any path fails to resolve (taxonomy drift), note the miss in the readout's Sources block and fall back to the rubric in this SKILL.md plus repo `CLAUDE.md` — don't block on it.

Carry the fetched content in working memory through Steps 1–7. Cite paths and Notion URLs in the final summary comment.

### Step 1 — Resolve the PR

Parse the input into `{ owner, repo, number, head_sha, base_sha, head_ref, base_ref, files[] }`:

```bash
# Full URL or number-in-current-repo
gh pr view <input> --json number,title,body,headRefName,baseRefName,headRefOid,baseRefOid,author,url,labels,files,additions,deletions,reviewRequests

# Just want the diff
gh pr diff <number>
```

Capture `headRefOid` (the head commit SHA) — Step 7's inline-comment payload **requires** it; comments anchored to a stale SHA silently fail to render.

If the PR has multiple commits, the latest commit on the head ref is the right SHA.

### Step 1.5 — Survey existing review state

Before reading any code, snapshot what's already on this PR. Three things to gather, all of which steer Steps 6 and 8:

**a) Pending (draft) reviews.** A PR can have at most one *pending* review per user, and posting a new review fails with `422 — User can only have one pending review per pull request` while one exists. Check up front:

```bash
gh api "repos/<owner>/<repo>/pulls/<num>/reviews" \
  --jq '.[] | select(.state == "PENDING") | {id, user: .user.login, submitted_at}'
```

For each pending review, list its draft inline comments so you know what's already drafted:

```bash
gh api "repos/<owner>/<repo>/pulls/<num>/reviews/<review_id>/comments" \
  --jq '.[] | {path, line, body: .body[0:120]}'
```

Decide upfront how to interact with any pending review found:

- **Own pending review** (the `gh auth status` user owns it).
  - **Empty draft (0 comments)** — almost always a phantom from a prior failed posting attempt (see Step 8 — every failed `POST /pulls/{pr}/comments` and every failed `addPullRequestReviewThread` silently creates one). Delete it without prompting:
    ```bash
    gh api -X DELETE "repos/<owner>/<repo>/pulls/<num>/reviews/<review_id>"
    ```
    Don't surface to the user — it's noise from the previous run, not intent.
  - **Non-empty draft (≥1 comment)** — real in-progress work. Three options to surface to the user in Step 7: (a) **add findings to the existing draft** (default — see Step 8 for how this works via `POST /pulls/{pr}/comments` with no explicit `pull_request_review_id`); (b) **submit the existing draft first**, then post a fresh review with this skill's findings; (c) **dismiss the pending draft** and post a single consolidated review. Never silently fragment.
- **Other user's pending review.** You **cannot** post to another user's pending draft regardless of permissions. Two options: (a) wait until they submit, then post — risk: if they submit before Step 8, the API behavior changes (see Step 8 gotcha); (b) skip auto-posting entirely and hand the readout to the user so they can paste comments by hand. Surface this to the user in Step 7 — don't try to be clever.

**b) Prior submitted reviews and their inline comments.** Read what other reviewers (humans, Claude/Codex/Cursor bots, etc.) have already left:

```bash
gh api "repos/<owner>/<repo>/pulls/<num>/reviews" \
  --jq '.[] | select(.state != "PENDING") | {id, user: .user.login, state, submitted_at, body: .body[0:200]}'
gh api "repos/<owner>/<repo>/pulls/<num>/comments" \
  --jq '.[] | {id, in_reply_to_id, user: .user.login, path, line, body: .body[0:200]}'
```

Build a small index: `{ (path, line) → [ {comment_id, author, body, replies: [...]}, ... ] }`. Step 3 and Step 6 both use this index.

**c) Existing comment threads + author replies.** For each top-level inline comment, follow `in_reply_to_id` chains so you see the full conversation. An author reply like *"Intentional — the rename was approved in the spec"*, *"Acknowledged but won't fix in this PR"*, or *"Done in a follow-up commit"* changes how Step 6 should treat that finding.

This step is cheap (3 API calls) and prevents the most common review-quality failures: duplicating a finding the author has already addressed, posting on top of a pending draft and getting 422'd, or fragmenting one review across two posts because someone's draft submitted mid-review.

### Step 2 — Read repo guidelines

Repo guidelines override Notion priors when they conflict — `CLAUDE.md` is the loudest, most up-to-date source for that codebase.

1. **Root `CLAUDE.md`** — read end to end. Extract: architecture principles, naming conventions, secrets handling, PR workflow, any "non-negotiable" rules.
2. **Nested `CLAUDE.md`s under changed paths** — `git diff --name-only $base..$head` then for each touched directory walk up to the repo root collecting any `CLAUDE.md` along the way. Nested instructions take precedence over root rules within their subtree.
3. **`README.md`** — skim for setup, test commands, and any conventions not in `CLAUDE.md`.
4. **Repo rule files** — `.claude/rules/*.md`, `.cursor/rules/*.mdc` and `.cursor/BUGBOT.md`. Read `security.md` / `security.mdc` / `BUGBOT.md` end to end: they carry the repo's copy of the security checklist.
5. **`.claude-plugin/` or `.cursor-plugin/` manifests** — if the PR touches plugin/skill files, the marketplace manifests define the contract.

Build a **rules dossier** for the review — a short list of concrete rules ("kebab-case skill names", "skills must be self-contained — no cross-skill imports", "secrets in `~/.claude/settings.json`, never in `.env`"). Reference rule names in inline comments so the author can audit your reasoning.

### Step 3 — Read the surrounding code patterns and existing reviewer signal

For each file in the diff, read the surrounding code so you can judge what "fits" vs. what doesn't.

- **The full file**, not just the hunks. A function looks fine in isolation but wrong relative to its sibling functions in the same module.
- **Sibling files in the same directory** — match the directory's existing style (naming, error-handling shape, logging conventions, test layout).
- **Callers and callees** of changed functions — `grep` / `rg` for the function name across the repo. A change that looks safe in the file can break a call site three directories away.
- **Tests for the touched code** — `__tests__/<file>.test.*`, `tests/test_<file>.py`, etc. If the diff doesn't update tests for changed behavior, that's a finding.

**Also read the existing reviewer signal** captured in Step 1.5:

- **Other humans' inline comments.** Skim each. They establish what the team already cares about on this PR — your review should *reference* their notes when they overlap (e.g. *"pairs with @<user>'s note above on missing nullability"*) rather than independently re-stating the same point. Reviewers should be visibly reading each other.
- **Bot reviewers (Cursor / Claude / etc.).** Read their findings *and* any author replies. A bot finding the author has already replied to with *"Intentional"*, *"Acknowledged"*, or *"Will fix in follow-up"* is not a fresh issue — Step 6 will route it (reply, skip, or fresh top-level — see Step 6 routing rules).
- **Bot findings should be verified, not trusted.** Cursor bot in particular has been wrong about naming/contract mismatches when the backend artifact (Linear ticket title, experiment server config, API spec) actually agrees with the PR. Before treating a bot's High Severity as ground truth, verify against the relevant backend artifact you already have from Step 4.

This step is the difference between a useful review and a stylistic noise machine. Spend the budget here.

### Step 4 — Linear ticket and PR description

Establish *intent* before judging *execution*.

1. **Extract Linear ticket id** from PR title or body — patterns like `[PROD-1336]`, `[ENG-381]`, `AUR-123`. The repo's PR template puts it in the title (per `CLAUDE.md` commit-message convention).
2. **Fetch the ticket** via `mcp__e54f5eeb-edb7-453f-8506-a15c155bf9c1__get_issue` (Linear MCP) — title, description, acceptance criteria, comments, linked issues. Capture the canonical URL.
3. **Read the PR body** — the Summary and Test plan sections that the repo's `gh pr create` workflow generates. Note any "out of scope" or "follow-up" callouts.

From these, write a one-paragraph **intent statement**:

> *"This PR is meant to do X (per Linear), with explicit non-goals Y. The author claims it does X1, X2, X3 (per PR body), validated by tests T1, T2 (per Test plan)."*

Every Step 6 finding is judged against this intent statement. A clean review of a PR that *doesn't do what the ticket asked* is a worse outcome than a messy review that catches the gap.

### Step 5 — Read the diff

```bash
gh pr diff <number> > /tmp/pr-<number>.diff
```

Walk the diff hunk by hunk. For each hunk capture:

- File path and the hunk's line range on the head SHA (the `+` lines — those are the ones inline comments will anchor to).
- The change's *kind*: new file, deleted file, edited file, renamed file, mode change.
- The change's *intent* (inferred from the hunk + intent statement from Step 4).

Don't form judgments yet — Step 6 does the lensing. Step 5 is just inventory so Step 6 can be systematic.

### Step 6 — Review against every layer

For each hunk, run the change through these lenses **in order**, stopping the first time a lens finds a real issue (don't pile redundant comments on the same line). **Exception: the security walk in lens 3 runs on every hunk of an Aura code repo, whatever an earlier lens found.**

1. **Intent (Step 4).** Does this change advance what the Linear ticket asked? Does it match the PR body's claims? Out-of-scope creep is a finding.
2. **Existing-thread check (Step 1.5 + 3).** Before drafting a new finding, look up `(path, line)` in the existing-comments index. If a prior reviewer (human or bot) already raised it, branch on what the author said:
   - **Author replied "Intentional" / "Acknowledged" / "Won't fix in this PR".** Default: **skip** — don't re-litigate. Two exceptions to flag to the user in Step 7: (a) the author's reasoning is wrong on a *Critical* point (rare, but worth a fresh top-level comment to make sure it gets attention); (b) the user explicitly wants reinforcement because *"good when several of us have the same comment"* — a fresh top-level comment then makes sense. Surface the choice; don't decide silently.
   - **Author hasn't replied yet.** Default: **reply via `in_reply_to`** to the existing thread (see Step 8), not a new top-level comment. Reply bodies should add genuinely new signal — a corroborating example, a counter-argument, a pointer to a sibling pattern — not just `+1`.
   - **Bot finding (Cursor / Claude / etc.) you suspect is a false positive.** Verify against the backend artifact (Linear ticket title, server config, API spec) before drafting anything. If the bot is wrong, do *not* echo their finding; either reply on the bot's thread with the verifying artifact link or skip entirely.
3. **`Procedures/Engineering/PR Process` checklist (Step 0).** Walk the rubric — correctness, tests, error handling, observability, rollout, security, performance, naming, doc. Flag any rubric item the change violates or fails to satisfy. **Security walk (always runs, see above):** walk every hunk of an Aura code repo through the sections of `Compliance/Security/Code Review Checklist` that match the repo type, cite the rule (e.g. "Checklist B2 — ownership"), and treat a violation as Critical. **Adversarial pass:** when the diff adds or changes a route, an RTDB rule or an SSR page, also run the eight probes in [`references/adversarial-pass.md`](references/adversarial-pass.md) on each one (no token, anonymous token, another user's token, `{}` body, bad id, other methods, a harvested id, an SSR page for a missing id); each probe the change gets wrong is a Critical finding citing its checklist rule. With no such surface, skip the pass without comment.
4. **Repo guidelines (Step 2).** Match the rules dossier. Cite the specific rule by name in the comment ("self-contained skills rule, `CLAUDE.md` Architecture principle 1").
5. **Surrounding patterns (Step 3).** Does this change fit the file/directory's existing shape? If you'd be the third reviewer to ask "why doesn't this look like the others?", that's a finding.
6. **Brand voice (Step 0).** *Only* for user-facing strings — copy in alerts, push notifications, voice prompts, marketing surfaces. Skip for log lines, error messages a user will never see, internal variable names. Mismatches with the right Identity/App page are a finding.
7. **General code quality.** Standard issues a careful reviewer always catches: race conditions, missing null checks at trust boundaries, unhandled error paths that leave bad state, secrets in code, API misuse, dead code introduced in this PR, copy-paste bugs.

For each finding, capture:

```yaml
- path: <file path relative to repo root>
  line: <line number on head SHA — the `+` line in the hunk>
  start_line: <optional, for multi-line comments>
  side: RIGHT                 # always RIGHT for `+` lines; LEFT only when commenting on `-` lines (rare)
  severity: critical | important | nit
  category: <rubric heading from PR Process — e.g. "Tests", "Naming", "Brand voice">
  routing: top_level | reply_to_thread | skip
  in_reply_to: <existing comment id from Step 1.5, when routing == reply_to_thread>
  body: |
    <comment text — see "Comment style" below>
```

**Routing values** drive Step 8's posting strategy:

- `top_level` — fresh inline comment on the line. The default for issues no one else has raised.
- `reply_to_thread` — reply on an existing comment thread via `in_reply_to`. Use when extending or corroborating a prior reviewer's point. Cannot be batched into the reviews-endpoint payload — Step 8 issues these as separate `POST /pulls/{pr}/comments` calls.
- `skip` — captured for the chat readout but **not** posted to GitHub. Use when a thread already covers it and the author has already replied. Surfacing the skip in Step 7 lets the user override on a case-by-case basis.

**Severity definitions:**

- **Critical** — must fix before merge. Bugs, security issues, broken contracts, missing required tests, violations of repo non-negotiables ("never commit to main", "no cross-skill imports"), things that would break prod.
- **Important** — should fix before merge but won't block by itself. Pattern misalignment, missing nice-to-have tests, naming drift, brand-voice mismatches in shipped copy.
- **Nit** — optional polish. Minor wording, comment cleanup, formatting the linter doesn't catch. Prefix the comment body with `nit:` so the author can triage at a glance.

**Comment style** (applies to every inline comment body):

- **Open with a Claude attribution line.** Every inline comment and the top-level review summary must start with the tag:

  ```
  🤖 _Posted by Claude via the `review-pr` skill (on behalf of @<gh-user>)._
  ```

  Substitute `<gh-user>` with the `gh auth status` user. The PR author should never be in doubt about whether a human or this skill drafted the comment, even when reviewing on a phone where the bot badge isn't visible. Keep it as a single blockquote/italic line so the body that follows reads cleanly.

- Lead with **what's wrong**, then **why** (the rule or pattern it conflicts with), then **what to do** (a concrete suggestion or a code block).
- One issue per comment. Don't pile two findings into one body.
- Cite the source — Linear ticket, PR Process section, repo `CLAUDE.md` principle, sibling file path. Author should be able to verify your reasoning in one click.
- Use `~/strikethrough~` for "I'd remove this" suggestions. Use ` ```suggestion ` blocks (GitHub-native suggested-changes syntax) when the fix is a few-line code change — those become a one-click commit for the author.
- Be terse. A reviewer's value is signal, not volume.

**Skip rules.** Don't comment on:

- Lines outside the diff (GitHub will reject the comment as out-of-range).
- Auto-generated files (lockfiles, generated SDKs, snapshots) unless the issue is *the regeneration itself* (e.g. lockfile was regenerated unnecessarily).
- Style issues a linter or formatter already enforces in CI — let the bot catch them.
- Things you flagged in an earlier Step 6 lens — don't double-report the same finding under a different category.

### Step 7 — Show the user the draft and ask before posting

Before any GitHub API call, show the user the **full set of findings** in chat, grouped by severity and file. Format:

```
PR review draft for #<num> — <PR title>

Summary
  <2–3 sentences: did this PR do what the ticket asked, your overall read,
   any rollout/test concerns the author should know about>

Existing review state (from Step 1.5)
  - Pending review by you: <yes/no, with id and N draft comments — or "none">
  - Pending review by others: <user list — or "none">
  - Prior submitted reviews: <count, by whom, latest state>
  - Bot findings already on PR: <count, with how many the author has replied to>

Critical (<n>)
  - <file>:<line> · <category> · <routing> · <one-line summary>
  ...

Important (<n>)
  - <file>:<line> · <category> · <routing> · <one-line summary>
  ...

Nit (<n>)
  - <file>:<line> · <category> · <routing> · <one-line summary>
  ...

Skipped (<n>) — already covered by an existing thread
  - <file>:<line> · <category> · <one-line summary> · <link to existing comment>
  ...

Sources
  - Notion: Procedures/Engineering/PR Process — <url>
  - Notion: Products/<Aurie|Aura> — <url>  (only if voice was a lens)
  - Linear: <ticket id> — <url>
  - Repo CLAUDE.md (root)<and any nested>
```

Routing column values: `top` (fresh top-level), `reply` (`reply_to_thread`), `skip`. Make the breakdown visible so the user can audit what will and won't be posted, and override per-finding.

**If a pending review exists** (yours or someone else's, from Step 1.5), surface that *first* and ask the pending-review question before the post-or-not question:

> A **pending review** is already open on this PR — handle it before I post:
>
> - **Yours**: `<id>` with `<n>` draft comments. Pick one:
>   1. **Add my findings to your draft** *(default)* — the new top-level inline comments will attach to your existing pending review, so you can review and submit them all together. (How: see Step 8 — `POST /pulls/{pr}/comments` without `pull_request_review_id` auto-attaches to your pending draft.)
>   2. **Submit your draft first**, then post mine as a fresh review.
>   3. **Dismiss your draft**, then post mine as a single consolidated review.
>
> - **Others'**: `@<user>` has a pending draft. Pick one:
>   1. **Wait** until they submit, then I'll re-check state and post (Step 8 will re-survey before posting).
>   2. **Skip auto-posting** — I'll print the readout here and you can paste comments by hand.

Once any pending-review question is answered, ask the main posting question:

> Want me to post this as a GitHub review? Options:
>
> 1. **Post as `comment` review** — leaves the inline comments and the summary; doesn't block merge. *(Default)*
> 2. **Post as `request_changes`** — same comments, but blocks merge until the author marks them resolved. Pick this if any Critical issue is real.
> 3. **Edit first** — I'll show you any specific finding so you can rephrase, drop it, change its routing (`top`/`reply`/`skip`), or downgrade severity, then re-confirm.
> 4. **Don't post** — keep the readout in chat only.

**Never post without explicit user approval.** Sending a GitHub review is a public action visible to the author and the team — treat it like `slack_send_message_draft`. Wait for an affirmative response (`yes`, `1`, `post it`, etc.) before Step 8.

### Step 8 — Post the review

On approval, post the findings. There are **three posting paths**, picked per finding from the `routing` field captured in Step 6 and per the pending-review answer from Step 7. Always re-check state immediately before any API call (see "Race-condition re-check" below).

#### 8a. Race-condition re-check (always run)

Between Step 7's confirmation and the actual `POST`, three things can change:

```bash
# Re-fetch head SHA — the author may have force-pushed
gh pr view <num> --json headRefOid --jq '.headRefOid'

# Re-fetch pending-review state — the user (or another reviewer) may have submitted, dismissed,
# or opened a draft since Step 1.5
gh api "repos/<owner>/<repo>/pulls/<num>/reviews" \
  --jq '.[] | select(.state == "PENDING") | {id, user: .user.login}'
```

If either changed in a way that invalidates the Step 7 plan, **stop and surface it**:

> Your pending review was submitted as `APPROVED` while I was preparing — the path you picked (add to draft) no longer applies. Should I post my findings as a separate `comment` review, or skip and let you add them by hand?

Don't silently fall back to a different posting path; that fragments the review history in ways the user won't expect.

**Phantom-pending sweep before every retry.** If a previous post in this same Step 8 attempt failed (REST 422 or GraphQL `thread: null`), GitHub may have left an empty pending review owned by your auth user — re-running the post without clearing it produces `User can only have one pending review per pull request`. Sweep before retry:

```bash
AUTH_USER=$(gh api user --jq '.login')
gh api "repos/<owner>/<repo>/pulls/<num>/reviews" \
  --jq ".[] | select(.state == \"PENDING\" and .user.login == \"$AUTH_USER\") | .id" | \
while read -r id; do gh api -X DELETE "repos/<owner>/<repo>/pulls/<num>/reviews/$id"; done
```

This is also belt-and-braces against state Step 1.5 missed. Run it silently — it's cleanup, not policy.

#### 8b. Posting path A — Single review via the Reviews API

This is the default path when the user has **no own pending review** and chose `Post as comment` or `Post as request_changes`. One API call, one review, all `top_level` inline comments batched.

```bash
# Build the JSON payload — one review, many inline comments
cat > /tmp/review-<num>.json <<EOF
{
  "commit_id": "<re-fetched headRefOid>",
  "body": "<the Summary block from Step 7, with the Claude attribution line at the top>",
  "event": "<COMMENT | REQUEST_CHANGES | APPROVE>",
  "comments": [
    { "path": "<file>", "line": <n>, "side": "RIGHT", "body": "<inline comment body, Claude tag included>" },
    { "path": "<file>", "start_line": <a>, "line": <b>, "side": "RIGHT", "start_side": "RIGHT", "body": "<multi-line comment body>" },
    ...
  ]
}
EOF

gh api -X POST "repos/<owner>/<repo>/pulls/<num>/reviews" --input /tmp/review-<num>.json
```

Capture the review URL (`html_url` in the response) for the final readout.

#### 8c. Posting path B — Add to your existing pending review

When Step 7's pending-review answer was **option 1 (add to your draft)**, do **not** call the reviews endpoint. Instead, post each `top_level` finding as a single-comment `POST` and let GitHub auto-attach to the open draft.

```bash
# For EACH top_level finding — issued separately, no reviews-endpoint payload
gh api -X POST "repos/<owner>/<repo>/pulls/<num>/comments" \
  -f commit_id="<re-fetched headRefOid>" \
  -f path="<file>" \
  -F line=<n> \
  -f side=RIGHT \
  -f body="<comment body, Claude tag included>"
```

GitHub's documented behavior for `POST /pulls/{pr}/comments` (the main gotcha to internalize):

- **If the auth user has a pending review on this PR**, the new comment attaches to that pending review as a draft. No `pull_request_review_id` field needed — GitHub picks the user's open draft automatically.
- **Otherwise**, GitHub creates a brand-new auto-submitted single-comment review for each call. There is no API path to create a pending comment without an existing draft to attach to.
- **You cannot post to another user's pending draft** regardless of permissions — the comment will create a separate auto-submitted review under your auth, fragmenting the history.

Practical implication: if Step 8a's re-check shows the user's pending draft was submitted between Step 7 and Step 8, switching to path B silently produces a series of separate single-comment reviews. **Don't auto-fall-back** — surface it via the Step 8a prompt and let the user pick the new path.

The summary `body` doesn't get its own slot in path B (the reviews endpoint owns that). If a summary is needed, post it as a top-level PR-issue comment via `gh api -X POST "repos/<owner>/<repo>/issues/<num>/comments" -f body="..."` after the inline comments.

#### 8d. Posting path C — Reply to existing threads

For each finding with `routing: reply_to_thread`, issue an `in_reply_to` reply. These are independent of any pending review (and run regardless of which posting path A/B is in play for `top_level` findings):

```bash
gh api -X POST "repos/<owner>/<repo>/pulls/<num>/comments" \
  -F in_reply_to=<existing comment id from Step 1.5> \
  -f body="<reply body, Claude tag included>"
```

Replies do **not** require `commit_id`, `path`, or `line` — those inherit from the parent. Replies bypass the pending-draft gotcha and post live to the thread immediately, so reserve them for genuinely additive content (a corroborating example, a counter-argument, a verifying artifact link).

#### 8e. Comment payload rules

- **`commit_id` is the head SHA at the time of posting** (Step 8a re-fetched). Comments anchored to a stale SHA silently fail to render in the PR view.
- **Single-line comment** → `line` only (omit `start_line` / `start_side`).
- **Multi-line comment** → both `start_line` and `line` (inclusive range), and both `start_side` and `side` set to `RIGHT`.
- **`side: RIGHT`** is comments on the head version (added or modified lines). Use `LEFT` only when commenting on a deleted line (rare).
- **Suggested-changes blocks** in `body` use the GitHub-native ```` ```suggestion ```` fence — render as a one-click "Commit suggestion" for the author.
- **Out-of-range comments fail the whole call** when using path A (the reviews endpoint rejects the entire review if any one comment's `line` isn't in the diff). On path B each comment posts independently, so an out-of-range single comment fails alone and the rest still go. Either way, dump rejected comments and report them in the final readout.
- **Claude attribution.** Every body — top-level inline, multi-line, reply, summary — must open with the attribution line from Step 6's "Comment style". This is non-optional; the PR audit trail relies on it.
- **Don't use `gh api --input <file>`** for review-comment endpoints. It intermittently triggers a false `422 — pull_request_review_thread.base internal error` even on payloads that succeed when re-issued with `-f`/`-F` flags (witnessed: same multi-line JSON body, identical fields, `--input` failed twice in a row, then `-f path=… -F start_line=86 -f start_side=RIGHT -F line=95 -f side=RIGHT -f body="$BODY"` succeeded first try). Always shape REST review-comment calls as `-f` for strings, `-F` for numbers, and pass the body through a shell variable: `BODY=$(cat /tmp/finding.json | jq -r '.body')` then `-f body="$BODY"`. Path A's reviews endpoint (one-shot review with `comments[]`) is the only place `--input` is acceptable, because the reviews endpoint payload genuinely needs JSON nesting `gh api`'s flag interface can't express.

#### 8f. Final readout

After the calls return, report back:

> Posted review on `<owner>/<repo>#<num>` — `<n>` Critical, `<m>` Important, `<k>` Nit, `<s>` skipped (already-covered threads). Posting path: `<A | B | C>`. Review URL: `<url>` (path A) — or — `<n>` comments attached to your pending draft `<id>` (path B) — or — `<r>` replies posted to existing threads (path C). Authored by Claude on behalf of `@<gh-user>`.

If the user picked `Edit first` in Step 7, loop back to Step 7 with the updated draft until they approve, then run Step 8 once.

#### 8g. When inline posting fails consistently

If REST returns `422 — pull_request_review_thread.base internal error`, or GraphQL `addPullRequestReviewThread` returns `thread: null` with no `errors` field, **read [`references/posting-troubleshooting.md`](references/posting-troubleshooting.md)** before debugging anything else. The TL;DR: it's almost always token-state corruption — probe a second PR with a one-call test, and if it fails the same way, generate a fresh classic PAT (same `repo` scope) and restart Claude Code. Don't spend time on commit-SHA variants, schema variants, or GraphQL fallbacks; they all fail through the same broken code path.

## Output format

Default chat output is the Step 7 readout — the structured findings list. Once posted, append the review URL and a one-line confirmation. Don't repaste the full comment bodies — they're now on GitHub.

When the input was a branch with no PR (Step 1 fallback), skip Step 7's posting prompt and just print the structured readout — there's no PR to post to.

## Notes and gotchas

- **Authoring on `main`.** If the PR author is committing directly to `main`, that's an automatic Critical finding ([`CLAUDE.md` PR workflow](../../../../CLAUDE.md)) — but in that case there's also no PR, so this skill won't have been invoked. Nudge the user to branch when you see drafts on `main` in any other context.
- **PRs that touch this skill itself.** When reviewing changes to `plugins/eng/skills/review-pr/`, the rubric inverts: the *self-contained* and *naming* rules from `CLAUDE.md` apply, plus the [Knowledge manifest](../../../../CLAUDE.md#knowledge-manifest) rules for `knowledge.reads`. Don't recursively invoke this skill — review by hand.
- **Cowork sandboxes.** `mcp__workspace__bash` strips host env (PROD-1305), and `gh`'s keyring credential doesn't reach the sandbox either, so the CLI works inside Cowork only when authenticated in-sandbox or via a session-scoped export. If `gh api` calls 401, fall back to printing the readout in chat and asking the user to post manually — don't paste a token into `outputs/`.
- **A 403 (not 401) on check/CI reads is a credential-precedence bug, not missing CI.** See AINative CLAUDE.md → "GitHub credentials": a `GITHUB_TOKEN` in the env shadows `gh`'s keyring credential and can't read Checks. Never conclude a PR's CI is unknown or failing from a 403.
- **Large PRs (>500 LoC changed).** Don't try to review every line. Anchor inline comments to the highest-leverage hunks (architectural choices, unsafe patterns, missing tests for new behavior) and use the summary `body` to call out the breadth issue itself. Suggest the author split the PR if size is the dominant problem.
- **PRs that pre-empt the review with "I know about X" notes.** Honor self-disclosure — if the author says "I'm punting tests to a follow-up", don't add a "missing tests" Critical. Downgrade to a `nit` reminder or drop entirely.
- **Don't approve PRs you authored.** If `gh pr view --json author` shows the current `gh auth status` user, refuse `event: approve` even if the user asks — GitHub blocks self-approval anyway, but flag it explicitly so the user picks `comment` instead.
- **Re-reviews.** If the same PR has been reviewed by this skill already (check `gh pr view --json reviews`), focus the new pass on the diff *since* the last review (`gh pr diff --since <last-review-sha>`). Don't re-paste comments the author has already addressed.
- **Cursor bot ≠ ground truth.** Cursor's bot reviews flag naming/contract mismatches by pattern matching, not by reading the source-of-truth artifact. When it raises a *High Severity* on a rename or signature change, verify against the Linear ticket title, server config, or API spec the PR is implementing *before* echoing the finding. A real-world false positive from this repo: cursor bot flagged a rename as a contract mismatch when the Linear ticket title `ab:aiThreeDayTrial` literally specified the new name. Skip — don't reinforce a wrong High Severity just because a bot raised it.
- **Multi-reviewer hygiene — read each other.** When other reviewers (humans or bots) have already left inline comments, the Step 1.5 + Step 3 reads are non-optional. Reference their notes (`pairs with @<user>'s note above on …`) instead of independently re-stating, and prefer `routing: reply_to_thread` (Step 8d) over a parallel top-level finding. The exception is when the user explicitly wants reinforcement because *"good when several of us have the same comment"* — in that case go top-level, but flag the choice in Step 7 first.
- **POST `/pulls/{pr}/comments` semantics gotcha.** Documented in Step 8c, worth re-reading every time: with a pending review by the auth user, comments attach to that draft; without one, each comment becomes its own auto-submitted single-comment review. There is no way to post a draft comment without already having a pending review. This is the *one* GitHub-API behavior most likely to fragment a clean review into half a dozen separate posts if you don't keep it in mind.
- **Race-condition between draft and post.** Even with Step 7 confirmation, the user's pending draft can be submitted (or another reviewer's opened) between confirmation and `POST`. Step 8a's re-check is mandatory — if state changed, surface it and re-prompt; never auto-fall-back to a different posting path.
- **Self-attribution is non-optional.** Every body — inline, reply, summary — opens with the Step 6 Claude-attribution line (`🤖 _Posted by Claude via the `review-pr` skill (on behalf of @<gh-user>)._`). PR authors will see comments under the auth user's account, not Claude's; the tag is the only signal that distinguishes a skill-drafted comment from a hand-typed one.
- **`pull_request_review_thread.base internal error` ⇒ rotate the token, don't keep debugging.** REST `POST /pulls/{pr}/comments` returning `422 — pull_request_review_thread.base internal error`, or GraphQL `addPullRequestReviewThread` returning `thread: null` with no `errors` field, is GitHub-side state corruption keyed to your token — same `repo` scope, fresh issuance fixes it. See Step 8g for the one-call diagnosis (probe a second PR; if it fails the same way, it's the token) and the fix sequence. Skipping straight to "rotate the token" when these symptoms appear saves ~45 minutes of fruitless debugging through commit SHA variants, schema variants, and GraphQL fallbacks — they all fail the same way through the same broken code path.
- **Failed posts silently create empty pending reviews.** Every failed `POST /pulls/{pr}/comments` and every failed `addPullRequestReviewThread` call leaves an empty pending review owned by your auth user behind on the server, even when the response was an error. Subsequent attempts then hit `422 — User can only have one pending review per pull request` from `addPullRequestReview`, masking the original cause. Step 1.5's "Empty draft (0 comments)" branch deletes these silently, and Step 8a's phantom-pending sweep deletes any new ones between retries — both are required to avoid getting stuck in this loop. Don't surface phantom drafts to the user; they're noise from prior failures, not intent.
- **GraphQL `addPullRequestReviewThread` ≠ exception when it fails.** When the same backend bug fires through GraphQL, the mutation returns `{"data": {"addPullRequestReviewThread": {"thread": null}}}` with NO `errors` field — this looks like a soft success but no thread is created (and an empty pending review IS created). Any GraphQL-based posting code MUST treat `data.addPullRequestReviewThread.thread === null` as a hard failure, not a partial success. The default error-handling pattern of "throw if `errors[]` is non-empty" silently no-ops here.
- **Avoid `gh api --input <file>` for review-comment endpoints.** It intermittently triggers the same `pull_request_review_thread.base` 422 even on payloads that succeed when re-issued with `-f`/`-F` flags carrying the same content (witnessed: identical multi-line JSON, `--input` failed twice in a row, `-f` flags succeeded first try). Use `-f` for strings, `-F` for numbers, and pass the body via shell variable: `BODY=$(cat /tmp/finding.json | jq -r '.body')` then `-f body="$BODY"`. Path A's reviews endpoint (one-shot review with `comments[]`) is the only place `--input` is acceptable, because the reviews endpoint payload genuinely needs JSON nesting that `gh api`'s flag interface can't express.

## Example invocations

### Single PR by URL

> "review https://github.com/AuraHealth/AINative/pull/72"

1. Resolve PR, head SHA, files (Step 1).
2. Pull `Procedures/Engineering/PR Process` + brand voice priors (Step 0).
3. Read root + nested `CLAUDE.md`, `README.md` (Step 2).
4. Read each changed file in full + sibling files + callers (Step 3).
5. Fetch the linked Linear ticket `PROD-1336`, read the PR body (Step 4).
6. Walk the diff and run the 6-lens review (Steps 5–6).
7. Show the structured readout in chat and ask before posting (Step 7).
8. On `1` (post as comment), submit the review and report the URL (Step 8).

### Current branch, no argument

> "review this PR"

`gh pr view` resolves the PR for the current branch. If there isn't one, the skill asks whether to do a pre-PR dry-run (Step 1 fallback) and skips posting.

### Pre-PR dry-run on a branch

> "review my branch jerome/eng-381-add-braintrust-topics"

`gh pr list --head <branch>` returns no PR. Run Steps 0–6 against the branch's diff vs `main`, print the readout, skip Step 7's posting prompt. The user can post manually after `gh pr create`.
