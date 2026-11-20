# Rating-sweep dedup — shared contract

The rating-analysis cadences dedup on **Slack thread state**, not on a local watermark.

> **An entry is unprocessed if — and only if — its thread carries no reply from the
> analysis bot `U0ATMPESR37`.** Every tick re-checks the full window and processes
> everything that check says is unprocessed.

Both sweeps read the same channel (`#ai-data-intelligence` = `C07MB0TMF5J`) and share
that one signal:

| Sweep | Fetch slice |
|---|---|
| Group 1 analysis (`data:run-rating-feedback-analysis-cadence`) | `--window 1d --polarity all` |
| Group 2 analysis (`data:run-feedback-analysis-cadence`) | `--window 7d --polarity negative` |

## Rule 1 — the reply IS the watermark

Resolve the bot id once per tick via `auth.test`, then call `conversations.replies` per
entry and look for a message whose `user` equals that id (skipping the thread parent).
Pace ~0.35s between checks — `conversations.replies` is Slack Tier-3.

**If `auth.test` fails, post nothing for the tick and report it.** Without a resolved bot
id the check is blind, and a blind check reads every thread as unprocessed.

`data:run-cancellation-feedback-analysis-cadence` (Group 4) and
`data:run-guardrails-analysis-cadence` (Group 5) use the same mechanism. Three properties make
it the right signal, and each is a reason not to add a local list back:

- **Self-healing.** An entry missed for any reason — a crash mid-tick, a stopped sweep, a
  widened upstream filter, a machine rebuild — is picked up on the next tick, because the
  thing being asked is "does a reply exist", not "did some past run claim to have looked".
- **No cap, so no coverage horizon.** There is no list to prune, so no volume of ratings can
  push an in-window entry out of the dedup set. A cap sized in entries cannot track a window
  sized in days.
- **Observable.** The dedup signal is exactly what a human sees in the channel, so a
  disagreement between the sweep and the channel is impossible rather than merely unlikely.

## Rule 2 — every processed entry ends with a reply on its thread

Because the reply is the watermark, an entry that gets no reply is by definition still
unprocessed and will be retried next tick. So **every** terminal outcome posts something:

| Outcome | Reply |
|---|---|
| Analysed | The normal review reply. |
| Held by the content-safety gate (active SI / CSA / distressed minor) | A **minimal** placeholder — that the entry was withheld and needs a human look, and nothing else. The analysis itself goes to the operator in-session, never to the channel. |
| Terminally unanalysable (see Rule 3) | A one-line note naming what failed. |

A safety-held entry needs the placeholder for the same reason as any other: without a reply
the next tick re-fetches, re-analyses and re-flags the same sensitive content on every
tick. The placeholder carries **no** detail about the content — it exists to claim the
thread, not to describe why.

**The placeholder asks for a human; it never claims one already has it.** No wording in the
reply may assert that the entry has been routed, assigned, escalated, or picked up. The
in-session flag is a line in a transcript nobody reads on an unattended run, so the reply is
the only artifact anyone will ever see, and a reply asserting a handoff makes a held entry
read exactly like a handled one — a missing output has no shape
([ENG-1045](https://linear.app/aura-health/issue/ENG-1045),
[ENG-1219](https://linear.app/aura-health/issue/ENG-1219)). Which way this errs, stated
plainly, because this is a safety path: the reply must claim the thread to stop the loop, so
it has to carry the ask itself and must never read as an all-clear. Naming the *gate* is not naming the *content* — that is the line
the placeholder holds. Both sweeps also re-surface every held entry in their own run readout,
because a reply in the channel is not the same as the operator having seen it.
`data:run-guardrails-analysis-cadence` posts the same shape for an unanalysable alert; keep
the three worded consistently.

## Rule 3 — transient failures retry, terminal failures get an error reply

Post an error reply only when retrying cannot help. Otherwise post nothing and let the
missing reply schedule the retry.

- **Transient — post nothing, retry next tick.** A missing token, a 5xx, a rate limit, a
  network error, or a `0`-match transcript on an entry **younger than 1 hour** (OpenAI
  stored-completions indexing lags behind the k8bot message).
- **Terminal — post the error reply.** A `0`-match on **both** OpenAI and Braintrust for an
  entry **older than 1 hour**, a malformed entry, or any failure that is a property of the
  entry rather than of this moment.

The age test needs no state — the entry's own `ts` carries it, which is why it is preferred over
a grace counter (see `edge-cases.md`). Keep the error reply short and factual — what was attempted, what failed — with no
speculation and no user content:

```
:warning: *Rating review unavailable — uid `<8-char prefix>` · <shape>*
No transcript found for this conversation on either OpenAI stored completions or Braintrust.
Nothing to analyse, so this entry is closed out rather than retried.
```

## Rule 4 — oldest-first, and never suppress on volume

Process the unprocessed set **oldest-first**, sequentially. Oldest-first guarantees forward
progress: if a tick is interrupted, the work that landed is the work that had waited longest,
and the remainder is still unprocessed by Rule 1.

**There is no per-tick cap and no seed-instead-of-post guard.** A large unprocessed set means
real feedback is sitting unanswered; the correct response is to answer it, not to mark it
handled. Report the count so an unusually large tick is visible, and let Slack's rate limits
pace the posting.

## Rule 5 — the pre-post race check

Two sessions can sweep the same channel concurrently. Immediately before posting, re-run the
Rule 1 check for that entry; if a reply now exists, a sibling session got there first — skip
it. This is the only guard against a double-post. It is also lock-free by construction: the
signal lives in Slack, so there is no read-modify-write window in which one sweep's write drops
the other's.

## Rule 6 — do not add a local watermark back

Neither sweep reads or writes `~/.claude/rating_sweep_state.json`. A copy on disk is inert;
leave it alone rather than deleting it — cleaning it up is not this cadence's job.

A local list of claimed `ts` values looks like a cheap fast-path, and it always grows the same
scaffolding: a cap to bound it, a first-miss map so a retry rule can span restarts, a filter
generation counter so a widened upstream filter isn't mistaken for new work, and seed guards so
none of the above mass-posts a backlog. Each piece compensates for one blind spot — a local
list cannot distinguish *already answered* from *never looked at* — and every piece is a place
to be wrong about coverage while reporting success. The reply answers that question directly,
which is why none of it is needed here.

Rationale and the two failures that motivated it: [`/CLAUDE.md`](../../../../../CLAUDE.md) →
"Canonical source for `dedup.md`" · [ENG-1055](https://linear.app/aura-health/issue/ENG-1055).
