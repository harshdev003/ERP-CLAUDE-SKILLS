# Relationship to `data:investigate-user-rating` (and why this cadence is lighter)

`data:investigate-user-rating` is the analysis engine. Its documented per-entry
loop (its Step 3) invokes the full `data:profile-user` sub-skill for each
conversation-shape entry — Mixpanel + Aura API + OpenAI transcript + a Notion
`Learnings/Conversations` record. That is the right depth for an *interactive*
review of a handful of entries.

This cadence runs **unattended every 4 hours** and a single tick typically carries
roughly 20–40 fresh entries. Running the full `profile-user` chain per entry at
that volume is far too expensive. So this skill deliberately uses a **lighter
path**: it fetches the transcript directly via
`investigate-user-rating/scripts/fetch_logs.py` and writes a self-contained
analysis (Session / Why-polarity / Signal) from the transcript alone.

The one shape without a transcript to read is `profile`, which is rated against a
generated artifact rather than a conversation — Step 3 fetches that artifact in two
GETs. That is still the lighter path: it is a small minority of entries and none of
it is the `profile-user` chain.

**What the lighter path keeps:** the `Learnings/Conversations` row. For a
negative or neutral conversation rating, Step 7 distils the lighter path's own
write-up into a row and writes it with one `shared:record-knowledge` call — no
Mixpanel read, no second analysis, no `profile-user` chain. What the row gives
up against the fuller path is depth: it carries the transcript-only read, not
the behavioural profile. Positives are not recorded.

## Script access

Steps 1 and 3 call scripts that live in the `investigate-user-rating` skill
folder. Resolve that folder via the **freshly loaded Skill path at runtime**
(invoke `data:investigate-user-rating` once to get its base directory, or
otherwise resolve the plugin's live skill path) — **never hardcode an
`rpm/plugin_<hash>/…` snapshot path**, which goes stale between sessions and
silently points at an old skill revision.

This is a deliberate composition dependency (reaching a sibling skill's scripts
rather than duplicating two large fetch scripts + carrying drift-tracking); it is
the one place this skill is not fully folder-isolated, and it is intentional. It
also means this cadence inherits changes to those scripts immediately, with
nothing in its own folder to diff — anyone changing `fetch_ratings.py` or
`fetch_logs.py` has to treat this skill as a consumer.
