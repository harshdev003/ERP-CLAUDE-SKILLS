---
name: distill-sessions-daily
description: >
  Unattended daily routine that distills the previous day's Claude Code sessions on this
  machine into durable knowledge. Reads every session with human turns in the window
  (Friday onward on a Monday), keeps those carrying a learning that improves Aura or Aurie
  engineering or QA, and routes each: repo knowledge goes into one draft PR per repo, taken
  to ready once BugBot is clean, never merged; cross-cutting learnings go straight to AI
  Knowledge. Bugs, security findings and cross-repo fixes become deduped Linear cards, with
  a Claude session offered for each fix a session can finish alone. Built on
  `shared:distill-session`, run as a local scheduled task. Trigger on "run the daily distill", "distill yesterday's sessions",
  "distill sessions daily", "run distill-sessions-daily", or "what did we learn yesterday
  across sessions". Do NOT use to distill the current session interactively
  (`shared:distill-session`), or to ingest a single external source into AI Knowledge
  (`shared:ingest-knowledge`).
metadata:
  version: "1.5.0"
knowledge:
  reads:
    - Guide/Index              # baseline routing aid (universal)
    - Guide/Placement Rules    # where a proposed AI Knowledge entry belongs
    - Compliance/Privacy       # redact PII in evidence quotes before any PR or Notion write
    - Tools/GitHub             # gh auth + PR conventions
    - Tools/Notion             # page edits for the AI Knowledge writes
  writes:
    - "*"   # cross-cutting: a learning lands on the Procedures/Tools/Learnings page that fits, resolved at runtime via search-knowledge
---

# distill-sessions-daily

You are the team's end-of-day note-taker. Yesterday's sessions corrected Claude, settled
conventions and found gotchas, and most of that is gone the moment the session closes.
This routine catches it while nobody is watching, so take the time to be right: a wrong
rule merged into a repo misleads every later session, and a missing one costs nothing
more than it does today.

## Goal

Every in-window session is accounted for in the run log — distilled, judged not relevant,
or skipped with a counted reason — and every learning worth keeping is either in a draft
PR on its repo or written to AI Knowledge. Every bug, security finding and cross-repo fix
the sessions surfaced is a Linear card, new or existing, and a fix a session can finish
alone has a session offered for it.

## Inputs

| Input | Default | Meaning |
|---|---|---|
| `since` / `until` | yesterday 00:00 → today 00:00 local; Friday → today on a Monday | Window for **human turns**, not files. |
| `assignee` | `@me` | Assignee of every PR and Linear card the run opens, so it lands in that person's queue. |
| `max_learnings_per_pr` | 8 | Keeps each repo PR reviewable. Extra learnings are logged as "deferred". |
| `max_cards` | 5 | Linear cards created per run, strongest first. Comments on existing cards don't count. The rest are logged as deferred. |
| `dry_run` | false | Find, triage and extract, then print the plan, including the cards and sessions it would create; no PR, no Notion write, no Linear write, no session offer, no ledger write. |

## This skill overrides `distill-session`'s approval gate — read this first

`shared:distill-session` requires the user to pick each learning before anything is
written, and never canonicalizes a rule on its own. An unattended run has nobody to ask,
so this skill replaces that gate. This is its own decision, not `distill-session`'s rule:

- **Repo knowledge → a draft PR is the gate.** Nothing lands until a person merges it.
  The routine never merges, and never pushes to a branch it did not create.
- **AI Knowledge → written directly.** Notion has no PR, so there is no gate before a
  write. The run makes up for it after the fact: each write is **additive** (a new section
  or row, never an edit or deletion of existing text), carries a dated `# Sources` line
  naming the source sessions, and is listed in the run log, so any write can be found and
  reverted.

Because the Notion branch has no gate, it holds a **higher bar** than the PR branch, and it
errs toward **not writing**. A learning goes to Notion only when it is a stated decision or
correction from the user, or a fact verified in the session (a test result, a doc read, a
command's output). An inference, a recommendation not yet adopted, or anything you are
unsure of is logged as `not written: <reason>` instead. Re-check this section whenever
`distill-session`'s Step 4 or Step 5 changes.

**Linear cards → created directly through `shared:add-linear-issues`, which owns where a
card goes** (team, parent, label, state). That skill asks the user when a parent or label
is unclear; nobody is here to answer, so where it would ask, this run logs
`not filed: <its question>` instead of guessing. A card only tracks work,
so its bar is "a session showed it", not the Notion bar — but it errs toward **commenting**
on an existing card over creating a new one. A session offer creates nothing on its own:
the user starts it, and the session it starts opens only a draft PR.

## Workflow

Scripts are invoked through the plugin root, never a cwd-relative path — the run starts
in whatever directory the scheduler gives it:

```bash
FIND="${CLAUDE_PLUGIN_ROOT}/skills/distill-sessions-daily/scripts/find_sessions.py"
HARVEST="${CLAUDE_PLUGIN_ROOT}/skills/distill-session/scripts/harvest_pr_comments.py"
```

`find_sessions.py` keeps its ledger at `~/.claude/distill-sessions/ledger.json`; the
ledger is what makes a re-run a no-op.

### 1. Find the sessions

```bash
python3 "$FIND" find [--since … --until …]
```

Its output starts with the resolved `window`. **Pass that same `--since` / `--until` to
every later `extract` call** — `extract` otherwise recomputes the default window, which
drifts from `find`'s on a custom range or across midnight and returns an empty transcript.

Returns `candidates` (sessions with ≥3 human turns in the window, under
`~/Development/Aura`, not already distilled) and `skipped` counts by reason. Sessions
with no human turns — scheduled runs, cadence sweeps, headless `claude -p`, this
routine's own runs — never become candidates. Copy the `skipped` counts into the run log
as they are; they are what shows the corpus was read in full.

**Incident sessions are never read** ([`scripts/incident_guard.py`](scripts/incident_guard.py)): a
title starting `[Privileged` counts as `privileged`; an id in `~/.claude/legal-hold/exclude-sessions`,
or any session while an incident hold is marked and that file is missing, counts as `incident`.
Neither appears in `candidates` or `skipped_sessions`, and `extract` refuses them (exit 4). Report
the two counts only, never which sessions they were.

### 2. Triage each candidate

Read the session with `python3 "$FIND" extract <session_id> --since <window.since>
--until <window.until>`: human turns in full, assistant prose truncated, tool calls as
names. A candidate with `retry: true` is one whose writes failed on an earlier run; its
extract reaches back to that run's window on its own. An empty extract for a candidate
means the windows disagree: stop and fix the arguments, never mark it as `nothing`.

Keep the session only if **both** hold:

1. **It is about Aura or Aurie work** — a repo under `~/Development/Aura`, the product,
   its data, its QA, its skills and tooling. Personal writing, generic questions and
   travel notes are `not-relevant` even when run from an Aura repo.
2. **It carries a learning that improves engineering execution or QA.** The signals,
   strongest first:
   - the user **corrected** Claude's approach, scope or output ("no, …", "don't …",
     "we always …"), especially more than once;
   - a **convention or decision** was stated or settled (naming, process, where things live);
   - a **gotcha** was found the hard way — a tool, API, simulator or build behaviour that
     cost time, with the fix;
   - a **QA finding** about how to test, not just a bug (bugs become Linear cards in step 7,
     not rules).

Note, for step 7, every **bug**, **security finding** or **fix needed in another repo**
the session surfaced, with its evidence, even when the session has no learning.

A routine task done smoothly has no learning. Record the verdict and one-line reason for
every candidate; `not-relevant` and `nothing` are outcomes, not omissions.

### 3. Extract the learnings

For each kept session, follow `shared:distill-session` Steps 2–3 for the shape of a
learning (`rule`, `evidence_quotes`, `scope`, `suggested_destination`). Sources:

- the extracted transcript;
- for each PR in the session's `pr_urls`, its review threads via
  `python3 "$HARVEST" --pr <url>`;
- commit messages on the session's branch, where it has one.

Then, across all sessions:

- **Merge duplicates** — the same correction in three sessions is one learning citing three.
- **Drop what is already written down.** Grep the destination file and the repo's
  `CLAUDE.md` / `.claude/rules/` — or fetch the target AI Knowledge page — for the rule
  before writing it. A rule that exists but was ignored is a finding for the run log
  ("ignored twice: <rule>"), not a new rule.
- **Surface conflicts, don't resolve them.** A learning that contradicts an existing rule,
  or two existing rules a session could not both follow, is a **decision needed** for the
  run log's "Needs you" block, with the question to answer — never a silent edit to
  either rule.
- **Route by scope.** `repo` → the repo's own knowledge: `.claude/rules/*.md` (closest
  file, or a new one), `CLAUDE.md` when the repo has no rules directory, or knowledge
  files the repo's `CLAUDE.md` names (e.g. AuraConsumerApp's sim-test `knowledge/`).
  `cross-cutting` → an AI Knowledge page resolved with `shared:search-knowledge` and
  `Guide/Placement Rules`.
- **A shared rule file is a layer, not a repo.** Some rule files are byte-identical copies
  across a layer and say so at the top (the backend services' `architecture`, `coding`,
  `database`… files start with *Shared by every backend service*; `security.md` and
  `logging.md` name their canonical AI Knowledge page). A general learning whose closest
  file is one of these goes into **every** repo of that layer, one draft PR per repo, with
  each repo's `.claude/rules/<name>.md` and `.cursor/rules/<name>.mdc` edited the same. When
  the file names a canonical page, the learning is a **decision needed** for the "Needs
  you" block, with the proposed text, and no repo copy changes: that page is human-curated,
  outside step 6's additive writes. A learning true of one repo only goes into its
  `.claude/rules/service-specific.md` and `.cursor/rules/service-specific.mdc`.
- **Redact** every evidence quote per `Compliance/Privacy` (UID → short prefix, email →
  domain only, no names of end users) before it goes anywhere. Strip account, org and
  project IDs, API keys and tokens too — transcripts carry them, and a PR or a Notion page
  is no place for them.

### 4. Show the plan, then act

Print the plan: per repo, the learnings and the file each lands in; then the AI
Knowledge writes and their target pages; then the Linear cards (new or `existing card
<id>`, with team and parent) and the sessions it would offer. On a `dry_run`, stop here.

Before writing anything, re-check the override section: a Notion learning that does not
clear its bar is logged, not written.

### 5. One draft PR per repo

For each repo with at least one learning, work in a worktree at
`<repo>/.claude/worktrees/distill-<date>` — never the main checkout, which another session
may be using — cut from the repo's **default branch**, which is `master` in some repos:

```bash
git -C "$REPO" fetch origin --quiet
BASE=$(git -C "$REPO" symbolic-ref --short refs/remotes/origin/HEAD | sed 's|^origin/||')
WT="$REPO/.claude/worktrees/distill-$DATE"
```

1. **Drop learnings an open distill PR already carries.** A retried session (a crash, or a
   `failed` mark) can re-surface a learning that an earlier run's PR, still unmerged,
   already proposes — and the default branch doesn't have it yet, so step 3's check misses
   it. List the repo's open distill PRs
   (`gh pr list -R <owner/repo> --state open --search "head:chore/distill-sessions-"`),
   read each one's `gh pr diff`, and drop every learning already in one, logging it as
   `already in open PR <link>`. If nothing is left, skip the repo.
2. Branch `chore/distill-sessions-<YYYY-MM-DD>`. If it already exists **on origin**, this
   run already happened: log the existing PR and skip the repo. If only the worktree or
   the local branch is left over from a run that stopped early, remove both
   (`git worktree remove --force "$WT"`, `git branch -D …`) and start clean — that run
   marked nothing, so its work is redone here. Then
   `git -C "$REPO" worktree add "$WT" -b chore/distill-sessions-$DATE "origin/$BASE"`.
3. Edit the destination files. Each added rule follows the file's existing style, states
   the current behaviour (no ticket IDs or migration prose in the rule text), and gets a
   short **Why** drawn from the evidence.
4. Commit, push, and open the PR **as a draft**, following that repo's own PR rules
   (its `CLAUDE.md` / `.claude/rules/pr-process.md`; AINative's versioning gate applies
   when a skill's files change), with `--assignee <assignee>`. Title
   `chore: distill sessions <date>`. The body lists each learning with its evidence
   quotes and source session IDs: write it to a temp file and pass `--body-file` —
   evidence quotes carry apostrophes and em-dashes that break an inline `--body`.

   A worktree lacks the main checkout's gitignored files, and a pre-push hook can fail on
   that alone (a missing hook shim such as husky's `_/husky.sh`, or missing build
   secrets). Follow the repo's own rule for that case: restore the files the way its
   `CLAUDE.md` describes, or push with `--no-verify` **only** where the repo allows it —
   AuraConsumerApp, for one, forbids it. If neither works, log the repo as failed at the
   push, with the hook's error, and leave the local branch for a person.
5. Run BugBot with `eng:bugbot-review` in **wait** mode, fix its findings, push, and
   re-run, up to 5 rounds. A run counts as clean only when all three hold on the PR's
   **current head SHA**: the `Cursor Bugbot` check-run has `status: completed`, the latest
   BugBot summary cites that SHA, and no inline finding's footer cites it. A summary for an
   older commit, or a check-run still in progress, is not clean — never mark the PR ready on
   it. Once clean, mark the PR ready. **Never merge.** After 5 rounds with findings left,
   leave it a draft and log it.

Learnings beyond `max_learnings_per_pr` are logged as "deferred", strongest first in the PR.

### 6. Write AI Knowledge

For each cross-cutting learning that clears the bar, follow `shared:distill-session`
Step 5's AI Knowledge writer for the routing:

- a **narrative convention** goes through `shared:ingest-knowledge` with
  **`unattended: true`** and the target page resolved in step 3. Unattended mode applies
  only additive writes and returns anything needing judgment under *Held for review*;
  without the flag the skill waits for approval and the run hangs. Log held chunks as
  `not written: held by ingest-knowledge — <its reason>`;
- a **structured learning or signal** goes through `shared:record-knowledge`.

Both writers apply the `Compliance/Privacy` guard themselves. Every write adds a line to the
page's `# Sources` list: `distill-sessions-daily <date> — sessions <id prefixes>`. A new section
goes above Sources and carries no "Sources:" paragraph of its own (ingest-knowledge's
`add_section` places both).

### 7. File cards and offer sessions

Turn the items noted in step 2 — and learnings deferred because they need a change in
another repo rather than a rule — into Linear cards. The placement table, card body and
session prompt template are in
[`references/cards-and-sessions.md`](references/cards-and-sessions.md).

1. **Classify** each item: bug, security finding, cross-repo fix, or needs a human
   decision. Merge the same defect seen in several sessions into one item.
2. **Dedupe** against Linear — title keywords, the file path, any PR link. A match gets
   a comment with the new evidence (none if it already cites these sessions), logged as
   `existing card <id>`. A re-run of the same window must create nothing.
3. **Create** the rest, strongest first, up to `max_cards`, through
   `shared:add-linear-issues`, passing each item's kind, repo and file paths, assigned to
   `assignee`. That skill places it — never pick a team, parent or label here. The body
   carries redacted evidence quotes, the source session ids, file paths and an acceptance
   section. Items over the cap are logged as deferred.
4. **Offer a session** with `spawn_task` for each card a session can finish alone — a
   repo change ending in a draft PR. The prompt is self-contained: card id, repo path,
   worktree, branch, the repo's PR rules, BugBot until clean on the head commit, never
   merge. No session for an item that needs a human decision (accepting a known limit, a
   scope call on a security fix): those get a card only.

### 8. Run log

Write `~/.claude/distill-sessions/runs/<YYYY-MM-DD>.md`, and end the run with the same
content as its final message.

**Lead with a "Needs you" block, before any counts.** What the assignee has to act on is
easy to miss at the bottom of a long summary, so it goes first, one line each with a link:

1. **Decisions needed**, each with the question to answer — rules that conflict (two
   rules a session could not both follow), a learning held for review, a card left
   `not filed` because `add-linear-issues` had a question;
2. **Cards created / updated**: id and link, kind (bug, security, cross-repo fix), and
   `new` or `existing card <id>` (commented);
3. **Sessions offered**: the card id and the `spawn_task` title;
4. **PRs to review**: the repo's distill PR links, and any left a draft after 5 BugBot
   rounds.

If nothing needs the assignee, the block is one line: `Needs you: nothing today.`

Then, as secondary detail:

- window and counts: candidates, distilled, `not-relevant`, `nothing`, and each
  `skipped` reason;
- per repo: the PR's learnings, one line each;
- every AI Knowledge write: page path and link, and the text added;
- learnings `not written`, with the reason; deferred learnings, and items deferred over
  `max_cards`;
- every write that **failed** (Notion, PR or Linear), with its session IDs and the error,
  and anything else that failed, with the step it failed at;
- rules that already existed but were ignored;
- cards deliberately left without a session (needs a human decision).

### 9. Mark the ledger

Only after the run log is written, mark every candidate. When its learnings all landed —
written to Notion, in a PR, or deliberately `not written`:

```bash
python3 "$FIND" mark <session_id> --through <last_turn> \
  --run <YYYY-MM-DD> --outcome learnings|nothing|not-relevant
```

When any of its writes **failed** — a PR, a Notion write, or a card create or comment
for an item it surfaced — mark it failed instead. `find` then returns it on later
runs from this window's start, after the default window has moved past it, until a run
marks it done:

```bash
python3 "$FIND" mark <session_id> --outcome failed --run <YYYY-MM-DD> \
  --retry-from <window.since>
```

A run that stops early marks nothing; the next run picks the same sessions up while they
are still in its window. A retried session may re-propose a learning that did land; step
3's "drop what is already written down" check catches it.

### 10. Raise a follow-up for every stop and every decision

The weekday routine has nobody reading the run log when it lands, so what it says only there
never reaches anyone. Every step that did not finish, and the "Decisions needed" list, becomes a
`shared:raise-followup` post. Keep an outcomes file in the session scratchpad and append one
JSON object per line as each step ends. The steps are `find`, `triage`, `extract`, `prs`,
`knowledge`, `cards`, `log` and `ledger`, numbered 1–9 above (step 4 belongs to `prs`). Status is:

- `ok` when the step finished;
- `failed` when it hit an error, including any write in it that failed, with the count in the
  reason (e.g. `{"step": "knowledge", "status": "failed", "reason": "2 Notion writes failed (HTTP 502)"}`);
- `blocked` when it could not start (a missing token, `gh` unauthenticated).

When the "Decisions needed" list is not empty, also append
`{"step": "decisions", "status": "gate", "reason": "<n> decisions need you: see run log <YYYY-MM-DD>"}`.
Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/distill-sessions-daily/scripts/outcomes.py" \
  --cadence distill-sessions-daily --session "${AINATIVE_SESSION_NAME:-scheduled}" \
  --in "$SCRATCH/distill-outcomes.jsonl" --due find,triage,extract,prs,knowledge,cards,log,ledger
```

A step with no entry counts as `stopped`, and a missing outcomes file means nothing was
recorded, so a run that stops early still reports the steps it never reached. The script prints
one object per follow-up: pass each to `shared:raise-followup` as is. A stop is `blocked` with a
card; decisions are one `decision` post keyed `distill-sessions-daily:decisions:stop`, so each
day's list arrives as a reply in the same thread. Cards created, sessions offered and PRs to
review stay in the run log; they are reports, not stops. Exit 0 means nothing to raise. Run
this step **before the turn ends, however it ends**, including after a run that stops early.
Skip it on a `dry_run` (it stops after step 4 by design, so every later step would read as
stopped) and on an interactive run with someone reading the run log.

## Don't

- Don't edit or delete existing AI Knowledge text — add only. Don't merge a PR, or push to
  a branch this run did not create.
- Don't run in a repo's main checkout — always a worktree.
- Don't turn a bug into a rule. Bugs become cards, never rules.
- Don't create a card that duplicates an existing one — comment on it instead. Don't offer
  a session for an item that needs a human decision.
- Don't distill a session's out-of-window turns — they were (or will be) distilled by
  their own day's run.

## Pairs with

- `shared:distill-session` — the single-session, interactive version; owns the learning
  shape and the PR-comment harvester this skill reuses.
- `shared:search-knowledge` — resolves the AI Knowledge page each learning lands on.
- `shared:ingest-knowledge` / `shared:record-knowledge` — the AI Knowledge writers.
- `eng:bugbot-review` — the BugBot trigger used in step 5.
- `shared:add-linear-issues` — the Linear writer used in step 7.
