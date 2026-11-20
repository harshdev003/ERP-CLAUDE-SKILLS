---
name: investigate-openai-trace
description: >
  Investigate Aura's model-pipeline traces (Memory, Conversation, Guardrail, Quick Processing,
  Journal, Summary, Profile, Notification, Feed and more) from inside Claude Code via a
  chained workflow — triage (what fired recently?), focus (every completion for a
  transaction), and trace (one completion in full). Also follows OpenAI Logs URLs — a
  platform.openai.com/logs link or a bare chatcmpl id. Trigger on "investigate openai trace",
  "investigate braintrust", "trace this transaction", "what fired in Memory in the last 4h",
  "memory guardrails today", "find completions for transaction {uuid}", "follow this OpenAI
  logs link", or a pasted OpenAI Logs URL. Use as the follow-up when `eng:investigate-gcp-logs
  --transaction` returns 0 rows: Aura's pipeline transactions live on OpenAI completion
  metadata, not as GCP log fields. Do NOT use for editing Intelligence Service prompts
  (`eng:update-intelligence-prompt`), prompt QA (`eng:qa-prompt`), or saving dataset rows
  (`data:save-to-braintrust-dataset`).

metadata:
  version: "3.2.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/OpenAI  # auth + "Reading stored completions" access pattern + metadata field reference + project inventory
    - Tools/Braintrust               # Braintrust fallback path (BRAINTRUST_API_KEY)
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# investigate-openai-trace

> **Migrated off Braintrust 2026-06-05** ([PROD-2464](https://linear.app/aura-health/issue/PROD-2464), umbrella [PROD-2456](https://linear.app/aura-health/issue/PROD-2456)). Traces now come from **OpenAI stored chat-completions**, not Braintrust BTQL. Auth is `OPENAI_ADMIN_API_KEY` (the Admin key the cost skills use), not `BRAINTRUST_API_KEY`. **As of [PROD-3572](https://linear.app/aura-health/issue/PROD-3572), stored-completions reads additionally require project-scoped `OPENAI_<PROJECT>_API_KEY` service-account keys** — OpenAI now enforces a `model.request` scope on `GET /v1/chat/completions` that Admin keys can't hold, so the Admin key works only for the `/v1/organization/projects` discovery call and every completions read uses the owning project's key. The metadata model renamed two fields: **BT `metadata.prompterName` → OpenAI `metadata.name`** (the prompter) and **BT `metadata.userId` → OpenAI `metadata.user`** (Firebase UID). The chained triage → focus → trace UX, the digest format, and the `→ Drill in:` hints are preserved. **Renamed `investigate-braintrust-trace` → `investigate-openai-trace` on 2026-06-05** — the deferred follow-up to PROD-2464, done once the guardrail skill that referenced the old path had merged. The two-word trigger phrase "investigate braintrust" is kept as a back-compat alias in the description below so existing muscle memory still routes here.

Read-side debugging skill for Aura's model traces. Chains three modes — **triage → focus → trace** — and follows OpenAI Logs URLs. Read-only against OpenAI's [stored chat-completions endpoints](https://platform.openai.com/docs/api-reference/chat).

## Goal

Replace the manual loop of "open the OpenAI Logs UI → guess the project → filter by transaction → click into each completion" with a chained set of CLI prompts that hand off to each other. Every output ends with the exact command for the next step, so investigation never stalls because the query syntax was forgotten.

The motivating case: a Slack alert (`:warning: Memory ingestion guardrail triggered`, conversation rating, quick-processing failure) carries a transaction UUID. `eng:investigate-gcp-logs --transaction <uuid>` returns 0 rows because transactions live only as `metadata.transaction` on OpenAI completions. This skill is the immediate next step.

## Reads both OpenAI surfaces (Responses is not enumerable)

Most production projects — `memory`, `profile`, `summary`, `journal`, `intent`, `notification`, `guardrail`, `suggestions`, `insights` — already run on the **Responses API** (ENGBACK-1235/1246), and Responses objects are **not enumerable**: `GET /v1/responses` rejects API keys ("must be made with a session key… only from the browser"). So the stored-completions fan-out below cannot see them at all.

The `llmCall` ledger (BigQuery `aura-main.llm.llm_call`, read by the bundled `llm_ledger.py`) is the index instead. It filters on `transaction`, `userId` and `openaiId` — the three pivots this skill already has — and each row carries `project`, so a `resp_` id resolves to the right project key on the **first** attempt rather than fanning out across projects and reading 404s as misses. Requires **`GCP_KEY`** alongside the per-project OpenAI keys.

All three pivots handle it:

| Pivot | Behaviour |
| -- | -- |
| `--transaction` | merges Responses calls into the cross-project view |
| `--span` | accepts a `resp_…` id, which is what the focus mode's own "Drill in" hints emit — so a hint can be pasted straight back in. Also accepts `--project` to retrieve directly without the ledger |
| `--user` | interleaves both surfaces, sorted together |

Transcripts for a `resp_` id come from `/{id}/input_items`. The response object's own `input` is **null**, so reading only the object would render an assistant-only trace that looks complete.

**If the ledger read fails**, the skill warns on stderr and returns Chat-Completions-only results. Treat that as *incomplete*, not as the answer — for most projects it means seeing nothing at all.

## The OpenAI access pattern (what the scripts rely on)

Verified empirically 2026-06-05; canonical doc is AI Knowledge `Tools/OpenAI` → "Reading stored completions (BT migration access pattern)".

- **Auth (dual-key since [PROD-3572](https://linear.app/aura-health/issue/PROD-3572)):** `OPENAI_ADMIN_API_KEY` (Admin key, `sk-admin-…`, Owner role) is used **only** for the `GET /v1/organization/projects` discovery call. Every stored-completions read (`/v1/chat/completions`, `/{id}`, `/{id}/messages`) uses a project-scoped service-account key `OPENAI_<PROJECT>_API_KEY` — the Admin key can't hold the `model.request` scope OpenAI now enforces on completions reads. The env var name is derived from the project's display name (`Memory [Production]` → `OPENAI_MEMORY_API_KEY`, `Conversation [Production]` → `OPENAI_CONVERSATION_API_KEY`, `Journal [Production]` → `OPENAI_JOURNAL_API_KEY`). The client auto-resolves the right project key per project during fan-out; if a project's key isn't set, that project is skipped with a stderr warning (soft miss) rather than aborting the run.
- **Project scoping:** every request needs an `OpenAI-Project: <proj_id>` **header** (not a query param). A completion can only be retrieved against its owning project — wrong/no project → HTTP 404. A bare `chatcmpl-…` id does NOT encode its project, so resolving an id means fanning out across the production projects until one returns 200.
- **Server-side metadata filters** combine as AND: `metadata[user]`, `metadata[name]`, `metadata[transaction]`, `metadata[conversationId]`, `metadata[dialogueId]`. The skill always prefers a server-side filter over a client-side walk-and-filter.
- **Pagination cap is 100.** Passing `limit > 100` silently returns an empty page, so the client hard-caps at 100 and paginates via `after` / `has_more` / `last_id`.
- **No server-side time filter.** For time-bounded queries (Mode 1 triage, user pivot) the client walks newest-first and stops when `created < since_unix` (`created` is unix seconds).
- **No timing/latency data** on completions — only `created` + token `usage`. The skill never surfaces latency.

## The metadata model

Every completion carries `metadata`:

`user` (Firebase UID) · `name` (the prompter — e.g. `coach-prompter`, `memory-ingest-prompter`; this is what Braintrust called `metadata.prompterName`) · `conversationId` · `dialogueId` · `transaction` · `model` · `service` · `version` · `platform` · `premium` · `aiPremium` · `aiPremiumState`.

The structured guardrail envelope (when a prompter emits one) is the assistant message content: `choices[0].message.content` — typically a JSON string carrying `{result, seriousHarmFlagSet, seriousHarmEvidence, …}`. The system prompt (incl. any `## User-Provided Summary` block) is the system message, fetched via `/chat/completions/{id}/messages`.

> **⚠️ Hard rule:** NEVER group / join / aggregate by `metadata.transaction` — it's a thread/session id that can span multiple unrelated conversations and users. It IS fine to *filter* by it (Mode 2 fans out by transaction — that's selecting all completions of one session, not aggregating). Group / join on `conversationId` / `dialogueId`.

## Inputs

The user provides one of:

- **An OpenAI Logs URL** — `https://platform.openai.com/logs/<chatcmpl-id>` (or a bare `chatcmpl-…` id). Auto-detected; the skill extracts the completion id and fans out to find the owning project.
- **A transaction UUID** to fan out across all production projects.
- **A specific completion id (`chatcmpl-…`)** to deep-trace. `--project` optional — without it the skill fans out to locate the completion.
- **A free-text triage intent** like "what fired in Memory in the last 4h" — `--project <name|id> --since <duration>`.
- **A Firebase UID** for a user-pivot view across projects.

## Workflow

### Resolve mode and inputs

| User said… | Mode | CLI flags |
|---|---|---|
| Pasted a `platform.openai.com/logs/…` URL or a bare `chatcmpl-…` id | Auto-detect from URL | `--url "<url-or-id>"` |
| "trace transaction `<uuid>`" or "what does OpenAI have for `<uuid>`" | **Focus (Mode 2)** — cross-project fan-out | `--transaction <uuid>` |
| "what fired in `Memory` in the last 4h" | **Triage (Mode 1)** | `--project Memory --since 4h` |
| "memory ingest guardrails today" | **Triage (Mode 1)** with prompter + status filter | `--project Memory --since 24h --prompter memory-ingest-prompter --status guardrail` |
| "drill into completion `<chatcmpl-id>`" | **Trace (Mode 3)** | `--span <chatcmpl-id>` (add `--project Memory` to skip the fan-out) |
| "what's user `<uid>` doing across pipelines" | **User pivot** | `--user <firebase-uid> --since 7d` |

Defaults when the user is vague: `--since 1h` (triage / user). There is no `--lookback` anymore — OpenAI has no server-side time filter, and the metadata filters already scope the result set. (`--lookback` is still accepted for CLI back-compat but ignored, with a note on stderr.)

### Run the query

Invoke the CLI from anywhere:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/investigate-openai-trace/scripts/investigate.py <flags>
```

Pass the digest through to the user verbatim — every output ends with a `→ Drill in:` hint that the user can copy-paste back as their next prompt.

### Don't auto-chain

The skill never picks which completion to drill into. The engineer reads the triage / focus digest, decides which completion is interesting, and pastes the suggested Mode 3 command back. Same for Mode 2 → Mode 3.

### PII guardrail

- **Triage (Mode 1), Focus (Mode 2), and User pivot** echo only structured fields — prompter name, model, the parsed envelope result / `seriousHarmFlagSet` / `seriousHarmEvidence`, transaction UUID, user ID (Firebase UID), `conversationId`, `dialogueId`, `created`. They never echo the request `input` messages, the assistant `content`, or the system prompt.
- **Trace (Mode 3)** echoes the full input + output + metadata by design. The engineer has explicitly drilled into one completion, so this is intentional. Aura's traces routinely contain user conversation text — treat Mode 3 output as user-conversation-grade sensitive.

### memoryIngest summary extraction

In Trace mode, when the completion is a memory-ingest call, the renderer scans the system message (fetched via `/messages`) for a `## User-Provided Summary` section and extracts the block. This skips the manual "scroll past 50K of prompt to find the summary" step.

The section runs from the `## User-Provided Summary` sentinel to **end-of-prompt** (or to a trailing `## User Information` block when one exists). The user-provided summary itself may contain `##`/`###`/`####` subheadings and horizontal rules — those don't terminate the slice. The extractor (`_extract_user_block`, shared verbatim with [`eng:investigate-memory-ingestion-guardrail`](../investigate-memory-ingestion-guardrail/SKILL.md)) captures the full multi-section paste; the old same-level-heading extractor truncated at the first sub-heading and is not reintroduced.

### Mode 2 output contract — what you can and can't chain on

Mode 2 (`--transaction <uuid>`) returns every completion across every production project that carries `metadata.transaction = <uuid>`. The structured fields it surfaces (and you can rely on chaining downstream) are:

* completion `id`, `metadata.name` (prompter), `model`, `created`
* `metadata.transaction`, `metadata.user` (Firebase UID)
* `metadata.conversationId`, `metadata.dialogueId`
* parsed envelope: `result` / `seriousHarmFlagSet` / `seriousHarmEvidence` (when present)
* the OpenAI Logs URL per match (`https://platform.openai.com/logs/<id>`)

**Not chainable as-is:** `metadata.conversationId` on **memory-ingest** completions. The field IS populated but holds the memory-ingestion run's own id — **not** the user's live coach conversation in the Conversation project. To find a plausibly-related live conversation, use `metadata.user` + the transaction's `created` timestamp to bracket a search in the Conversation project (e.g. `data:analyze-user-conversations --user-id <uid>` with a window that includes the transaction time).

## Setup (one-time, per user)

**1. OpenAI API keys (dual-key since [PROD-3572](https://linear.app/aura-health/issue/PROD-3572)).**

- **Admin key — `OPENAI_ADMIN_API_KEY`.** Still required, but used only for the `GET /v1/organization/projects` discovery call. Same key the sibling cost skills (`eng:analyze-openai-costs`, `eng:report-openai-user-cost`) use. Create at <https://platform.openai.com/settings/organization/admin-keys> (requires Owner role on the Aura OpenAI org). Add to `~/.claude/settings.json` under the `env` block as `OPENAI_ADMIN_API_KEY` (`sk-admin-…`). A missing Admin key is a **hard fail** — without discovery the skill can't enumerate the production projects.
- **Project-scoped keys — `OPENAI_<PROJECT>_API_KEY`.** One per OpenAI production project; required for all stored-completions reads (the Admin key can't hold the `model.request` scope). Create at <https://platform.openai.com/api-keys> → Create new secret key → Owned by: **Service account** → Project: `<name>`, and store it under the env var derived from the project display name — `Memory [Production]` → `OPENAI_MEMORY_API_KEY`, `Conversation [Production]` → `OPENAI_CONVERSATION_API_KEY`, `Journal [Production]` → `OPENAI_JOURNAL_API_KEY`, etc. A missing project key is a **soft miss** — that one project is skipped with a stderr warning; the rest of the fan-out still runs.

Restart Claude Code after adding any key.

**2. Python deps.** `python3 -m pip install --user -r ${CLAUDE_PLUGIN_ROOT}/skills/investigate-openai-trace/scripts/requirements.txt`. Just `requests`.

There is **no MCP fallback** for these OpenAI endpoints. Full walkthrough in [`references/auth-setup.md`](references/auth-setup.md).

**Cowork caveat ([PROD-1305](https://linear.app/aura-health/issue/PROD-1305)):** `mcp__workspace__bash` strips host env. Inside Cowork, paste-export the Admin key **and** the project keys you need once at session start:

```bash
export OPENAI_ADMIN_API_KEY='<paste from host ~/.claude/settings.json>'
export OPENAI_CONVERSATION_API_KEY='<paste from host ~/.claude/settings.json>'
export OPENAI_MEMORY_API_KEY='<paste from host ~/.claude/settings.json>'
# … plus any other OPENAI_<PROJECT>_API_KEY the investigation needs
```

## Output

Each mode produces a fixed-shape text digest:

- **Mode 1 — triage** — one block per completion (prompter name, model, parsed result, transaction, user, created, drill-in).
- **Mode 2 — focus** — completions grouped by project, each with the chainable fields and the OpenAI Logs URL so the engineer can open the trace in a browser when needed.
- **Mode 3 — trace** — header (project, created, model, transaction, user, conversationId, dialogueId, token usage, Logs URL), full `Input` (from `/messages`) / `Output` (`choices[0].message.content`) / `Metadata` sections, and the memoryIngest `## User-Provided Summary` block when extractable. Ends with `→ Done`.
- **User pivot** — completions grouped by project, with a `--transaction` drill-in suggestion per row.

Every output ends with `→ Drill in: investigate openai trace <next-step-flags>` (or `→ Done — last mode in the chain`). The OpenAI queries the skill ran are logged to stderr with a `#` prefix.

## Project inventory

The skill **auto-discovers** the org's production projects at runtime — `GET /v1/organization/projects`, keeping the ones whose name ends in ` [Production]` (the same discovery `eng:report-openai-user-cost` uses). There is no hard-coded project table to maintain; new pipelines appear automatically. The live set is ~13–17 projects (2026-06-05: Content, Conversation, Evaluations, Feed, Guardrail, Insight, Intent, Journal, Memory, Notification, Onboarding, Profile, Search, Suggestions, Summary, Support, Translation).

Project names resolve fuzzily against the live list: `mem` → `Memory`, the `proj_…` id directly → that project, a typo → a list of suggestions. Key ids for reference (documentation anchors only — the live list is authoritative): Conversation `proj_esFyd4eq2VIAjKUn69X1ZFfk`, Memory `proj_f0PpUSyYnJNifUZj44FrjUhr`, Journal `proj_qXZ9CZnjp4mL5PVsW43JyOpj`.

Re-fetch the live list any time via:

```bash
curl -sS -H "Authorization: Bearer $OPENAI_ADMIN_API_KEY" \
  "https://api.openai.com/v1/organization/projects?limit=100&include_archived=false" \
  | python3 -c "import sys,json; [print(p['id'], p['name']) for p in json.load(sys.stdin)['data'] if p['name'].endswith(' [Production]')]"
```

## Edge cases

- **Transaction not found in any project** → "0 completions matched" with the two likely causes (the transaction predates OpenAI's metadata rollout 2026-05-12 or is older than the stored-completion retention horizon; or the UUID is wrong). `metadata.transaction` is a server-side filter, so a 0-row result is authoritative — there's no lookback window to widen.
- **Completion id not found (Mode 3)** → with an explicit `--project`, a 404 prints "not found in project `<X>` — drop `--project` to fan out". Without `--project`, the fan-out tries every production project and reports "not found in any of the N production projects" if none returns 200.
- **Auth fails (401)** → clean message with the regenerate-key (Admin, Owner role) + add-to-settings + restart fix.
- **Project name typo** → fuzzy suggestions from `projects.suggest()` against the live list; if no single match, lists the closest 5.
- **Per-project fan-out failure** (e.g. one project transiently 5xxs through all retries) → the focus / user-pivot mode skips that project to stderr rather than failing the whole run.
- **Legacy Braintrust URL pasted** → the skill detects the `braintrust.dev` host and prints "this skill migrated to OpenAI logs; paste a `platform.openai.com/logs/<id>` URL or a `chatcmpl-…` id" rather than guessing.
- **URL with no extractable id** → returns an explicit "could not extract a completion id" message.
- **`--status guardrail` returns 0** → expected on projects whose prompters emit a free-text completion (no `{result, …}` envelope). The filter parses the assistant `content` as JSON and matches `result == 'guardrail'` or `seriousHarmFlagSet == true`; for free-text prompters it's intentionally a no-op.
- **Two-signal pitfall — independent layers, same hour, different content.** When a Memory-project transaction analysis is followed by a Conversation-project conversation analysis for the same user in a short timeframe, treat them as **independent investigations**. The shared timeframe is often coincidental — same user, two layers, two different signals. Concrete example: a memory-ingest guardrail can fire on past-trauma language in a pasted "Bring your memory" summary at 00:10Z; the same user's live coach conversation 30 min later might disclose unrelated passive-ideation language at 00:43Z. Don't assume one explains the other — pull each layer separately.
- **Re-running the same query** → safe (read-only); no idempotency concern.

## When to use this vs siblings

- **Use this** when a transaction UUID, completion id, or pipeline-level question is the entry point. Memory / Conversation / Guardrail / Quick Processing / Journal / Summary / Suggestions / Profile / Onboarding / Intent / Notification / Feed / etc.
- **Use [`eng:investigate-gcp-logs`](../investigate-gcp-logs/SKILL.md)** when the entry point is a service-layer log (HTTP request, Postgres query, container error). GCP logs don't see inside the model call. Both can be combined: GCP for the HTTP entry, this skill for the model trace.
- **Use [`eng:investigate-bugsnag-errors`](../investigate-bugsnag-errors/SKILL.md)** when the entry point is a Bugsnag error URL or class. Different surface (client/server exceptions), different query language.
- **Use [`eng:investigate-memory-ingestion-guardrail`](../investigate-memory-ingestion-guardrail/SKILL.md)** when the entry point is a `k8bot :warning: Memory ingestion guardrail triggered` alert and you want the guardrail-specific synthesis + CS-email draft. That skill is the guardrail-specific layer on top of this one.
- **Use [`data:analyze-user-conversations`](../../../data/skills/analyze-user-conversations/SKILL.md)** for higher-level conversation-quality analysis. This skill is for completion-level debugging, not narrative analysis.

## References

- [`references/openai-query-recipes.md`](references/openai-query-recipes.md) — per-mode OpenAI query templates, the metadata field reference, pagination + project-header gotchas, the `curl` equivalents.
- [`references/auth-setup.md`](references/auth-setup.md) — one-time Admin API key creation walkthrough, settings.json location, Cowork paste-export workaround.
- [Linear PROD-2464](https://linear.app/aura-health/issue/PROD-2464) — the Braintrust → OpenAI migration for this skill.
- [Linear PROD-2456](https://linear.app/aura-health/issue/PROD-2456) — umbrella: migrate AINative skills off Braintrust.
- [Linear PROD-1903](https://linear.app/aura-health/issue/PROD-1903) — original (Braintrust-era) design spec and test plan.
- `Tools/OpenAI` in AI Knowledge — auth, the "Reading stored completions" access pattern, the metadata field reference, the production-project inventory, and the `metadata.transaction` "never group by this" warning.
