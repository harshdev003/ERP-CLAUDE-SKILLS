---
name: bugbot-review
description: >
  Trigger a Cursor BugBot review on a GitHub pull request by posting the top-level `bugbot
  run` comment on the PR conversation, and optionally wait for BugBot to finish and report its
  findings. Resolves the target PR from a URL or number, or — when none is given — from the
  current branch's open PR in the working directory's repo. Refuses to post on closed or
  merged PRs. By default posts directly after resolving and returns immediately: no
  confirmation prompt, no waiting. Trigger the default fire-and-forget mode on "run bugbot",
  "bugbot review", "trigger bugbot on this PR", "@cursor review this", "ask cursor to review",
  "rerun bugbot on PR #1234", or a pasted GitHub PR URL with a request for a BugBot review.
  Trigger the wait mode on "run bugbot and wait", "wait for bugbot to finish", "run bugbot
  until it's done", "trigger bugbot and tell me when it's clean", or an orchestrating skill
  asking to block for the result. Do NOT use to author a code review yourself
  (`eng:review-pr`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index
    - Procedures/Engineering/PR Process
    - Tools/GitHub
---

# Run BugBot Review

## Goal

Post a top-level `bugbot run` comment on a GitHub pull request so Cursor's
BugBot picks up the PR and runs a review. Returns the comment URL once
posted. Optionally — when asked to wait — poll until BugBot finishes and
report its findings (or a clean result) back to the caller.

## Background — how BugBot triggering works

BugBot only fires on **top-level PR conversation comments**. Specifically:

- Inline code comments do **not** trigger it.
- Replies inside an existing review thread do **not** trigger it.
- The comment body must be exactly `bugbot run` or `@cursor review`
  (case-insensitive; either works — we default to `bugbot run` because it's
  shorter and unambiguous).

This skill always posts a top-level issue comment via `gh pr comment`,
which maps to the right GitHub API surface (`POST /repos/{o}/{r}/issues/{n}/comments`).

## Background — how BugBot reports completion

A single `bugbot run` produces two distinct artifacts, in this order, and
only the second one means BugBot is actually done:

1. **Acknowledgment (not completion).** Within seconds, `cursor[bot]` posts
   a top-level issue comment starting with `## PR Summary` (a
   `<!-- CURSOR_SUMMARY -->` marker in the body). This can land well before
   the review itself finishes — never treat it as a completion signal.
2. **Completion (the real signal).** Some minutes later, `cursor[bot]`
   submits an actual PR **review**. That review's *existence* is the
   completion signal — not anything in its body.

   Treat a review as **this run's** completion when all three hold: it is
   authored by `cursor[bot]`, `submitted_at` is after this run's trigger
   timestamp, and `commit_id` equals this run's **target commit** — the
   head SHA at the moment the trigger was posted or discovered (captured
   once; see Workflow — never re-fetched live during a wait, since the PR
   can be pushed to again mid-poll). Any inline review comments whose
   `pull_request_review_id` matches that review's `id` are this run's
   findings.

   **Never filter on the body — not even on `<!-- BUGBOT_REVIEW -->`.**
   When a newer run completes on the same PR, the older review's body is
   **emptied**, marker included, so a body filter makes a run that already
   finished look like no run at all. Inline comments survive the wipe; only
   the body is cleared. The marker buys nothing anyway: the `## PR Summary`
   acknowledgment is a top-level *issue comment* and cannot appear on
   `pulls/<n>/reviews`, so within that endpoint every `cursor[bot]` entry
   is already a completion.

   The body, **when present**, still carries a human-readable sentence
   worth showing the user. Its wording differs by outcome and by effort
   level (Default/High/Custom), and the clean case carries **no numeral at
   all**:

   | outcome | observed sentence |
   |---|---|
   | findings | `Cursor Bugbot has reviewed your changes using default effort and found N potential issues.` |
   | clean | `✅ Bugbot reviewed your changes and found no new issues!` |

   So a `found (\d+) potential issue` parse silently matches nothing on a
   clean run. Never derive the clean/not-clean branch from it — derive
   that from the findings list itself (Step 4b). Display the sentence when
   there is one; say nothing about it when the body is empty.

## Inputs

The user provides one of:

1. A full GitHub PR URL (e.g. `https://github.com/AuraHealth/AuraConsumerApp/pull/4521`).
2. A PR number with the repo implied from context (e.g. "run bugbot on #4521 in AuraConsumerApp").
3. Nothing — meaning "use the PR for my current branch in the working directory".

Plus an optional **wait** flag — set when the user's phrasing asks to wait
for the result (see trigger phrases in the frontmatter description), or
when an orchestrating skill/agent passes it explicitly. Default: `false`
(fire-and-forget).

## Workflow

### 1. Resolve the target PR

**If the user provided a URL or `<repo>#<number>` form** — parse owner, repo,
and PR number from it. Skip to step 2.

**If the user provided only a PR number** — derive the repo from the
working directory (`gh repo view --json nameWithOwner`) and proceed.

**If the user provided nothing** — detect the current branch's PR:

```bash
# Assumes cwd is the target repo. Don't suppress stderr — we need to
# distinguish auth errors from "no PR for branch" downstream.
gh pr view --json number,url,state,title,headRefName,baseRefName,isDraft
```

- If `gh pr view` errors with an auth message (`gh auth status` confirms
  this) → tell the user to run `gh auth login` and stop.
- If `gh pr view` errors with "no pull requests found for branch" → tell
  the user there's no PR for the current branch and ask them to provide
  one explicitly. Do not guess.
- Any other error → surface the `gh` stderr verbatim and stop.

### 2. Validate the PR is reviewable

Fetch the PR's current state with a single `gh` call:

```bash
gh pr view <number-or-url> --repo <owner/repo> \
  --json number,url,state,title,headRefName,baseRefName,isDraft,author
```

Refuse to post if `state` is `CLOSED` or `MERGED` — tell the user the PR
is no longer open and stop.

Draft PRs are fine — BugBot will still run on them. Repo org doesn't
matter — the skill is org-agnostic.

**If wait mode was requested**, check for an in-flight (or already-finished)
run before posting a new trigger. Capture the most recent trigger comment
(paginated, case-insensitive, so a busy PR's older comments don't hide it)
and the PR's current head SHA. **Append `// empty` to every `last` here** —
jq's `last` on an empty array is `null`, and a bash variable holding the
literal string `"null"` is not empty to `[ -z ... ]`; without the `//
empty` guard, "no match" silently reads as "found something":

```bash
TRIGGER=$(gh api "repos/<owner>/<repo>/issues/<number>/comments" --paginate | jq -s \
  'add | [.[] | select(.body | ascii_downcase | test("^(bugbot run|@cursor review)\\s*$"))] | last // empty')
TRIGGER_TS=$(echo "$TRIGGER" | jq -r '.created_at // empty')
HEAD=$(gh pr view <number-or-url> --repo <owner/repo> --json headRefOid --jq '.headRefOid')
```

If `TRIGGER_TS` is empty, there's no prior trigger to worry about — go
straight to Step 3.

If `TRIGGER_TS` is non-empty, check whether `cursor[bot]` has posted **any**
completion review (`submitted_at` after `TRIGGER_TS`) for that trigger yet —
regardless of which commit it's anchored to — again with the `// empty`
guard. Note there is **no body filter**: a superseded review is emptied, and
filtering on its text would hide a run that did finish (see "Background"):

```bash
ANY_COMPLETION=$(gh api "repos/<owner>/<repo>/pulls/<number>/reviews" --paginate | jq -s \
  --arg ts "$TRIGGER_TS" \
  'add | [.[] | select(.user.login == "cursor[bot]") | select(.submitted_at > $ts)]
       | last // empty')
ANY_COMPLETION_SHA=$(echo "$ANY_COMPLETION" | jq -r '.commit_id // empty')
```

- **A completion exists and `ANY_COMPLETION_SHA` equals `$HEAD`** — that
  trigger is already fully resolved for the current commit. There's
  nothing to wait on *and* no need to post a fresh trigger — the user
  asked to wait for a result, and one already exists. Skip Step 3 **and**
  Step 4b's polling: jump straight to "Once `COMPLETION` is non-empty" in
  Step 4b, using `ANY_COMPLETION` as `COMPLETION`.
- **A completion exists but `ANY_COMPLETION_SHA` does not match `$HEAD`** —
  the prior trigger was answered, but the PR has moved since (a new push).
  This isn't "in flight" — tell the user BugBot's last answer is for an
  older commit and proceed to post a fresh trigger for the current head
  (Step 3), rather than offering to wait on it.
- **No completion at all** (`ANY_COMPLETION` empty) — check `TRIGGER_TS`'s
  age before deciding this is "in flight":

  ```bash
  NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  to_epoch() { date -u -d "\$1" +%s 2>/dev/null || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "\$1" +%s; }
  AGE_SECONDS=$(( $(to_epoch "$NOW") - $(to_epoch "$TRIGGER_TS") ))
  ```

  - **`AGE_SECONDS` ≤ 600 (10 minutes)** — recent enough to genuinely still
    be running (every observed round in this skill's own review history
    completed in 2–4 minutes). Tell the user a run is already in progress
    and ask whether to wait on it instead of posting a new one. **If they
    choose to wait, skip Step 3 entirely.** Set `TARGET_SHA=$HEAD` (the
    head SHA just fetched above — this is the commit that trigger almost
    certainly targeted, since nothing else has indicated a push happened
    since), keep this `TRIGGER_TS`, and go straight into Step 4b's polling
    loop (Step 4b starts its own wait-start clock, so an old `TRIGGER_TS`
    doesn't shrink the polling budget — see "Gotchas").
  - **`AGE_SECONDS` > 600** — treat as abandoned (a credit outage or a
    failed run that will never post a completion), not "in flight." Tell
    the user the prior trigger is old with no result and proceed straight
    to Step 3 to post a fresh one, rather than offering a wait that would
    just run out the clock on a dead trigger.

Re-triggering while a prior run's completion review hasn't posted yet can
produce ambiguous, empty-bodied review artifacts that are hard to attribute
to the right run (see "Gotchas" below) — this is exactly what the check
above avoids.

### 3. Post the top-level comment

Post directly — do **not** ask the user to confirm. The skill is
explicitly fire-and-go: the user already asked for a BugBot run by
invoking it, and BugBot is non-destructive (it only adds review comments),
so the extra prompt is friction without safety value. This holds for both
the default mode and wait mode — the only difference wait mode adds is
what happens *after* posting.

Use `gh pr comment`, which always creates a top-level issue comment on the
PR conversation — exactly the surface BugBot listens to.

```bash
POSTED_URL=$(gh pr comment <number-or-url> --repo <owner/repo> --body 'bugbot run')
```

The default body is `bugbot run`. If the user explicitly asked for
`@cursor review` instead, pass that as the body — both trigger BugBot.

`gh pr comment` prints the new comment's `html_url` to stdout — that's
already everything default mode needs. **Wait mode additionally needs**
the comment's `created_at` (fetched by numeric id, not by scanning the
comment list — see 4b) and the head SHA at this exact moment:

```bash
TARGET_SHA=$(gh pr view <number-or-url> --repo <owner/repo> --json headRefOid --jq '.headRefOid')
```

Capture `TARGET_SHA` right after posting — this is the commit BugBot is
reviewing for this trigger, fixed for the rest of the wait (see
"Background — how BugBot reports completion" on why this must not be a
live re-fetch during polling).

### 4a. Default mode — report back immediately

The post in Step 3 already succeeded — `POSTED_URL` is the comment link.
Nothing further needs to be fetched or can fail at this point. Return:

- `POSTED_URL` (so the user can click through to the comment).
- The PR URL.
- A one-line "BugBot will post its findings back on the PR conversation as
  a native review comment when it's done — typical turnaround is a few
  minutes."

Stop here. Do not poll.

### 4b. Wait mode — poll for completion, then report findings

Only runs when wait mode was requested (see "Inputs"). There are three
ways to arrive here:

- **Step 2 already found a matching completion** (its "resolved for the
  current commit" branch) — `COMPLETION` is already set to that review.
  Skip straight to "Once `COMPLETION` is non-empty" below; no polling
  needed at all.
- **Step 2's "wait on the existing run" path** — `TRIGGER_TS` and
  `TARGET_SHA` are already known from that step. Skip to the polling loop
  below.
- **Step 3** (a fresh post) — `TARGET_SHA` is already known (captured
  right after posting). Get this trigger's exact timestamp with a direct
  lookup by the comment's numeric id (parsed from `POSTED_URL`), not a
  scan-and-match over the comment list:

  ```bash
  COMMENT_ID=$(echo "$POSTED_URL" | grep -oE '[0-9]+$')
  TRIGGER=$(gh api "repos/<owner>/<repo>/issues/comments/$COMMENT_ID")
  TRIGGER_TS=$(echo "$TRIGGER" | jq -r '.created_at // empty')
  ```

  If `TRIGGER_TS` comes back empty, this lookup failed — but the trigger
  itself already posted successfully (Step 3), so don't tell the user the
  run failed. Surface the raw `gh api` error, fall back to default-mode
  behavior (report `POSTED_URL` and stop), and note that wait mode
  couldn't be completed this time.

**The 5-minute timeout is measured from when polling starts, not from
`TRIGGER_TS`.** Record a separate `WAIT_START` the moment polling begins —
`TRIGGER_TS` only identifies which trigger to match completions against,
and it can be several minutes old already when Step 2 reused an in-flight
trigger. Anchoring the timeout to `TRIGGER_TS` in that case would leave
little or no real polling budget (see "Gotchas").

Poll every **~90 seconds**, checking for a `cursor[bot]` review carrying
the completion marker, submitted after `TRIGGER_TS`, anchored to
`TARGET_SHA` — all three conditions from "Background — how BugBot reports
completion". `TARGET_SHA` is fixed for the whole wait; **do not** re-fetch
the PR's live head SHA on each poll (a push mid-wait would move it, and
the review answering this trigger is anchored to the commit that existed
when the trigger was posted, not to whatever the head becomes later):

```bash
COMPLETION=$(gh api "repos/<owner>/<repo>/pulls/<number>/reviews" --paginate | jq -s \
  --arg ts "$TRIGGER_TS" --arg sha "$TARGET_SHA" \
  'add | [.[] | select(.user.login == "cursor[bot]")
              | select(.submitted_at > $ts)
              | select(.commit_id == $sha)] | last // empty')
```

Same `// empty` guard as Step 2 — without it, "no match yet" reads back as
the literal string `"null"`, which `[ -z "$COMPLETION" ]` would not treat
as empty. If `COMPLETION` is empty, keep polling. **Hard timeout: 5 minutes from
`WAIT_START`.** If no matching review appears within that window, stop
polling and tell the user BugBot hasn't completed within the timeout —
they can ask to check again later, or re-run.

Once `COMPLETION` is non-empty:

1. If the body is non-empty, keep its human-readable sentence for display
   (per "Background" above). Do **not** derive the clean/not-clean branch
   from it: the clean sentence carries no numeral, and a superseded body
   is empty. The findings list in the next step is the authority.
2. Fetch this run's actual findings, scoped to the matched review's `id`
   (not just a timestamp filter, so a slow or overlapping run's comments
   can't get misattributed):

   ```bash
   REVIEW_ID=$(echo "$COMPLETION" | jq -r '.id')
   gh api "repos/<owner>/<repo>/pulls/<number>/comments" --paginate | jq -s \
     --argjson rid "$REVIEW_ID" \
     'add | [.[] | select(.user.login == "cursor[bot]") | select(.pull_request_review_id == $rid)]
            | .[] | {path, line, url: .html_url, body: .body[0:200]}'
   ```

3. Fetch every BugBot thread still unresolved on the PR, from **any** run.
   A run reviews only its own diff and never re-posts an earlier finding,
   so a run with 0 comments says nothing about a High left open by the run
   before it. Outdated threads count too: `isOutdated` means the anchored
   lines changed, not that the finding was fixed. GraphQL names the bot
   `cursor`, not `cursor[bot]`:

   ```bash
   OPEN_THREADS=$(gh api graphql --paginate -F owner=<owner> -F repo=<repo> -F number=<number> -f query='
     query($owner:String!,$repo:String!,$number:Int!,$endCursor:String){repository(owner:$owner,name:$repo){pullRequest(number:$number){
       reviewThreads(first:100,after:$endCursor){pageInfo{hasNextPage endCursor}
         nodes{isResolved isOutdated comments(first:1){nodes{author{login} path line originalLine url body}}}}}}}' \
     | jq -s '[.[].data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved | not) | (.isOutdated) as $o | .comments.nodes[0] | select(.author.login == "cursor") | {path, line: (.line // .originalLine), outdated: $o, url, body: .body[0:200]}]')
   ```

   Also read the check-run for `TARGET_SHA` (`neutral` = findings, `success` = clean):

   ```bash
   gh api "repos/<owner>/<repo>/commits/$TARGET_SHA/check-runs" \
     --jq '.check_runs[] | select(.name == "Cursor Bugbot") | "\(.status) \(.conclusion)"'
   ```

   A 403 here is a credential fault, not a verdict (`/CLAUDE.md` → "GitHub
   credentials"); retry with `env -u GITHUB_TOKEN -u GH_TOKEN`, and if it
   still fails, say so and let the threads decide.

4. Report back:
   - **Clean** only when this run found 0 issues, `OPEN_THREADS` is `[]`,
     and the check-run did not conclude `neutral`. Tell the user BugBot is
     clean and mention that `gh pr ready <number>` is now an option —
     don't run it yourself.
   - **This run found N > 0 issues** — list each finding (file:line,
     one-line summary pulled from the comment body, comment URL) so the
     user or an orchestrating skill can decide to fix and re-run.
   - **Still open from earlier runs** — every `OPEN_THREADS` entry whose URL
     is not one of this run's findings (file:line, summary, URL, `(outdated)`
     where it applies). Not clean while any remain, even after a 0-finding
     run.
   - **Check-run `neutral` with nothing listed** — not clean; link the check-run.

This skill does not fix code or mark the PR ready itself — reporting
completion state and findings is the extent of its job. A caller driving
a fix → re-trigger → wait loop across multiple calls to this skill should
cap it (recommended: 5 rounds) and hand the decision back to the user
rather than looping indefinitely if findings persist — see the BugBot
cycle in `Procedures/Engineering/PR Process`, the canonical process doc this skill's
`knowledge.reads` declares.

## Output

**Default mode:** a short message containing the comment URL and the PR
URL. Nothing else — no editorializing about what BugBot might find.

**Wait mode:** either "BugBot is clean" (with the PR URL and a note that
`gh pr ready` is available), or this run's findings and any threads still
open from earlier runs (file:line, summary, URL), or — on timeout — a note
that BugBot hadn't completed within 5 minutes and how to check again.
"Clean" means no unresolved BugBot thread from any run.

## Gotchas

- **Don't re-trigger while a prior run is still in flight.** Every trigger
  produces its own run, and each completing run **empties the previous
  review's body** while leaving its inline comments in place — so a stack
  of overlapping runs leaves several bodiless reviews that are easy to
  misattribute. Check for an in-flight run before posting (Step 2) rather
  than firing blind, and when several triggers are outstanding, wait for a
  completion matching *your* target SHA, not merely the first to land.
- **Match completion on the author, the trigger timestamp, and
  `TARGET_SHA` together** — never on anything in the body (it is emptied
  when superseded, and the sentence's wording changes with effort level),
  and never on recency alone ("the latest review" or "the highest review
  id"). A slow completion from an earlier trigger can otherwise be
  mistaken for this run's result.
- **`TARGET_SHA` is captured once, never re-fetched live during polling.**
  It's the head SHA at the moment the trigger was posted or discovered.
  If the poll instead re-fetched "the current head" on every iteration, a
  push that lands mid-wait would move the match target out from under a
  completion review that's correctly anchored to the *original* commit —
  producing a false timeout even though BugBot did finish.
- **Scope findings to the matched review's `id`** (`pull_request_review_id`
  on each inline comment), not just `created_at > trigger_ts` — a
  timestamp-only filter can pull in comments from a different, overlapping
  run.
- **The wait timeout is anchored to when polling starts, not to
  `TRIGGER_TS`.** When Step 2 reuses an already-in-flight trigger, that
  timestamp can be minutes old; timing the 5-minute budget from it instead
  of from `WAIT_START` would leave little or no real time to poll.
- **"In flight" requires no completion yet, not just a head-SHA mismatch.**
  If BugBot already answered the last trigger but the PR has since been
  pushed to, that trigger isn't still running — it's just stale for the
  new head. Step 2 checks for *any* completion first (ignoring commit) to
  tell these two cases apart; only "no completion at all" means wait,
  otherwise post a fresh trigger.
- **If Step 2 finds the last trigger already resolved for the current
  head, don't re-trigger.** A user asking to "wait for BugBot" wants the
  result, not necessarily a brand-new run — if one already exists for the
  current commit, report it immediately instead of posting again and
  forcing another full poll cycle.
- **`jq`'s `last` on no match is the literal string `null`, not empty.**
  Every place this skill checks "did anything match?" (`ANY_COMPLETION`,
  `COMPLETION`) must end the jq pipeline with `last // empty` so a
  non-match becomes a truly empty bash variable — `[ -z "$VAR" ]` does not
  treat the 4-character string `"null"` as empty, so skipping the guard
  makes "nothing found" silently read as "found something."
- **Bound "in flight" by trigger age, not just by the absence of a
  completion.** A trigger that never got a completion (bot credit outage,
  a run that silently failed) looks identical to one that's still running
  unless you also check how old it is. Treat anything older than ~10
  minutes with no completion as abandoned and post a fresh trigger, rather
  than offering an in-flight wait that will just run out the clock.
- **A 0-finding run is not a clean PR.** Unresolved threads from earlier
  runs, outdated ones included, decide "clean" (Step 4b, item 3).
- **BugBot's findings are not authoritative.** This skill only surfaces
  what BugBot reported; verifying whether a finding is actually correct is
  the caller's job. See `eng:review-pr`'s Step 6 "Cursor bot ≠ ground
  truth" guidance for the reasoning pattern — no need to duplicate it here.

## Edge cases

- **`gh` not authenticated.** `gh pr view` will error with an auth
  message. Tell the user to run `gh auth login` and stop.
- **PR is closed or merged.** Refuse and tell the user. BugBot won't
  review historical PRs through this path.
- **Trigger timestamp lookup fails in wait mode.** Don't poll with an
  empty `$ts` — it matches every past completion review as "after" it, so
  wait mode would report a stale result as this run's outcome. The
  trigger comment itself already posted successfully at this point, so
  don't report a failure to the user — fall back to default-mode output
  (the comment URL) and note wait mode couldn't complete this time (see
  Step 4b).
- **User wants to rerun BugBot.** Same flow — posting `bugbot run` again
  on an open PR retriggers it. This skill is idempotent in the sense that
  re-running it just queues another BugBot run; tell the user if they
  already triggered one in the last few minutes and ask whether they want
  to post again (see also "Gotchas" — check for an in-flight run first).
- **User asks to use `@cursor review` instead of `bugbot run`.** Honour it
  — they're equivalent. Default stays `bugbot run`.
- **Comment post fails (network, permissions).** Surface the `gh` error
  verbatim to the user; don't silently retry.
- **Wait mode times out.** Report the timeout plainly (see "Output") —
  don't keep polling past 5 minutes, and don't silently fall back to
  default-mode behavior.

## Why this skill is mostly deterministic

The whole job is "resolve PR → post one comment → (optionally) poll a
head-scoped, timestamp-scoped API filter for a stable marker and report
back." No LLM reasoning is needed past input parsing and, in wait mode,
summarizing the findings list — so there's no `scripts/` directory: `gh pr
view`, `gh pr comment`, and `gh api` carry the deterministic work directly.
Judging whether a BugBot finding is *correct* is explicitly out of scope
(see "Gotchas") — that's `eng:review-pr`'s job.
