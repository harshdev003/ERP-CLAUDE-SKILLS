# Prompter config rules

How to recommend each field of the new `config.prompt.<key>` entry. Model prices, project
inventory and tier rates live on AI Knowledge `Tools/OpenAI`; point at that page rather than
restating numbers.

## Service tier: flex or standard

| Call path | Tier |
|---|---|
| Awaited inside an HTTP request (a user is waiting) | **standard** (omit `serviceTier`) |
| Cron job, or a side effect nobody awaits (conversation-end hooks, background jobs) | **`serviceTier: 'flex'`** |

Flex is half price but can be queued for minutes, and a flex call runs with the 15-minute flex
timeout. A flex prompter in a request path leaves the user waiting on a queue: the
`affirmation` prompter was moved back to standard for exactly this (IntelligenceService #449).
Trace the planned call path to its entry point (route, job, listener) before choosing.

`createWithServiceTierFallback` retries a flex 429 on the standard tier (#454), so flex capacity
shedding degrades to standard price, not to a failed call. SDK retries alone don't cover it.

List the prompters already on flex with `scripts/inventory_prompters.js` (Tier column) and
compare: the new prompter should look like the ones with the same call path.

## Model and reasoning effort

- Recommend one model and effort, and say why (task shape, output length, cost per run × run
  volume from the gating decisions).
- A model id that appears nowhere in `config.prompt` is unverified here. Flag it, ask for the
  exact string, and list what to check on the first real call: the id resolves, `service_tier:
  'flex'` is accepted, strict JSON schema output works, `reasoningEffort` is accepted.
- **Rejected parameters look like empty output.** `callResponses` / `streamCallResponses` log
  and return `undefined` on failure, so the caller sees "no response", not an error. Say so in
  the plan's verification step.
- Some aliases accept only default settings (e.g. `chat-latest` models accept only medium
  verbosity and their default effort; `services/openai.js` coerces them). Check
  `services/openai.js` for guards on the chosen model family.

## OpenAI / Braintrust project

`project` picks both the OpenAI API key (`config.openai.<project>`) and the Braintrust project
(`config.braintrust.<project>`). Before recommending one:

- List what already runs on it (`inventory_prompters.js`, Project column) and whether its keys
  are still configured. A project whose only prompter was deleted can still be configured and
  reusable.
- Trade-off: sharing a project mixes the new spend into its usage dashboard (split later by
  prompter `name` in Braintrust and the `llmCall` ledger); a new project needs new keys, env
  vars and README entries.
- A project for real-time chat (`conversation`) is a poor home for a batch prompter on flex:
  the dashboard mixes tiers and it shares rate limits with live traffic.

## JSON output

Follow IntelligenceService `.claude/rules/config.md` → "Prompter JSON-output schemas":
`jsonOutput: true` in config, raw schema body only in `src/prompters/<configKey>.schema.json`,
no `jsonSchema` field in config. Draft the schema in the plan:

- `additionalProperties: false` and every field `required` (strict mode).
- Enums for fixed categories.
- `minItems: 0` when "nothing" is a valid answer; `maxItems` from the volume decision.

## Naming

- Config key and files: camelCase, matching (`<configKey>.js`, `.txt`, `.schema.json`); class
  `<ConfigKey>Prompter`.
- `name`: kebab-case `<thing>-prompter`.
- New prompters copy the closest existing prompter into a standalone class that extends the
  base `Prompter`; never `extends` a sibling (IntelligenceService `.claude/rules/architecture.md`
  → "Prompter variants — copy, don't extend").
- New per-prompter feature flags ship disabled (`.claude/rules/project-rules.md`); a brand-new
  prompter behind its own experiment gate is not affected.
