# Sources

Provenance for the qa-prompt skill: the AI Knowledge pages it reads, the Linear tickets that shaped each phase/provider, and the external specs + prior QA pass it derives its contracts from.

- [Tools/Braintrust](https://www.notion.so/34c75e23af2081968f7bf7ee43e1442a) —
  prompt + dataset row contract (`input` + `metadata.contextInstructions`),
  camelCase field rule, `/function/{id}/invoke` for real-tool passthrough.
- [Tools/OpenAI](https://www.notion.so/34c75e23af20812cbac8e0fcb0eefe1e) —
  Chat Completions + function-calling access pattern, env var.
- [Products/Aurie](https://www.notion.so/34a75e23af2081d59267e8eb5f5914e7) — brand
  voice anchor used by the self-critique pass.
- [ENGBACK-1029](https://linear.app/aura-health/issue/ENGBACK-1029) — 4-phase
  flow, interactive mode, tool defaults, two-track scoring. Sweep mode +
  prereq check + write_report `--label` follow-up tracked under the same
  ticket.
- [PROD-1724](https://linear.app/aura-health/issue/PROD-1724) — original
  design decisions and API spike findings.
- [ENGPROM-52](https://linear.app/aura-health/issue/ENGPROM-52) — OpenAI
  Response API support (stateless / full-message mode) + verbosity /
  reasoning_effort strip-bug fix.
- [ENGPROM-53](https://linear.app/aura-health/issue/ENGPROM-53) — Google
  Gemini provider (`gemini-3.5-flash` + family). `--provider {openai,google}`
  flag, auto-detect from `bundle.model`, conditional `GEMINI_API_KEY`
  prereq, Chat ↔ Gemini translation (system_instruction routing,
  thought_signature persistence, synthesized tool ids,
  `parameters_json_schema` schema passthrough). Test prompt:
  [Gemini QA Test [Development]](https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/prompts/81000877-c9f0-489e-9ce9-fbccf8f57bb7)
  (slug `gemini-qa-test-development`).
- [ENGPROM-54](https://linear.app/aura-health/issue/ENGPROM-54) — Anthropic
  Claude provider (`claude-sonnet-4-6` + family).
  `--provider {openai,google,anthropic}` flag, auto-detect from
  `bundle.model`, conditional `ANTHROPIC_API_KEY` prereq, Chat ↔ Anthropic
  Messages translation (system kwarg routing, verbatim `tool_use.id`
  round-trip, `input_schema` tool shape, `max_tokens`
  default-with-warning, `temperature` clamp, `thinking` block capture
  under `state.turns[].reasoning`). Test prompt: [Claude QA Test [Development]](https://www.braintrust.dev/app/Aura%20Health%201/p/Conversation/prompts/a93de801-2240-4588-b08f-55aab4b0d699)
  (slug `claude-qa-test-development`).
- [Braintrust OpenAPI spec — InvokeApi schema](https://github.com/braintrustdata/braintrust-openapi/blob/main/openapi/spec.json) — `messages`, `input`, `metadata`, `overrides`, `version`, `mode` field semantics.
- Quick Processing prompt QA pass, 2026-05-15 (hand-rolled 4-scenario sweep
  that motivated sweep mode + the three Change set A fixes). The output shape
  this sweep mode produces matches the hand-rolled `AGGREGATED-REPORT.md`
  from that pass.
