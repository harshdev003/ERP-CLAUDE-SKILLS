# Where a per-entry rating review stops

Canonical for how far a single rating review is allowed to reason, shared by the
interactive review and both unattended rating sweeps. Read it before writing any
`Signal:` line.

## The division of labour

A per-entry review produces an **observation about one session**. It does not
produce a prompt change.

Prompt changes are made by a prompt engineer reading the **accumulated corpus** —
in `Learnings/Conversations` where the review records one, and in the posted Slack
replies either way. One rating cannot support a change to a prompt that runs on all
traffic: a single session has no base rate attached to it, so "this phrasing missed"
and "this phrasing misses at a rate worth fixing" are different claims and only the
second justifies an edit.

So the deliverable is the write-up. Where the review records a
`Learnings/Conversations` row, that row is the hand-off and the reviewer's job ends
there.

The card's `*Follow-up*` section is where a draft rule may appear — as a **candidate**
for the prompt engineer, paired with the corpus query that would justify shipping it,
never as a decided change. The table below governs the `Signal:` line; `follow-up.md`
governs the section after it.

## What a `Signal:` line may contain

| In scope | Out of scope |
|---|---|
| What was observed in this session, quoted or cited to a turn | A prompter-version change |
| A **falsifiable hypothesis** — what would have to be true across the corpus for the observation to matter, and how you would check | A config-param change |
| A Braintrust eval case (cheap, no production risk, useful whether or not the prompt changes) | Anything phrased as "the prompt should…" |

A hypothesis names the measurement. "Abstract second-person phrasing draws explicit
non-comprehension" is an observation; "an explicit non-comprehension turn is followed
by another abstraction rather than a concrete rephrase at a rate above X across QP
sessions — countable by sweeping for those user turns and classifying the next
assistant turn" is a hypothesis. The second tells the prompt engineer what to check;
the first tells them what one user did.

## State the evidentiary weight

Say what the rating can carry. A neutral or negative with **no typed feedback** is
thin on its own — most neutrals carry none — and a review that omits this reads as
though one session settled something. Where the transcript is partial (a stored-window
tail rather than the whole conversation), say which turns are missing rather than
reasoning over the gap.

## Designed behaviour is not a defect

The trap for anyone reading only a transcript: a deliberate product behaviour looks
identical to a failure. Check what the prompter is *for* before writing it up as a
miss.

The worked example, because it has already been got wrong: `quick-processing-sentinel-prompter`
exists to decide a session is complete. Its prompt signals completion by appending an
`**ENDCONVERSATION**` marker, which the IntelligenceService streaming filter strips and
replaces with an `endConversation` toolCall. A transcript therefore *ends* — often right
after a decision or a disclosure, because that is what resolution looks like. Writing
"the session closed too early" as a finding asserts a defect where the design is working.
The same holds for the absence of a mid-session continuity offer on that flow: the
sentinel generates no insight mid-conversation, and the completion screen comes from
`/end`.

When a closing, a handoff or a silence looks wrong, name the prompter and check its
contract first. If it is designed, say so explicitly in the write-up — a reader who
has the transcript and not the contract will otherwise re-derive the same wrong
conclusion.
