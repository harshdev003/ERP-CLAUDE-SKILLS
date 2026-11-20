# Edge cases

Every case the `data:mixpanel-event-approval` workflow is expected to handle.
Step numbers refer to the Workflow section of [`SKILL.md`](../SKILL.md).

- **No hidden events** → exit after step 1 with a one-line note.
- **All hidden events have zero volume** → exit after step 2.
- **Mixpanel query batch fails** → retry that batch with a smaller size
  (≤10) once before reporting failure. Skip events whose volume can't be
  determined rather than blocking the whole run.
- **Several cards specify one event** → pick the most recent completed
  ticket; cite the others in the description.
- **Linear only mentions the event** — an acceptance criterion such as "events
  flow to Mixpanel", a trigger-wiring card, a QA or Bugbot-fix card — and no card
  specifies it → treat it as having no design ticket: put it in the legacy / no
  ticket widget group, unchecked, and name the mention-only cards in the table so
  the reader sees what was found and why it did not count.
- **A promoted event has no design ticket** → leave its description empty
  rather than paraphrasing the event name; report it as unsourced.
- **A later card renamed or removed the event, or the design card itself was
  edited in place to rename it** → put it in the superseded widget group, leave
  it unchecked, and name the superseding card in the table.
- **No `Data Investigation [Cycle N]` card for the active cycle** → create
  the run card with `cycle` set and `parentId` omitted, and tell the user the
  parent was missing so it can be re-parented.
- **Returned selection has typos / unmatched names** → re-prompt with the
  unmatched names; do not silently drop them.
- **Empty selection (nothing checked, or user says keep hidden)** → no
  writes, no Slack post; one-line confirmation.
- **`Bulk-Edit-Events` partial failure** → fall back to per-event
  `Edit-Event` for failures; report what succeeded and what didn't.
- **User declines confirmation in step 11** → no Slack post, no descriptions,
  no card. Report the Mixpanel un-hide that already happened, so they know it
  is live without an announcement. Then ask once about the two remaining
  writes **separately** — backfill the descriptions? record the run card? —
  and say that declining the card costs the next run the held-back list its
  step 4 reads. Each answer stands on its own, and a yes is that write's gate
  in place of step 11; steps 13 and 14 accept it as such. A card written on
  this path carries no Slack permalink.

## Idempotency

- Re-running on a freshly-promoted set is a no-op: step 1 returns fewer
  hidden events, and the step-7 widget lists only whatever is still hidden.
- `Bulk-Edit-Events` with `hidden: false` against an already-visible
  event is harmless.
- The Slack post and the run card are the non-idempotent steps; the step-11
  confirmation is the guard for them and for the description backfill.
- The backfill is idempotent by construction: it only writes where the
  description is empty, so a second run finds nothing left to fill. Re-running after a completed run writes
  a second card, so check for this cycle's card before writing another.
