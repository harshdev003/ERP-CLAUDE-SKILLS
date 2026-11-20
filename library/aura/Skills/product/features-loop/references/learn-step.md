# The learn step

Read by `features-loop` at step 3d of every `run`. The learn leg is `product:learn-idea`; this
file says how the tick drives it.

## What the tick runs

```bash
LEARN="${CLAUDE_PLUGIN_ROOT}/skills/learn-idea/scripts/learn_idea.py"
python3 "$LEARN" sweep --apply --announce
```

The sweep looks at every `experiment_running` row. For each one whose experiment has ended it
reads why from Slack — the experiment monitor's `Experiment Won|Lost|Inconclusive: <key>` post
in `#abtest-monitoring` and the `Experiment ended` post in `#data-experiment` — writes the
verdict with that reason, and replies in the idea's Slack thread. When an end post disagrees
with the `Learnings/Experiments` row, the end post wins. A row whose experiment is still
running is held. One that ended with no end post gets no verdict: Yawar is asked once in its
thread, and it waits on a person.

The verdict is always the one Yawar and Viktor reached. The loop never ends an experiment and
never picks a winner; it carries their decision back to the idea that started it.

## The learning

Each verdict also writes a learning — what was tested (from the row's `Variants`), the result
(the monitor post's winner, metric and confidence), who ended it and why (the
#data-experiment post), and what it implies for the next ideas. It lands in two places: the
idea row's `Learning` property, and one row per idea in `Learnings/Loop/Loop Learnings`, keyed
by `Idea ID` so a correction rewrites it. Ideation reads both before proposing.

The sweep writes a generic implication for the verdict. The tick then writes the real one: read
the result and the reason, say in one sentence what the next idea on that surface should do or
avoid, and record it with `learn_idea.py learning <ref> --implication "…" --apply`. Example
(IDEA-6): *personalising the package paywall headline by onboarding archetype cut paid
conversion about 28% per paywall view; keep the proven value-prop headline there.*

## Only the loop's own experiments

The learn step covers the experiments the loop created: the rows in the loop's ideas database,
each by its own `Experiment` key. It never records, announces or files a card for any other
experiment, even one that ended in the same release.

## The code cleanup

For each row it just decided, the sweep prints a cleanup instruction. The tick follows it:

1. Invoke `eng:sweep-ended-experiments key=<that row's experiment>` to file the cleanup card for
   that key only. Never run it as a full sweep from the loop.
2. Parent the card directly under the current cycle's PROD **Quality / Tech Debt [Cycle N]**
   card (find it by title and the PROD cycle number; numbers differ per team). The sweep also
   files a batch parent ("Clean up inactive experiments — <repo> [Cycle N]"); a one-key loop
   cleanup does not need it, so move the card off it and cancel the batch parent.
3. Link the card back to the row: `learn_idea.py record <ref> --cleanup-issue <url>`.

File the card without routing it to Cursor: the loop's own agent removes the code, at step 3e —
[`cleanup-step.md`](cleanup-step.md). The removal PR goes through the normal review path; the
loop never merges it.

## In the tick summary

List each row the sweep decided (idea, verdict, the reason, the implication, cleanup card), every disagreement
between an end post and the `Learnings/Experiments` row (for the experiment owners to fix), each
row still running, and each row waiting on a person. A tick where nothing ended says nothing about the learn step.
