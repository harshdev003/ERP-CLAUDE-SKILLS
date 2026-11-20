---
name: qa-prompt
description: >
  QA a Braintrust prompt before deploying it: fetch the prompt and its tools, pick each
  tool's response strategy, drive the conversation (replay a dataset row, or Claude simulates
  the user), and score it with GPT rubrics. OpenAI Chat/Responses, Gemini and Claude,
  auto-detected from the prompt's model. Trigger on "QA this prompt",
  "QA the new Braintrust prompt", "test the new prompt before deploy", "run prompt QA",
  "simulate a user against this prompt", "QA with responses API", "QA this Gemini prompt", "QA
  this Claude prompt", "compare these two prompts", "compare prompt A vs B", "side by side",
  "run a sweep against {prompt}", "test {prompt} on multiple personas", or a pasted Braintrust
  prompt URL with intent to validate it. Also triggers on "check the prompt at {braintrust.dev
  URL}", "test the Sonnet prompt", "test claude-sonnet", "QA {prompt} across N scenarios", "QA
  all 4 scenarios". Do NOT use to score a whole dataset across prompt versions as Braintrust
  experiments (`eng:compare-prompt-versions`).

metadata:
  version: "3.3.4"
knowledge:
  reads:
    - Guide/Index           # baseline routing aid (universal)
    - Tools/Braintrust      # prompt + dataset row + tool function endpoints
    - Tools/OpenAI          # Chat Completions + Response API access pattern
    - Tools/Gemini          # google-genai SDK + function-calling access pattern
    - Tools/Anthropic       # anthropic SDK + Messages API + tool_use shape
    - Products/Aurie   # brand voice anchor used by the self-critique pass
---

# QA Prompt

Drive a Braintrust prompt through one or more OpenAI runs. Claude orchestrates
the run as a sequence of small Python steps, decides each tool's response
strategy with you upfront, replays a dataset row or simulates the user against
a scenario, then scores the result with a GPT rubric and a Claude qualitative
read.

## Prerequisites

- `BRAINTRUST_API_KEY` in `~/.claude/settings.json` `env` — required to fetch
  the prompt definition, fetch the registered tool function schemas, and
  invoke real Braintrust tools. Obtain at
  <https://www.braintrust.dev/app/aura-health/settings/api-keys>.
- `OPENAI_EVALUATIONS_API_KEY` in `~/.claude/settings.json` `env` — required to drive
  OpenAI prompts AND to run the self-critique scorer for every QA run
  (regardless of provider; the scorer stays on OpenAI for consistency). This is
  a **project-scoped service-account key** for the OpenAI **`Evaluations
  [Production]`** project — the eval/QA project every AINative testing skill
  bills to. It is deliberately *not* a product project: `Evaluations` is the sole
  member of `INTERNAL_PROJECT_BASE_NAMES` in `eng:analyze-openai-cost-per-user`,
  so QA spend is excluded from per-user attribution **and** from the coverage
  ratio instead of being divided across real users
  ([ENG-1171](https://linear.app/aura-health/issue/ENG-1171)). It is an
  **OpenAI** key (used for
  GPT inference + scoring), distinct from `ANTHROPIC_API_KEY` (used only for
  Claude-*model* prompts). The scripts pass it explicitly to `OpenAI(api_key=…)`,
  so the SDK's `OPENAI_API_KEY` auto-read is not relied on. Create at
  <https://platform.openai.com/api-keys> → Create new secret key → Owned by:
  Service account → Project: `Evaluations [Production]`.
- `GEMINI_API_KEY` in `~/.claude/settings.json` `env` — required ONLY when
  QAing a Gemini prompt (i.e. the prompt's model starts with `gemini-` or
  `google/`). Verified at setup_run.py time after the prompt is fetched, so
  OpenAI-only runs don't need the key. Obtain at
  <https://aistudio.google.com/apikey> under your aurahealth.io Google
  account.
- `ANTHROPIC_API_KEY` in `~/.claude/settings.json` `env` — required ONLY
  when QAing a Claude prompt (i.e. the prompt's model starts with `claude-`
  or `anthropic/`). Verified at setup_run.py time after the prompt is
  fetched, so non-Anthropic runs don't need the key. Obtain at
  <https://console.anthropic.com/settings/keys> under the Aura Anthropic
  org.
- Python packages: `openai`, `requests`, (for Gemini runs only)
  `google-genai` ≥ 2.5.0, and (for Anthropic runs only) `anthropic` ≥ 0.104:
  ```bash
  python3 -m pip install --user openai requests google-genai 'anthropic>=0.104'
  ```
  `google-genai` and `anthropic` are deferred-imported inside `send_turn.py`
  so OpenAI-only runs work without either installed; only the matching
  provider runs raise if the package is missing.

Braintrust + OpenAI tokens are hard requirements (Phase 0 fails fast). The
Gemini and Anthropic keys are soft requirements — setup_run.py only checks
each one after detecting the prompt resolves to the matching provider. If
any required key is missing, run the `update-config` skill to add it, then
restart Claude Code so the new env values load.

**Cowork caveat.** Per `AINative/CLAUDE.md`, `mcp__workspace__bash` strips host
env (PROD-1305), so `OPENAI_EVALUATIONS_API_KEY` and `BRAINTRUST_API_KEY` are not
inherited inside a Cowork sandbox. At the start of a Cowork session,
paste-export both keys once.

## The four phases

Each phase is implemented as one or more small Python steps that read/update a
shared `state.json` file (default location: `outputs/qa-prompt-<slug>-<ts>/state.json`).
Claude is the orchestrator — it runs the steps, surfaces tool calls to you via
`AskUserQuestion`, and composes simulated user turns in interactive mode.

### Phase 0 — Prereq check (fail fast)

Before any prompt fetch, before any tool-default questions, before spawning any
subagent in a sweep — verify the env qa-prompt's downstream scripts need is
actually present. Skipping this just defers the same failure to Phase 3's first
`send_turn.py` call, after the user has already burned time clicking through
tool-default questions and (in a sweep) any spawned subagents have failed
identically.

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/prereq_check.py
```

Exits 2 with a clear remediation message if `OPENAI_EVALUATIONS_API_KEY` or
`BRAINTRUST_API_KEY` is missing. `setup_run.py` and `setup_sweep.py` both call
the same check internally on the first invocation in a session — Phase 0 just
surfaces the failure earlier when the orchestrator already knows it'll need
multiple downstream calls.

### Phase 1 — Gather

Fetch the prompt, its model, params, inline tools, and referenced
`tool_functions[]` schemas; resolve the context source; render the template.

**Pick the provider with `--provider {openai,google,anthropic}`** on both
`setup_run.py` and `setup_sweep.py` — default auto-detects from the prompt's
model (`gemini-*`/`google/*` → google, `claude-*`/`anthropic/*` → anthropic,
else openai); override only when the model string is non-canonical.
**Pick the OpenAI API with `--api {chat,responses}`** (openai-provider runs
only; default `chat`, ignored for google/anthropic) — match the API the
production prompter uses.

**Ask which service tier before the first call, and offer flex first.** QA
inference has nobody waiting on it, and an omitted tier bills at standard — so
the expensive choice is the one you get by saying nothing. Ask the user once,
with the cost attached: **flex** 0.5x (the default, right for any ordinary QA
run), standard 1x, fast 2x. Pass the answer as
`--service-tier {flex,standard,fast}` to `setup_run.py` / `setup_sweep.py`; it
lands on the state and every later call inherits it.

> Precedence, the unattended-run env vars, what happens when a tier is refused
> or misspelled, and why a prompt's own `service_tier` is ignored:
> [`references/service-tier.md`](references/service-tier.md). Rates and model
> eligibility are canonical on AI Knowledge `Tools/OpenAI`.

> Provider + API selection edge cases, the Responses statelessness contract, and the full per-provider param translation (Gemini / Anthropic model+param mapping, `verbosity` / `reasoning_effort` sentinel handling, Responses nested-kwarg mapping): [`references/provider-translation.md`](references/provider-translation.md).

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/setup_run.py \
  <braintrust-prompt-url> \
  --mode dataset \
  --context dataset:<slug>:<row-id> \
  --dataset-id <uuid> \                             # optional — pass when the dataset lives in any project OTHER than Conversation (e.g. Memory, Quick Processing). When set, --context can be `dataset:<row-id>` (slug ignored).
  --user-id <uid> \                                 # optional, only used by --kind real on real-tool calls
  --version <_xact_id> \                            # optional, pins a Braintrust prompt version; omit for latest
  --api {chat,responses} \                          # optional, default chat — picks which OpenAI API send_turn.py hits (ignored when --provider google or --provider anthropic)
  --provider {openai,google,anthropic} \            # optional — auto-detected from bundle.model; override only when the model string is non-canonical
  --service-tier {flex,standard,fast} \             # optional, default flex (0.5x cost) — ask the user; recorded on the state and inherited by every later call
  --state-file outputs/qa-prompt-<slug>-<ts>/state.json
```

The dataset row's `input` is auto-detected as either chat messages or a
structured JSON object (per-field template variables), and its `expected`
field is persisted for the `structured-plan-match` scorer. For interactive
mode, pass `--mode interactive` with `--scenario` and either `--context`
(seed metadata from a dataset row) or `--no-context` (empty seed) — the two
are mutually exclusive and one is required; the context row's `input`
messages are discarded (Claude composes user turns) while its metadata still
flows through.

> Dataset-row input shapes + interactive-mode invocation recipes: [`references/setup-recipes.md`](references/setup-recipes.md).

After setup, Claude reads the state and surfaces a brief summary to you:
prompt name, model, scenario, declared tools, context warnings (if any).

### Phase 2 — Decide tool defaults (per drive)

For each declared tool, Claude asks you once via `AskUserQuestion` which
strategy to use for this drive. Four kinds:

1. **auto-mock** — `auto_mock.py` walks the tool's `returns` JSON schema and
   emits a shape-valid stub. Deterministic — same schema produces the same
   value every call.
2. **real** — `apply_tool_default.py` posts the model's arguments to
   `POST /v1/function/{id}/invoke` on Braintrust. Only available when the
   tool has a `function_id` (came via `tool_functions[]`, not inline JSON).
   For tools that need an Aura UID (today: `recommendTracks`), `--user-id`
   must have been passed to `setup_run.py`.
3. **canned** — you paste a JSON response once; `apply_tool_default.py`
   returns it verbatim on every call to that tool. Best for regression
   experiments where you want stable tool data.
4. **ask-per-call** — Claude pauses on each call and asks how to respond.
   This is also the implicit default when no choice is set.

Persist the choice:

```bash
# auto-mock / real / ask-per-call: no stdin needed
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/set_tool_default.py \
  --state-file <state.json> --tool-name <name> --kind auto-mock

# canned: pipe the JSON response
echo '{"tracks": [...]}' | python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/set_tool_default.py \
  --state-file <state.json> --tool-name <name> --kind canned
```

Mid-drive switch: any time before the next OpenAI turn, re-run
`set_tool_default.py` to change a tool's strategy.

### Phase 3 — Drive

**Surface each turn inline as the run progresses.** After each `send_turn.py`
call print the assistant text back to chat in a readable shape, and same for
every user message you compose in interactive mode, so the viewer follows the
conversation as it happens rather than waiting for the final report. Format:

```
**Turn N — user:** <text>

**Turn N — assistant:** <text>
```

Skip this surfacing when running as a subagent (chat output is captured and
replayed in bulk to the parent) — the parent reads the final transcript
section of the report.

#### Send one turn against OpenAI

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/send_turn.py \
  --state-file <state.json>
```

Stdout returns either `stop: true` (with the final assistant text) or
`stop: false` (with a `tool_calls` array — each entry carries `id`, `name`,
parsed `arguments`, and the matching tool registry entry).

#### Handle each tool call

For every entry in `tool_calls`, Claude runs:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/apply_tool_default.py \
  --state-file <state.json> \
  --tool-call-id <id-from-send_turn> \
  --tool-name <name> \
  --arguments '<json-string-of-parsed-args>'
```

- Exit 0 → response appended; loop back to `send_turn.py`.
- Exit 10 → "ask-per-call" (or no default set). Claude pauses, surfaces the
  tool call to you via `AskUserQuestion` with the four options. Whichever
  you pick is piped into `append_tool_response.py` with the matching
  `--source` tag.
- Exit 2 → error; surface stderr.

`auto_mock.py` and `invoke_real_tool.py` are still callable directly for a
one-off response without setting a default (e.g. "auto-mock just this once"
in an `ask-per-call` flow).

#### Drive shape: dataset vs interactive

- **Dataset mode**: `send_turn → apply_tool_default*` repeats until the
  model returns no more tool calls. Then the drive ends.
- **Interactive mode**: after the model returns a final assistant message
  (no tool calls), Claude composes the next user turn based on the scenario
  and the conversation so far, and pipes it into `append_user_message.py`:

  ```bash
  echo 'I just woke up and feel anxious' | python3 \
    ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/append_user_message.py \
    --state-file <state.json>
  ```

  Then loops back to `send_turn.py`. Stop conditions: the user (Jerome)
  interrupts and asks to end, the scenario naturally completes, or Claude
  decides it has enough material to score. There is no automatic turn cap —
  partial transcripts can be reported any time.

### Phase 4 — Score

#### Scorers are a guide, not the verdict

Before running anything, internalize how scores are meant to be used. The GPT
rubrics are a **first-pass signal, not the source of truth.** Two standing rules:

- **`expected` columns are never exact-match ground truth.** Grade on whether the
  output is *defensible* given the prompt's own written rules + the testing goals
  the user described at setup, using `expected` only as a reference example — a
  divergence matters only when it violates a rule or the stated goal.
- **Always sanity-check the scorer, and read the prompt yourself.** Spot-check
  graded rows; if the rubric flagged a defensible call as an error (or
  rubber-stamped a real defect), say so and **suggest a concrete revision** to the
  scorer template rather than reporting the number at face value. Anchor your own
  qualitative read on the prompt's intent and the user's goals.

> Full rationale + the post-scoring sanity-check procedure: [`references/scoring.md`](references/scoring.md#scorers-are-a-guide-not-the-verdict).

#### Pick the scorer(s)

Before running any scorer, Claude asks you which one(s) to apply via
`AskUserQuestion` (multi-select) — the full registry is shown; pick one,
several, or all. If you skip the question (e.g. a fully automated run), Claude
falls back to the default `aurie-conversation` scorer. Common picks:
`aurie-conversation` for dialogue prompts, all four memory scorers for
memory-extraction prompts, or just the one(s) you care about when
comparing/debugging. Templates live in
[`references/scorers/`](references/scorers/).

> Full scorer registry (six slugs — `aurie-conversation` default, the four memory scorers, `structured-plan-match` — with when-to-use + output shapes): [`references/scoring.md`](references/scoring.md#scorer-registry).

#### Run the chosen scorer(s)

For each scorer the user picked, run:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/self_critique.py \
  --state-file <state.json> \
  --scorer <slug>
```

Each call appends to `state.critiques[<slug>]` and mirrors the most
recent grade to `state.critique` for back-compat; the report renders
every scorer that was run. (A loop for running all four memory scorers,
and the per-scorer grading semantics below, are in the reference.)

The memory scorers grade the model's emitted memory *objects*, and the
`structured-plan-match` scorer judges whether the final JSON is a *defensible*
result against the prompt's own rules (not a field-by-field diff of `expected`).

> Per-scorer grading semantics: [`references/scoring.md`](references/scoring.md#per-scorer-grading-semantics).

Adding a new scorer: drop the template at
`references/scorers/<slug>.md` and register the slug in
[`scripts/self_critique.py`](scripts/self_critique.py)'s `SCORERS` dict.
It then shows up automatically in the Phase 4 picker.

#### Claude qualitative analysis

After self-critique runs, Claude reads the transcript from chat context and
writes a free-form QA analysis (strengths, weaknesses, concrete issues,
ship/no-ship recommendation) to a markdown file in
`outputs/qa-prompt-<slug>-<ts>/claude-analysis.md`, then passes it to
`write_report.py` via `--analysis-file`.

#### Write the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/write_report.py \
  --state-file <state.json> \
  --analysis-file outputs/qa-prompt-<slug>-<ts>/claude-analysis.md \
  --output-dir outputs
```

For sweep runs (or any run where multiple scenarios share an `--output-dir`),
pass `--label <slug>` so the filenames don't collide. Without it, parallel
scenarios overwrite each other because the deterministic filename is
`qa-prompt-<slug>-<timestamp>.md`. With it, the filename becomes
`qa-prompt-<slug>-<label>-<timestamp>.md`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/qa-prompt/scripts/write_report.py \
  --state-file <run-dir>/<label>/state.json \
  --analysis-file <run-dir>/<label>/claude-analysis.md \
  --output-dir <run-dir>/<label> \
  --label <label>
```

Emits `outputs/qa-prompt-<slug>-<timestamp>.md` (or
`outputs/qa-prompt-<slug>-<label>-<timestamp>.md` with `--label`) — an
11-section document (header, resolved context, conversation transcript, tool
registry, rendered messages, run timeline, token usage, final message, GPT
scorer grades, Claude analysis, sources).

> Full report-contents layout: [`references/scoring.md`](references/scoring.md#report-contents).

Alongside the report, `write_report.py` also writes a **standalone conversation
transcript** — `transcript.md` (or `transcript-<label>.md` with `--label`) in the
same `--output-dir`. It contains only the conversation (short header + paired
turns + inline tool calls/responses), so the dialogue is always available on its
own without scrolling the full report. `write_report.py` prints two lines: the
report path, then `TRANSCRIPT: <path>`. To produce that transcript on its own
(e.g. a partial run), run `write_transcript.py`.

> Standalone `write_transcript.py` recipe: [`references/scoring.md`](references/scoring.md#standalone-transcript).

**Standing rule — always surface the transcript.** End every run by surfacing the
full transcript file(s) as clickable markdown link(s) in your final chat message —
one for a single run, one per variant for a comparison, one per scenario for a
sweep. The reviewer wants to read the conversation itself, not only the scores or a
summary. Never deliver only results, scores, or a summary: the transcript link must
always be present, even when you also give a summary. Parse the `TRANSCRIPT:` line(s)
from the writer's stdout and render each as a markdown link, e.g.
`📄 Full transcript: [transcript.md](outputs/.../transcript.md)`.

Claude prints the report path and the `TRANSCRIPT:` path on completion.

## Multi-drive modes

Two modes layer on top of the four-phase contract above:

### Comparison runs — two prompt variants in parallel

One scenario, two prompt versions (or two prompt URLs) in lockstep. The
orchestrator parent owns the shared user-turn arc; a
`qa-prompt-variant-runner` subagent advances each variant by one assistant
turn at a time. Output: a side-by-side comparison report with a setup-parity
check + per-turn paired replies + side-by-side rubric scores.

Use when: you want to know whether prompt-edit-X improves on the current
prod prompt against a representative scenario.

Full how-to: [`references/comparison-runs.md`](references/comparison-runs.md)
+ [`plugins/eng/agents/qa-prompt-variant-runner.md`](../../agents/qa-prompt-variant-runner.md).

### Sweep runs — one prompt, N scenarios in parallel

One prompt version, N personas. The orchestrator parent fans the prompt out
across all N scenarios via `setup_sweep.py`, mirrors tool defaults across
all state files, then spawns N parallel `qa-prompt-sweep-runner` subagents —
each drives its scenario end-to-end (composing user turns, looping until
natural close or a 6-turn cap) and returns a structured summary. The parent
aggregates everything into a single cross-scenario report via
`write_sweep_report.py`.

Use when: you want pre-ship confidence that a prompt holds up across the
spread of emotional registers it'll see in prod (stressed / anxious /
grieving / depleted / etc.).

Full how-to: [`references/sweep-runs.md`](references/sweep-runs.md) +
[`references/sweep-subagent-prompt.md`](references/sweep-subagent-prompt.md)
(the battle-tested subagent prompt template — load-bearing for sweep
reliability) +
[`plugins/eng/agents/qa-prompt-sweep-runner.md`](../../agents/qa-prompt-sweep-runner.md).

## Invoking the skill

There is no single end-to-end CLI — the phases above are the contract.
Fastest start: paste the prompt URL into chat and tell Claude what you want.

- *"QA this prompt with row abc123 from feedback-conversation"* → dataset
  mode, Claude walks you through the four-phase flow.
- *"QA this prompt interactively with a stressed-parent scenario"* →
  interactive mode, Claude simulates the user.
- *"Compare these two prompts on a work-stress scenario: <url-A> vs <url-B>"*
  → comparison mode, Claude spawns two `qa-prompt-variant-runner` subagents
  in parallel per user turn and writes a side-by-side report.
- *"QA this prompt across 4 personas: stressed, anxious, grieving, depleted"*
  → sweep mode, Claude spawns four `qa-prompt-sweep-runner` subagents in
  parallel (one per scenario), drives each end-to-end, and writes a
  cross-scenario aggregated report.

For each declared tool, Claude will ask once how it should respond for the
drive (mirrored across both variants in comparison mode; mirrored across all
N scenarios in sweep mode).

## Edge cases

A full catalog of edge cases (no-tools prompts, invalid `returns` schemas,
`recommendTracks` without `--user-id`, missing `contextInstructions`, the
Anthropic `claude-opus-4-7` / `max_tokens` / `temperature` / empty-text-block
cases, Gemini schema + `thought_signature` cases, and loop-stuck handling)
lives in a reference.

> Full edge-case catalog: [`references/edge-cases.md`](references/edge-cases.md).

## Pairs with

- [`eng:update-intelligence-prompt`](../update-intelligence-prompt/) — "QA
  before you deploy." Run `qa-prompt` first to validate, then
  `update-intelligence-prompt` to ship.
- [`data:save-to-braintrust-dataset`](../../../data/skills/save-to-braintrust-dataset/) —
  turns a prod conversation into a dataset row. `qa-prompt` consumes those
  rows via `--context dataset:<slug>:<id>`.
- [`eng:compare-prompt-versions`](../compare-prompt-versions/) — the dataset-wide
  counterpart: every row through 2+ prompt versions, scored in code or by a rubric
  judge, one Braintrust experiment per version.

## Sources

The full source list — the AI Knowledge pages (Braintrust / OpenAI / Aurie),
the Linear tickets behind each phase and provider (ENGBACK-1029, PROD-1724,
ENGPROM-52/53/54), the Braintrust OpenAPI spec, and the prior hand-rolled QA
pass — lives in a reference.

> Full source + citation list: [`references/sources.md`](references/sources.md).
