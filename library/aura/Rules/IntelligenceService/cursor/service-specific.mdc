---
description: Rules that apply to the Intelligence Service only
globs: 
alwaysApply: true
---

# Intelligence Service rules

Rules for this service only. The rules shared by every backend service are in the other files in this directory and are identical in all 13 services; a general rule goes there, not here.

## Architecture

### Prompter variants — copy, don't extend

When a new prompter needs the same input-shaping and message-building as an existing one (e.g. `MemoryIngestionSortingPrompter` mirrors `MemorySortingPrompter`), **copy the full implementation into a standalone class that extends the base `Prompter` directly** — do not `extends` the sibling prompter. Each prompter then resolves its own config key and `.txt` file from its class name and can diverge without coupling to another prompter's behaviour. This is a deliberate exception to DRY: prompter bodies are small, and the shared base is `Prompter`, not a peer. ([apps#241 review](https://github.com/AuraHealth/IntelligenceService/pull/241))

### A message-layout change breaks the AINative prompt skills

The order and roles of the messages a prompter sends (`Prompter.doBuildMessages`, `utils/buildContextMessages`) are mirrored outside this repo: the AINative skills that render, replay or edit Braintrust templates (`eng:qa-prompt`, `eng:compare-prompt-versions`, `eng:update-intelligence-prompt`, `eng:audit-intelligence-prompts`, `product:draft-prompt-candidate`) parse that layout. When a change moves, splits or adds a message — a delimiter, the context moving into its own message, a new time message — open an AINative card in the same change, listing the old and new layout, so those skills are updated before they are next run.

**Why:** the cache-boundary layout (system → developer `---` → user `---` → developer context → user) shipped without one, and the prompt skills broke the same day: they doubled the delimiters, dropped developer messages in translation, and failed the placeholder check on every correct commit. Fixing them took two AINative PRs after the fact.

### Prompter context builders

- **A section's text comes from a formatter in `src/prompters/formatters/`**: a pure function that takes the data and returns the section text, with no I/O, no logger and no `this` (`formatProfileInsightsContext`, `formatPracticeContext`). The Prompter method that reads the database stays on the base `Prompter`, next to the other context builders, and only loads the data and calls the formatter. Reuse an existing builder or formatter before writing a new one. `prompter.js` is being split this way one section at a time (ENGBACK-3052).
- **Moving a section must not change a byte of the prompt.** Before moving one, add its cases to the golden spec (`test/unit/prompters/golden/`), capture the current text with `UPDATE_GOLDEN=1`, then move it and run the spec without the flag. Only re-capture when the wording is meant to change.
- **Render each item as key-value lines**, the way past conversations are rendered: one `- **Key:** value` line per field, dates as `MMMM D, YYYY h:mm A` in the user's timezone, and a blank line between items. ([#658 review](https://github.com/AuraHealth/IntelligenceService/pull/658))

### A new LiveKit job-metadata key joins the allow-list

The app's voice token request sends a metadata object that IS forwards to the agent as its job metadata. `livekit.getConversationToken` checks every key against `VALIDATORS` in `src/models/livekit.js` (value lists in `config.livekit.metadata`). A key it doesn't know logs "LiveKit metadata failed the check" and, once `LIVEKIT_METADATA_CHECK` is `enforce`, is dropped before the agent sees it.

So when an app or agent change adds a job-metadata key, add it to `VALIDATORS` with a value check, and a case to `test/unit/models/livekit.validateMetadata.spec.js`, before the app ships it.

**Why:** Aurie iOS 1.33.14 started sending `audio_route`, `audio_session_mode` and `playback_rate`, which nobody added here. Every voice call logged the warning, and under enforcement the `aiVoiceProvider2` arms c and d would have lost their 1.05x pace (PROD-8465).

### A prompter change keeps `dialogue.count` covering it

`dialogueModel.count` filters replies by their stored `prompt->>'name'`, and the app asks by prompter (`coach`, `quickProcessing`). When you add, rename or merge a prompter, keep the count covering it:

- A new prompter type joins its family list (e.g. `conversation.quickProcessingTypes`).
- A retired stored name moves to `prompt.<key>.legacyNames`, and stays there while rows stored under it may exist.
- `STORED_PROMPT_NAMES` in `test/unit/models/dialogue.count.spec.js` gains the new name. Names are added, never removed. The "dialogueModel.count coverage" tests fail when a counted name is missing.

**Why:** the QP Sentinel experiment prompter stored its replies as `quick-processing-sentinel-prompter`, which the count didn't match, so they never counted toward the Limited Free counter restore or the QP unlock gate for the whole experiment ([#757](https://github.com/AuraHealth/IntelligenceService/pull/757), ENGBACK-2083).

### Before removing a prompter, prove no client still sends its name

A prompter is removed only when nothing has sent its name for at least 10 to 20 days, and two checks prove it:

- **Count the name the client sent, not the resolved prompter.** The dialogue ledger records the prompter that answered. A name served through an alias, or merged into another prompter, is recorded under its target, so its own count reads zero while users are still sending it.
- **Grep every client for the literal name**: Aurie iOS, Aura mobile, the LiveKit agent and the web apps. A client can hard-code a prompter name with no experiment or remote config behind it.

After a release that removes a name, read the `Unknown prompter name` warnings in the +15 min check: a removed name that is still sent shows up there before it shows up as user errors.

**Why:** `quickProcessingSentinel` was removed after a 20-day "0 calls" check that read the resolved name. The Aurie app sends that name for every Quick Processing session since 1.30.0, so Quick Processing text and voice were refused with a 422 for about 34 minutes, until a rollback. The next release accepted the name again as an alias of `quickProcessing`.

## Config

### Prompter JSON-output schemas

When a prompter declares `jsonOutput: true` in its config, the Structured
Outputs JSON Schema lives on disk by file convention — not inline in config.

- File path: `src/prompters/<configKey>.schema.json`, co-located with the
  prompter's `.txt`.
- File contents: **the raw JSON Schema body only**, no envelope. No `name`,
  no `strict`, no wrapper.
- The OpenAI envelope is composed by `Prompter.readSchema()` and
  `Prompter.buildPrompt()`:
  - `name` is derived as `<configKey>_schema`
  - `strict` defaults to `true` (override via `promptConfig.jsonOutputStrict`)
  - schema name override available via `promptConfig.jsonSchemaName`

Sort the schema alphabetically: `properties` keys, the `required` list and `enum` values. ([#658 review](https://github.com/AuraHealth/IntelligenceService/pull/658))

Don't add a `jsonSchema:` field to the config object — it would be a second,
parallel source of truth that drifts from the on-disk file. The single
opt-in is `jsonOutput: true`; the rest is convention. ([#265](https://github.com/AuraHealth/IntelligenceService/pull/265))

## Database

### Shrinking an AI output contract

When a prompter's JSON output loses fields (e.g. the summary prompter went from 9 keys to 3 in [#268](https://github.com/AuraHealth/IntelligenceService/pull/268)):

- **Change the schema file and the write path in the same PR.** Update `src/prompters/<name>.schema.json` and the model code that destructures, warn-checks, and persists the response together, so the parser never expects fields the prompt no longer produces.
- **Keep the DB columns.** Don't drop or NULL-migrate columns for the removed fields — old rows keep their values, new rows simply stop writing them. Dropping columns is a separate, later decision (see "Dropping or renaming columns").
- **Verify every reader tolerates NULL before stopping writes.** Grep for reads of each affected column and confirm the consumer handles it going NULL on new rows. In #268, `nextFirstMessage` consumers gate on `nextFirstMessageExpiryAt`/`nextFirstMessageUsedAt`, and the previous-conversations context builder selects `followup` but never renders it — so both features wind down naturally with no code change.

## Documentation

### Postman paths

Note the mount when writing the path: the conversation router is mounted at
`/conversations` (plural) in `src/http/router/ai/index.js`, while its files live under
`router/ai/conversation/`. The route declares only the suffix, so the singular form is
easy to write by mistake in a docstring or PR body and impossible to notice from the
route file alone. ([#459](https://github.com/AuraHealth/IntelligenceService/pull/459))

## Error handling

- **A swallowed error becomes an empty result, for the non-streaming calls only.**
  `callResponses` and `callCompletionsChat` log and return `undefined` rather
  than rethrowing. A failed LLM call then looks exactly like a genuinely empty
  completion at the call site: the caller takes its `!response?.content`
  branch, not its `catch`. When tracing "no response from AI" on those paths,
  look for the `openaiService` error line in the same transaction rather than
  a stack trace at the model layer.
- **The streaming calls rethrow.** `streamCallResponses` and
  `streamCallCompletionsChat` rethrow after releasing any matched sentinel tool
  call and writing the Braintrust logs. `dialogue.say` then takes its failure
  path: no reply is saved, side effects don't run, and the turn gets
  `DIALOGUE_GENERIC`. On the chat-completions SSE route the client receives an
  error frame and no `[DONE]`.

## Logging
- **Prod logs at `info` here, not `warn`.** Unlike the other services, `info` lines reach prod Cloud Logging, so they follow the same never-log rules as `warn` (ENGBACK-1897).
- **Three lines feed AINative skills. Keep their message, level and fields:**
  - `src/tools/handoffToGuardrails.js`, `logger.info('Handing off to guardrails for crisis response', meta)`: `eng:investigate-conversation-guardrail` and `data:analyze-conversation-guardrails` filter on `component` and `message` and read `category` and `conversationId`.
  - `src/services/openai.js`, `logger.info('OpenAI streaming response started', meta)`: counted per version by `eng:monitor-response-message-drops`.
  - `src/services/openai.js`, `logger.warn('Dropped extra response message items', …)` (two sites): the same monitor reads `keptChars`, `responseId` and each `dropped[]` item's `chars`, `duplicate` and `looksLikeOpener`.
- **If the prod level is ever lowered to `warn`, raise the two `info` lines to `warn` (or `audit`) first,** or those skills go blind.

## Project

- **Modules** beyond the shared layout: `tools` (AI tool implementations for memory and recommendations), `connectors`, `listeners`, `prompters`.
- **Config:** `braintrust: BRAINTRUST_CONFIG` is keyed by project (`{ projectKey: { apiKey, projectName } }`).
- **Database:** through the prod tunnel, `SELECT current_database(), current_schema();` must return `intelligenceDB` / `public` before you trust an `ALTER` landed.
- **JSONB columns:** `metrics`, `usage`, `prompt`, `toolCalls`, `payload`, `suggestions`, `scores`, `guardrail`. `services/openai.js` `parseUsage()` rewrites `prompt_tokens`/`completion_tokens` to camelCase before writing `metrics.llm`.

### AI/ML Features
- **OpenAI Integration**: Proper error handling for API failures
- **Memory Management**: Efficient storage and retrieval of user memories
- **Tool Calling**: Implement proper tool calling patterns
- **Streaming**: Handle streaming responses properly
- **Per-prompter opt-in features ship disabled by default.** When introducing
  a per-prompter feature gate (`jsonOutput`, a new tool, a model swap behind
  a flag), ship the wiring with the switch set to `false` on every prompter.
  Enable each prompter one at a time in a follow-up PR alongside its own
  eval pass. A schema or config bug then only affects the one prompter
  being flipped on, not the whole service. ([#265](https://github.com/AuraHealth/IntelligenceService/pull/265))
