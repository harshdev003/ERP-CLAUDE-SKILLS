# Context builders

Every prompter extends the base `Prompter` in IntelligenceService
`src/prompters/prompter.js`. Its `build*Instructions` methods are the menu of inputs a
new prompter starts from. **The file is the source of truth**: re-list the methods before
planning and read the body of each one you intend to use.

```bash
git -C "$IS_DIR" show origin/master:src/prompters/prompter.js \
  | grep -n "^  async build[A-Za-z]*(\|^  build[A-Za-z]*("
# who uses a builder today
git -C "$IS_DIR" grep -l "this.buildRecentMemoriesInstructions" origin/master -- src/prompters
```

For every builder you plan to use, answer from its body: **what it selects** (the SQL or
model call), **its window and cap**, **what it formats** (fields, dates, timezone), and
**what it silently returns when empty**. The gotchas below were each found by doing that;
treat them as things to re-check, not as facts that will stay true.

## The menu

| Builder | Selects | Window / cap | Gotchas to re-check |
|---|---|---|---|
| `buildUserInformationInstructions` | Name, `user.profile` (via `buildUserProfile`), listening and conversation streaks, current local date and time, join date, last activity | n/a | Already contains the profile, so a separate profile input is a duplicate. The local date/time comes free. |
| `buildPastConversationInstructions({numberOfPreviousConversations, sinceNumberOfDays})` | Conversation **summaries** with start and end times | Latest N; optional `sinceNumberOfDays` window | Skips conversations with no summary (still open, or the summary call failed). Summaries only, no messages. |
| `buildPreviousConversationsInstructions()` | Conversation summaries | `recentConversationsDays` (7) / `recentConversationsLimit` (20) from the prompt config | Config-driven variant of the above. |
| `buildDayConversationsInstructions()` | Conversations and their dialogue for **today in the user's timezone** | Current local day | Empty for anything that runs just after local midnight (a 3am job reads the new, empty day). |
| `buildLastMessagesInstructions({numberOfPreviousMessages, withAssistant, withUser, withToolCalls})` | Messages of the **current** conversation | Latest N messages | Needs `dialogues` passed to the prompter; only for prompters that run inside or at the end of a conversation. |
| `buildCurrentConversationContextInstructions()` | Whatever started the conversation, by `firstMessageContext` flag (feed card, notification, profile, practice, previous conversation) | n/a | A new tap-to-chat surface needs its own branch here *and* a pass-through in `conversation.buildFirstMessage`, or the flag is dropped. |
| `buildRecentMemoriesInstructions({numberOfRecentMemories, sinceNumberOfDays})` | Memory rows of **any type** by `createdAt` | Latest N within the window (10 / 7 days by default) | Mixes durable facts with open threads. |
| `buildCoreMemoriesInstructions()` | `type = 'core memory'` | **Uncapped** | Durable facts about who the user is; overlaps heavily with `user.profile`. |
| `buildRelevantRunningMemoriesInstructions({numberOfRunningMemories})` | `type = 'running memory'`, `expiryAt` NULL or future | Newest N (20 by default) | Open, evolving threads. Each row carries a `recall` question. |
| `buildRelationalPreferencesInstructions()` | `type = 'relational preference'` | Uncapped | How the user wants Aurie to communicate; tone guidance, not content. |
| `buildRelatedMemoriesInstructions({numberOfRelatedMemories})` | Memories semantically similar to the **last user message** | Top N | Needs dialogues; no use outside a conversation. |
| `buildRecentAffirmationsInstructions(n)` | The user's recent affirmation cards | Latest N | Used for no-repeat input by the affirmation prompter. |
| `buildOnboardingConversationInstructions()` | The onboarding conversation | n/a | Used by the journal prompter. |
| `buildMemoriesInstructions(memories)` | Formatter, not a query | n/a | Prints `memory` and its `(Recall: …)` question. Reuse it so a new prompter formats memories like the others. |

## Memory types

The memory prompter types every row, under a strict-schema enum. Its rules live in
`src/prompters/memory.txt` (section "Choose the Memory Type"); read them before choosing
which type a feature should consume.

- **Core memory**: durable context about who the user is. No timestamps.
- **Running memory**: active, evolving or recurring threads likely to need follow-up. Carries
  `expiryAt` only when the thread has a real end; null otherwise.
- **Relational preference**: how Aurie should relate to the user. No timestamps.
- **Recent memories** is not a type; it's a date window over all three.

## New builders

Write a new method on the new prompter (not on the base class) when no builder fits, for
example: the feature's own previous outputs (no-repeat input), a capped and ordered memory
query, or conversation summaries since a stored timestamp. Promote it to the base class only
once a second prompter needs it.

## Prod data checks

Before relying on a type, field or size, sample what is actually stored. Hand the user
read-only SQL to run through the prod tunnel; don't run it against a connection you haven't
confirmed is prod (`SELECT current_database(), inet_server_addr();` plus a row-count sanity
check). Queries that changed past plans:

```sql
-- type split and timestamp coverage
SELECT "type", COUNT(*),
       COUNT(*) FILTER (WHERE "expiryAt" IS NOT NULL) AS with_expiry_at
FROM "memory" WHERE "deletedAt" IS NULL AND "createdAt" > NOW() - INTERVAL '30 days'
GROUP BY 1 ORDER BY 2 DESC;

-- per-user size distribution, to size a cap
SELECT percentile_cont(ARRAY[0.5, 0.9, 0.99]) WITHIN GROUP (ORDER BY n), MAX(n)
FROM (SELECT "userId", COUNT(*) n FROM "memory"
      WHERE "type" = 'running memory' AND "deletedAt" IS NULL
        AND ("expiryAt" IS NULL OR "expiryAt" > NOW())
      GROUP BY 1) t;

-- a random sample to read by hand
SELECT "memory", "recall", "expiryAt", "createdAt" FROM "memory"
WHERE "type" = 'running memory' AND "deletedAt" IS NULL ORDER BY random() LIMIT 30;
```

Reading the sample by hand is the step that finds quality problems no count shows: generic
rows (onboarding-survey answers stored as memories) and sensitive content the prompt must
handle. Never paste raw sampled rows into Linear, Slack or a PR; summarise them per
`Compliance/Privacy`.
