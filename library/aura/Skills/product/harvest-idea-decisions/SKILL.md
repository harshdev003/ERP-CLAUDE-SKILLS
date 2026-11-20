---
name: harvest-idea-decisions
description: >
  Read human decisions out of the Slack idea threads and write them onto the
  `Learnings/Loop/{Loop}` Notion rows — status, reason, who decided and when. Sweeps the
  loop channel (and the older `#ai-ideas` archive, by following each row's own thread
  URL), classifies reactions and replies, and writes only the verdicts it can read with
  confidence; anything ambiguous comes back as `needs_judgment` for Claude to settle. An
  unanswered question in a thread comes back as `needs_answer` — Claude answers it in-thread
  from the priors (backlog, experiments, collision set) without ever deciding the row.
  Runs on demand or as a scheduled tick — a quiet channel is a zero-write no-op. Use when
  the user says "harvest the idea decisions", "check Slack for idea approvals", "did
  anyone approve an idea", "answer the questions on the ideas", "sweep the loop channel",
  "pull the Slack decisions into Notion", or "run the idea harvest". Default target is the
  Aurie Features database; pass `--data-source-id {id}` for the other loops.

metadata:
  version: "1.14.0"
knowledge:
  reads:
    - Guide/Index
    - Learnings/Loop/Aurie Features Ideas
    - Learnings/Experiments
    - Tools/Notion
    - Tools/Slack
  writes:
    - Learnings/Loop/Aurie Features Ideas
---

# harvest-idea-decisions

Closes the Slack → Notion gap. `share-ideas` posts a pending row to the loop channel; the team reacts or replies; this reads that back onto the row so the backlog reflects what the team actually decided.

Two layers, the same split `manage-ideas` uses:

1. **Mechanical** ([`scripts/harvest_idea_decisions.py`](scripts/harvest_idea_decisions.py)) — fetch each thread, classify what is unambiguous, write it. Deterministic and unit-tested.
2. **Judgment** (this file, executed by Claude) — settle the threads the script hands back as `needs_judgment` using the review's own criteria rather than a keyword list, and answer the ones it hands back as `needs_answer` from the priors, in-thread, without deciding.

## The rule that matters: a thumbs-up is not an approval

The reaction rules are asymmetric on purpose.

| Signal | Verdict |
|---|---|
| ✅ `white_check_mark` | approved |
| ❌ `x`, 👎 `-1` | rejected |
| 👍 `+1`, ❤️, 👀 | **sentiment only — never decides anything** |

That asymmetry is not fastidiousness, it is what the archive shows. Of the 22 ideas shared to `#ai-ideas` in April 2026, nine drew a 👍 and **five drew a 👍 and nothing else**. Two of those five were later answered the other way by an experiment: `aiTestimonials` lost (paid cancellation +55% at 95%), and the save-to-collection half of `2990424c412f` was settled by `bookmarkChat` ending on control. A classifier that read 👍 as approval would have queued both for build.

The costs are asymmetric too. A wrong rejection is recoverable — it lands in the database with a reason attached, and the next ideation pass reads it. A wrong approval spends engineering weeks. So approval demands the stronger signal.

### In a loop channel, a hedge never approves — and a commitment does

Two real replies, one day apart, are the whole rule:

| | |
|---|---|
| *"i would like to test `d` … and yes we can test sharing as an image"* | **not** a decision |
| *"Let's go with a only since other two are too messy"* | a decision |

They are a clause apart. The first is somebody thinking out loud in a thread; the second
picks an arm and commits to it — and each author said so themselves, *"I just want to make
sure this is not considered an approval"* and *"'Let's go with a' is like an approval"*.

So the discriminator is the **hedge**: *I would like*, *we could*, *not sure*, *suggest*,
*maybe*, *what if*, *should we*. With one, a reply that would otherwise read as yes comes
back as `needs_judgment` carrying the quote and saying what to write instead. Without one, a
commitment decides and the row's status says which gate it answers.

A hedge cannot veto an approval that **names its gate** — *"I approve for implementation,
though I could be wrong about the placement"* is still an approval.

Outside the loop channels none of this applies. Those threads are the April archive, where
the same words meant what they said, and re-reading them under today's rule would rewrite
decided history — the same reason a check mark means *approved* there and *done* here.

The strictness is one-directional. A generic **no** still rejects: nobody writes "not doing
this" by accident, and the costs settle it — a missed rejection re-proposes an idea, an
invented approval starts work nobody asked for.

### One person decides, and only they do

`DECIDER` names one person per loop channel. Their reply decides; **everyone else's is
input**. Because of that, the hedge rule above applies to everyone *except* them: it
exists to stop a bystander's musing becoming a verdict, and the person whose job is to
decide is not a bystander. Steve, whose channel it is: *"'sounds great' from a decision
maker is a move forward."*

So the decider's plain sentiment decides — **both ways**. *sounds great*, *looks good*,
*works for me* approve; *suggest skipping*, *let's skip*, *not keen* and a bare *skip* or *pass* (a statement, never a question) refuse. A bare *skip* from anyone else stays input. Hearing only
the yes would be a rule that can approve and never refuse.

Genuine uncertainty still does not decide, even from them: *not sure*, *could be*, *what
if*, *should we*. A decider asking a question is asking, not answering.

The soft signals — the decider's sentiment and a commitment phrase — are bounded four
ways:

- **A question never decides softly.** Both are read from the reply's *statements*
  only: each clause that ends in `?` or opens on an interrogative is set aside first.
  *"Looks good?"* and *"which one are we going with?"* leave nothing to read. *"Let's go
  with a. Can we ship this week?"* decides — a reply that decides and then asks has
  decided. *"Would love to create a card"* is a statement: a dropped-subject opener, not
  a question.
- **A commitment is a whole word.** *let's go with* is a prefix of *let's go without*;
  every alternative closes on a word boundary.
- **Every commitment in a reply is scored.** *"Not going with a, let's go with b"* is a
  choice of b; a reply is a no only when every commitment in it is negated. A choice
  also outranks the decider's doubt about another option: *"I don't think a would work,
  let's go with d"* is a yes to d.
- **A verdict phrase that lands on a design is not a verdict on the idea.** When a
  commitment or an approval takes a design noun as its object — *"let's go with a
  different placement"*, *"let's go ahead with a smaller badge"* — it chooses the
  picture, which is the redesign ask itself, so it carries no verdict and never shadows
  it. Only a phrase with an **object slot** can land on one — *go ahead with*, *go
  with*, *let's build* — and the noun has to fill it: inside the clause, close by, with
  no comma or subordinating conjunction in between. A coordinator does **not** end the
  object, which has one accepted cost: *"let's go with a and the layout is fine"* comes
  back `needs_judgment` rather than `approved`. Telling that apart from *"a clean and
  simple badge"* needs a finite-verb test, and a finite verb is an open word class —
  three enumerations of it each left another phrasing on the wrong side. The failure
  stays in the safe direction, which is the asymmetry the whole module is built on. A phrase
  with no slot is never filtered — *"ship it, the layout is wrong"* ships,
  *"Approved, though the design isn't right yet"* approves, and *"Let's go with a.
  Should the badge sit lower?"* decides. A negated phrase is never filtered, or *"not
  approved, the layout is wrong"* would read as a request for another picture.

The decider's doubt, on its own, is a no: *"I don't think this experiment would produce
results"* rejects.

Today: `ai-loop-aurie-features` → Steve, who asked for it in the channel. Everyone
else's reply is read, quoted, and reported as what the row waits on.

Without a decider the verdict belongs to whoever replied last, which makes it a property of
typing order rather than of authority.

The cost is a **critical path**: every idea now waits on one person, and a thread where
somebody else answered comes back `needs_judgment` rather than settled. That is the
intent — it is what the narrowing is for — but it means the digest's "waiting on Gate 1"
list is the thing to watch, and widening it is one line here.

A channel absent from `DECIDER` — the archive, and any loop channel not listed — behaves
as before: anyone decides, last reply wins.

## One thread, two gates

The same thread carries two decisions weeks apart — *build it?* and, once a person
has tried the build in TestFlight, *ship it?* Nothing in the text reliably tells
them apart: "ship it" means **build it** on a shared row and **release it** on one
in TestFlight.

So the **row** decides which gate a reply answers, not the wording and not the
ordering:

| Row status | Gate |
|---|---|
| `pending`, `shared` | implementation |
| `implementing` | release |

An approval that **names a gate the row has not reached** — "I approve for
release" on a row nobody has built — is not resolved. It means somebody is on the
wrong thread or the row is behind what the team thinks, and both want a person.

A reply naming a gate the row has **already passed** is the opposite: it is that
gate's own answer, still sitting in the thread, and it is *why* the row is here.
It is skipped, not re-read. Without that, the exact phrase the channel topic asks
for — "I approve for implementation" — would become a permanent `needs_judgment`
the moment the row it approved started being built.

### The release gate opens when there is something to judge

A thread keeps its whole history, so a floor is needed or gate one's answer gets
read as gate two's. The floor is the **announcement the ship leg writes into the
thread**: only replies newer than the most recent one answer the release gate.

**Two anchors, because the loop families ship differently.** A Features idea is
judged from a TestFlight build (`product:ship-idea testflight --post`). A
recommendations change is server-side — there is no build to try — so the
equivalent moment is the **backend release**, where the code is deployed and the
experiment exists but assigns nobody (`product:ship-recommendation`).
`RELEASE_ANCHORS` holds both sentences and the gate logic is otherwise identical;
which one opened the gate is the only thing that differs.

That one rule covers three ways of being wrong:

- a generic "ship it" said while the row was `shared` cannot become a release yes —
  it is above the floor (and in a loop channel it does not approve the build either,
  per the section above);
- a round-one refusal sits above round two's announcement, so an `iterate` round
  does not replay the previous round's verdict;
- a **reaction** sits on the parent message, posted before any build existed, so
  reactions are not read at this gate at all.

No announcement in the thread means `no_decision`, and the evidence says so. That
is the honest answer: before something exists to judge there is nothing to answer.

**Changing an announcement's wording closes the gate for that loop.** Each ship
leg owns its sentence (`TESTFLIGHT_ANCHOR` in `ship-idea`; the release line in
`ship-recommendation`) and this skill matches both (`RELEASE_ANCHORS`); they have
to move together.

### A negated approval is a refusal, not a miss

Every approval pattern matches inside its own negation — "Not approved for
release" contains "approved for release", and the generic `approved` too. Read as
a yes it would print the release command for a refusal, which is how a rejected
idea reaches Viktor as an experiment request. `ship-idea` hit the same trap with
substring markers; here it is the same bug in regex form, and the guard is a
negator check on the text immediately before the verb.

## Reactions mean different things in different channels

The two channels use the same emoji for opposite things, and **both readings are
correct in their own place**.

| | `#ai-ideas` (archive) | `#ai-loop-*` |
|---|---|---|
| :white_check_mark: | approved | **done** — the loop's mark once the feature card is Done (a TestFlight build carries it) |
| :x: | rejected | **abandoned** |
| decisions | reactions or replies | **replies only** |

The archive's threads are full of a check mark meaning *approved*, and re-reading
them under today's protocol would rewrite decided history. So the old reading is
scoped to that channel — and is also the **default** for anything unrecognised,
because an unknown channel is far likelier to be historical than new.

In a loop channel a check mark is therefore *not* an approval. The thread comes
back as `needs_judgment` **saying so**, which is the point: a silently ignored
reaction teaches nobody that it did not count.

## The release gate is read here and written elsewhere

This skill reads a release-gate verdict and prints the `ship-idea` command for it.
It does not write one.

`ship-idea` owns those writes — it appends the round to the note, carries the
approval marker `request-experiment` later checks for, and requires a reason on a
refusal. Reproducing any of that here would mean two writers for one state,
drifting apart the first time either changed.

## How a thread is read

In order, first match wins:

1. **A reply containing a Linear issue URL** → approved. Someone wrote the ticket; that is the least ambiguous approval there is, and it hands over the `Linear issue` value for free.
2. **A reply matching the decision vocabulary** → that verdict. The **last** decisive reply wins, because a thread argues its way to a conclusion — the live example is `0fbcae45c2c2`, which runs "is this a bug?" → a correction → "Ah, i misread it" → and only then "let's leave as is", five messages in.
3. **An unanswered question** → `needs_answer`. A reply that asks something — a `?`, or a leading interrogative — with no answer after it is neither a decision nor silence: it is the gap between `shared` and a verdict, and the loop is the participant best placed to fill it. Detected **after** the decision scan, so a reply that both decides and asks still counts as the decision, and a question is **never** read as a soft rejection — "Have we tested this?" is a question, not a no. If a bot reply already sits after the question it has been handled, so the thread falls through and a re-run never re-answers.
4. **Reactions.** ✅ approves, ❌ / 👎 reject. Reactions carry **no timestamp**, so two that disagree cannot be ordered and are never guessed between — that is a `needs_judgment`, not a coin flip.
5. **Replies but nothing decisive** → `needs_judgment`. Someone engaged, nobody concluded.

   **Bot replies are dropped before any of this.** `share-ideas` posts each idea's screenshot into its own thread, so counting bot messages as discussion would mark every screenshotted idea `needs_judgment` and re-surface it on every hourly tick, forever. The discriminator is `bot_id`, **not** `app_id` — a human replying through a Slack integration carries an `app_id` too, and Rohan's PROD-2126 link in the archive is exactly that shape.
6. **Silence, or 👍 only** → `no_decision`. The row stays where it is.

Deferral is tested **before** rejection and approval, because a deferral sentence usually contains one of the others: "let's do it later" is a deferral, and reversing that order silently turns every deferral into a build commitment.

The vocabulary is deliberately small and high-precision. Anything unmatched falls through to `needs_judgment`, which is a better answer than a confident wrong one. The archive supplies the case that proves it: Steve's *"technically in the feed - but not doing a great job."* is a real verdict to a human and unreadable to a keyword list, so the script declines it.

## CLI

```bash
SCRIPT=${CLAUDE_PLUGIN_ROOT}/skills/harvest-idea-decisions/scripts/harvest_idea_decisions.py

# Dry run — fetch, classify, print. Writes nothing.
python3 $SCRIPT plan

# Write the confident verdicts
python3 $SCRIPT apply
```

`plan` before `apply`, always. Needs `NOTION_TOKEN` and `SLACK_BOT_TOKEN`.

`${CLAUDE_PLUGIN_ROOT}` rather than a repo-relative path: a plugin-loaded session does not run with the AINative checkout as its working directory, so `plugins/product/...` resolves to nothing and the command would no-op rather than fail loudly.

The offline mode reads threads from a JSON fixture instead of Slack, which is how the classifier is exercised without credentials. The fixture lives in the repo, so this one is run from an AINative checkout:

```bash
python3 $SCRIPT classify tests/fixtures/ai_ideas_april_threads.json
python3 $SCRIPT plan --fixture tests/fixtures/ai_ideas_april_threads.json
```

## What gets written

| Property | Value |
|---|---|
| `Status` | `approved` / `rejected` / `deferred` |
| `Quick status note` | `Harvested from Slack: {the quote or the emoji}`, plus any 👍 recorded as sentiment |
| `Rejection reason` | `other` on a rejection, `timing` on a deferral — a **harvest default**, flagged as such in the note |
| `Decided by` | the replier or reactor, mapped Slack → Notion by email; left empty when they do not match |
| `Decided at` | the reply's own date (UTC), write-once — an existing date is never replaced |

The reason is deliberately blunt. A thread rarely states a reason in the vocabulary `Rejection reason` uses, and inventing a specific one would be worse than admitting the harvest could not read it. The note says so in words, so a human or a later pass can sharpen it.

## Idempotency

Only `pending` and `shared` rows with a Slack thread are candidates. A row that already carries a verdict has left the candidate set, so **a re-run with no new Slack activity writes nothing and posts nothing**, and an hourly tick on a quiet channel is a no-op. There is no bot-reply marker to maintain and no local state file — the row's own status is the dedup key.

## The judgment pass

When `plan` reports any `needs_judgment`, read those threads yourself and decide:

1. `manage_ideas.py show {fingerprint}` for the idea's own description and hypothesis.
2. Read the thread — the script prints the last reply as evidence.
3. Check the idea against current reality before accepting a stale approval: `Learnings/Experiments` for a test that already answered it, and the app's own state for a surface that no longer exists. PROD-5920 found both failure modes across the April backlog.
4. Write it with `manage_ideas.py approve|reject|set-status`, which enforces `--reason` and records `--by` / `--at`.

Never let the script's silence stand as a verdict. `no_decision` means nobody has decided, not that the idea is fine.

## The follow-up pass

Every idea thread stays open after its gate — a person can ask about the plan, change
their mind, or tell the loop to go ahead long after Gate 1. So `plan` also reads the
threads of `approved`, `shipped_dormant` and `experiment_running` rows, and of a gate row
whose thread decides nothing, and reports **`needs_follow_up`** for the newest human reply
that comes after the loop's last reply and that the loop has not marked 👀.

For each one, read the reply in its thread and **address it**:

- a question → answer it in the thread, with evidence (same rules as the answer pass);
- an instruction or a decision → act through the owning skill (`build-idea unhold`,
  `sub-card`, `ship-idea …`) and reply with what was done;
- nothing to do (a thank-you, a comment) → `harvest_idea_decisions.py mark-seen <reply permalink>`,
  which reacts 👀 as the loop so the next tick does not raise it again.

Any reply from the loop after it also clears it. Questions the loop needs to ask back go in
the same thread, never anywhere else.

## The answer pass

When `plan` reports any `needs_answer`, someone asked a question on a shared idea and nobody has answered it. Most such questions are already answerable from the priors — answer them in the thread, with evidence, so the discussion can reach a decision instead of stalling.

**Answering is not deciding.** This pass posts a reply; it **never** writes `Status`. The row stays `shared` until a *human* replies with an approval or a refusal — a loop that answers its own question and proceeds has removed the gate. Do not run `manage-ideas approve/reject` off the back of an answer.

For each `needs_answer` thread:

1. **Gather the evidence from where it actually lives** — do not assert from memory:

   | The question | Look it up in |
   |---|---|
   | "Haven't we tried this?" | `manage_ideas.py list` — the backlog rows for that screen/domain and their verdicts |
   | "What happened last time?" | the `Learnings/Experiments` row and its result |
   | "Why this screen?" | the row's Screen + the sim-test screenshot that produced it |
   | "What would we measure?" | the row's `Lever` and `Hypothesis` (`manage_ideas.py show`) |
   | "Isn't this the same as X?" | the fingerprint dedup + the record-ideas similarity/prior-art warning |
   | "Is anything else running there?" | `build_idea.py guard-collision --domain {domain}` against the live set |

2. **Compose an answer that links its claims.** Cite the row, the experiment, the result. An answer that asserts without a link is worse than none — it cannot be checked, and traceability is the whole point. Keep it short and factual.

3. **Post it as a threaded reply** by composing `shared:post-to-slack` (thread `ts` from the row's `Slack thread` permalink), attributed `footer=product:harvest-idea-decisions`. This is a pre-authorized unattended reply on an hourly sweep — post non-interactively; the anchor/bot-reply flagging means it can never later be read back as a decision.

4. **Escalate what the database cannot answer.** A question about strategy, timing or priority is not in the priors. Say so plainly in-thread and leave it for a human (route to Jerome) rather than generating a plausible answer. Guessing here is the one thing worse than staying quiet.

The **release gate shares this classifier** — the same thread carries the build-review discussion, and a question there ("does this work on iPad?", "which build is this?") is answered the same way, from the build and the row rather than the experiment history.

## Posting is narrow, and never a decision

The **only** thing this skill posts to Slack is an answer to an unanswered question — the answer pass above. It never posts a decision or a confirmation, never chases anyone for a reason, and never writes a status off a reply: it reads Slack and writes Notion, and the single exception is a linked, evidence-backed answer. Keeping the posting surface this small is what lets it run unattended on an hourly sweep without spamming a channel.

## Related skills

- [`product:share-ideas`](../share-ideas/SKILL.md) — posts pending rows to the loop channel. Run it first; this harvests what comes back.
- [`product:manage-ideas`](../manage-ideas/SKILL.md) — the write verbs, and where a `needs_judgment` thread is resolved by hand.
- [`product:record-ideas`](../record-ideas/SKILL.md) — creates the rows. Never touches Status.

## References

- Notion: [Aurie Features Ideas](https://www.notion.so/7e721e75374645a7acfa085e3aeca6df) (data source `8d14f3eb-0c55-4f25-9322-ab2b2a69f092`)
- Slack: `#ai-loop-aurie-features` (`C0C1JTK3YCC`), and the `#ai-ideas` archive (`C0ATUL8T936`)
- Tests: `tests/test_harvest_idea_decisions.py` — runs the whole April archive through the classifier and pins every verdict against the human review's independent conclusions
- Linear: [PROD-5921](https://linear.app/aura-health/issue/PROD-5921) (parent [PROD-5917](https://linear.app/aura-health/issue/PROD-5917))
