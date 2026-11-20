# Provider param translation (Phase 1 — Gather)

Per-provider translation details for how a Braintrust prompt's model + params map onto each provider's API. `state.messages` always stays in OpenAI Chat-Completions shape; every translation below happens only inside `send_turn.py` at the API boundary.

## Provider + API selection detail

The `--provider` default auto-detects from the prompt's model — `gemini-*` or
`google/*` → `google`, `claude-*` or `anthropic/*` → `anthropic`, everything
else → `openai`. The override is for two edge cases: (a) a Braintrust prompt
whose model string is non-canonical (e.g. someone typed
`googleai/gemini-3.5-flash`), and (b) testing. The script errors clearly when
the explicit flag conflicts with the detected provider — pick one. The `--api`
flag is ignored when `--provider google` or `--provider anthropic` (each has
its own single API path).

For the OpenAI API choice, Braintrust does not record an API choice on prompts
— it lives in IntelligenceService's `config.prompt.*.openaiAPI` (per
`eng:report-intelligence-prompters`). Pick the API the production prompter
uses; if you're QAing a prompt that is migrating to Responses, run it
under `--api responses` (and compare against `--api chat` if you want the
two paths side by side via comparison mode). The skill is stateless under
Responses: the full `state.messages` list is re-sent every turn (no
`previous_response_id`, no Conversations object), so `state.json` stays the
canonical source of truth and sweeps + comparisons need no further changes.

**Gemini specifics.** `state.messages` stays in OpenAI Chat-Completions
shape for every provider — Gemini's `contents[]` translation happens only
inside `send_turn.py` at the API boundary. Param translation:
`temperature` / `top_p` pass through; `max_tokens` →
`max_output_tokens`; `reasoning_effort` → `thinking_config.thinking_budget`
(`none: 0`, `low: 128`, `medium: 1024`, `high: 8192`; verified live on
`gemini-3.5-flash` 2026-05-21); `tool_choice` → `tool_config`'s
function_calling_config mode (AUTO / NONE / ANY); `verbosity`,
`presence_penalty`, `frequency_penalty` are dropped with a stderr warning
(no Gemini equivalent). Tool ids are synthesized (`call_<turn>_<8hex>`)
because Gemini doesn't return them; they're internal-only and Gemini
matches function responses by `name` + position within the same turn.
Gemini 2.5+ thinking models attach a `thought_signature` blob to
function-call parts which we persist as base64 under
`_gemini_thought_signature` on the chat-shape tool_call entry and
re-attach on subsequent turns (without it Gemini 400s on the next call).
Tool schemas pass through under `parameters_json_schema` so the full JSON
Schema dialect (including `additionalProperties: false`) is accepted —
Gemini's `parameters` field is the stricter OpenAPI v3 subset.

**Anthropic specifics.** Same shape contract — `state.messages` stays in
OpenAI Chat-Completions shape; translation to Anthropic's `messages[]`
happens only inside `send_turn.py`. Param translation:
`temperature` passes through with a clamp to **≤1.0** (Anthropic's range is
0..1; OpenAI allows 0..2 — values >1.0 are clamped and surfaced in
`state.render_warnings`, visible in the final report); `top_p`/`top_k` pass
through; `max_tokens` / `max_completion_tokens` → `max_tokens` (REQUIRED
by Anthropic — defaults to 4096 with a warning if Braintrust didn't set
one); `reasoning_effort` → `thinking: {type: "enabled", budget_tokens: N}`
(`low: 1024`, `medium: 4096`, `high: 16384`; the API requires
`max_tokens > budget_tokens` so the skill auto-raises `max_tokens` to
`budget + 1024` with a warning when needed; verified live on
`claude-sonnet-4-5-20250929` 2026-05-21); `tool_choice "auto"` →
`{type: "auto"}`; `tool_choice "none"` → tools omitted from request entirely
(both `{type: "none"}` and tool-less requests are accepted by Anthropic, we
pick the latter to keep the request minimal); `tool_choice "required"` →
`{type: "any"}`; `tool_choice {function: {name}}` → `{type: "tool", name}`;
`verbosity`, `presence_penalty`, `frequency_penalty` are dropped with a
warning surfaced in `render_warnings`. Tool ids round-trip verbatim —
Anthropic accepts any string as `tool_use.id` (verified live), so the
OpenAI-style `tool_calls[i].id` flows through as Anthropic's
`tool_use.id` directly, no synthesis. Multiple `tool_use` blocks within
one assistant response collapse into one Chat-shape assistant message
with multiple `tool_calls[]` entries. The system message routes to a
top-level `system` kwarg (mirrors Gemini's `system_instruction`), NOT a
`messages[]` entry. Tool schemas pass through under `input_schema`. Empty
user text blocks are dropped before send (Anthropic 400s on
"text content blocks must be non-empty"). Anthropic's `thinking` content
blocks (extended-thinking models like `claude-sonnet-4-5`) are discarded
from `state.messages` and captured under `state.turns[].reasoning` with
the `signature` preserved; the `enabled` form is supported on the 4.5/4.6
series and `claude-sonnet-4-6` (no warning). Note `claude-opus-4-7`
requires `thinking.type=adaptive` + `output_config.effort` and rejects the
`enabled` shape — those models route through but raise a 400 at the
API; use `claude-sonnet-4-6` (no thinking) or the 4.5 series (thinking
with `enabled`) when QAing today. Cached tokens use Anthropic's
auto-prompt-caching field `cache_read_input_tokens`; Anthropic doesn't
break out thinking tokens in `usage` so `reasoning_tokens` stays at 0 in
the normalized usage shape even when thinking blocks are present.

**`verbosity` and `reasoning_effort` flow through to both APIs.** Earlier
versions of this skill stripped `verbosity` as a Braintrust-UI hint —
that was wrong (verified empirically: `gpt-5.4` + `verbosity: low` returns
200 OK on Chat Completions, and the GPT-5 family treats both params as
first-class). The model is the authority on what values it accepts.
Sentinel handling:

- `reasoning_effort: "none"` — passes through verbatim on both APIs (live
  API accepts it).
- `tool_choice: "none"` — real OpenAI value that disables tools; passes
  through.
- `verbosity: "none"` — both APIs reject this with `invalid_value` (only
  `low|medium|high` are supported). Braintrust uses `"none"` as a UI
  placeholder for "unset", so the skill strips it before the API call.
- `use_cache` and `store` — Braintrust UI-only hints; stripped for both
  APIs.

Under the Responses path, the SDK does not accept flat `verbosity` /
`reasoning_effort` kwargs; the skill maps them to the nested forms
(`text={"verbosity": ...}` and `reasoning={"effort": ...}`) at the API
boundary. `state.messages` stays in OpenAI Chat shape regardless of API —
the Responses input/output translation happens only inside `send_turn.py`.
