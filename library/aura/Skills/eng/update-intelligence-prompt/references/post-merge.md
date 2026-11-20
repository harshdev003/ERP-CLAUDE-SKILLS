# Post-merge: Braintrust prod sync + optional QA chat

This reference covers Steps 7 and 8 of the [`update-intelligence-prompt`](../SKILL.md)
skill — the two phases that run **after** the IntelligenceService PR from Step 6 merges
(or after a direct push deploys, in the Mode A opt-in path).

Step 7 runs in **both** modes. Mode A promotes dev's text plus its model and params;
Mode B changes `options` only, building the payload from the Production prompt's own
`prompt_data`. Step 8 is Mode A only.

Keep this file in sync with the merge gate in SKILL.md → "Step 7 (overview)".

## Step 7: Update the Production prompt on Braintrust

> **Gate:** wait for the IntelligenceService PR from Step 6 to be merged before running
> this step. The Braintrust Production prompt is the live prod surface — syncing it
> before the IS PR merges (and the matching version deploys) creates a window where
> production Braintrust calls use the new prompt but IS code still expects the old shape.
>
> Two ways to confirm the merge:
>
> 1. **`gh` poll** — check the PR state until it reports `MERGED`:
>    ```bash
>    gh pr view <PR_NUMBER> --repo AuraHealth/IntelligenceService --json state,mergedAt
>    ```
> 2. **Ask the operator** to say "merged" / "released as <version>" and provide the
>    `package.json` version they want recorded in Step 7d's activity comment.
>
> For the direct-push opt-in (Step 6's "Direct push" subsection), there is no PR to
> wait for — proceed once the operator confirms the deploy is live (same probe as
> Step 8c below).

After the IntelligenceService PR merges (or the direct push deploys), sync the Production prompt in the
same Braintrust project. **Two paths depending on whether a Production prompt already
exists:**

- **Exists** → update it (7a → 7b → 7c-preflight → 7c → 7c-verify → 7d)
- **Does not exist** (new prompter) → create it (7a → 7a-create → 7d)

### 7a — Find the Production prompt (or confirm it needs to be created)

From the list fetched in Step 2 of SKILL.md, find the prompt whose name or slug contains
"production" (case-insensitive) **and does not contain "legacy"**, e.g.
`journal-production-338f`, `Production`. Skip any legacy objects (e.g.
`summary-production-legacy`) — those are never the sync target.

- **Non-legacy production prompt found** → note its `id` (UUID) and proceed to **7b**.
- **Only legacy prompt(s) found, or none at all** → proceed to **7a-create**.

### 7a-create — Create a new Production prompt (new prompter only)

Skip 7b and 7c-preflight — there is no prior prod content to fetch or compare against.
Re-fetch the dev prompt fresh (the IS PR may have taken time to merge; using a new curl ensures the latest version).

Choose a name and slug following the `<Project> (Production)` / `<project>-production`
convention (e.g. "Insights (Production)" / "insights-production"). Use the same
`project_id` as the dev prompt.

A new prompter is a new stored name, so it also needs AINative's prompter-name check: see
[A new stored name has to reach AINative too](experiment.md#a-new-stored-name-has-to-reach-ainative-too).

Apply N1 normalization (drop `reasoning_effort` for `*-chat-latest` models) before
building the payload, then POST to create the first Production version:

```bash
DEV_RESPONSE=$(curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&slug=<dev-slug>" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY")

DEV_MSGS=$(echo "$DEV_RESPONSE" | jq '.objects[0].prompt_data.prompt.messages')
DEV_MODEL=$(echo "$DEV_RESPONSE" | jq -r '.objects[0].prompt_data.options.model')
DEV_PARAMS=$(echo "$DEV_RESPONSE" | jq -c '.objects[0].prompt_data.options.params // {}')
DEV_POSITION=$(echo "$DEV_RESPONSE" | jq -r '.objects[0].prompt_data.options.position // "0|hzzzzz:"')
PROJECT_ID=$(echo "$DEV_RESPONSE" | jq -r '.objects[0].project_id')

# N1: drop reasoning_effort for chat-latest models
case "$DEV_MODEL" in
  *-chat-latest)
    DEV_PARAMS=$(echo "$DEV_PARAMS" | jq 'del(.reasoning_effort)')
    ;;
esac

PAYLOAD=$(jq -n \
  --argjson msgs    "$DEV_MSGS" \
  --argjson params  "$DEV_PARAMS" \
  --arg     proj_id "$PROJECT_ID" \
  --arg     model   "$DEV_MODEL" \
  --arg     pos     "$DEV_POSITION" '{
  project_id:    $proj_id,
  name:          "<Project> (Production)",
  slug:          "<project>-production",
  function_type: null,
  function_data: {type: "prompt"},
  prompt_data: {
    prompt:  {type: "chat", messages: $msgs},
    options: {
      model:    $model,
      params:   $params,
      position: $pos
    }
  }
}')

curl -s -X POST "https://api.braintrust.dev/v1/prompt" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  | jq '{id, _xact_id, created}'
```

Capture the returned `id` (UUID) — this is the Production prompt UUID used in the Step 7d
activity URL. Verify the response has a non-empty `_xact_id` and today's `created` timestamp.

Verify the created content matches dev:

```bash
curl -s "https://api.braintrust.dev/v1/prompt/<new-prod-uuid>" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY" \
  | jq '.prompt_data.prompt.messages' > /tmp/prod-final.json
echo "$DEV_RESPONSE" | jq '.objects[0].prompt_data.prompt.messages' > /tmp/dev-full.json
diff /tmp/dev-full.json /tmp/prod-final.json && echo "VERIFIED: prod == dev"
```

**Do not proceed to 7d if any of these is true:** non-empty diff, `_xact_id` absent or null,
or `created` not today. Tell the operator:

> The Braintrust commit didn't land via API (see verification above). Please open the
> [Production prompt](https://www.braintrust.dev/app/<org>/p/<project>/prompts/<production-uuid>)
> in the UI, paste the new dev text into the editor (or use *Save as new version* from
> the Development prompt), and Save. Once a new entry appears in the Activity tab with
> today's date, reply "done" and I'll continue with the activity comment in 7d.

Block on that reply before proceeding to 7d.

Once verified (or the operator confirms the UI save landed), skip to **7d**.

### 7b — Fetch the Production prompt's current content via REST API

```bash
PROD_RESPONSE=$(curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&slug=<production-slug>" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY")

echo "$PROD_RESPONSE" | jq '.objects[0] | {id, project_id, name, slug, position: .prompt_data.options.position}'
echo "$PROD_RESPONSE" | jq -r '.objects[0].prompt_data.prompt.messages[0].content'  # current text (for diff only)
```

From the result, capture these fields for the POST in 7c:
- `.objects[0].project_id` — required in the POST payload
- `.objects[0].name` — required in the POST payload (e.g. "Memory Array [Production]")
- `.objects[0].slug` — required in the POST payload (same slug as 7a)
- `.objects[0].prompt_data.options.position` — preserve in the POST payload (e.g. `"0|hzzzzz:"`)
- `.objects[0].prompt_data.prompt.messages[0].content` — current prod text, used only to diff against dev so you can show the operator what's about to change. The text written in 7c comes from **dev**, not from this prod fetch.

### 7c-preflight — Inspect dev vs prod shape before the POST

Before constructing the payload, compare the **message structure** of dev and prod —
not just the model/params. The two can drift independently. If they differ, decide
explicitly whether the structural delta is in scope; do not promote it as a side
effect of a config-only sync.

```bash
DEV_RESPONSE=$(curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&slug=<dev-slug>" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY")

echo "=== Shape compare ==="
for L in prod dev; do
  [ "$L" = prod ] && R="$PROD_RESPONSE" || R="$DEV_RESPONSE"
  echo "$R" | jq -r --arg l "$L" '.objects[0].prompt_data
    | "\($l): \(.prompt.messages|length) messages, roles=\([.prompt.messages[].role]), tool_functions=\((.tool_functions // [])|length)"'
done
```

Three outcomes:

1. **Identical shape** (same message count + roles). Proceed to 7c with dev's full
   `prompt_data.prompt` verbatim — the standard "mirror dev" path.
2. **Dev has *fewer* messages than prod.** Rare. Usually a sign prod has accumulated a
   structural change (e.g. a `{{input}}` separator) that dev was reset past. Pause —
   blindly promoting dev would *delete* messages from prod. Ask the operator.
3. **Dev has *more* messages than prod** (the common case when only this skill's prior
   runs have touched prod). The extra messages may be a real change the team intends to
   ship — or scope creep that snuck into dev between releases. Default to **preserving
   prod's message array** and only updating `options.model` + `options.params`. Build
   the payload from `PROD_RESPONSE` instead of `DEV_RESPONSE`, surgically replacing
   only the changed `options` fields. Surface the divergence to the operator and ask
   whether to keep it as a separate follow-up or to escalate scope now.

A **template-shape change** (appended → delimited, or back) always shows up here as a
message-count difference (2 vs 5 for the conversation prompters); `bt_scaffold.py shape`
names each side. Promoting dev across a shape change is a layout release — confirm it with
the operator rather than treating it as drift.

When in doubt, prefer the minimal-payload path (option 3 above): a config-only sync
should change config-only fields. Live example: ENG-675 caught a 1-vs-2-message
divergence on the Memory Sorting prod prompt during sync; preserving prod's shape kept
the revert scoped to the model/reasoning-effort change the ticket asked for.

### 7c — Commit a new Production prompt version via REST API

Use **POST** `/v1/prompt` (not PATCH) to create a new versioned commit, with the payload built by `scripts/bt_payload.py` from the target's own record (a POST clears any field it leaves out, the description included). Mirror dev exactly: model, params, position from 7b, plus `function_type: null` and `function_data: {type: "prompt"}` (the schema dev uses for chat prompts). The text written to prod is **dev's full text** — which already contains the `{{metadata.contextInstructions}}` placeholder — so passing dev's content verbatim preserves the placeholder.

> **Skip the "mirror dev verbatim" guidance if 7c-preflight surfaced a shape divergence.**
> In that case build the payload from `PROD_RESPONSE` instead, replacing only the
> fields the sync is scoped to change (typically `options.model` and individual keys
> under `options.params`).

> **Apply N1 normalization before constructing the payload.** The same chat-latest rule from [Step 5 / Normalizer](../SKILL.md#normalizer--chat-latest-auto-strip-n1) applies to Braintrust writes: if the dev prompt's `options.model` matches `*-chat-latest` (e.g. `gpt-5.2-chat-latest`), strip `reasoning_effort` from `options.params` before posting. Dev prompts sometimes carry a stale `'medium'` from an earlier iteration; promoting that to prod re-introduces drift against `config.js`, which has the key absent under N1.
>
> ```bash
> DEV_MODEL=$(echo "$DEV_RESPONSE" | jq -r '.objects[0].prompt_data.options.model')
> DEV_PARAMS=$(echo "$DEV_RESPONSE" | jq -c '.objects[0].prompt_data.options.params // {}')
> case "$DEV_MODEL" in
>   *-chat-latest)
>     if echo "$DEV_PARAMS" | jq -e 'has("reasoning_effort")' >/dev/null; then
>       VAL=$(echo "$DEV_PARAMS" | jq -r '.reasoning_effort')
>       echo "⚠️  N1: dropping reasoning_effort=\"$VAL\" from BT params — $DEV_MODEL is chat-latest"
>       DEV_PARAMS=$(echo "$DEV_PARAMS" | jq 'del(.reasoning_effort)')
>     fi
>     ;;
> esac
> # Use $DEV_PARAMS (and $DEV_MODEL) in the PAYLOAD jq invocation below.
> ```

**Send the whole `prompt` object and the whole `tool_functions` array — never a rebuilt
single message.** A POST replaces `prompt_data` wholesale, so any message or tool absent
from the payload is deleted from prod. Multi-message prompts are the norm, not the
exception: the conversation prompters carry a second `## Conversation\n{{input}}` system
message, and `coach` / `quickProcessing` carry 2–4 `tool_functions`. Passing
`messages: [{role, content}]` silently drops all of it, and the byte-diff in the next
block only compares `messages[0]`, so the loss passes verification unnoticed.

Which object supplies the `prompt` and `tool_functions` depends on the mode and on what
7c-preflight found:

| Source | `prompt` | `tool_functions` | `options` |
|---|---|---|---|
| Mode A, shapes identical | dev's, whole | dev's, whole | dev's model + params |
| Mode A, shapes diverge (7c-preflight 2/3) | **prod's**, whole | **prod's**, whole | dev's model + params |
| Mode B (config-only) | **prod's**, whole | **prod's**, whole | prod's, with only the changed keys replaced |

```bash
# Pull the SOURCE object per the table above, then reuse its sub-objects verbatim.
# Mode A: SRC_SLUG=<dev-slug>. Mode B / shape divergence: SRC_SLUG=<production-slug>.
SRC=$(curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&slug=$SRC_SLUG" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY" | jq '.objects[0]')

# Whole sub-objects — byte-exact, placeholder and every message preserved.
SRC_PROMPT=$(echo "$SRC" | jq -c '.prompt_data.prompt')
# tool_functions ALWAYS comes from prod (7b), so a tool wired in prod but not in the
# source is never dropped. `// []` guards a prompt that has none.
PROD_TOOLS=$(echo "$PROD_RESPONSE" | jq -c '.objects[0].prompt_data.tool_functions // []')

# Build the payload from the TARGET's own record, so every field the release does not
# set survives: a POST replaces the whole record, and a field left out is cleared. The
# description is the one that bit (PROD-8232: a "Do not delete again" guard note on
# Coach [Development] was wiped). Pass --description only when the release changes it.
echo "$PROD_RESPONSE" > /tmp/target.json
echo "$SRC_PROMPT"    > /tmp/src_prompt.json
PAYLOAD=$(python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-intelligence-prompt/scripts/bt_payload.py \
  --target /tmp/target.json --prompt /tmp/src_prompt.json \
  --model "$DEV_MODEL" --params "$DEV_PARAMS")   # after N1 normalization above
# Kept from the target: project_id, name, slug, description, tags, tool_functions and
# every other options key. Mode B: drop --prompt (the target's own prompt is sent), and
# pass --params as the target's params with only the changed keys replaced.

curl -s -X POST "https://api.braintrust.dev/v1/prompt" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  | jq '{id, _xact_id, created, function_type, function_data, model: .prompt_data.options.model, params: .prompt_data.options.params}'
```

Confirm the response has:
- a fresh `_xact_id` (different from the value before the POST) — proves a new commit landed
- a fresh `created` timestamp (today) — this is what the UI's activity log displays
- `function_type` / `function_data` that match dev (re-POST with the corrected payload if they don't — each POST is a new commit, and only the latest matters)

Then re-fetch the prod prompt and diff the **whole** `prompt` object and `tool_functions`
against what the payload sent. Diffing only `messages[0]` passes even when a second
message or a tool was dropped — the very failure the payload rules above prevent:

```bash
AFTER=$(curl -s "https://api.braintrust.dev/v1/prompt/<production-prompt-uuid>" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY")

# Whole prompt object — every message, byte-exact, not just messages[0]
diff <(echo "$SRC_PROMPT" | jq -S .) \
     <(echo "$AFTER" | jq -S '.prompt_data.prompt') \
  && echo "VERIFIED: prompt object intact"

# Tool set survived
diff <(echo "$PROD_TOOLS" | jq -S .) \
     <(echo "$AFTER" | jq -S '.prompt_data.tool_functions // []') \
  && echo "VERIFIED: tool_functions intact"

# Description survived (or changed only to what --description set)
[ "$(echo "$PAYLOAD" | jq -c '.description // null')" = "$(echo "$AFTER" | jq -c '.description // null')" ] \
  && echo "VERIFIED: description intact"

# Placeholder still present, in the same template shape as the source. Coach's
# delimited shape keeps it in its own message (messages[3]), not in messages[0] —
# checking messages[0] alone fails a correct commit.
echo "$SRC_PROMPT" > /tmp/src_prompt.json
echo "$AFTER" | python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-intelligence-prompt/scripts/bt_scaffold.py \
  check --like /tmp/src_prompt.json && echo "VERIFIED: placeholder retained"
```

### 7c-verify — If the commit didn't land, hand off to the operator

A POST response with HTTP 200 is **not** sufficient to declare success. Treat the sync as failed if any of these is true:

- The new `_xact_id` equals the value captured in 7b (no version bump).
- The new `created` timestamp is not today.
- Any of the three 7c checks above fails — prompt object, `tool_functions`, or the
  `bt_scaffold.py check` placeholder/shape check.

**Do not continue to 7d in that case** — posting an "ITERATION N" activity comment for a commit that didn't actually land is worse than no comment.

Instead, tell the operator the commit didn't take and ask them to apply it manually via the Braintrust UI:

> The Braintrust commit didn't land via API (verification details above). Please open the [Production prompt](https://www.braintrust.dev/app/<org>/p/<project>/prompts/<production-uuid>) in the UI, paste the new dev text into the editor (or use *Save as new version* from the Development prompt), and Save. Once a new entry appears in the Activity tab with today's date, reply "done" and I'll continue with the activity comment in 7d.

Block on that reply before proceeding to 7d. The same UI-save fallback is the right path in any environment where REST POST isn't available — see the MCP note below.

> **If a Braintrust MCP is your only update path.** This skill is built around REST POST because that's verifiably how a versioned commit gets created. If `BRAINTRUST_API_KEY` isn't available and a Braintrust MCP is the only path you have, attempt the update through the MCP and then run the same verification above (fresh `_xact_id`, fresh `created`, byte-diff vs dev). If verification fails, the MCP write didn't create a real commit for this call — don't claim the release shipped; fall back to the manual UI-save path in 7c-verify and ask the operator to update through the Braintrust UI.

> **Why POST, not PATCH.** `PATCH /v1/prompt/{id}` edits the prompt's *current state* in place — GET reflects the new content, but **no new `_xact_id` is recorded, no commit appears in the UI activity log, and `updated_at` stays `null`**. To teammates the prompt looks untouched since the last real commit. `POST /v1/prompt` with the same `project_id` + `slug` is the idiomatic "create new version" path: it bumps `_xact_id`, sets a fresh `created`, and surfaces in the UI activity tab. Use POST for any release that should be visible to the team. Discovered live during PROD-2209 — a successful-looking PATCH left the UI showing the prior version's date.

> **Note on the prompt text + placeholder.** Braintrust templates carry the placeholder in one of two shapes. **Delimited** (Coach, Quick Processing, QP Sentinel, QP Done, Done Prompt and Guardrails Handler `[Production]` / `[Development]` since 2026-10-20, IS 1.40's order): system body · system `---` · user `---` · system `{{metadata.contextInstructions}}` · system `## Conversation\n{{input}}`. **Appended** (old eval and experiment copies, and any template not yet migrated): system body ending `\n\n---\n\n{{metadata.contextInstructions}}\n`, then the conversation message. The `.txt` file in IS has neither. Dev's prompt object already carries its scaffold, so passing it verbatim into the POST payload preserves it — do **not** read from the IS `.txt` file here, and never re-append the appended suffix to a delimited body (it would put the context in twice and revert the layout). If a body must be written from text, use `bt_scaffold.py set-body`, which keeps whichever shape the prompt has.

### 7d — Add an activity comment (manual)

The Braintrust comments API does not support API key authentication. Both endpoints return an
error, and the specific codes have shifted before — treat them as observations, last verified
2026-08-12, not as a stable contract:
- `POST /v1/function/{id}/comments` → 405 Method POST not supported
- `POST /v1/prompt/{id}/comments` → 403 (requires user session, not API key). The body is an
  odd AWS-style `Invalid key=value pair (missing equal-sign) in Authorization header` — that is
  **not** a malformed `BRAINTRUST_API_KEY`; it suggests the route isn't served by the normal API
  auth path at all. Don't go hunting for a key problem.

This step ends with the operator, not the agent. Do not attempt to work around the
403 by driving a browser session (e.g. a `claude-in-chrome`-style tool) to post the
comment on the operator's behalf — that posts as (or through) the operator's own
logged-in session without their turn-by-turn confirmation. Hand the operator the
formatted comment below plus the direct Activity-tab link, then stop.

First, read the IntelligenceService release version from the remote branch — this always
reflects the merged state regardless of whether the local checkout has been pulled:

```bash
git -C "$IS_ROOT" fetch origin --quiet
IS_VERSION=$(git -C "$IS_ROOT" show origin/master:package.json | jq -r .version)
echo "IntelligenceService version: $IS_VERSION"
```

Then provide the user with the formatted comment to paste manually in the
[Production prompt activity tab](https://www.braintrust.dev/app/Aura%20Health%201/p/<Project>/prompts/<production-uuid>?pt=activity):

> **Use the UUID, not the slug.** Braintrust prompt *page* URLs — including the
> `?pt=activity` tab — must use the prompt's UUID (the `id` captured in 7a), not the
> slug. A slug-based page URL like `.../prompts/memory-array-production-9060?pt=activity`
> returns "Prompt ... does not exist or you do not have access to it." (The slug is only
> valid as a `?slug=` **query param** on the REST API in 7a/7b — never in a web page path.)

```
ITERATION <N> - <IS_VERSION> - <DD/MM/YYYY>

Updated: <brief description of what changed>
```

Example:

```
ITERATION 15 - 0.181.0 - 01/09/2026

Updated: ### 2. Diagnosis Prevention
Updated ## Communication Style: In most responses, include one of the following when
appropriate: an affirmation, a reflection, or a moment of orientation — whichever best
fits the current tier and the user's readiness. Do not stack all three in the same turn.
```

- **Iteration number**: increment from the last comment in the activity log (check the UI); for a newly created Production prompt, start at `1`
- **Version**: `$IS_VERSION` — the IntelligenceService version from the command above
- **Date**: today's date in DD/MM/YYYY format
- **Updated**: a short summary of what changed (ask the user if not obvious from the Linear comments)

## Step 8: Optional QA chat with Aurie

### Which prompters are testable, and how each one closes

The gate is the `/ai/dialogue/ask` server allowlist in `src/models/dialogue.js`:
`companion`, `chat`, `coach`, `quickProcessing`, `quickProcessingSentinel`. Anything else
returns HTTP 422 `DIALOGUE_INVALID_PROMPTER_NAME`.

The three families close differently, and `qa:chat-with-aurie` owns all three — pass the
prompter name through and let it pick the right `/end` shape:

| Prompter | Stop signal on `/ask` | `/end` |
|---|---|---|
| `coach` / `chat` / `companion` | none — natural end of arc, `toolCalls` stays `null` | plain, or `--insight` for the insight pass |
| `quickProcessing` | `createInsightCard` toolCall, carrying `affirmation` + `trackId` | plain — `--insight` would regenerate a *different* insight |
| `quickProcessingSentinel` | `endConversation` toolCall, emitted from the prompt's `**ENDCONVERSATION**` marker; no payload | `--quick-processing-done`, which runs the `quick-processing-done` prompter to build the completion screen |


Only relevant when the deployed prompter is a conversation prompter exposed by
`/ai/dialogue/ask`: **`coach`**, **`chat`**, or **`companion`**. For everything else
(`journal`, `notification`, `memory`, `feed`, …) the dialogue API can't exercise the
prompt — skip this step entirely and report the deployment as done.

### 8a — Gate on prompter name

Use the `name` field from the prompter config block in `src/config.js` (the
`<name>-prompter` value, e.g. `coach-prompter`, not the project name from Braintrust).
Strip the `-prompter` suffix.

- If the result is `coach`, `chat`, or `companion` → continue to 8b.
- Otherwise → print one line: `QA chat not applicable for this prompter (only coach / chat / companion are testable via /ai/dialogue/ask).` and stop.

### 8b — Offer the QA chat

Ask the user once whether they want to drive a QA chat against the new prompt:

> Want to QA the new `<prompter>` prompt by chatting with Aurie? (yes / no)

If `no`, stop and report the deployment as done.

### 8c — Wait for the release to land

The release pipeline runs on merge to `master` (default PR flow) or on direct push
(opt-in subsection of Step 6). Either way the deploy takes a few minutes to roll out;
running `chat-with-aurie` immediately afterward will exercise the **previous** prompt
and produce a misleading verdict.

Confirm with the user which environment they want to QA against (dev or prod) and that
the deploy is live before any conversation calls go out. Two ways to confirm, in order
of preference:

1. **Version probe.** Hit the target env's root health endpoint and compare the
   reported version to `$IS_VERSION` from 7d:

   ```bash
   # Dev:  https://services.dev.aurahealth.io
   # Prod: https://services.aurahealth.io
   curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/eng:update-intelligence-prompt (session ${AINATIVE_SESSION_NAME:-adhoc})" -s https://services.dev.aurahealth.io/ | jq -r '.version // .build // .'
   ```

   If the version matches the pushed release, continue. If it doesn't, wait and
   re-probe — don't hand off until the new version is live.

2. **Manual confirmation.** If no version field is exposed (or the operator wants to
   wait for prod / a specific CI signal), ask them to confirm in chat:

   > Reply `live` once the new release is deployed to the target env. I'll then hand
   > off to `qa:chat-with-aurie`.

   Block on that reply — do not proceed until the user explicitly confirms.

### 8d — Hand off to `qa:chat-with-aurie`

Invoke the `qa:chat-with-aurie` skill with the relevant context pre-stated so the
operator doesn't re-answer questions we already know the answer to:

- **Environment**: the env confirmed in 8c (default `https://services.aurahealth.io`).
- **`prompterName`**: the gated value from 8a (`coach`, `chat`, or `companion`).
- **Scenario hint**: a one-line scenario derived from the Linear ticket title or the
  commit message — e.g. for a "make coach less directive" prompt change, hint *"User
  looking for advice; check that Aurie offers options rather than telling them what to
  do."* If nothing usable can be pulled from the ticket, hand off without a scenario
  hint and let `chat-with-aurie` ask for one in its Step 1.

`qa:chat-with-aurie` owns the rest — token preflight, identity gate, the conversation
loop, `/end`, transcript. Do not duplicate any of that here.
