# config.js editable properties, normalizer, and validator

The rule spec applied in Step 5 of the [`update-intelligence-prompt`](../SKILL.md) skill
before any write, in **both** modes: which properties are editable, the chat-latest
normalizer (N1), the hard-block ERROR rules (E1–E5), and the WARNING gates (verbosity+JSON,
and chat-latest verbosity on the Response API).

## Updatable properties

The skill can edit any of these (both modes):

**Standard OpenAI params:**

| Key | Type | Notes |
|---|---|---|
| `model` | string | e.g. `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.2-chat-latest`. Subject to validation. |
| `openaiAPI` | `'chatCompletions'` \| `'responses'` | Selects `/v1/chat/completions` or `/v1/responses`. The singular `'chatCompletion'` / `'response'` spellings were retired in [ENGBACK-1267](https://linear.app/aura-health/issue/ENGBACK-1267) and are rejected by the service. |
| `reasoningEffort` | `'none'` \| `'low'` \| `'medium'` \| `'high'` | Subject to validation. |
| `verbosity` | `'low'` \| `'medium'` \| `'high'` | Subject to validation. |
| `temperature` | number | Keep `1` for all current prompters (per [PR #201](https://github.com/AuraHealth/IntelligenceService/pull/201)). |
| `tools` | string[] | Array of tool names; updates the function-tools list. |

### Default: Response API for non-conversation projects

When setting or creating a prompter config, default `openaiAPI` by the prompter's
`project`:

- **`project: 'conversation'`** (chat, coach, companion, guardrailsHandler, insightCard,
  quickProcessing, starters) → default `openaiAPI: 'chatCompletions'`. These are multi-turn
  conversational prompters; keep them on Chat Completions unless the request explicitly moves them.
- **Every other project** (feed, onboarding, memory, insights, guardrail, journal,
  notification, profile, suggestions, summary, intent) → default `openaiAPI: 'responses'`,
  and add `useResponseConversation: false` for one-shot (non multi-turn) prompters so the
  full built message set is sent as input.

The default seeds `openaiAPI` **only when the key is absent** from the config block (a new
prompter, or Mode A creating one). When `openaiAPI` is already present, leave it untouched —
change it only if the user's request explicitly asks to. This preserves a deliberate setting
in either direction (e.g. a conversation prompter intentionally on `responses`, or a
non-conversation prompter intentionally on `chatCompletions`).

**Announce any API-type change.** Whenever the skill flips a prompter's `openaiAPI` (seeding
this default or honoring an explicit request), surface it to the user before committing —
e.g. `Changing feed-prompter openaiAPI: chatCompletions → responses (+ useResponseConversation: false)`.
An API-type switch must never be silent.

Rationale: the Response API enables prompt caching and preserves per-call cost attribution
via the IntelligenceService LLM-call ledger. `gpt-5.4-mini` + reasoning/verbosity + tools —
blocked on Chat Completions (E1/E2/E3) — works on the Response API (see the validator note
below).

**Custom per-prompter props** (real keys found across `config.prompt.*` — Mode B can
edit these too; no special validation, just the same key-order + lint discipline):

- `numberOfPreviousMessages`, `numberOfPreviousConversations`, `numberOfPreviousNotifications`
- `numberOfRecentMemories`, `numberOfRelatedMemories`, `recentMemoriesDays`
- `everyNUserTurns`, `minUserTurns`
- `lock.maxWaitSeconds`, `lock.pollIntervalSeconds`, `lock.ttlSeconds` (nested object)

For removals, delete the key entirely (do not set to `null`/`undefined`). For additions,
insert the key in alphabetical order (ESLint `sort-keys` rule). Alphabetical order for
common keys: `everyNUserTurns`, `key`, `lock`, `minUserTurns`, `model`, `name`,
`numberOfPreviousConversations`, `numberOfPreviousMessages`, `numberOfPreviousNotifications`,
`numberOfRecentMemories`, `numberOfRelatedMemories`, `openaiAPI`, `project`, `reasoningEffort`,
`recentMemoriesDays`, `temperature`, `tools`, `verbosity`.

## Normalizer — chat-latest auto-strip (N1)

Before the hard-block validator runs, the skill normalizes one well-known class of
input: `*-chat-latest` models do not honor `reasoning_effort`, so any requested value
is silently dropped from both `config.js` and any Braintrust write the same run produces.

| # | Match | Action |
|---|---|---|
| N1 | `model` matches `*-chat-latest` (e.g. `gpt-5-chat-latest`, `gpt-5.2-chat-latest`, `gpt-5.3-chat-latest`) + the requested change sets `reasoningEffort` to **any** value | Drop `reasoningEffort` from the change set entirely. Apply to both the `config.js` edit in this Step 5 and any Braintrust POST emitted in [Step 7c](post-merge.md#7c--commit-a-new-production-prompt-version-via-rest-api). |

When N1 fires, surface a one-line warning and continue (no abort, no `CONFIRM` prompt):

> ⚠️ Dropped `reasoningEffort: <value>` on `<name>-prompter` — `<model>` is `*-chat-latest` and does not honor `reasoning_effort`. Any value either returns HTTP 400 or is a silent no-op (`reasoning_tokens` always 0). Writing config without the key; will also strip from Braintrust params if Step 7c runs.

**Empirical basis** (verified live 2026-06-03 in [ENGPROM-57](https://linear.app/aura-health/issue/ENGPROM-57)):

- `gpt-5-chat-latest` (canonical OpenAI): every `reasoning_effort` value returns HTTP 400 *"Unrecognized request argument"*.
- `gpt-5.2-chat-latest`, `gpt-5.3-chat-latest`: accept **only** the literal string `'medium'` — every other value returns HTTP 400 *"Unsupported value: 'reasoning_effort' does not support '<x>' with this model. Supported values are: 'medium'."*. `reasoning_tokens` is always 0 even at `'medium'`. Omitting the param produces a byte-identical response to passing `'medium'`.

The full per-model support matrix, BT-UI-vs-config.js semantics, and re-verification
test commands live in
[`reasoning-effort-matrix.md`](reasoning-effort-matrix.md).

N1 only applies to `reasoningEffort` (broken on chat-latest under both APIs, so it's
auto-stripped). `verbosity` on chat-latest is API-specific: accepted on Chat Completions;
on the Response API `low`/`high` are coerced to `'medium'` at runtime by the
IntelligenceService (`buildResponsesRequest`, PROD-3915), so the validator only **warns**
(see below) rather than blocking. `temperature`/`tools` pass through unchanged.

## Validator — hard-block ERRORs (no override)

Validate the **resulting** combination of `model`, `openaiAPI`, `reasoningEffort`, and
`verbosity` before writing. If any rule fires, **abort immediately** — print the error
block (combo + reason + fix paths) and stop. There is no "proceed anyway" prompt; these
are known-broken in production.

| # | Combo | Why it's broken | Fix paths |
|---|---|---|---|
| E1 | `openaiAPI: 'chatCompletions'` + `model: 'gpt-5.4-mini'` + any `reasoningEffort` + **function tools attached at call time** | OpenAI returns HTTP 400: `Function tools with reasoning_effort are not supported for gpt-5.4-mini in /v1/chat/completions. Please use /v1/responses instead.` Caused [PROD-1966](https://linear.app/aura-health/issue/PROD-1966/bug-openai-400-function-tools-with-reasoning-effort-not-supported-on). Re-verified live 2026-06-02. | (a) drop `reasoningEffort`, (b) move to `openaiAPI: 'responses'`, or (c) confirm the prompter does not pass a `toolManager` at call time (no `tools` reach OpenAI). |
| E2 | `openaiAPI: 'chatCompletions'` + `model: 'gpt-5.4-mini'` + any `reasoningEffort` + **function tools attached** | On Chat Completions, `gpt-5.4-mini` rejects `reasoning_effort` when function tools are attached (HTTP 400); tool-less calls return HTTP 200 with reasoning tokens consumed ([PR #179 / ENGBACK-977](https://github.com/AuraHealth/IntelligenceService/pull/179), re-verified 2026-06-02). The Response API accepts the combo — see the note below. | (a) move to `openaiAPI: 'responses'` (preferred), (b) drop `reasoningEffort`, (c) switch to a model that supports it under tools on Chat Completions (`gpt-5.1`, `gpt-5.2`, `gpt-5.4`, `gpt-5.5`), or (d) confirm no tools at call time. |
| E3 | `openaiAPI: 'chatCompletions'` + `model: 'gpt-5.4-mini'` + any `verbosity` + **function tools attached** | Same Chat-Completions limitation and source as E2 — tool-less Chat Completions calls accept `verbosity` (HTTP 200, verified 2026-06-02). The Response API accepts the combo — see the note below. | (a) move to `openaiAPI: 'responses'` (preferred), (b) drop `verbosity`, (c) switch models, or (d) confirm no tools at call time. |
| E4 | `model: 'gpt-4o'` or `model: 'gpt-4o-mini'` + any `reasoningEffort` | Classic GPT-4 models don't accept `reasoning_effort`. | Drop the param. |
| E5 | `model: 'gpt-4o'` or `model: 'gpt-4o-mini'` + any `verbosity` | Same — `verbosity` is a GPT-5 reasoning-model param. | Drop the param. |

**Detecting tool attachment.** The validator only sees `config.js`; whether a prompter passes
function tools is decided at the call site (whether `openai.call(...)` receives a
`toolManager` option — see [`src/services/openai.js`](https://github.com/AuraHealth/IntelligenceService/blob/main/src/services/openai.js)).
When E1/E2/E3 would otherwise fire, **prompt the user to confirm whether the prompter
attaches function tools at runtime**:

> ⚠️ `<name>-prompter` is `gpt-5.4-mini` + `reasoningEffort=<x>`. This breaks only when function
> tools are attached at call time. Does this prompter pass a `toolManager` to `openai.call`?
> Type `NO_TOOLS` to confirm it does not (skip block); anything else aborts.

If the user confirms `NO_TOOLS`, skip the rule and proceed. Otherwise, abort with the
error block. Document the user's confirmation in the PR description.

E1/E2/E3 are all **Chat-Completions-only** — they fire only when `openaiAPI: 'chatCompletions'`.
E1 is a stricter subset of E2 (both fire on `chatCompletions + gpt-5.4-mini + reasoningEffort + tools`);
it is called out separately because it's the live PROD-1966 incident with the API-specific 400 message.
The Response API accepts `gpt-5.4-mini` + reasoning/verbosity + function tools: verified live 2026-07-23
against `/v1/responses` — `responses` + `gpt-5.4-mini` + `reasoning_effort: medium` + tools ([ENGBACK-1245](https://linear.app/aura-health/issue/ENGBACK-1245))
and `responses` + `gpt-5.4-mini` + `verbosity: low` + tools ([ENGBACK-1246](https://linear.app/aura-health/issue/ENGBACK-1246))
each returned HTTP 200 through a full tool round-trip. This is a core reason non-conversation projects
default to `openaiAPI: 'responses'` (see [Updatable properties](#updatable-properties)).

The canonical per-variant support matrix is tracked in
[ENGBACK-1041](https://linear.app/aura-health/issue/ENGBACK-1041/audit-and-clean-up-temperature-reasoningeffort-verbosity-across-gpt-5)
— update this table when that audit lands.

## Validator — WARNING (asks for CONFIRM)

If the prompter has `verbosity` set after the requested changes **and** the prompt `.txt`
file at `$IS_DIR/src/prompters/<name>.txt` asks for JSON output, surface a warning and
require an explicit `CONFIRM`. Verbosity can cause the model to wrap JSON in prose,
breaking downstream parsers.

Detection — case-insensitive grep against the `.txt` for any of:

- `JSON`
- `output a JSON` / `output JSON`
- `respond with JSON` / `respond in JSON`
- `` ```json `` (fenced JSON block — common in few-shot examples)

```bash
PROMPT_FILE="$IS_DIR/src/prompters/<name>.txt"
if [ -f "$PROMPT_FILE" ] && grep -qiE 'json|output a json|respond with json|```json' "$PROMPT_FILE"; then
  # surface the warning
fi
```

Surface as:

> ⚠️ `<name>-prompter` has `verbosity: <value>` set, but the prompt asks for JSON
> output. Verbosity can cause the model to wrap the JSON in prose, breaking downstream
> parsers. Continue anyway? (type `CONFIRM` to proceed, anything else to abort.)

### W2 — chat-latest verbosity on the Response API (informational, no CONFIRM)

If the resulting config is `openaiAPI: 'responses'` + a `*-chat-latest` model (e.g.
`gpt-5.2-chat-latest`) + `verbosity` other than `'medium'`, surface a warning and **continue**
(no abort, no `CONFIRM`). The Responses API only accepts `'medium'` for `text.verbosity` on
`*-chat-latest` models, so the IntelligenceService coerces `low`/`high` to `'medium'` at runtime
(`buildResponsesRequest`, PROD-3915). The write succeeds; the config value is just misleading.

> ⚠️ `<name>-prompter` is `openaiAPI: 'responses'` + `<model>` (chat-latest) + `verbosity: <value>`.
> The Responses API only honors `'medium'` here, so the service coerces `<value>` → `'medium'` at
> runtime. Consider setting `verbosity: 'medium'` (honest config), or keep it on
> `openaiAPI: 'chatCompletions'` where `<value>` is used as-is.

Only proceed if the user types exactly `CONFIRM`. If the prompt `.txt` does not exist
for this prompter (some prompters are JS-only), skip the warning.
