# Edge cases

Lookup catalog of edge cases the qa-prompt drive handles, and what to do when you hit each one.

- **Prompt has no tools** — phase 2 is skipped entirely; phase 3 sends one
  turn and stops.
- **Tool `returns` schema missing or invalid** — auto-mock falls back to
  `{"ok": true, "mocked": true}` and warns.
- **`recommendTracks` set to `real` without `--user-id`** —
  `apply_tool_default.py` appends `{"ok": false, "error": "missing user_id"}`
  as the tool response so the model can recover; the drive continues.
- **`{{metadata.contextInstructions}}` missing from the context payload** —
  renders to empty string with a warning. Mirrors prod behavior in
  `IntelligenceService/src/prompters/*.js` (`${prompt}\n\n${contextInstructions || ''}`).
- **Template shape** — The standard-layout prompters' Dev/Prod templates (Coach, QP, QP Sentinel, QP Done, Done Prompt, Guardrails Handler) are *delimited* (system body, system
  `---`, user `---`, system context, conversation) since 2026-10-20; old eval and experiment
  copies are *appended* (context at the tail of the system body). `--layout
  production` (default) renders both into IS 1.40's order via
  `render_template.production_layout` (roles stay `system`; `send_turn` maps
  them per provider); `--layout template` keeps the render as is. In dataset
  mode `metadata.developerInstructions` is appended after the replay unless the
  logged input already ends with its `### Current Time` message.
  `state.template_shape` records which shape the prompt had.
- **Prompt declares `claude-opus-4-7`** — Anthropic's 4-7 series rejects
  the `thinking.type=enabled` form qa-prompt emits and requires
  `thinking.type=adaptive` + `output_config.effort` instead. If you need
  extended thinking, QA against `claude-sonnet-4-5-20250929`,
  `claude-opus-4-6`, or `claude-sonnet-4-6` (no thinking) today. Non-
  thinking runs against 4-7 work fine (just don't set `reasoning_effort`).
- **`max_tokens` ≤ `thinking.budget_tokens`** — Anthropic requires
  `max_tokens > budget_tokens` (the API 400s otherwise). When
  `reasoning_effort` is set but the Braintrust prompt's `max_tokens` is too
  small, the skill auto-raises `max_tokens` to `budget + 1024` and emits
  a `render_warnings` entry. Visible in the final report.
- **`temperature > 1.0` against Anthropic** — Anthropic's range is 0..1
  (OpenAI allows 0..2). The skill clamps to 1.0 and warns. Visible in
  the final report.
- **Empty user text block sent to Anthropic** — Anthropic 400s on
  "text content blocks must be non-empty". Common cause: a Braintrust
  template variable (`{{message}}`) resolved to empty string. The skill
  drops empty user messages before send; the next non-empty user turn
  carries the real content.
- **Gemini tool schema uses OpenAPI-specific features** — schemas pass
  through under Gemini's `parameters_json_schema` field which accepts the
  full JSON Schema dialect (`additionalProperties: false`, deep
  `oneOf`/`anyOf`, etc.). If Gemini rejects a schema, the 400 surfaces with
  the offending key; the tool name + the schema fragment are in the error
  message so the prompt author can adjust the Braintrust definition.
- **Gemini 3.5+ thought_signature missing** — if a Gemini run starts mid-
  conversation against a state file written by an older version of this
  skill (one that didn't persist `_gemini_thought_signature` on tool_call
  entries), the next turn after a function call will 400 with "Function
  call is missing a thought_signature". Re-run the prompt from scratch
  rather than resuming a stale state.
- **Stuck in a tool-call or interactive loop** — there is no automatic turn
  cap. Tell Claude to stop; the partial `state.json` is preserved and
  `self_critique.py` + `write_report.py` can still run against it.
