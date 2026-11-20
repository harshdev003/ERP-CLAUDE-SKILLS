---
name: report-growth-roi
description: >
  Produces the weekly Growth ROI insight note for Aura or Aurie from its Growth tracker
  sheet. Reads the week that just closed, separates genuinely off-band moves from normal
  ones, explains each by its mechanism rather than its level, cross-references live A/B
  experiments and open bugs, drafts the Slack post in the house format for review, and
  closes with a mandatory retro. Trigger on "growth ROI notes", "weekly
  growth ROI", "write the growth ROI post", "Aura growth ROI insights", "Aurie growth ROI
  notes", "what changed in the growth sheet", "insights from the growth sheet", "weekly
  growth insights", "draft the growth note", "CPT and CAC for last week", "what is off in
  the growth numbers", "growth sheet readout", "weekly growth summary", or "why did CAC
  move". Do NOT use for the daily automated sheet write (Viktor's 6am cron), a single
  experiment readout (`data:analyze-experiment`), or the running-experiment board
  (`data:monitor-all-experiments`).

metadata:
  version: "1.10.0"
knowledge:
  reads:
    - Guide/Index
    - Tools/Google Sheets
    - Tools/Google Sheets/Google Sheets (API access)
    - Tools/Slack
    - Tools/Linear
    - Tools/Mixpanel
    - Procedures/Marketing
---

# Report Growth ROI

## Goal

One short post that tells the growth team what actually changed last week and
**why**, in the author's own format: three to five bullets, each a one-line
headline with its supporting figures nested under it, every figure
and every reason read off the growth sheet, every claim carrying the week it
belongs to, and every anomaly judged against that metric's own history rather
than intuition.

The failure mode this skill exists to prevent is a note that reports levels
correctly and causes not at all. That note reads fine and generates a round trip:
"are there any more notes on what caused the CPT or CAC rise?" The answer goes
into the post as the sheet numbers that show it — spend flat, trials down, one
channel's CPT up. The work behind that answer (the decomposition, the
confounders, Viktor's cross-check) is how the run gets the reading right, and it
is reported in chat for the author rather than in the post.

## What this skill owns, and what it does not

| Owns | Does not own |
|---|---|
| The weekly human insight note, Aura and Aurie | The daily automated sheet write — Viktor's 6am PT cron |
| Deciding which moves are worth reporting | Filling the sheet's cells — the owner refreshes the tabs |
| Naming the mechanism behind the headline move | A single experiment's readout — `data:analyze-experiment` |
| Flagging live experiments and bugs as confounders | The running-experiment board — `data:monitor-all-experiments` |
| Putting the claims to Viktor before they go out | Viktor's own ad-account analyses — his to run |
| Creating the run's Linear card | `product:yawar-create-weekly-cards` files it each week — this skill only fills it in and closes it |
| Its own gradual improvement (Step 7) | Cost rows — `eng:report-aurie-weekly-costs` |

## Inputs

Nothing required. Optional:

- **App** — `both` (default), `aura` or `aurie`. The default runs the contract
  twice and posts two separate notes to two separate channels; it never merges
  them. The author expects both notes every Monday, so a single-app run is for a
  re-run or a correction, not the weekly cadence.
- **A week** — defaults to the most recently closed Sun–Sat week.
- **`--no-post`** — draft and report in chat, skip the Slack post.

## Workflow

### Step 0 — Load the contracts

Read [`references/sheet-contract.md`](references/sheet-contract.md) (which sheet,
which tab, how the columns fill) and
[`references/note-conventions.md`](references/note-conventions.md) (the post
shape and the destination channel) **before** reading any data. Both encode
traps that produce a confidently wrong note if skipped.

### Step 1 — Resolve the week and read the sheet

Weeks are **Sunday–Saturday**. The reportable week is the one that closed on the
most recent Saturday. Resolve the row by matching the week label in column A,
never by counting rows — the Aura tab carries label typos (the 8/30–9/5 row reads
`8/30 - 8/5`) and a counted offset lands silently on the wrong week.

Pull the target week **plus the ten preceding weeks** — eleven rows. A band needs
eight prior weeks to mean anything, and the late columns start one or two rows
back: most of them fill a week late (the two-speed fill in
`references/sheet-contract.md`), and Aura's `Paid -> Cancellation (7D)` columns
(`AQ`, `AV`) fill two weeks late. A shorter pull leaves those with seven or fewer
prior weeks and they degrade to `insufficient-history`. Eleven rows gives every
metric a band regardless of which week its newest value belongs to.

Read row 1 and row 2 in the **same range call** and anchor every column on row 2.
Row 1 carries section labels that are offset from row 2 in places, so a column map
built from row 1 alone is wrong. Full layout: `references/sheet-contract.md`.

**Then diff the sheet against what last week's note published, before drafting.**
Pull the prior note from the app's channel with `shared:read-slack`, bounding the
read to the ten days before this run rather than paginating the whole channel.
That window can hold more than one note — a midweek run spans two Mondays, and a
re-run spans this run's own post — so select the one whose quoted weeks are the
previous run's rather than taking the newest. Check every figure it quoted against
the cell that holds it now. A column gets re-pulled or
redefined between runs, and the changed value reaches the note as a fake trend:
an Aura run read mobile registration → trial as having matured `+2-4pp` when the
CR reports had simply been rebuilt, and built a bullet on the wrong cause.

A re-pull is a **definition change**, not maturation, and the two need different
handling — maturation means wait, a definition change means the series either was
restated end to end or now has a seam in it. Ask the sheet owner which; they made
the change and the answer is not recoverable from the cells.

Where a restatement touched several columns in one pass, a **sibling column is the
control**: if one moves sharply across a week boundary while another from the same
pass is flat across it, the move is real rather than an artifact of the seam. That
is what separated a genuine 16pp web registration → trial break from the CR rebuild
that hit mobile and web together.

### Step 2 — Separate normal from off-band

Do not eyeball this. Hand the series to the script:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-growth-roi/scripts/flag_anomalies.py --file week.json
```

It returns, per metric: the latest value, the WoW delta, the trailing band from
the prior weeks, a z-score, and a verdict of `in-band`, `high`, `low`,
`window-high` or `window-low` — sorted by how far out of band the latest reading
is. That ranking is the note's agenda.

`window-high` and `window-low` are relative to the trailing band only. A claim
that a reading is a record, a high for the year, or the lowest in the sheet is a
separate check against the **full column**, and it fails often enough to be worth
running every time.

A metric inside its own band is **not** a finding, however uncomfortable its
absolute level. Aura blended CPT has sat at \$28–34 for most of 2026 against a
sub-\$20 target; reporting "\$31.3, well above target" every week is noise. What
earns a bullet is a reading outside its band, or a run of consecutive moves in
one direction.

### Step 3 — Explain the headline move by its mechanism

For each finding that survives Step 2, decompose it before writing it up. A cost
metric moved for one of two reasons, and the note has to say which:

- **Cost side** — spend rose, or the channel mix shifted toward a more expensive
  channel, or a channel's own efficiency degraded.
- **Conversion side** — the same traffic converted at a different rate, at a
  named step of the funnel.

Blended CPT can hold flat while both paid channels get cheaper, if unpaid trials
fell by roughly the amount paid trials rose. That decomposition *is* the finding;
"CPT flat" on its own hides it. Reconcile the arithmetic before reporting: paid
trials plus organic-and-other must equal total trials.

Then localise it — which surface (web or mobile), which channel, which funnel
step. A finding that names no surface is not yet finished.

The decomposition is for getting the reading right. The post carries only the
parts of it the sheet itself shows — spend, trials, a channel's CPT, a CR column —
as a short clause on the bullet. On Aura, spend and trials appear only as words in
that clause ("spend went down but trials fell faster"), never as figures. The
arithmetic, the channel split and anything sourced outside the sheet go in the
chat output.

### Step 4 — Cross-reference confounders

Before attributing any move to performance, work through
[`references/confounders.md`](references/confounders.md). It covers the five traps
that have already produced wrong readings:

1. **A live price or pricing-mix test moves sub counts and attach rates by
   design.** When one is running, the count and the attach percentage are the
   wrong metrics; pair them with ASP for the same week and say plainly that the
   result needs another one to two weeks before it can be read.
2. **Never inherit another product's exclusion window.** Check a Linear ticket's
   `project` field before applying an Aurie bug's excluded dates to Aura numbers,
   or the reverse.
3. **Test and QA traffic lands in the organic bucket.** An end-to-end suite signs
   up and converts like a user with no install source. Check the day-level shape
   of any organic jump before reporting it.
4. **A diagnosis found in chat is a lead, not a fact.** It carries no data behind
   it and the thread that produced it often revises it. Carry it into Step 4b
   rather than into the note.
5. **A documented incident outranks a fresh theory.** Before naming the mechanism
   behind a cost, revenue or margin move, search `#ai-cost`, the app's analytics
   channel and recent Linear issues for the incident that already explains it,
   and take the cause from there. Viktor's theories are leads under rule 4 too.

Confounders go in **the chat output**, listed with the ones ruled out. The post is
built from sheet data only, so a confounder reaches it only when the author asks
for one — and then as its own closing bullet, never folded into the metric bullet
it would explain, so the reader sees the observation and the caveat as two
separate claims.

### Step 4b — Put the claims to Viktor

Viktor holds the ad-account, Mixpanel and App Store data the sheet only
summarises, so a claim about *why* a paid metric moved stays unverified until he
has seen it. Post via `shared:post-to-slack` with `--as-user` to `#bot-commands`
(`C0B23N0KM38`), and read his replies with `shared:read-slack` — the same posting
layer Step 6 uses, so attribution and channel resolution stay consistent. Send a
numbered list of the specific assertions with their figures, never the draft note,
which draws editorial feedback instead of mechanism. Ask for agree/disagree plus
reasoning, and say that "the data doesn't support a call either way" is an
acceptable answer.

This one needs no approval gate: it goes to an internal working channel rather
than to the founders, and it runs under `--no-post` too, since the check is what
makes the draft correct.

Send the three to five claims whose answer would change a bullet. He acknowledges
within seconds and returns the analysis several minutes later, so poll the thread
patiently rather than timing out on him.

Treat the replies as evidence, not verdicts. He labels his own confidence per
finding, and he has ranked a hypothesis at 85% and then retracted it in the same
thread — so where he disagrees, take the reasoning and check it. Give him sheet
IDs, tabs and column letters rather than relaying figures on his behalf: he reads
the sheets himself, and a number that reconciles to neither source is a finding in
its own right.

His answers correct the draft and the chat output; his ad-account and Mixpanel
figures do not go into the post. When he says the newest week will revise, check
the sheet's own history before repeating it: the previous run's newest row is
still in the sheet, so compare what was quoted then with what the cell holds now.

The protocol and the failure modes it catches: [`references/confounders.md`](references/confounders.md).

### Step 5 — Draft the note, and get approval

Draft to the shape in [`references/note-conventions.md`](references/note-conventions.md)
— three to five bullets, each a one-line headline with its figures nested beneath
it, growth-sheet data only — and show it in chat.
**Do not post without approval** — the note goes to the founders under the owner's
name, and the house format has judgement in it that this skill will sometimes get
wrong.

Three rules that are easy to violate while trying to be helpful:

- **Sheet data only.** Every figure and every reason in the post is a cell in the
  growth sheet. An explanation from Viktor, a Linear incident, the experiment board
  or a Mixpanel pull goes in chat, where the author can choose to add it.
- **An open question stays open.** Where the cause is genuinely unknown, keep it
  as its own bullet. Do not substitute a guess, and do not silently replace the
  author's open question with an answer found in Step 4 — surface the answer
  separately and let the author decide.
- **Cut level-and-trend-only bullets.** A spend total or a channel-mix percentage
  with no decision attached does not earn a line.

### Step 6 — Post

Post to the app's channel via `shared:post-to-slack` **with `--as-user`**. Aura
goes to `#data-analysis`, Aurie to `#ai-analytics`; cc the two recipients named
in `references/note-conventions.md` as real mentions so they are notified. Write
the bullets as a markdown list so the nesting renders (note-conventions.md →
Shape). Return the permalink.

`--as-user` is not optional here. The note is founder-facing and goes out under
the owner's name in their own voice — the `username`/`icon_url` bot path still
carries an "APP" badge, which contradicts the attribution.

**No skill-written footer.** Like `shared:daily-status`, this skill does not
append the `Tools/Slack` post-format footer: the integration that posts on the
user's behalf auto-appends its own signature, so a `--footer` here produces a
duplicated tail on a post that every historical note in both channels ships
without. Pass no `--footer`, `--footer-skill-md`, or `--footer-version`, and end
the message body at the last content line. This is a deliberate exception to the
footer default, not an omission.

### Step 6b — Close the run's card in Linear

This step records a run that actually happened, so it follows the post rather
than replacing it: under `--no-post`, or when the author declines the post in
Step 5, nothing is recorded and the step is skipped.

**The card normally exists already; never create a second one.** `product:yawar-create-weekly-cards`
files one card per app every week, titled
`Update and report the {Aura|Aurie} Growth ROI sheet (week of M/D-M/D)`, under the
current cycle's `Sheet Update/Creation/Changes [Cycle N]` tracker on `Product/Design`
with an `## Update` and a `## Report` checklist. This step finds that card, writes the
run onto it, and marks it Done — one card per app, never a combined "Aura + Aurie" card.

**Find it — in the cycle that opens after the reported week closes.** The card carries the
week it reports on, and is filed in the cycle that opens the Monday immediately after that
week ends — normally the same Monday this note runs. Take the reported week's Saturday + 2,
fetch `list_cycles` with `type: current`, `type: previous` and `type: next` on
`Product/Design`, and pick the cycle whose `startsAt` calendar date equals that Monday — on a
Monday run that is `current`, on a run made the Sunday before it is `next`.
Never pick by which umbrella exists, and never by cycle number. Page
`list_issues(cycle: <id>, team: "Product/Design")` to exhaustion, match
`Sheet Update/Creation/Changes [Cycle N]` by title, list its children, and match the card
by this app's title and the reported week's label (a suffixed title counts). If it is not
there, check that same cycle's `Data Investigation [Cycle N]` as well — cards filed before
2026-09-22 sat under that tracker.

If it is still not there, look in the **previous** cycle, the one whose Monday falls inside
the reported week. Cards filed before 2026-09-23 were created at the *start* of the week they
report on rather than after it closed, so they carry the right title one cycle early.

**Update it, then close it — one concern per call.**

1. `save_issue(id, patch: [...])` — tick the `## Report` items the run completed
   (`replace` `- [ ] <item>` → `- [x] <item>`), and `append` a `## Run <YYYY-MM-DD>`
   section carrying the Slack permalink, the headline finding, the corrections Step 4b
   produced, and any open follow-ups as unchecked boxes. Use `patch`, never a full
   `description`: the `## Update` section is `data:update-growth-roi-sheet`'s record and a
   full overwrite erases it.
2. `save_issue(id, state: "Done")` — a separate call. Passing `state` alongside `patch`
   has silently no-op'd label and parent writes on other skills; keep them apart.
3. Read back `completedAt`, `parentId` and `cycleId` on the response. Linear moves a card
   to the cycle it was completed in; for a card filed in the cycle that opens after the
   reported week that is the cycle it is already in, so the move is a no-op. For an older
   card found via the previous-cycle fallback it is not: if `cycleId` changed, set `cycle`
   back in one more call so the card stays on the board it was filed on. The same happens
   to a card closed before its cycle has started — a Sunday-night run closes a card filed
   in the cycle that opens the next morning, and `Done` files it under the running cycle —
   so apply the same fix there and read `cycleId` back once more.

**Only if no card exists for this app and week**, create it with the roster shape — the
title above, parent `Sheet Update/Creation/Changes [Cycle N]` in the cycle that opens after
the reported week closes,
label `Analytics & Data`, estimate 5, no priority, assignee the user, and the roster's
description with its `## Update` and `## Report` checklists (the Growth ROI templates in
`product:yawar-create-weekly-cards`) — then update and close it the same way, and say in
the report that the weekly card was missing. A run made before that cycle opens normally
finds no card, because the weekly cards are filed on the Monday; creating it is safe, since
`product:yawar-create-weekly-cards` skips any title that already exists under its parent. Do not
trust `cycle` on that create: the reported week's cycle has usually ended, and Linear can
drop an ended cycle on create and assign the live one without erroring. Read `cycleId`
back; if it is not the reported week's cycle, set `cycle` to that cycle's id in a separate
`save_issue(id, cycle)` update, read it back again, and if it still did not move report
the card as sitting on the live cycle rather than claiming it was pinned. A re-run in the
same week appends a second `## Run` section and leaves the card Done.

### Step 7 — Retro (mandatory, never skipped)

The run is not finished when the note is posted. Close every run by working
through [`references/retro.md`](references/retro.md), which asks four questions:
what did this run get wrong, what did the contract miss, what was reported on
incomplete evidence, and what one concrete edit would make the next run more
correct.

Report the retro in chat every run. When it finds a **repeatable** defect — one
that would recur next week rather than a one-off — route the fix through
`shared:update-aura-skill`, which owns the version bump and the PR. Edits the
author makes to the draft in Step 5 are the highest-signal input the retro has:
every cut bullet and every rewritten line is a convention this skill did not yet
encode. Carry them into `references/note-conventions.md`.

A retro that produces no finding for several consecutive runs is itself a
finding: the checks have gone stale and need sharpening.

## Output

1. The drafted note, ready to post, in the house format.
2. The off-band table from Step 2 — metric, latest, band, verdict — so the author
   can see what was considered and rejected, not only what made the note.
3. The confounder list from Step 4, including the ones ruled out and why, and anything
   else kept out of the post (Viktor's corrections, explanations from outside the sheet)
   so the author can add it.
4. The Slack permalink once posted.
5. The Linear card the run closed — identifier and state, or that it had to be created.
6. The Step 7 retro, always.

## Edge cases

- **The newest row is only half filled.** Expected, not an error — the two-speed
  fill in `references/sheet-contract.md` means cost metrics land a week before
  conversion metrics. Report each figure against the week it belongs to and say
  which columns are still pending. Never read a blank as a zero.
- **A prior week's numbers have changed since they were last reported.** Spend
  and sub counts get reconciled after the fact. If a figure reported last week
  now reads materially differently, say so in one line and move on — a silent
  revision makes every trend in the note unfalsifiable.
- **The sheet has not been refreshed yet.** If the target week's row is entirely
  empty, stop and say so. Do not fall back to the prior week and present it as
  current.
- **A metric has fewer than eight prior weeks** (a new column, a new channel) —
  report the delta only, and say the band is not yet established. Do not compute
  a z-score off three points.
- **Running for both apps (the default).** Two notes, two channels, two Linear
  cards, two retros. The apps have different funnels and different confounders;
  a merged note serves neither.
- **Re-run in the same week.** Recompute and update the existing post rather than
  adding a second one, unless the author asks for a fresh post.

## References

- [`references/sheet-contract.md`](references/sheet-contract.md) — the two sheets, their tabs, the column anchors, the two-speed fill rule.
- [`references/note-conventions.md`](references/note-conventions.md) — post shape per app, the sheet-data-only rule, destination channel, recipients, what gets cut, worked examples for both apps.
- [`references/confounders.md`](references/confounders.md) — checking live experiments and open bugs, the cross-product exclusion trap, test-traffic contamination, and the Viktor cross-check protocol.
- [`references/retro.md`](references/retro.md) — the Step 7 checklist and known defect classes.
