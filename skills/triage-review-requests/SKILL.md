---
name: triage-review-requests
description: >
  Lists open, ready GitHub PRs where you or your team is a requested reviewer, across all
  repos, and checks each one's Cursor Bugbot gate on its head commit. PRs failing the gate
  can be sent back with a Request-changes review, only after you confirm; passing ones go to
  `review-pr`. Flags code-owner requests on PRs someone else is reviewing and offers to
  remove you. Also lists your own PRs with no reviewer, and others' Bugbot-clean PRs you
  could pick up if you have time. Trigger on "what PRs do I need to review", "any PR for me
  to review", "get my review requests", "PRs assigned to me for review", "my review queue",
  "triage my review requests", "which PRs are waiting on me", "which of my PRs have no
  reviewer", "PRs I could review if I have time", "send back PRs without bugbot", "reject
  PRs that haven't run bugbot", or "remove me from PRs where I'm only the code owner". Do
  NOT use to review a single PR's code (`review-pr`) or to trigger Bugbot on your own
  PR (`bugbot-review`).
metadata:
  version: "1.2.2"
---

# Triage review requests

Build the user's review queue and enforce one rule before any human review time is spent:
**Bugbot has reviewed the current head, and its findings on that head are dealt with.** The
gate is computed by a script. You present the result and post nothing without a yes.

The answer has three sections, always kept apart:

1. **The queue** — PRs requesting the user's review. Theirs to act on.
2. **Your PRs with no reviewer** — the user's own open, ready PRs that nobody has been asked
   to review or has reviewed, so they can assign one.
3. **If you have time** — other people's Bugbot-clean PRs the user could pick up. Optional
   suggestions, never presented as the user's queue.

The queue also surfaces requests the user doesn't need to act on: CODEOWNERS adds them to
PRs that another engineer is already reviewing. Those are offered for removal, never removed
silently.

## Inputs

None. The queue belongs to whoever `gh` is authenticated as. Optional:

| Flag | Effect |
|---|---|
| `--limit N` | Search cap for the queue and your own PRs (default 100, the GitHub cap) |
| `--no-mine` | Skip section 2 |
| `--no-suggest` | Skip section 3 |
| `--owner O` | Draw section 3 from owner `O` (repeatable). Default: every owner the user opened a PR in over the last 90 days, plus the owners in sections 1–2. Org membership is not used — it includes orgs whose PRs are none of the user's business |
| `--suggest-limit N` | Most recently updated open PRs checked per owner for section 3 (default 50) |

A full run re-reads every PR live and takes about a minute.

## Step 1 — Triage (read-only)

```bash
python3 $HOME/.claude/skills/triage-review-requests/scripts/triage.py list
```

Use `--json` when you need the head SHAs for Step 4 or the step-back fields for Step 3. The search is `review-requested:@me`,
open, `draft:false`, so it includes PRs requested from a team you're on. Each PR is then
re-read live over GraphQL, and a PR that turned draft or closed since the search indexed it
is skipped.

Each PR gets exactly one status, from the PR's **current head commit**:

| Status | Meaning | Gate |
|---|---|---|
| `findings_open` | Bugbot reviewed head and ≥1 finding raised **on head** is unresolved | fail |
| `bugbot_missing` | No Bugbot review or check-run on any commit | fail |
| `bugbot_stale` | Bugbot reviewed an older commit, not head | fail |
| `bugbot_running` | The `Cursor Bugbot` check-run on head is queued or in progress | wait |
| `already_sent_back` | Your latest review on head is already Request changes | skip |
| `ready` | Bugbot reviewed head and has no open findings on it | pass |

How the script decides, so you can explain a verdict without second-guessing it:

- **"Reviewed head"** means a `cursor` bot review whose `commit` is head, **or** a completed
  `Cursor Bugbot` check-run on head. The decision never reads the review body, because Bugbot
  empties older review bodies when a newer run completes (see `bugbot-review` →
  Background).
- **"Raised on head"** uses the thread's `originalCommit`, with the comment footer
  `… for commit <sha>` as a fallback. It never uses the line anchor, because GitHub re-anchors
  already-fixed findings onto the new head.
- Unresolved Bugbot threads from **older** commits don't fail the gate. They are counted in
  the Status column so you can see them.
- A running check-run is never judged, because findings can still land.

Independently of the gate, each queue row carries a **step-back** verdict:

| Field | Meaning |
|---|---|
| `code_owner_request` | The user is requested **by name** and GitHub marked the request `asCodeOwner` |
| `other_reviewers` | Every other requested user or team, plus every human (not the author, not a bot) who has already submitted a review |
| `step_back` | Both of the above: a code-owner request on a PR someone else is reviewing |
| `code_owner_review_required` | For step-back rows only: whether a ruleset on the base branch requires code-owner review (`null` when unreadable) |

`asCodeOwner` is GitHub's own flag on the request it creates from CODEOWNERS. The timeline
can't stand in for it, because it attributes code-owner requests to the PR author. A team
request is never a step-back: it can't be withdrawn for one member.

**Section 2 (`mine` in JSON)** lists the user's open, ready PRs with no requested reviewer
and no submitted review from anyone but bots. Each row carries the same Bugbot `status` as
the queue, so the user sees whether it's ready to assign or needs `bugbot run` first.

**Section 3 (`suggestions` in JSON)** lists other authors' open, ready PRs that pass the
gate (`ready`), don't request the user by name, and aren't in the queue, with `why`:

| `why` | Meaning |
|---|---|
| `no_reviewer` | No requested reviewer and no human review. Listed first |
| `stalled` | A reviewer's latest decisive review (approve / request changes / dismissed) is Request changes, the head commit is newer than every review they submitted, and that commit is ≥2 days old. Listed by `stalled_days`, longest first; `reviewers` names who it's waiting on |

A thread reply after the push is a COMMENTED review, so a reviewer still discussing it is not
stalled. A PR with an active reviewer is never suggested.

## Step 2 — Present the three sections

Show the script's output as-is: **Assigned to you**, then **Your PRs with no reviewer**, then
**If you have time**, each already sorted. Every section is one table: PR (`repo#number`),
Description (title and author), Status (Bugbot state plus notes; section 3 has none, since
everything in it is Bugbot-clean) and Link. Sections 2 and 3 are left out when empty, unless
their search hit its cap. Don't reflow the tables into prose or bullets. Never merge section 3
into the queue or describe its PRs as waiting on the user — its Description names the current
reviewer. Then, in one line each:

- the **ready** PRs: offer to review one with `review-pr`.
- the **failing** PRs: the proposed send-back (Step 4), listed by PR.
- the **step-back** PRs (👥 in Status): the proposed removal (Step 3), with who else is
  reviewing.
- your **unreviewed** PRs: offer to request a reviewer (Step 5) on the `✅ Bugbot clean`
  ones; for the rest, offer `bugbot-review` first.
- the **if you have time** PRs: offer to review one with `review-pr`. Nothing is posted
  for them.
- any ⚠️ lines (search cap hit, >100 threads, a PR that couldn't be classified, a base
  branch that requires code-owner review or whose rulesets couldn't be read, the per-owner
  suggestion cap). Report them;
  don't drop them silently.

**Repos without Bugbot.** `bugbot_missing` on every PR in a repo can mean Bugbot isn't enabled
there, not that the author skipped it. Asking those authors to run it achieves nothing, so
when every PR from one repo is `bugbot_missing`, say so and let the user exclude that repo
from the send-back.

## Step 3 — Step back from code-owner requests (only on explicit confirmation)

Skip this step when no row has `step_back`. Otherwise ask once, listing each step-back PR
with its other reviewers, and let the user approve all, a subset, or none. **A reply that
doesn't clearly approve means none.** Use `AskUserQuestion` with `multiSelect: true` when
there are several. Removing a reviewer is visible on the PR, so ask even when the answer
seems obvious.

Put any PR whose `code_owner_review_required` is `true` or `null` in its own question, and
say why: removing the user from a branch that requires code-owner review blocks the merge
until a code owner approves, and `null` means that couldn't be ruled out.

For each approved PR, check first, then remove:

```bash
S=$HOME/.claude/skills/triage-review-requests/scripts/triage.py
python3 $S remove-self <pr-url> --dry-run   # re-checks, changes nothing
python3 $S remove-self <pr-url>             # withdraws the user's review request
```

`remove-self` re-reads the PR and the base branch's rulesets, and **refuses** (exit 3) if the
PR is no longer open and ready, the user is no longer requested by name as a code owner, no
one else is reviewing, or code-owner review is required or unreadable. Add
`--allow-required` only for a PR the user approved in the separate question above. A refusal
is the safe outcome: report it and leave that PR alone.

A PR the user stepped back from leaves the queue. Drop it from Step 4.

## Step 4 — Send back (only on explicit confirmation)

Ask once, listing every failing PR. The user can approve all of them, a subset, or none.
**A reply that doesn't clearly approve means none.** Use `AskUserQuestion` with
`multiSelect: true` when there are several.

For each approved PR, preview the body first, then post:

```bash
S=$HOME/.claude/skills/triage-review-requests/scripts/triage.py
python3 $S send-back <pr-url> --head <head-sha-from-step-1> --dry-run   # show the body
python3 $S send-back <pr-url> --head <head-sha-from-step-1>             # post it
```

`send-back` re-reads the PR and **refuses** (exit 3) if the head moved since Step 1, if the
PR is no longer open and ready, or if it now passes the gate (e.g. Bugbot finished in the
meantime). A refusal is the safe outcome. Report it and re-run Step 1 for that PR; don't try
to work around it. The review is a GitHub **Request changes** review posted as the user. Its
body names the reason and the head SHA, explains that `bugbot run` must be a comment on its
own, and ends with the `triage-review-requests:<version>` attribution line.

Re-running is safe. Submitting a review removes the user from the PR's requested reviewers,
so a sent-back PR drops out of the queue until the author re-requests. A team request that
survives is caught as `already_sent_back`.

## Step 5 — Request reviewers on your own PRs (only on explicit confirmation)

Skip this step when section 2 is empty. The script never picks a reviewer: ask the user who
should review each PR they want assigned, and only for PRs whose Bugbot status is
`✅ Bugbot clean` — requesting review on a PR that fails the gate asks a colleague for time
the gate would refuse. **A reply that doesn't name a reviewer means none.** Requesting a
reviewer notifies them, so ask even when the answer seems obvious.

For each approved PR and reviewer:

```bash
gh pr edit <number> --repo <owner>/<repo> --add-reviewer <login>
```

## Step 6 — Summarize

One line per PR that was acted on (stepped back / sent back / reviewer requested / refused /
skipped), plus the ready PRs still waiting for review. Nothing else.

## Failure modes

- **`gh` 403 or "Resource not accessible"**: a credential fault, not a verdict. Usually
  `GITHUB_TOKEN` is shadowing the keyring credential (see `/CLAUDE.md` → GitHub credentials).
  Retry with `env -u GITHUB_TOKEN -u GH_TOKEN`.
- **A PR in the ⚠️ error list**: report it as unclassified, never as ready.
- **Exit 2**: `gh` failed or a URL didn't parse. The error is printed on stderr.
