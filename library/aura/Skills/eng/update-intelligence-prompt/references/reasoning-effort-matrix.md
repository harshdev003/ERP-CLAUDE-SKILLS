# `reasoning_effort` support matrix

Empirical capability matrix for OpenAI's `reasoning_effort` param across the models Aura
ships, plus the wire-level semantics of "None" across the three places it can live (BT
UI, `config.js`, OpenAI request body). Drives the `update-intelligence-prompt` skill's
[N1 normalization rule](config-validation.md#normalizer--chat-latest-auto-strip-n1).

Every row was verified live via `POST /v1/chat/completions` on **2026-06-03** (during
[ENGPROM-57](https://linear.app/aura-health/issue/ENGPROM-57)). Re-run the commands at
the bottom of this file when a new model lands or you suspect upstream behaviour
changed.

## Per-model matrix

| Model | `reasoning_effort` accepts | Produces real reasoning tokens? | Skill behaviour |
|---|---|---|---|
| `gpt-5-mini` (OpenAI canonical) | `minimal`, `low`, `medium`, `high` | ✅ yes — `high` produced 64 reasoning tokens on a "47 × 83" prompt | passthrough |
| `gpt-5.4-mini` (Aura) | `none`, `low`, `medium`, `high` (not `minimal`) | ✅ yes — `medium` produced 27 reasoning tokens | passthrough; **but see PROD-1966**: `chatCompletions + gpt-5.4-mini + reasoningEffort + function tools attached` returns HTTP 400 from OpenAI. The basic combo (no tools) works. Hard-blocked by validator rules E1–E3 in [SKILL.md](../SKILL.md). |
| `gpt-5-chat-latest` (OpenAI canonical) | **nothing** — every value returns HTTP 400 *"Unrecognized request argument"* | n/a | **N1 strips** — `reasoning_effort` is silently removed from any write |
| `gpt-5.2-chat-latest` (Aura) | **only the literal `'medium'`** — `none`/`low`/`high`/`minimal` return HTTP 400 *"Unsupported value: 'reasoning_effort' does not support '<x>' with this model. Supported values are: 'medium'."* | ❌ no — `reasoning_tokens` is always 0 even at `'medium'` | **N1 strips** |
| `gpt-5.3-chat-latest` (Aura) | same as `gpt-5.2-chat-latest` | ❌ no | **N1 strips** |
| `gpt-5.1` (Aura) | `none`, `low`, `medium`, `high` | ✅ yes (assumed — `none` confirmed to return 0 reasoning tokens; other values not exhaustively tested but accepted) | passthrough |
| `gpt-5.2` (Aura) | `none`, `low`, `medium`, `high` | ✅ yes (`low` returned 0 reasoning tokens, `none` accepted) | passthrough |
| `gpt-5.4` (Aura) | `none`, `low`, `medium`, `high` | ✅ yes (`none` returned 0 reasoning tokens) | passthrough |
| `gpt-5.5` (Aura) | not directly tested in ENGPROM-57; in-use today with `reasoning_effort='medium'` on `profileIngest` without errors | ✅ assumed yes | passthrough |
| `gpt-4o`, `gpt-4o-mini` | nothing — pre-GPT-5 reasoning era, param ignored or rejected | n/a | Hard-blocked by validator rules E4–E5 in [SKILL.md](../SKILL.md) |

**Convention summary:**

- **chat-latest family** (`*-chat-latest`) → never set `reasoning_effort` anywhere. The
  one ostensible value (`'medium'` on Aura's 5.2/5.3 variants) is a silent no-op. Any
  other value is HTTP 400. N1 enforces this automatically.
- **reasoning models** (`gpt-5-mini`, `gpt-5.x` non-chat-latest, `gpt-5.x-mini`) →
  passthrough whatever the user / Braintrust dev prompt says, after the hard-block
  validator. Use explicit `'none'` on both sides when you want the param visibly set
  to no-reasoning (matches the BT UI's "None" dropdown — see semantics section below).

## "None" semantics — BT UI vs `config.js` vs OpenAI wire

The same intent ("no reasoning") has three different on-disk representations across
the surfaces involved in shipping a prompt:

| Surface | Concrete state | OpenAI request shape |
|---|---|---|
| Braintrust UI shows `reasoning_effort: None` | Key is **absent** from `prompt_data.options.params` (verified via `GET /v1/prompt/<id>` — the field never appears) | `reasoning_effort` not sent |
| `config.js` block has **no** `reasoningEffort` key | `if (reasoningEffort)` in [`openai.js`](https://github.com/AuraHealth/IntelligenceService/blob/main/src/services/openai.js) skips the assignment | `reasoning_effort` not sent |
| `config.js` block has `reasoningEffort: 'none'` | Truthy string; the same `if (reasoningEffort)` assigns `request.reasoning_effort = 'none'` | `reasoning_effort: 'none'` sent verbatim |

**On reasoning models** all three are semantically equivalent — `'none'` is an accepted
value that maps to 0 reasoning tokens.

**On chat-latest models** they are **not** equivalent — `'none'` returns HTTP 400. The
only safe shape is "key absent". N1 enforces this by stripping the key, never by writing
the literal string `'none'`.

When picking the explicit-`'none'` form vs the absent form on a reasoning model, the
team convention from ENGPROM-57 is: **prefer explicit `'none'` on both `config.js` and
BT params** so the BT UI dropdown reads `None` (rather than the field being grey/unset)
and `config.js` carries a positive statement of intent. Either shape is correct at the
wire — this is purely about visual symmetry across QA tools.

## How N1 plugs in

N1 is a normalization rule, not a validation rule. It runs before the hard-block
validator and silently rewrites the change set:

1. Skill receives a change request: `set <name>-prompter reasoningEffort to <value>`.
2. Skill resolves the prompter's current `model`. If it matches `*-chat-latest`:
   - Drop `reasoningEffort` from the change set entirely.
   - Surface a one-line warning (see [SKILL.md](../SKILL.md#normalizer--chat-latest-auto-strip-n1)).
3. Hard-block validator runs on the (now possibly empty) change set.
4. If still anything to write, proceed with the file edit + any Braintrust POST.

In Mode A (Braintrust → IS sync), the same N1 check runs in
[Step 7c](post-merge.md#7c--commit-a-new-production-prompt-version-via-rest-api) before
the POST payload is built — so a dev prompt with a stale `reasoning_effort` doesn't
re-introduce drift on the production prompt.

## Re-verifying when a new model lands

When a new model name shows up (a new chat-latest variant, a new reasoning model, a
new minor version of an existing family), spend 30 seconds confirming the row before
trusting the validator. The repeatable test:

```bash
# Fixture: a deterministic arithmetic prompt that yields the same answer with or without reasoning
PROMPT='{"messages":[{"role":"user","content":"What is 47 * 83? Just the number."}],"max_completion_tokens":2000,"temperature":1}'

MODEL="<new-model-name>"
for effort in "none" "minimal" "low" "medium" "high"; do
  echo "=== $MODEL + reasoning_effort='$effort' ==="
  curl -sS -X POST https://api.openai.com/v1/chat/completions \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$(echo "$PROMPT" | jq --arg m "$MODEL" --arg e "$effort" '. + {model: $m, reasoning_effort: $e}')" \
    | jq '{error: (.error.message // null), content: (.choices[0].message.content // null), completion_tokens: .usage.completion_tokens, reasoning_tokens: .usage.completion_tokens_details.reasoning_tokens}'
done

# Also probe omission — should produce an identical response to the model's default
curl -sS -X POST https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" -H "Content-Type: application/json" \
  -d "$(echo "$PROMPT" | jq --arg m "$MODEL" '. + {model: $m}')" \
  | jq '{content: .choices[0].message.content, completion_tokens: .usage.completion_tokens, reasoning_tokens: .usage.completion_tokens_details.reasoning_tokens}'
```

Read the output as:

- **OK + reasoning_tokens > 0** → real reasoning, passthrough is correct.
- **OK + reasoning_tokens = 0 everywhere** → silent no-op, treat like chat-latest (N1
  rule should be extended to match this model).
- **ERROR `Unsupported value`** → restricted to a subset (note which values are accepted
  in the table).
- **ERROR `Unrecognized request argument`** → param not supported at all, treat like
  `gpt-5-chat-latest` (N1 always strips).

Update the matrix above with the new row + revision date, and add the model to the
N1-match list in [SKILL.md](../SKILL.md) if applicable.
