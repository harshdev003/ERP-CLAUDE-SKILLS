---
name: update-intelligence-prompt
description: >
  Updates IntelligenceService prompter config. Mode A redeploys a prompt plus config from a
  Braintrust link or UUID (.txt + config.js, then a PR). Mode B is config-only (model,
  openaiAPI, reasoningEffort, verbosity, temperature, tools, custom props). Mode C ships a
  Braintrust prompt as an A/B experiment arm, and later ends it: promote the winner or revert,
  then remove the arm. Hard-blocks known-broken combinations. Trigger Mode A on "release
  prompt", "update the prompt", "deploy prompt", "ship prompt", "promote prompt", "release
  the prompt from PROD-XXX", a Linear URL whose title mentions a prompt release, a
  braintrust.dev prompt URL, or a bare Braintrust prompt UUID. Trigger Mode B on "set
  feed-prompter reasoningEffort to medium" or any named prompter plus a property change.
  Trigger Mode C on "release the prompt as an experiment", "AB test this prompt", "the
  {experiment} experiment ended", "arm a won, promote it", or "control won, remove the
  experiment".
metadata:
  version: "3.10.1"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Tools/Braintrust
    - Tools/GitHub
    - Tools/Linear
    - Tools/OpenAI
    - Tools/Aura API                 # Aura API access pattern (AURA_API_TOKEN)
---

# Update Intelligence Prompt

Updates IntelligenceService prompter configuration — the `.txt` prompter file under
`src/prompters/` and/or the per-prompter block in `src/config.js`. Runs a hard-block
validator before any write to catch known-broken model + param combinations.

> **Braintrust deprecation (in progress).** Aura is deprecating Braintrust, and a
> replacement prompt store (versioning, promotion) is planned but not yet chosen.
> **Span logging from IntelligenceService is on.** A missing span is worth
> investigating rather than expected, and `data:save-to-braintrust-dataset` reads
> those spans to build eval rows. Until
> it lands, the BT prompt registry remains the authoring source for Mode A and the
> Step 7 Production sync still applies — but don't add new BT-dependent steps. To
> inspect what a prompter actually sent in production, use OpenAI stored completions
> with the per-prompter key from the IS `.env` (e.g. `OPENAI_SUMMARY_API_KEY`):
> `GET /v1/chat/completions?limit=N` lists calls with metadata (`conversationId`,
> `transaction`, service `version`); `GET /v1/chat/completions/{id}` and
> `/{id}/messages` return the params and messages. Note the stored object does not
> echo `reasoning_effort` — infer it from the deployed config plus reasoning-token
> counts.

## Modes

The skill operates in one of three modes, chosen from the user's request:

- **Mode A — Braintrust-driven** (existing). Triggered by a Linear issue/comment URL
  containing a `braintrust.dev` prompt link, a bare `braintrust.dev/.../prompts/...`
  URL, or just a Braintrust prompt UUID. Updates the `.txt` prompter file from
  Braintrust, applies the prompt's model/params to `config.js`, then **opens a PR
  by default** against `AuraHealth/IntelligenceService` and waits for it to merge.
  After merge, syncs the Braintrust Production prompt and optionally drives a QA chat.
  Flows through Steps 1 → 8. Direct push to `master` is an explicit opt-in (see
  Step 6) — useful for trivial reverts/rollbacks or operator-driven hotfixes, not
  the default.

- **Mode B — Config-only** (new). Triggered by direct user input naming a prompter and
  one or more property changes. Skips Steps 1–4 and Step 8 (no Braintrust fetch, no
  `.txt` edit, no QA chat). Goes straight to Step 5 (config
  edit) → validator → **PR via `gh`** against `AuraHealth/IntelligenceService`. The PR
  is reviewed and merged by the user; merging triggers the release. Once that release
  deploys, **Step 7 runs in Mode B too**: a config-only change moves the model and params
  the Production prompt advertises, so leaving it unsynced makes the registry disagree
  with what production actually runs. Mode B's Step 7 changes `options` only and leaves
  the stored prompt text untouched.

- **Mode C — Experiment arm.** Same input as Mode A plus an explicit ask to release it
  as an experiment. Runs Steps 1–3, then writes the candidate to an arm file
  (`<key>.<experimentName>.a.txt`) plus an `experiments` block in `config.js` — the base
  `.txt` and params stay as control `b` — then Step 6 (PR only). **Skips Step 7**: Braintrust
  Production keeps advertising the control. After merge, asks Viktor to create the
  experiment via `shared:post-to-slack`. **Ending it** (C7): confirm it ended with one arm,
  file a card of its own for the clean-up, then remove the arm. When the arm won, promote its
  body to the base first, then sync Braintrust Development and Production (Step 7).
  Full procedure: [`references/experiment.md`](references/experiment.md).

**How to pick a mode:**

| User input | Mode |
|---|---|
| Linear/Braintrust URL, or "release/deploy/update the prompt from <ticket>" | A |
| Prompter name + property change ("set `<name>` `<key>` to `<value>`", "remove `<key>` from `<name>`", "switch `<name>` model to `<X>`") | B |
| URL plus "as an experiment" / "A/B" / "AB test" | C |
| "the `<experiment>` experiment ended", "arm a won", "control won, remove the experiment" — **wins over any URL or Linear link in the same request** | C (end, C7) |
| Both signals (URL **and** an explicit config-only request) | Ask once which mode to run |
| Neither signal clear | Ask the user what they want to change |

Steps marked **(Mode A only)** are skipped in Mode B. The validator in Step 5 runs in
**both** modes.

> **When Mode B is cleaner even though a Braintrust URL was provided.** If the operator
> has already updated the Dev prompt's `options` outside the skill (e.g. via a direct REST
> POST or through the BT UI) **and** the prompt body isn't changing, Mode A's Step 2/4
> become no-ops: there's nothing to pull from BT that the operator didn't just put there,
> and there's no `.txt` file to rewrite. Mode B is the cleaner path, and it keeps the
> post-merge BT Production sync — the same `POST /v1/prompt` payload pattern
> from [`references/post-merge.md`](references/post-merge.md#7c--commit-a-new-production-prompt-version-via-rest-api)
> and the same pre-flight shape check in
> [7c-preflight](references/post-merge.md#7c-preflight--inspect-dev-vs-prod-shape-before-the-post),
> scoped to `options`. Surface the choice when both signals are present and the operator
> confirms the body is unchanged.
>
> **Every sync POST, Dev or Prod, builds its payload with `scripts/bt_payload.py`** from the
> target's own record. A POST clears any field it leaves out, so a hand-built payload wipes the
> target's description; the script keeps it unless `--description` sets a new one.

> **Activity comments belong on both surfaces.** Whenever a meaningful BT prompt change
> lands via REST POST — Dev or Prod — add an activity comment in the BT UI (the comments
> API is still 403/500 with an API key, see Step 7d). For Mode B and out-of-band Dev
> updates, this means the operator pastes a comment on the **Dev** prompt's activity tab
> too, not only on Prod after merge. Same format as the
> [Step 7d ITERATION template](references/post-merge.md#7d--add-an-activity-comment-manual) —
> the goal is that anyone scrolling the activity tab three months from now can answer
> "why did this version land" without leaving the page.

## Step 1: Fetch the Linear Issue and Comments (Mode A only)

Use the Linear MCP to get the issue and its comments. Look for `braintrust.dev` URLs in
comment bodies — that's where the prompt link lives.

```
mcp__plugin_linear_linear__get_issue(id: "<PROD-XXX>")
mcp__plugin_linear_linear__list_comments(issueId: "<PROD-XXX>")
```

Extract all `braintrust.dev` URLs from the comment bodies. The relevant URL format is:
```
https://www.braintrust.dev/app/{org}/p/{project}/prompts/{slug-or-id}
```

Parse out:
- `project` — e.g. `Journal`, `Notification`
- `slug` — the last path segment (e.g. `journal-development-80b0`, or a UUID like `e554dbb3-03c0-4617-a98c-22988813d580`)

If multiple Braintrust URLs are found, use the most recent comment's URL, or ask the user
to confirm which one to deploy.

> **The Braintrust URL is authoritative, not the ticket title.** Ticket titles can
> mislabel the prompter (e.g. a ticket titled "Release memory prompt" linking the
> Summary project). Derive the project and prompter from the BT URL and the prompt's
> name; if they disagree with the ticket title, flag the mismatch to the operator and
> offer to rename the ticket.

## Step 2: Fetch the Development Prompt from Braintrust (Mode A only)

> **Why REST API, not MCP:** The Braintrust MCP was found to return stale prompt params
> (e.g. wrong temperature). Always use the REST API to guarantee the latest version.

Use `$BRAINTRUST_API_KEY` from the shell environment — assume it is already set.

```bash
curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&slug=<dev-slug>" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY" | jq .
```

This always returns the **latest** version of the prompt.

Extract from `.objects[0]`:
- **System prompt text**: `.objects[0].prompt_data.prompt.messages[0].content`
- **Model**: `.objects[0].prompt_data.options.model` (e.g. `gpt-5.4`, `gpt-5.4-mini`)
- **Params**: `.objects[0].prompt_data.options.params`:
  - `reasoning_effort` → maps to config key `reasoningEffort` (camelCase)
  - `verbosity` → maps to config key `verbosity`
  - `temperature` → maps to config key `temperature`

You can extract each field individually with jq:

```bash
RESPONSE=$(curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&slug=<dev-slug>" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY")

echo "$RESPONSE" | jq -r '.objects[0].prompt_data.prompt.messages[0].content'  # system prompt
echo "$RESPONSE" | jq -r '.objects[0].prompt_data.options.model'               # model
echo "$RESPONSE" | jq    '.objects[0].prompt_data.options.params'              # params (temp, reasoning_effort, verbosity)
```

Also note the `id` (UUID) of the development prompt, and look for a Production prompt in
the same project — you can list all prompts in the project to find it:

```bash
curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&limit=20" \
  -H "Authorization: Bearer $BRAINTRUST_API_KEY" | jq '[.objects[] | {id, name, slug}]'
```

Find the entry whose name or slug contains "production" (e.g. `journal-production-338f`,
`summary-production`, `Production`) — save its `id` (UUID) for Step 7.

Some projects also carry a **legacy** production prompt (e.g. Summary's
`summary-production-legacy`); the sync target is the current one (`Summary (Production)`
/ `summary-production`), never the legacy object. If a project has no current Production
prompt at all, create one via `POST /v1/prompt` (fresh name + slug following the
`<Project> (Production)` / `<project>-production` convention, mirroring dev's content
and options) instead of updating the legacy prompt.

## Step 3: Map Project to IntelligenceService Files (Mode A only)

> **Mode B operators:** the user names the prompter directly (e.g. `feed-prompter`).
> The `<name>` is the prefix before `-prompter` **kept verbatim, not re-derived from the
> BT project name** — files are camelCase and match the config key (e.g.
> `memory-sorting-prompter` → file `memorySorting.txt`, config key `memorySorting`).
> `$IS_DIR` is the worktree path — resolve `$IS_ROOT` and create the worktree using the
> resolution block at the end of this step (the same one Mode A uses; it sets
> `$IS_DIR` to `$IS_ROOT/.claude/worktrees/<ticket-id>-<slug>`). Then jump to Step 5.

A single Braintrust project can host multiple runtime prompters — they're separate Dev
+ Prod prompt pairs inside the same project. Map the BT *prompt* name (not the project
name) to a camelCase identifier; that identifier is both the `.txt` filename and the
`config.prompters.*` key. Brackets and the `[Development]` / `[Production]` /
`- Development` / `- Production` tag are dropped; the remainder is camelCased.

| Braintrust prompt name            | Prompter file                                | Config key                                |
|-----------------------------------|----------------------------------------------|-------------------------------------------|
| Journal                           | `src/prompters/journal.txt`                  | `config.prompt.journal`                   |
| Notification                      | `src/prompters/notification.txt`             | `config.prompt.notification`              |
| Memory                            | `src/prompters/memory.txt`                   | `config.prompt.memory`                    |
| Memory Ingestion                  | `src/prompters/memoryIngest.txt`             | `config.prompt.memoryIngest`              |
| Memory Ingestion Sorting          | `src/prompters/memoryIngestionSorting.txt`   | `config.prompt.memoryIngestionSorting`    |
| Memory Sorting                    | `src/prompters/memorySorting.txt`            | `config.prompt.memorySorting`             |
| Feed                              | `src/prompters/feed.txt`                     | `config.prompt.feed`                      |
| Summary                           | `src/prompters/summary.txt`                  | `config.prompt.summary`                   |
| Quick Processing Done             | `src/prompters/quickProcessingDone.txt`      | `config.prompt.quickProcessingDone`       |
| Quick Processing Sentinel         | `src/prompters/quickProcessingSentinel.txt`  | `config.prompt.quickProcessingSentinel`   |

The config block lives at `config.prompt.<key>` — `src/config.js` defines it under
`prompt:`, and `Prompter.buildPrompt()` reads `config.prompt[configKey]`.

> **Mapping isn't 1:1 between BT project and IS file.** The Memory project alone hosts
> four runtime prompters (`memory`, `memoryIngest`, `memoryIngestionSorting`,
> `memorySorting`), each with its own Dev + Prod prompt pair in BT and its own
> `.txt` + config block in IS. When in doubt, list the project's prompts and pick
> the matching pair:
>
> ```bash
> curl -s "https://api.braintrust.dev/v1/prompt?project_name=<project>&limit=30" \
>   -H "Authorization: Bearer $BRAINTRUST_API_KEY" \
>   | jq '[.objects[] | {name, slug}]'
> ```
>
> Then derive the camelCase identifier from the prompt name (drop the dev/prod tag,
> drop brackets, camelCase the rest). Memory eval-only prompts like
> `Memory Object Quality` or `Memory Candidate Selection` are evaluator harnesses
> (no runtime prompter file or config block) — skip those.

Resolve the IntelligenceService **main checkout root** (`$IS_ROOT`), then create an
**isolated worktree** off fresh `origin/master`. All edits (Steps 4–5) and the commit/PR
(Step 6) happen inside the worktree, so the shared checkout is never disturbed.

Worktrees live **inside** the repo at `$IS_ROOT/.claude/worktrees/<slug>` (gitignored),
**never** as a sibling like `../IntelligenceService-<ticket>` — a sibling clutters
`~/Development/Aura/Services/` and isn't where our tooling looks. See
[`/CLAUDE.md`](../../../../CLAUDE.md) → "PR workflow".

```bash
# 1. Find the main checkout root
if pwd | grep -q "IntelligenceService"; then
  IS_ROOT="$(git rev-parse --show-toplevel)"
else
  for cand in ~/Development/Aura/Services/IntelligenceService \
              ~/Work/AuraHealth/IntelligenceService \
              ./AuraHealth/IntelligenceService; do
    [ -d "$cand/.git" ] && IS_ROOT="$(cd "$cand" && pwd)" && break
  done
fi
[ -z "$IS_ROOT" ] && { echo "IntelligenceService checkout not found — ask the user for its path"; exit 1; }

# 2. Create the worktree off fresh origin/master (branch: <firstname>/<ticket-id>-<slug>)
git -C "$IS_ROOT" fetch origin --quiet
WT="$IS_ROOT/.claude/worktrees/<ticket-id>-<slug>"
git -C "$IS_ROOT" worktree add "$WT" -b "<firstname>/<ticket-id>-<slug>" origin/master

# 3. lint + the husky pre-push hook need node_modules; symlink the main checkout's copy
#    so the worktree works with no reinstall (fall back to `npm ci` in $WT if absent).
if [ -d "$IS_ROOT/node_modules" ] && [ ! -e "$WT/node_modules" ]; then
  ln -s "$IS_ROOT/node_modules" "$WT/node_modules"
fi

IS_DIR="$WT"   # every subsequent $IS_DIR reference points at the worktree
ls "$IS_DIR/src/prompters/<name>.txt"
```

## Step 4: Update the Prompter .txt File (Mode A only)

Replace the contents of `$IS_DIR/src/prompters/<name>.txt` with the system prompt text
from Step 2b.

**Take the body with `python3 ${CLAUDE_PLUGIN_ROOT}/skills/update-intelligence-prompt/scripts/bt_scaffold.py body < dev.json`** — it strips the trailing
`---` + `{{metadata.contextInstructions}}` of the *appended* template shape, and is a no-op on
Coach's *delimited* shape (body alone in `messages[0]`, `---` pair and context as their own
messages). Neither scaffold belongs in the .txt; `bt_scaffold.py` documents both shapes.

**Check the output keys are camelCase** before committing the `.txt`. Braintrust bodies are
sometimes authored in snake_case, and the keys become the API contract mobile reads. Rename
them in the `.txt` and the schema file rather than translating in model code; full procedure
and rationale in [`references/output-keys.md`](references/output-keys.md).

## Step 5: Update config.js Params

Both modes converge here. The source of the new params differs:

- **Mode A:** params come from Braintrust (`prompt_data.options` in Step 2's response).
- **Mode B:** params come directly from the user's request (e.g. "set `reasoningEffort`
  to `medium`"). The prompter is named by the user (`feed-prompter`, `coach-prompter`,
  …); the config block lives at `config.prompt.<name>` where `<name>` is the prefix
  before `-prompter`.

Find the prompter config block in `$IS_DIR/src/config.js`:

```js
<name>: {
  key: '<name>',
  model: 'gpt-5.4-mini',
  name: '<name>-prompter',
  // ... other keys
  temperature: 1,
},
```

### Idempotency check (Mode B)

Before going further, compare the requested key/value diff against the file's current
state. **If every requested change already matches the file's current value, stop:**
print `No changes needed — config already matches requested state.` and exit without
committing or opening a PR.

### API default by project

Default `openaiAPI` by the prompter's `project` (as defined in `config.js`):
`conversation`-project prompters stay on `chatCompletions`; every other project defaults to
`openaiAPI: 'responses'` (+ `useResponseConversation: false` for one-shot prompters). Seed
this default only when `openaiAPI` is absent from the config block; when it's already
present, leave it unless the request explicitly asks to change it.

**Announce any API-type change.** Whenever the skill flips a prompter's `openaiAPI` — whether
applying this default or honoring an explicit request — tell the user before committing, e.g.
`Changing feed-prompter openaiAPI: chatCompletions → responses (+ useResponseConversation: false)`.
An API-type switch must never be silent. Full rule and rationale in
[`references/config-validation.md`](references/config-validation.md#default-response-api-for-non-conversation-projects).

### Editable properties, normalizer, and validator

Before writing — in **both** modes — apply the config rule spec in
[`references/config-validation.md`](references/config-validation.md): the full list of
editable properties (standard OpenAI params + custom per-prompter props) with key-order
and removal discipline; the API-default-by-project rule; the chat-latest normalizer (N1);
the hard-block ERROR rules (E1–E5, abort with no override — E1/E2/E3 fire only on
`openaiAPI: 'chatCompletions'`); and the verbosity+JSON WARNING gate (requires an explicit
`CONFIRM`). The validator runs in both modes; do not commit until it passes.

## Step 6: Lint, Commit, and Ship

**Default for both modes: branch + lint + PR via `gh` against
`AuraHealth/IntelligenceService`.** The user reviews and merges the PR; the merge to
`master` triggers the release pipeline. Direct push to `master` is an explicit Mode A
opt-in covered at the end of this step.

### Default — branch + PR via `gh`

```bash
cd "$IS_DIR"   # the worktree — already on branch <firstname>/<ticket-id>-<slug> off fresh origin/master

# Edits from Steps 4–5 are already in this worktree.

# Lint must pass before commit
source ~/.nvm/nvm.sh && nvm use && npm run lint

# Stage the files actually changed by the mode:
#   Mode A → both the .txt and config.js (config.js only if params changed)
#   Mode B → src/config.js only
git add src/prompters/<name>.txt src/config.js   # adjust per mode

# Mode A commit message:
git commit -m "Update <name> prompt: <model> with <reasoningEffort> reasoning, <verbosity> verbosity [<TICKET>]"
# Mode B commit message:
git commit -m "Update <name>-prompter config: <one-line summary> [<TICKET>]"

# Push (pre-push husky hook runs `npm run lint:cache` and needs nvm in PATH)
source ~/.nvm/nvm.sh && nvm use && git push -u origin "$(git branch --show-current)"

# Open the PR. Write the body to a temp file first, then pass it with
# --body-file — never the command-substitution/process-substitution heredoc
# forms `--body "$(cat <<'EOF' … )"` / `--body-file <(cat <<'EOF' … )`, which
# mis-parse on apostrophes, backticks, and em-dashes in the body.
cat > /tmp/pr-body-intel-prompt.md <<'EOF'
## Summary
- <Mode A: one-line summary of the prompt revision; Mode B: bulleted key/value diff>
- Linear: <TICKET URL>
- Braintrust: <dev prompt URL>     # Mode A only

## Validation
- Hard-block validator (E1–E5): passed
- Warning gate: <none / verbosity+JSON warning was confirmed by operator>
- `npm run lint`: passed

## Test plan
- [x] Lint passes locally
- [ ] Merge → release pipeline picks up the change
- [ ] After deploy, sync Braintrust Production prompt + paste activity comment
- [ ] After deploy, confirm prod logs reflect the new behaviour
EOF
gh pr create --draft \
  --repo AuraHealth/IntelligenceService \
  --base master \
  --title "<commit title>" \
  --body-file /tmp/pr-body-intel-prompt.md
```

> **Note:** The pre-push husky hook runs `npm run lint:cache`. If `npm` is not in PATH,
> the push will fail with "command not found". Always source nvm before pushing:
> `source ~/.nvm/nvm.sh && nvm use && git push`

The PR opens as a draft. **Run the BugBot cycle before calling it ready**
([`references/bugbot-cycle.md`](references/bugbot-cycle.md)): `eng:bugbot-review` in wait mode on the
current head, answer and resolve every finding, and run `gh pr ready` only when 0 unresolved BugBot
threads remain and every CI check has passed. Then report the PR URL. In **both** modes, **wait for the
PR to merge** and its release to deploy before proceeding to Step 7 (see Step 7 intro for
the gate). Step 8 is Mode A only.

### Direct push to `master` — Mode A opt-in only

Direct push remains available as an explicit Mode A opt-in for situations where a PR is
overhead the operator doesn't want — typically:

- a trivial revert or rollback of a prompt that was just deployed
- a hotfix where the operator is acting deliberately and accepts responsibility
- the operator explicitly says "push direct", "skip PR", "direct release", "no PR", or
  "release directly" when invoking the skill

Do **not** infer this path from a Linear URL or Braintrust link alone — the operator
must say it. If they do, run the same lint + commit sequence in the worktree, then push
the branch tip straight to `master` (no PR). A worktree never has `master` checked out,
so push with an explicit refspec rather than `git checkout master`:

```bash
cd "$IS_DIR"   # the worktree, on the feature branch off fresh origin/master
source ~/.nvm/nvm.sh && nvm use && npm run lint
git add src/prompters/<name>.txt src/config.js
git commit -m "Update <name> prompt: <model> with <reasoningEffort> reasoning, <verbosity> verbosity [<TICKET>]"
# Push the worktree branch's commit directly to master — no master checkout needed
source ~/.nvm/nvm.sh && nvm use && git push origin HEAD:master
```

In the direct-push path, Steps 7 and 8 can proceed once the deploy is live (Step 8c
already covers that gate). No separate merge gate is needed.

### Clean up the worktree

Once the PR has merged (or the direct push is deployed), remove the worktree. The
`node_modules` symlink is removed with it; the main checkout's real `node_modules` is
untouched.

```bash
git -C "$IS_ROOT" worktree remove "$WT"   # or run eng:clean-up-worktrees later
```

## Step 7: Update the Production prompt on Braintrust (Modes A and B, and Mode C when an arm wins; post-merge)

> **Gate:** wait for the IS PR from Step 6 to be merged (or the direct-push deploy to
> go live) before running this step. Syncing Braintrust prod ahead of the IS code
> creates a window where prod Braintrust calls use the new prompt while IS still
> expects the old shape. Confirm via `gh pr view <PR_NUMBER> --repo AuraHealth/IntelligenceService --json state,mergedAt`
> or by asking the operator to say "merged" / "released as `<version>`".

After the merge gate clears, sync the Production prompt so Braintrust advertises what
production actually runs, then provide the operator with the manual ITERATION activity
comment to paste. What gets synced depends on the mode:

- **Mode A** — model, params, and the prompt text, matching the dev prompt. The text
  written to prod is dev's, which already carries the
  `{{metadata.contextInstructions}}` placeholder, in whichever template shape it has.
- **Mode B** — `options` only. There is no dev prompt in play, so build the payload from
  the **Production** prompt's own `prompt_data` and replace only the `options` keys the
  config change moved. The stored text, messages, and tools stay exactly as they are.

Full procedure with REST payloads, the `curl` fallback for SSL-broken environments,
and the activity comment template lives in
[`references/post-merge.md`](references/post-merge.md#step-7-update-the-production-prompt-on-braintrust).

## Step 8: Optional QA chat with Aurie (Mode A only, conversation prompters only)

Only applicable for the prompters in the `/ai/dialogue/ask` server allowlist
(`src/models/dialogue.js`) — **`coach`**, **`chat`**, **`companion`**,
**`quickProcessing`**, **`quickProcessingSentinel`**. For every other prompter
(`journal`, `notification`, `memory`, `feed`, …) print one line: `QA chat not applicable
for this prompter.` and stop. The two quick-processing prompters take different `/end`
shapes — see the reference below.

When applicable: confirm the new release is live (version-probe the target env's `/`
endpoint), then hand off to `qa:chat-with-aurie` with the prompter name, environment,
and a one-line scenario hint drawn from the Linear ticket title or commit message.

Detailed gating, deploy probe, and hand-off contract live in
[`references/post-merge.md`](references/post-merge.md#step-8-optional-qa-chat-with-aurie).
