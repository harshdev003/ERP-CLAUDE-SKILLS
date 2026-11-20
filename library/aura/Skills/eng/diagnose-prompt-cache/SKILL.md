---
name: diagnose-prompt-cache
description: >
  Explain why an Aura prompter misses OpenAI's prompt cache. Part 1 (read-only, any model)
  reads the IntelligenceService LLM-call ledger and reports the prompter's cache hit rate,
  the share of calls with zero cached tokens, an hourly trend with the step change flagged,
  splits by platform, version, service tier and model, and whether misses cluster by
  conversation. Part 2 (paid, GPT-5.6+ only) replays pairs of real calls with OpenAI's prompt
  cache diagnostics and groups the misses by reason. Trigger on "diagnose prompt cache",
  "why is the cache hit rate low", "prompt cache misses", "cache hit rate dropped", "why
  aren't we getting cached tokens", "zero cached tokens", "check the prompt cache for
  coach-prompter", or "run prompt cache diagnostics". Do NOT use for the cache rate and its
  cost day by day (eng:report-openai-cache), dollar reports (eng:analyze-openai-costs) or raw
  token counts (eng:analyze-openai-tokens).
metadata:
  version: "1.2.1"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/OpenAI       # per-model rates (to price a replay) and service-tier table
    - Tools/Aura API     # LLM-call ledger semantics (now read from BigQuery llm.llm_call)
    - Compliance/Privacy # replays read real user prompts; the output must stay free of them
---

# Diagnose Prompt Cache

## Goal

Tell the user **whether** a prompter is missing OpenAI's prompt cache, **since when**,
**where** (platform, version, tier, model), and — on models that support it — **why**. Lead
with the answer, not the tables.

Two facts about OpenAI's cache decide how to read every number here:

- **The cache lives on individual machines.** A request only hits it if it is routed to a
  machine that recently served the same prefix. Routing hashes the start of the prompt plus
  the request's `prompt_cache_key`; above ~15 requests a minute per prefix + key, traffic
  overflows to machines with nothing cached. That failure looks like **random full misses**
  (zero cached tokens) scattered through otherwise-healthy conversations.
- **A prefix change looks different.** Something dynamic early in the prompt (a timestamp,
  a reordered tool list, a shifting history window) produces **partial** cache hits that stop
  at the same boundary, or misses on every turn of a conversation.

Mechanics: [OpenAI prompt caching guide](https://developers.openai.com/api/docs/guides/prompt-caching),
[diagnostics](https://developers.openai.com/api/docs/guides/prompt-caching/diagnostics).

## Inputs

- **Prompter** — the ledger's `prompterName`, e.g. `coach-prompter`. Ask if not given.
- **Window** — default the last 24 hours. For "when did it start", widen it to span the
  change (e.g. a day either side).
- **Replay** (Part 2) — only if the user asks for the reason behind misses, the model is
  GPT-5.6+, and they approve the spend.

## Step 0 — Prerequisites

```bash
test -n "$GCP_KEY" && echo "OK: GCP_KEY" || echo "MISSING: GCP_KEY"
```

Part 1 needs only `GCP_KEY` (the ledger is BigQuery `aura-main.llm.llm_call`). Part 2 additionally needs a key
for the OpenAI **project that made the calls**, exported as `OPENAI_PROJECT_API_KEY` (or name
another variable with `--key-env`). The org admin key is **not** enough: it lacks the
`api.responses.read` scope needed to read a stored request's inputs.

## Part 1 — Ledger report (read-only, every model)

```bash
python3 scripts/ledger_report.py --prompter coach-prompter --hours 24
python3 scripts/ledger_report.py --prompter coach-prompter \
  --since 2026-10-07T12:00:00Z --until 2026-10-08T00:00:00Z --json /tmp/cache.json
```

It prints, from BigQuery `llm.llm_call` (metadata only — no prompt text):

- **Hit rate** (cached ÷ input tokens) and **zero-cache share** for the window
- **Median input vs median cached-on-hit** — the size of the prefix that *can* be shared
- **Step change** — the first hour (≥50 calls) whose zero-cache share rises `--step-points`
  (default 20) above the window's opening quarter
- **Conversation pattern** for conversations with 3+ calls: mixed / all-miss / all-hit
- **Hourly** table and **splits** by `platform`, `version`, `serviceTier`, `model`

### Reading it

| Pattern | Likely cause | Next step |
|---|---|---|
| Zero-cache share jumps at an hour with no version change; misses are **mixed** within conversations | Routing overflow — the shared prefix gets more traffic than one route holds | Set or re-bucket `prompt_cache_key` (target ~15 req/min per key) |
| Step lines up with a **version** row changing | A deploy changed the prompt prefix | Diff that release's prompt assembly |
| Misses concentrated in one **serviceTier** or **model** | Tier or model routing | Compare those rows; check the model alias didn't move |
| Cached-on-hit far below input on **every** call | Dynamic content early in the prompt | Move timestamps and per-turn data after the stable part |
| **All-miss** conversations dominate | The per-conversation part of the prefix never matches | Inspect history assembly (sliding windows, regenerated ids) |

State which row fits and how confident you are. A step with no version change and mixed
misses points at OpenAI's routing; say so, and note that only a `prompt_cache_key` change on
our side or a fix on OpenAI's side will move it.

## Part 2 — Diagnostics replay (paid, GPT-5.6+ only)

Use it when Part 1 shows misses and the user wants the reason. OpenAI answers diagnostics only
on the Responses API for GPT-5.6+; older models (including `chat-latest` as of 2026-10)
answer `unavailable`.

**1. Plan — always first, spends nothing:**

```bash
python3 scripts/replay_diagnostics.py --prompter coach-prompter --pairs 3 --hours 2
# or explicit pairs (earlier response, later response):
python3 scripts/replay_diagnostics.py --pair resp_A resp_B
```

It picks consecutive turns of one conversation (≤10 min apart, preferring pairs whose later
call missed), reads each original's settings and inputs from OpenAI, and prints the number of
replay calls, the model(s) and an input-token upper bound.

**2. Price it and get a yes.** Multiply the token bound by the model's input rate from
`Tools/OpenAI` → per-model rates, and tell the user the figure. Each pair costs two or three
calls; replays cap output at `--max-output-tokens` (default 512). Wait for approval.

**3. Run** with `--yes`. For each pair it replays A, waits `--gap` seconds (default 8),
replays B with `prompt_cache_options.comparison_response_id` pointing at the replayed A, and
retries B once if the answer is `unavailable`. Two `unavailable` answers on a pair stop the run:
the model has no diagnostics, so don't pay for the remaining pairs.

**Replayed A must complete.** A comparison against a response that ended `incomplete` comes back
`comparison_response_not_found` or `unavailable`, which looks like a model limitation and isn't.
A reasoning model on a tight output cap runs out before answering (measured: 16 tokens →
`incomplete`). The script checks A's status and reports `a_not_completed` for that pair; raise
`--max-output-tokens` and re-run rather than concluding the model has no diagnostics.

Replays run on the **default** tier, not flex. This is a deliberate exception to the
paid-inference rule: on flex, diagnostics always answer `unavailable`, so a flex replay pays
for a request that cannot measure anything (verified 2026-10-08 on `gpt-5.6-terra`).

**4. Report** the outcomes grouped by `reason`:

| `reason` | Meaning | Fix |
|---|---|---|
| `input_changed` | Earlier input differs — timestamps, request ids, edited or shifted history | Move dynamic content after the stable prefix; append turns instead of rewriting |
| `tools_changed` | Tools added, removed, reordered or edited | Keep definitions and order stable |
| `prompt_cache_key_changed` | The key differed between the turns | Keep one key per group (expected once after a prompt-version bump) |
| `model_changed` / `service_tier_changed` | A different model or tier served one turn | Pin both for requests meant to share a prefix |
| `cache_hit` | No miss between these two turns | The misses come from routing, not the prompt — back to Part 1 |

Both turns are replayed from the same stored source, so the comparison reports differences
between the turns themselves, not between a replay and its original.

## Output

1. One-line verdict: is the prompter missing the cache, since when, and the likely cause.
2. The Part 1 summary bullets and the hourly table around the step, not the whole window.
3. Part 2's reason counts, if it ran, with the fix for the top reason.

**Privacy (`Compliance/Privacy`).** Never print prompt text, model output or a full user id.
The scripts print only response ids, token counts and diagnostics. If you cite a user, use a
UID prefix.

## Edge cases

- **No ledger rows** — wrong `prompterName`, or the prompter runs outside the IntelligenceService
  (the ledger only records production IntelligenceService traffic).
- **Ledger read failed** — `GCP_KEY` unset, or its service account lost BigQuery read on `aura-main.llm`.
- **`api.responses.read` missing** — the key is the admin key or for a different project.
- **Original not retrievable** — stored responses expire; pick a more recent window.
- **Re-running is safe.** Part 1 only reads. Part 2 creates new replay responses each run;
  it changes nothing else.
