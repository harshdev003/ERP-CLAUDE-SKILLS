# Prompter → Braintrust mapping (snapshot 2026-08-12)

> **Read at runtime, not from this file.** `scripts/audit.py` parses
> `src/config.js → prompt.*` live and discovers Braintrust prompts by display-name
> suffix (`[Production]` / `[Development]`). This table is a human-readable hint
> for the operator — to know which prompters exist, which projects they live in,
> and which ones are dormant / live. Stale entries here will not break the
> script.

## Prompters with both [Production] and [Development] prompts on Braintrust

These are the prompters the audit compares across all three sources. UUIDs as of 2026-08-12.

| Prompter (config key) | BT Project | [Production] UUID | [Development] UUID |
|---|---|---|---|
| `coach` | Conversation | `3f716f75-4c8d-400c-a39b-8242ec9ca72a` | `ceb98a13-0537-4c3a-be92-969a01b50c2c` |
| `insightCard` | Conversation | `154c3241-7af6-4aaf-bd43-c6d197eb413b` | `42ae3550-cdb6-4e9d-8aa0-2385694a1b80` |
| `guardrailsHandler` | Guardrail | `d835d75f-c91e-4725-91aa-768de1591c66` | `2badcf0f-5a16-4a9a-b7bf-8da381517473` |
| `feed` | Feed | `270b4355-04e3-4d2c-9ffd-335d7d3ea10c` | `ab6766e0-e2bf-4861-806c-1a9e6e958eb9` |
| `journal` | Journal | `3ab85548-8a22-48b2-a9ff-86f88c81c5c1` | `e554dbb3-03c0-4617-a98c-22988813d580` |
| `notification` | Notification | `47d19b6f-1d20-4ddf-b7e2-82ddc5401461` | `38155c1f-e92b-4403-8eb6-b0bbd92ae3d0` |
| `summary` | Summary | `bdfff7f7-e20c-4353-b595-faf26b5d759b` | `81c03953-d8ab-4da7-ae17-8081ef60fb92` |
| `memory` | Memory | `dc27b202-b648-4484-aded-a7034111bebe` | `0495f32b-8847-4e8c-b167-fb1e531d2095` |
| `memoryIngest` | Memory | `c261f7d6-df42-458c-b394-fec7e9718834` | `95b35b71-f449-478e-b226-b93f2a29f218` |
| `memoryIngestionSorting` | Memory | `23ed1319-261d-440c-87f3-77a5ba28f731` | `fc87c25f-28cd-4bc3-a9c8-bd4095474041` |
| `memorySorting` | Memory | `4c3c9a72-f262-4591-833c-c9d13a005505` | `6be7ce7f-a58e-4c70-a620-372a9c04612b` |
| `onboarding` | Onboarding | `711ae730-c9f1-47ac-a311-c40d806a2794` | `34c00e87-3da3-4d8e-a329-d2e74cfa219a` |
| `onboardingResults` | Onboarding | `d87e1edb-77e8-4502-bf53-995f574c3800` | `76e37bc6-65e0-4c6c-8ba4-a000cf8bf3fe` |
| `profile` | Profile | `f7a1d2de-9a6d-4e83-9db9-f724d761b06a` | `b861e1e1-9c70-4b2a-b0d4-8060d3dc2dc9` |
| `profileIngest` | Profile | `c9b7883a-9811-4e4e-9e8a-82178c635b6c` | `512e541d-0651-48e1-8f93-377bd3f15792` |
| `quickProcessing` | Quick Processing | `be36eb2e-5c64-4d0d-bdf0-e38bd1b00256` | `f721e02c-fa1a-43e3-b594-433a30b71916` |
| `quickProcessingDone` | Quick Processing | `1a9a701f-a30f-4e41-991a-a754b4fdc159` | `09167ce1-5ef2-47cb-8ba2-125913414cb0` |
| `quickProcessingSentinel` | Quick Processing | `e2c8ba77-7b87-40f1-88e3-1161d9967ce6` | `893eec8f-9d93-4063-851e-fd2f7b87dcdf` |

`quickProcessingDone` carries a third Braintrust prompt that discovery-by-suffix does not
see. `Quick Processing Done V0 Prompt` (`88f87f3d-3de8-4b99-902c-1eef204e2eba`) is the
version the runtime `.txt` was *originally* released from; its name ends in neither
`[Development]` nor `[Production]`, so the audit never reads it. It is superseded and kept
only for history — prompt engineering owns retiring it.

**The dev-staleness note that used to live here is resolved.** As of 2026-08-31
(ENGBACK-1331, IS v1.16.5) `quickProcessingDone` was re-released *from* `[Development]`:
the repo `.txt` was rewritten from that prompt and `[Production]` was then synced from the
same body, so all three sources agree and the audit should report `R=P ✓` / `R=D ✓` /
`P=D ✓`. The earlier warning — that dev held a shorter iteration with no `## Output Format`
block and snake_case output keys — no longer applies; dev now carries the full body with
camelCase `header` / `oldLine` / `newLine` / `rememberQuote`.

`quickProcessingSentinel` is new in IS v1.16.6 (ENGBACK-1332). Its `[Production]` prompt was
created from `[Development]` on 2026-08-31, so expect all three to agree on first audit.

## Prompters with only [Development] on Braintrust

Dev exists but no `[Production]` has been published. The skill marks these as `NO_BT_PROD`.

None today.

## Prompters with no published Braintrust prompt

The skill marks these `NO_BT_PROD` / `NO_BT_DEV`. They run from the repo `.txt` only; nothing on Braintrust to compare against. Many are dormant per Tools/OpenAI's per-prompter spend table.

| Prompter (config key) | Project | Reason |
|---|---|---|
| `chat` | Conversation | Aura content-app chat — live but no BT prompt published |
| `companion` | Conversation | Live but no BT prompt published |
| `starters` | Conversation | Dormant |
| `intent` | Intent | Intent project is empty (dormant) |
| `suggestions` | Suggestions | Suggestions project is empty (dormant) |
| `assistantGuardrail` | Guardrail | Only `simplified-guardrail-assistant` exists (no `[Production]` tag in the name) |
| `userGuardrail` | Guardrail | Only `simplified-guardrail-user` exists (no `[Production]` tag in the name) |
| `score` | (Gemini) | Runs on Gemini, no Braintrust |

For `assistantGuardrail` and `userGuardrail`, the audit treats them as `NO_BT_PROD` even though a paired prompt does exist on Braintrust under a different naming convention. If the team standardises on `Simplified Guardrail: Assistant [Production]` (or similar) in the future, update the script's display-name matching.

## How the script discovers these at runtime

1. Parse `src/config.js → prompt.*` to enumerate every prompter key, its `project`, and any `filename` override.
2. For each unique project, GET `/v1/prompt?project_id=<id>&limit=200` and collect everything.
3. For each prompter:
   - **Production candidate**: the prompt whose `name` ends with `[Production]` or matches an explicit override (see below).
   - **Development candidate**: the prompt whose `name` ends with `[Development]` or `[Dev]`.
4. A name matches a prompter when the part before the suffix, with whitespace, hyphens and
   underscores stripped and lower-cased, is **exactly equal** to the expected prefix — the
   config key, or its `NAME_OVERRIDES` entry. `_match_prompt_name()` in `scripts/audit.py`
   is the authority. Equality rather than containment is what keeps one prompter key from
   claiming another's prompt when it is a prefix of it: the Quick Processing project holds
   both `quickProcessing` and `quickProcessingDone`, and `quickprocessing` is not equal to
   `quickprocessingdone`, so each takes only its own.

   Where the same name matches in more than one project, `pick()` prefers the prompter's
   `config.project` hint, then falls back to the first match.

## Name overrides

Some BT display names don't share their config-key prefix. The script maintains a small override map for these:

```python
NAME_OVERRIDES = {
    "insightCard":     "Done Prompt",          # historic name
    "memory":          "Memory Array",         # 'memory' would be too broad
    "memoryIngest":    "Memory Ingestion Array",
    # others added as they appear
}
```

Add to this map (in `scripts/audit.py`) whenever a new prompter's BT display name doesn't follow the `<configKey-titlecased> [Production]` pattern.
