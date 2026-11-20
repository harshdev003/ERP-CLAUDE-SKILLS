---
name: plan-prompt
description: >
  Plans a new IntelligenceService prompter from a feature plan before any prompt text or
  code is written. Reads the card, PRD and Figma, surfaces prompt-changing product
  decisions, then picks prompts to start from (including deleted ones), recommends model,
  effort, tier and project, chooses context inputs, drafts the output schema, safety rules
  and eval plan, and offers a first prompt draft. Trigger on "plan the prompt for PROD-123", "plan a prompt for
  this feature", "prompt planning", "what prompt do we need for this card", "design the
  prompter for {feature}", "which prompt can we reuse for this", "what inputs should the
  prompt get", "what model and tier for this prompter", "scope the prompt", "draft the prompt from the plan", "re-plan the prompt", "check the sub-issues against the card", or a Linear
  URL with a request to plan its prompt. Do NOT use to change or release an existing
  prompt (eng:update-intelligence-prompt), QA a prompt (eng:qa-prompt), or iterate the
  coach prompt (product:prompt-knowledge).
metadata:
  version: "1.1.1"
knowledge:
  reads:
    - Guide/Index        # baseline routing aid (universal)
    - Tools/Linear       # reading the feature card, comments and related cards
    - Tools/Notion       # PRD and linked docs
    - Tools/OpenAI       # models, tier rates, OpenAI project inventory
    - Tools/Braintrust   # projects and datasets for the eval plan
    - Products/Aurie     # Aurie's voice, for prompts that speak as Aurie
    - Compliance/Privacy # prod data samples and plans posted to Linear
---

# Plan Prompt

You plan a new prompter for the Aura IntelligenceService: what it reuses, how it's
configured, what it reads, what it returns, and how it's evaluated. You produce a plan, not
a prompt. The plan is finished when every decision in it has an answer and a source, and an
engineer could split it into tickets without re-reading the code you read.

Most of the work is reading code and history that the feature card doesn't mention: an old
prompt deleted months ago may be the right base, a builder may silently return nothing at the
time the job runs, a stored field may not mean what its name says. **Read before you
recommend, and cite what you read.**

## Inputs

- A Linear URL or identifier (usual), and/or a PRD or Notion link, Figma link, or a pasted
  description.
- An IntelligenceService checkout: `INTELLIGENCE_SERVICE_PATH`, or the path the user gives
  when asked. Never guess a home-directory layout.

## Workflow

### Step 1: Read everything about the feature

1. **Linear.** Linear MCP first (`get_issue` with relations, `list_comments`); fall back to the
   Linear API with `LINEAR_API_KEY`. Read the parent, sub-issues and related cards too: answers
   often sit in a sibling's comment thread.
2. **Docs.** Fetch linked PRD or Notion pages (Notion MCP).
3. **Design.** Figma MCP for linked frames, including the annotations next to the frames,
   which often carry behaviour rules. If Figma MCP isn't authenticated, tell the user, and read
   the card's embedded screenshot (Linear `extract_images`) as a fallback.
4. **Execution cards.** Collect the cards that already carry an implementation of this feature:
   the feature card's sub-issues and their own sub-issues (e.g. a backend card with one sub-issue
   per execution step), plus cards linked as related. Note each card's identifier, state and
   description. Step 10 compares them against the plan.
5. **Ask** the user for anything the card points at but you can't reach.

Write a short requirements list, one line per requirement, each with its source and date. A
requirement given later in a comment thread replaces an earlier one; quote both when they
differ.

### Step 2: Surface the decisions, then stop

Walk [`references/decision-checklist.md`](references/decision-checklist.md) against the
requirements. List only the decisions that are still open and would change the prompt, its
inputs, its config or its cost. Quote contradictions between sources side by side.

Present them as numbered questions, each with the options and your recommendation, worded so
the user can paste them to the PM. **Do not start Step 3 until the user says the requirements
are settled**, either by answering or by telling you to proceed with your recommendations. A
plan built on an unanswered trigger or lifecycle question gets rebuilt.

### Step 3: Prepare the code

Read IntelligenceService at `origin/master`, not the user's working tree, and confirm which
checkout you're reading:

Resolve the checkout from `INTELLIGENCE_SERVICE_PATH`; if it's unset, ask the user for the
path. If the path isn't a git work tree, stop and say so; don't substitute another path.
`git rev-parse` accepts both a main checkout and a linked worktree, where `.git` is a file.

```bash
IS_DIR="${INTELLIGENCE_SERVICE_PATH:?set INTELLIGENCE_SERVICE_PATH or pass the path the user gave}"
git -C "$IS_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || { echo "Not a git checkout: $IS_DIR"; exit 2; }
IS_ROOT=$(git -C "$IS_DIR" rev-parse --show-toplevel)
git -C "$IS_ROOT" fetch origin --quiet
# drop read-only worktrees left by earlier runs before making a new one
git -C "$IS_ROOT" worktree list --porcelain \
  | awk '/^worktree .*\/\.claude\/worktrees\/plan-prompt-[0-9]+$/ {print \$2}' \
  | while read -r old; do git -C "$IS_ROOT" worktree remove --force "$old"; done
SRC="$IS_ROOT/.claude/worktrees/plan-prompt-$(date +%Y%m%d%H%M%S)"
git -C "$IS_ROOT" worktree add --detach "$SRC" origin/master --quiet
```

The read-only worktree lives under the repo's `.claude/worktrees/`, never as a sibling of the
repo or in a temp directory.

Run the inventory once and keep it for Steps 4–5:

```bash
node "${CLAUDE_PLUGIN_ROOT}/skills/plan-prompt/scripts/inventory_prompters.js" --repo "$SRC"
node "${CLAUDE_PLUGIN_ROOT}/skills/plan-prompt/scripts/inventory_prompters.js" --repo "$SRC" --deleted
```

The first prints every prompter with model, effort, tier, project, API, JSON output and static
call sites; the second lists prompt files deleted from history with the command to restore each.
Keep the worktree for the rest of the run, including Step 12. Remove it whenever the run
ends, at whatever step: `git -C "$IS_ROOT" worktree remove "$SRC"`. A run that ends without
cleanup is covered by the sweep above on the next run.

### Step 4: Find prompts to start from

1. **Shortlist** 2–4 candidates by purpose from the live inventory, the deleted list, and the
   `.txt` files themselves. Deleted prompts count: a removed daily-feed prompt held the closest
   existing "question about something from your past conversations" item.
2. **Check each candidate is alive.** "Static callers: none found" isn't proof of dead code
   (conversation prompters are resolved from a variable), and a caller isn't proof of life. Trace
   each caller to an entry point: a mounted route in `src/http/router`, a job in `package.json`
   scripts, a listener, or a config flag that is on. Report dead or switched-off ones as such.
3. **Compare voice and shape.** Read the prompt text. Who speaks (Aurie to the user, or the
   user's own words)? Does it output what the feature needs (questions vs statements, one item
   vs a list)? A candidate in the wrong voice is a source of tone guidance at most.
4. **Record** for each: what to take (which section), what to leave, and its model, effort,
   tier, project and inputs (its `doBuildContextInstructions`).

Choose one base, and say which section. The new prompter is a copy in a standalone class
extending `Prompter`, never a subclass of the candidate.

### Step 5: Recommend the config

Follow [`references/config-rules.md`](references/config-rules.md):

- **Tier** from the call path decided in Step 2: awaited in a request → standard; cron or
  un-awaited side effect → flex. Name the entry point.
- **Model and effort**, with the reason. Flag any model id not already in `config.prompt` and
  list what to verify on its first call.
- **Project**: what already runs on it and the cost-attribution trade-off. Point at
  `Tools/OpenAI` for prices; don't restate them.
- **JSON output** with an on-disk schema; draft it in Step 7.

### Step 6: Choose the inputs

Start from [`references/context-builders.md`](references/context-builders.md), then read the
body of every builder you pick in `$SRC/src/prompters/prompter.js`. For each input, state what
it selects, its window and cap, and why the prompt needs it. Then:

- **Check timing.** Will the builder return data at the moment this prompter runs? Date-scoped
  builders are empty just after local midnight; dialogue-based builders need a live
  conversation.
- **Cut overlap.** User information already includes the profile; core memories overlap the
  profile; recent memories overlap running memories plus summaries. Say what you left out
  and why.
- **Cap anything unbounded** in the new prompter's own query. Size the cap from data.
- **Add the feature's own history**: previous outputs for no-repeat, and outputs the user acted
  on as an interest signal.
- **Summaries vs raw messages.** Default to summaries; if raw text might add specificity,
  propose it as a capped eval variant (Step 8) rather than deciding blind.
- **Check stored data.** When an input's quality matters (memory types, field coverage, sizes),
  give the user the read-only SQL from the reference, then read the results with them. Raw rows
  stay out of Linear, Slack and PRs (`Compliance/Privacy`).

### Step 7: Draft the output and the content rules

- **Schema**: strict, enums for categories, `minItems: 0` when nothing is a valid answer,
  `maxItems` from the volume decision.
- **Content rules** for the prompt text: which base section to extend and how, category and
  volume rules, the no-repeat rule, and Aurie's voice (`Products/Aurie`).
- **Safety**: for any output shown without the user asking, rules on which topics may be
  resurfaced, how much detail a visible field may carry, and which signals (self-harm, crisis,
  acute grief) never become an output.

### Step 8: Plan the eval

- **Braintrust dataset scenarios**, one line each: sparse history, rich history, heavy user with
  many previous outputs, nothing worth producing, sensitive content, inputs that don't fit the
  categories, plus anything specific to the feature. `data:save-to-braintrust-dataset` builds
  rows from prod spans.
- **Variant comparisons** for every input question left open in Step 6, with the metric that
  decides (specificity, repeat rate, safety, tokens per run).

### Step 9: Write the plan

Fill [`references/plan-template.md`](references/plan-template.md) and show it in chat.

### Step 10: Compare the plan with existing execution cards

Skip this step when Step 1 found no execution cards.

Otherwise, compare every execution card, its description and the comments this skill posted on
it, against the plan, and list each place they disagree,
using the table and the comparison checklist in
[`references/divergence-table.md`](references/divergence-table.md): schema enums, stored columns
and states, inputs and their caps, config (model, tier, project), gating, routes, and safety
rules. Each row names the card, what the card says, what the plan now says, and the source of
the change. A card that agrees with the plan gets no row.

Mismatches already sent to a card as a comment are shown as already flagged, not as new. When
nothing new differs, say so in one line and offer no card updates.

### Step 11: Offer the next actions

Offer, without doing any of them unasked:

- **When execution cards exist:** updating them with the Step 10 differences, as anchored
  replacements and insertions in each description, showing the exact old and new text first. A
  card already **In Progress** or later gets a marked comment listing its differences instead of
  a description edit, so the implementer sees the change and a re-run doesn't repeat it.
- **When none exist:** creating execution sub-issues from the plan's steps with
  `shared:add-linear-issues`, carrying each step's decisions into its card.
- Posting the open questions to the feature card as a comment, addressed to whoever owns them.

All of these are writes to Linear: show the exact text and wait for a yes.

### Step 12: Offer a first draft of the prompt

Once the plan has no open questions, offer to draft the prompter's `.txt`. Draft only on a
yes. When this step is the entry point (drafting from a plan written earlier), run Step 3
first so `$SRC` is a fresh `origin/master` worktree; never read headings from the user's
working tree. Then follow [`references/prompt-draft-template.md`](references/prompt-draft-template.md):

- **Instruction sections** (role, task, guidelines, output format) written in full from the
  plan's content rules, starting from the chosen base section, in Aurie's voice
  (`Products/Aurie`).
- **Context sections** with their real headings and `[placeholder]` bodies, in the order the
  prompter will append them. Take each heading verbatim from the builder that emits it
  (`grep -n '###' "$SRC/src/prompters/prompter.js"`); give each new builder a heading in the
  same style and mark it new.

Save the draft as `{configKey}.txt` in the session scratchpad and show it in chat. It is a
starting point for prompt work and the eval, not a commit: don't write it into the
IntelligenceService checkout.

## Output

A prompt plan in the template's shape: requirements, decisions with sources, open questions,
starting point, config, inputs, output schema, content and safety rules, eval plan and proposed
execution steps. When execution cards exist, a table of where they differ from the plan. On
request, a first-draft prompt with placeholder context sections.

## Edge cases

- **No IntelligenceService checkout** → ask for the path; plan only Steps 1–2 without it.
- **Feature isn't an IntelligenceService prompter** (a client-side string, another service) →
  say so and stop after Step 2.
- **Changing an existing prompter, not adding one** → use `eng:update-intelligence-prompt`.
- **Requirements never settle** → produce the plan with the open questions table filled and every
  dependent section marked "blocked on question N"; don't guess the answer into the plan.
- **Re-run on the same card** → re-read the card and comments first; answers given since the last
  run replace open questions, and Step 10 shows how the execution cards have fallen behind. A
  re-run right after the cards were updated reports no differences. Writes to Linear happen only
  on a fresh yes.
