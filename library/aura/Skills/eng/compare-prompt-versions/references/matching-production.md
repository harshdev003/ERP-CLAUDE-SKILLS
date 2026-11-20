# Matching the call production makes

An experiment is only evidence about production if each row sends the request production
sends. Three things live **outside** the Braintrust prompt and have to be supplied:

| What | Why Braintrust doesn't have it | Flag |
|---|---|---|
| The endpoint | Intelligence Service config chooses Chat Completions or Responses per prompter (`openaiAPI`); the coach uses `responses` | `--api responses` |
| Runtime params | `reasoningEffort`, `verbosity`, `top_p`, `temperature` are set in `config.js`; `Coach [Production]` carries only `tool_choice` | `--params-json` |
| Tool definitions | Production builds its tools in code (strict mode, its own descriptions and schemas). The Braintrust `tool_functions` have drifted from them | `--tools-json` |

## Getting the values

Take them from a **stored production response** of the same prompter and version. The
`llmCall` ledger (BigQuery `aura-main.llm.llm_call` by `conversationId`, `GCP_KEY`) gives each call's
`openaiId`, `prompterName` and `version`; `GET /v1/responses/{openaiId}` with the
`OPENAI_CONVERSATION_API_KEY` key returns the request settings:

```bash
python3 - <<'PY' > /tmp/prod_call.json
import json, os, urllib.request
rid = "resp_…"   # a coach-prompter openaiId from the ledger
req = urllib.request.Request(f"https://api.openai.com/v1/responses/{rid}",
                             headers={"Authorization": "Bearer " + os.environ["OPENAI_CONVERSATION_API_KEY"]})
print(json.dumps(json.load(urllib.request.urlopen(req))))
PY
python3 -c "import json;r=json.load(open('/tmp/prod_call.json'));json.dump(r['tools'],open('/tmp/prod_tools.json','w'));print(json.dumps({k:r[k] for k in ('reasoning','text','temperature','top_p','parallel_tool_calls','truncation') if r.get(k) is not None}))"
```

The second command writes `--tools-json` and prints the `--params-json` value. Keep the
files local: the response also carries the user id.

## Checking the match

A dataset row renders to the production request when `input` is the conversation (user and
assistant turns, oldest first) and `metadata.contextInstructions` is the context block (it
starts at `## Context`). Intelligence Service 1.40 on sends: the static prompt (system), a
developer `---` + user `---` cache-delimiter pair, the context (developer), the conversation,
then the `### Current Time` lines (`metadata.developerInstructions`, developer).

Braintrust templates come in two shapes, and `--layout production` (the default) produces that
order from either:

| Template shape | Messages | What `production` does |
|---|---|---|
| **delimited** — Coach, Quick Processing, QP Sentinel, QP Done, Done Prompt and Guardrails Handler `[Production]` / `[Development]` since 2026-10-20 | system body, system `---`, user `---`, system `{{metadata.contextInstructions}}`, system `## Conversation\n{{input}}` | Already in order; sets the delimiter and context roles to developer |
| **appended** — old eval and experiment copies (`Coach [Guardrail trigger test vN]`), and any template not yet migrated | system `body\n\n---\n\n{{metadata.contextInstructions}}`, system `## Conversation\n{{input}}` | Splits the context off the system message into the delimiter pair + developer message |

Either way the time message is appended **only when the row's input does not already end with
one** — spans logged since IS 1.40 do, so a dataset built from them is not given a second.

`--layout template` sends the template as rendered. For a delimited template that is the
production order with Braintrust's `system` roles in place of `developer` (Braintrust has no
developer role) and no time message added; prefer the default, which matches the roles too.
For an appended template it is not the production layout at all.

Before trusting a comparison, run the baseline version on a few rows whose production outcome
you know. If it doesn't reproduce, one of the three rows above is missing. Without
`--tools-json`, five reproduced coach false positives fired 3 times in 5 calls; with it, 23 in 25.

## Trials

The model isn't deterministic. A single call per row flipped a known true positive in a
de-identified replay, and a critical self-harm row is caught 4 times in 5 even by production.
Use `--trials 3`–`5` for any decision, and confirm a new miss on a crisis row with more trials
before reporting it.
