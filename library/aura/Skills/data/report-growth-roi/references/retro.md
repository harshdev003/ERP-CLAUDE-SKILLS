# Step 7 — the retro

Every run ends here. The point is not to praise the run; it is to find the one
thing that would make next week's note more correct, and to make that change
cheap enough that it actually happens.

This skill is read by the founders every week under someone else's name. A defect
that survives a run gets repeated fifty times a year.

## The four questions

**1. What did this run get wrong?**

Compare the figures reported against what the sheet says now, and against what
last week's note claimed. Three specific checks:

- **Did a `W-1` figure move since it was reported?** Step 1 runs this diff before
  drafting, so by the time the retro asks, a surprise here means the Step 1 check
  was skipped rather than that a number moved late. Record which.
- **Was a moved figure attributed to the right cause?** A reconciliation and a
  column re-pull look identical in the cell and need opposite handling. If a
  revision was called maturation without asking the sheet owner, that is the
  finding.
- **Did a claimed record survive?** "Lowest all year" is falsifiable. Re-check it
  against the full column, not the eight-week window — a band is not a record.
- **Was a within-band move reported as a finding?** If so, the band computation
  was ignored or the threshold is wrong. Say which.

**2. What did the contract miss?**

List every column and every external source used this run. Any one not described
in `sheet-contract.md` or `confounders.md` is an undocumented dependency: next
week's run will not reproduce it and a reader cannot audit it. Either add it to
the reference or stop using it.

Column letters drift as sections are added — if an anchor in `sheet-contract.md`
did not resolve to the header it claims, fix the reference this run, not next.

**3. What was reported on incomplete evidence?**

Name every claim that rested on a half-filled column, a metric with fewer than
eight prior weeks, a channel with a single-digit denominator, or a confounder
that could not be verified. A claim is allowed to rest on thin evidence — it is
not allowed to do so silently.

**4. What should change in this skill?**

One concrete, minimal edit. "Be more careful about organic" is not an edit. "Add
the survey-answered-rate degradation trap to `sheet-contract.md`" is.

### The highest-signal input: the author's edits

Every change the author made to the draft in Step 5 is a convention this skill
did not encode. Diff the posted note against what was drafted and classify each
change:

| Author's edit | What it means |
|---|---|
| Deleted a bullet | That class of observation does not earn a line — add it to the "Does not" table in `note-conventions.md` |
| Rewrote a line | The phrasing or framing convention is wrong — update the shape section |
| Added a bullet | A finding this skill missed — work out which step should have caught it |
| Reordered bullets | The importance ranking in Step 2 disagrees with the author's |
| Kept something this skill proposed replacing | A judgement rule to record verbatim, not to reason about again |

Recorded this way so far: spend-streak bullets get cut; the author keeps their own
open question rather than letting the confounder answer replace it; the post is a
handful of one-line bullets built from growth-sheet data only; WAL / WAPU moves
never earn a line; an off-band Aura New Subs move leads the note; and the Aurie note
carries milestones and a closing sheet updates bullet. All of them live in
`note-conventions.md`.

## Routing the finding

| Finding | Action |
|---|---|
| Would recur next week | Route to `shared:update-aura-skill` with the specific edit |
| One-off, environment or data hiccup | Report in chat, no PR |
| Instrumentation or sheet-formula gap | Report it; the fix is a ticket or a sheet edit, not a skill change |
| Disagreement with a daily automated report | Report both readings and which is more conservative; never silently overwrite |
| Names a defect that is still live | The entry carries a card link or a closure note — a retro entry is not a record |

**A retro entry that names a live defect is not a record of it.** A retro is read
by a human, later, about that one run; a card is worked. A sibling
skill's retro named a live onboarding split by id, wrote the correct guard, and
the row went on assigning real users for months. So an entry naming
something still live carries **either** a card link **or** a closure note saying
it is fixed and where. Neither present means this entry is the only place the
defect exists, which is the failure the entry is describing. See CLAUDE.md,
*A finding about the tool belongs in the tool, not in the output*.

## Known defect classes

Patterns already observed. Check each run against them.

**Reporting a level instead of a mechanism.** The founding failure mode. Symptom:
a follow-up question asking what caused it. Guard: Step 3 is not optional, and a
finding that names no surface is not finished.

**Mixing two weeks without labelling them.** The two-speed column fill makes this
the easiest mistake in the skill. Symptom: a note where CPT and CAC appear to
describe the same seven days. Guard: label the week on every conversion or
revenue figure.

**Reading a blank as a zero.** A not-yet-loaded column looks like a collapse.
Symptom: an alarming drop in exactly the columns that fill a week late. Guard:
the fill table in `sheet-contract.md`.

**A confounder reported as performance.** A price test's effect on sub counts
presented as a conversion regression. Guard: `confounders.md` §1 before Step 5.

**Inheriting another product's exclusion.** Applying an Aurie bug's excluded
dates to Aura. Guard: read the Linear ticket's `project` field.

**A ticket's creation date mistaken for its incident window.** A backlog
investigation opened this week about errors from two months ago explains nothing
about this week. Guard: read the description, not the timestamp.

**A rejected build treated as a shipped defect.** An App Store rejection blocks
the release; the broken build never reached users. Guard: check whether the build
shipped before letting it explain a number.

**A band computed on too few points.** A z-score off three observations. Guard:
eight prior weeks minimum, and report the delta only when the history is shorter.

**A record claimed against the window instead of the column.** "Lowest in the
sheet" verified against eight weeks. The script's `window-high` / `window-low`
verdicts are band-relative and say nothing about the full history. Guard: check
the full column before writing "all year" or "in the sheet".

**A sheet figure an independent source contradicts by an order of magnitude.**
Treat it as a data error until proven otherwise, rather than as two views of one
truth. Symptom: no definitional difference accounts for the size of the gap.
Guard: escalate to the sheet owner before publishing either number — reporting
both and letting the reader choose still puts a wrong figure in front of the
founders. A family attach cell read `4.6%` on 11 subs while an independent Stripe
cut showed a noisy `0-8%` band; the cell was wrong and now reads `31.7%` on 76,
by which point two consecutive notes had published it as a record low.

**A cost cell its own source contradicts by tens of percent, posted with a caveat.**
A gap that size is a wrong cell, not a measurement nuance, and every figure derived
from it is wrong too. Symptom: a "reconciling" or "confirming" caveat on a headline
built from that cell. Guard: the owner corrects the cell before the post, and the
note carries the corrected figure. The 2026-10-12 Aurie note shipped margin `-17%`
from an LLM total of `$8,611` that the OpenAI Costs API put at `$6,443` (+33.7%).

**Column anchors resolved from row 1.** Row 1 section labels are offset from row 2
headers in places, so the map is wrong by one or more columns and the numbers look
plausible. Guard: read both rows in one call and anchor on row 2.

**A week resolved by row arithmetic.** Week labels contain typos, so counting rows
lands on the wrong week silently. Guard: match on the start date in column A.

**A paused channel's zero reported as a finding.** Everything right of the
`Inactive >>` marker is off. Guard: the paused list in `sheet-contract.md`.

**Inflating a small move to fill the note.** A four-bullet note is a good note. A
within-band move written up as a finding costs the author credibility with the
readers who matter. Guard: fewer bullets is always allowed.

**A row padded from the wrong end.** The Sheets API trims trailing blanks, so rows
differ in length; padding one incorrectly shifts a whole section sideways while
every value still reads plausibly. Symptom: a block whose numbers are the right
shape but attached to the wrong labels. Guard: pad to header width and verify a
known identity before trusting a block.

**A chat diagnosis reported as mechanism.** An explanation lifted from Slack, or
from a subagent summarising Slack, is a hypothesis rather than evidence, and the
thread often revises it. Guard: `confounders.md` section 7, and Step 4b before it
reaches the note.

**A long decomposition post.** Every mechanism the run found, written into the post
as sub-bullets. Symptom: the author asks for it "a lot shorter" and points at their
own past posts. Guard: `note-conventions.md` → rule 2; the decomposition stays in
chat.

**A move explained with a source outside the sheet.** A Viktor ad-account figure, a
Linear incident, an experiment-board confounder or a Mixpanel pull used as the
"why" in the post. Symptom: "Why are you putting that there? Just use the data from
the growth sheet." Guard: `note-conventions.md` → rule 1; outside evidence goes in
the chat output.

**A routine decline reported as a finding.** A column that falls almost every week
flags off-band on a big week and reads like news. WAL and WAPU are the known cases.
Guard: the "Does not" table in `note-conventions.md`.

**A revision caveat taken on trust.** "The newest week will revise down" repeated
from Viktor when the sheet's own history says otherwise — the previous run's
newest-week figures still sit in the sheet, unchanged. Guard: `confounders.md` →
Working with Viktor; compare before repeating.

**Supporting figures packed into the headline line.** Four numbers and two
arrows on one bullet. Symptom: "should be in more bullet points nested, no?".
Guard: `note-conventions.md` → Shape; the headline carries the move and the
plain-English why, each figure gets a sub-bullet.

**Column letters trusted over row-2 names.** The Aurie grid gains and loses
columns, and a one-column shift leaves every value plausible. Symptom: a block
whose numbers are the right shape under the wrong labels. Guard: resolve every
letter from its row-2 name each run (`sheet-contract.md` → Aurie column anchors).

**A single-app run on a Monday.** The author expects both notes; a run that
stops at Aura produces "where is the Aurie draft?". Guard: `both` is the default
(`SKILL.md` → Inputs).

## When the retro finds nothing

Several consecutive clean retros mean the checks have gone stale, not that the
skill is finished. Sharpen one: add a defect class from a recent surprise, tighten
a threshold, or record a convention from the author's most recent edit. Record
that as the run's finding.
