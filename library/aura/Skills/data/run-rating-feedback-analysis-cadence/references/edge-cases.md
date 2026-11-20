# Edge cases — per-tick rules

- **Zero unanswered after dedup** → report `in-window N → internal-filtered → unanswered 0`
  and stop. Nothing is persisted, so there is no stamp to write on the way out and no way for
  a quiet tick to affect the next one.
- **Transcript 0-match on BOTH OpenAI and Braintrust even with the full UUID** → split on
  whether retrying could help, using the entry's **own `ts` as the clock**. No marker file, no
  grace counter, no state.
  - **Younger than 1 hour** → transient. OpenAI stored-completions indexing lags behind the
    k8bot message, so this is very likely a timing artifact. **Post nothing.** The absent reply
    is what schedules the retry: next tick's Step 2 check still reads the entry as unanswered.
  - **Older than 1 hour** → terminal. Post the error reply so the thread is closed out rather
    than retried on every tick forever:

    ```
    :warning: *Rating review unavailable — uid `<8-char prefix>` · <shape>*
    No transcript found for this conversation on either OpenAI stored completions or Braintrust.
    Nothing to analyse, so this entry is closed out rather than retried.
    ```

  **Use the age test, not a persisted first-miss marker.** A grace-counter rule has to remember
  the first miss across restarts, which means a marker map — and a marker can strand an entry
  outright: one whose first miss ages out of the fetch window before its second look is never
  retried and never closed. The `ts` needs no memory and cannot strand anything.
- **Other failures while analysing one entry** (missing token, 5xx, rate limit, network) →
  transient by the same rule: post nothing for that entry, report it, and let the next tick
  retry. Do **not** post an error reply for these — a token that comes back should not leave a
  permanent "unavailable" note on a thread that could have been reviewed properly.
- **User explicitly asks "what should I say / give me ideas" and the coach keeps
  asking open reflective questions instead** → flag as a specific named product
  signal (delayed concrete guidance on deep-rooted guilt/boundary patterns reads
  as unhelpful). Seen recurring in this cadence.
- **A "sweep" trigger with no active session state** → just run the tick. There is no session
  state and no on-disk state to restore; the channel is the state.

---

These are **tick contract** rules, not background reading: without them a tick cannot decide
whether a failed entry should be retried or closed out, which is the one decision that
determines whether real feedback gets answered or a thread gets retried forever. Read them
before the first post.

Shared dedup contract (the reply check, the every-outcome-posts rule, the race check):
[`dedup.md`](dedup.md).
