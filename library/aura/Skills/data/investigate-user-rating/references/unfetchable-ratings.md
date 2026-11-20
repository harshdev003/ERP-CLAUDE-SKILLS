# Known-unfetchable ratings — skip-list

A small, maintained list of rated items whose conversation/journal **cannot be
fetched** from OpenAI stored completions (the completion was never stored, the
conversation predates the retention window, the `conversationId` is malformed,
or the write went to a project this sweep doesn't read). Re-attempting them on
every sweep wastes a turn and produces the same "0 conversations found" dead end.

**How the sweep uses this list**

- **Step 2 (roster):** exclude any in-scope entry whose `thread_ts` (or
  `conversation_id`) appears below, and note the count as `skipped (unfetchable)`
  in the roster so the skip is visible, not silent.
- **Step 3 (per-entry loop):** if a lookup fails as *unfetchable* (the sub-skill
  returns 0 conversations AND the failure is a stored-completions miss, not a
  transient 401/429 — those now raise a catchable `FetchLogsError` and are
  retried per PROD-3653), append a row here so the next sweep skips it.

Only add genuinely-unfetchable items. A transient auth/rate-limit failure is
**not** unfetchable — it will resolve on the next run and must not be listed.

**Format** — one row per item, most recent first. PII rules apply (UID prefix
only, no names/emails).

| Added (date) | Shape | UID (prefix) | thread_ts | Why unfetchable |
|---|---|---|---|---|
| _(none yet — append as identified)_ | | | | |
