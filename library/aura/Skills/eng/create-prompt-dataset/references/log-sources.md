# Finding inputs in the logs

Every prompter call is stored on OpenAI with its full rendered input, and every call is in the
IntelligenceService `llmCall` ledger. A context input the new prompter needs is usually
already rendered, with the same builder, inside some other prompter's request. The job is to
find that prompter, prove its block is filled in, and parse it back into structured data.

## The two indexes

**The ledger** (BigQuery `aura-main.llm.llm_call`, auth `GCP_KEY`): one row per call
with `userId`, `prompterName`, `project`, `openaiAPI`, `openaiId`, `conversationId`,
`createdAt` and token counts. Filters: `prompterName`, `userId`, `project`, `openaiAPI`,
`from`, `to` (exclusive). `scripts/ledger.py` reads the whole window in one query, newest first. To see which prompters a user's calls went through,
query by `userId` alone over a short window and count by `prompterName`.

**Stored requests** (`Tools/OpenAI` → reading stored completions): Responses API calls are
read with `GET /v1/responses/{id}` and `GET /v1/responses/{id}/input_items`, using the key of
the call's OpenAI project. They can't be listed with an API key, which is why the ledger is
the index. Chat Completions calls can be listed with metadata filters. The rendered context
is in the system message up to Intelligence Service 1.35, then in a developer message after it
(1.40 on, after a developer `---` delimiter); `system_text()` joins every system and developer
message so it is read either way. The response output holds what the prompter returned.
`stored_responses.py` reads only ledger rows whose `openaiAPI` is `responses` and reports the
rest as skipped; a prompter that changed API mid-window has its older calls under Chat
Completions. It reports `expired` (404) separately from `errors` (wrong key, retries
exhausted), so a rate-limit burst never reads as lost retention.

## Reading a block

Context blocks are Markdown sections under stable headings (`### Past Conversation [last 3
conversations]`, `### Relevant Running Memories`, `### User Information`). Parse by heading,
up to the next heading of the same or higher level. Field formats come from the builder:

| Builder (in `src/prompters/prompter.js`) | Renders |
|---|---|
| `buildUserInformationInstructions` | Name, age band, country, listening stats, local date and time-of-day line, join date, last activity |
| `buildPastConversations` / `buildPastConversationInstructions` | `- **Started:**`, `- **Ended:**`, `- **Summary:**` per conversation |
| `buildPreviousConversationsInstructions` | Same fields, date only, limited to `recentConversationsDays` |
| `buildRecentMemories` | `#### <type>` then `Type`, `Created`, `Memory`. Skips memories without a recall but **doesn't render the recall text** |
| `buildMemoriesInstructions` | `- <memory>` with a `*(Recall: …)*` line |
| Summary prompter's conversation block | `- **Role:**` / `- **Content:**` pairs: the full transcript of one conversation |

Builders change. Read them in the IntelligenceService checkout at `origin/master` before
writing a parser, and confirm which prompters call each one.

## Prove the block is filled in

Run `stored_responses.py probe` on a 50-id sample of every source before relying on it. Read
three numbers per heading: how often it appears, how often its body is empty (`Empty.`, `No
… found.`), and which field labels render.

- **Empty in nearly every request** → the builder is broken for that block, not quiet users.
  Example: a builder whose model query filters `expiryAt IS NULL` and `expiryAt > now`
  together returns no rows at all, so every request shows `Empty.` for that block. Report it
  to the user as a bug and take the input from somewhere else.
- **A field the prompt needs isn't rendered** (recall text in `buildRecentMemories`) → SQL.
- **The window or limit differs** from the new prompter's (last 3 conversations vs all since
  the last run; last 10 memories from 7 days vs all active) → use it if the gap is small for
  most users, say so, or hand over SQL.

## Choosing T and pairing sources

T is the moment the new prompter would have run. Anchor it on a real call with the same
timing: a nightly 3am-local job pairs with another nightly prompter's run, whose `User
Information` block then carries the right local date. For each other source, take that
user's nearest call in a window before T (24 hours worked for daily-ish prompters). Discard
candidates with no pair.

## History from a predecessor

When the new feature replaces or extends an older prompter (including one deleted from the
code), that prompter's stored outputs on the nights before T are a realistic history of
earlier items for the same user. Map each output item to the new item shape, tag its topic by
a keyword map, and set states (open, completed, removed) by a fixed rule recorded in metadata.
Engagement states live in the database only (`completedAt`, `removedAt`); set them by rule or
hand over SQL.

## Variant inputs

Raw user messages for a "summaries plus the user's own words" variant come from the summary
prompter's stored requests: take the latest call per `conversationId` in the window, keep
`Role: user` turns, newest first, cap by characters. Collect them only when the variant was
confirmed in Step 2; they are the most sensitive thing in the dataset.

## Retention

Fetch one stored request from the oldest week you plan to use before building on it. A 404 on
an id the ledger lists means the stored request has expired.
