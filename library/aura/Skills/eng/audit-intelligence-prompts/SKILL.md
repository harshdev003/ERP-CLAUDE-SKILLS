---
name: audit-intelligence-prompts
description: >
  Three-way drift detector for IntelligenceService prompters. For each prompter in
  `src/config.js`, compares three sources — the repo, the Braintrust Production prompt and the
  Braintrust Development prompt — and prints a per-prompter matrix of body / model / params /
  tools drift. Also diffs the live config against the Tools/OpenAI prompter inventory table
  and proposes additions, or rebuilds it from config.js. Read-only; fixes go through
  `eng:update-intelligence-prompt`. Trigger on "audit prompts", "audit prompt drift", "check
  intelligence prompts", "are the prompts in sync", "check repo vs Braintrust", "find prompt
  drift", "sync AI knowledge prompter table", "is Tools/OpenAI up to date", "is the prompter
  config aligned", "full prompter inventory", "regenerate the prompter table", or "rebuild the
  prompter inventory table". Do NOT trigger for snapshotting the local inventory
  (`eng:report-intelligence-prompters`), OpenAI cost analysis (`eng:analyze-openai-costs`), or
  QA-chatting with Aurie (`qa:chat-with-aurie`).

metadata:
  version: "3.4.3"
knowledge:
  reads:
    - Guide/Index
    - Tools/Braintrust
    - Tools/OpenAI
    - Tools/Notion                   # Notion REST / MCP access pattern (NOTION_TOKEN)
  writes:
    - Tools/OpenAI    # prompter inventory table — missing rows + stale model updates
---

# Audit Intelligence Prompts

## Goal

Detect drift across the three places a prompt's truth lives:

1. **IntelligenceService repo** — `src/prompters/<name>.txt` + the per-prompter block in `src/config.js → prompt.<key>`. This is what production runs.
2. **Braintrust `<Name> [Production]`** — the production prompt as recorded on Braintrust. Used by replay/eval.
3. **Braintrust `<Name> [Development]`** — the dev iteration where prompt-engineering happens.

The repo and Braintrust [Production] should be **byte-identical** (modulo the trailing `{{metadata.contextInstructions}}` template scaffold). Braintrust [Development] can legitimately lead [Production] — that's where iteration happens — but when work is done, [Development] is supposed to be promoted to [Production] and the IS repo updated via `eng:update-intelligence-prompt`. This skill is how you find the gaps.

## What gets compared

For each prompter:

| Dimension | repo↔prod | repo↔dev | prod↔dev |
|---|---|---|---|
| **Body** — system prompt text | yes | yes | yes |
| **Model** — `model` string | config↔prod | n/a | yes |
| **Params** — `reasoningEffort` / `verbosity` / `temperature` | config↔prod | n/a | yes |
| **Tools** — function-tool list, by name | config↔prod | n/a | yes |
| **Tool parameters** — each tool's property names + required set | config↔prod | n/a | yes |
| **Tool text** — each tool's description, and each property's description + enum | config↔prod | n/a | yes |

**Stripped before comparing bodies:** the trailing `\n\n[---\n\n]{{metadata.contextInstructions}}` template scaffold. It's a Braintrust-only marker that the runtime fills in. Coach's templates use the *delimited* shape instead (body alone in `messages[0]`; `---` pair and context as their own messages), where nothing needs stripping. See [`references/strip-scaffold.md`](references/strip-scaffold.md) for the exact regex and why both Braintrust sides need stripping (not just Prod).

**Tool parameters compare property names and the required set**, read from the repo's own `src/tools/<name>.json` (what `toolManager.getSchema()` sends) against Braintrust's inline tools and referenced tool functions. Descriptions, `$schema` and `additionalProperties` are ignored: Braintrust stores keys the repo JSON does not and often carries no descriptions, so comparing them literally would be entirely false drift. **Tool text is its own row** (`tool:<name>.text`): the tool description and each property's `description` and `enum`, whitespace-normalised, with `$schema` / `additionalProperties` still ignored. The model reads this text, so drift here changes calls without changing the schema ([ENG-1574](https://linear.app/aura-health/issue/ENG-1574)). The excerpt starts just before the first difference.

**Ignored entirely:** `tool_choice` in params. Braintrust adds it as a UI default; IntelligenceService doesn't write it. Including it would be all false drift.

**Normalized:** the tools array is sorted by tool name before comparing — array order doesn't matter to OpenAI, so order mismatch isn't real drift.

## Step 1: Locate the IntelligenceService repo

```bash
IS_DIR="${INTELLIGENCE_SERVICE_PATH:-$HOME/Development/Aura/Services/IntelligenceService}"
[ -d "$IS_DIR/.git" ] || { echo "IntelligenceService not found at $IS_DIR — set INTELLIGENCE_SERVICE_PATH"; exit 2; }
```

Confirm the operator is auditing `master`:

```bash
git -C "$IS_DIR" branch --show-current
git -C "$IS_DIR" status -s
```

If on a feature branch with uncommitted work, ask whether to audit the current state or stash + check out master first. Default is to audit whatever is currently in the working tree — the operator usually wants to know "is what I'm about to merge in sync with BT?".

## Step 2: Run the audit script

```bash
python3 scripts/audit.py --repo "$IS_DIR" --output table
```

`scripts/audit.py` does the heavy lifting:

- Parses `src/config.js` for the `prompt.*` block (regex-based, tolerant of formatting).
- Lists all Braintrust projects and indexes prompts by `name` ending in `[Production]` / `[Development]`. Matching by **display name suffix**, not slug — slugs are stable in normal use but the Aura team has renamed them in bulk before. UUIDs from the matched result are cached for the run.
- For each prompter, fetches the [Production] and [Development] prompt bodies and params via REST.
- Applies the strip + normalization rules above.
- Prints the matrix table to stdout. Drifts also expand into a per-row detail block.

Flags:

- `--output table` (default) — human-readable matrix
- `--output json` — machine-readable, one row per prompter
- `--prompter <key>` — single-prompter mode; audit just that prompter
- `--strict` — exit non-zero on any drift (handy for CI)

Auth: `BRAINTRUST_API_KEY` must be set in the environment.

## Step 3: Read the matrix

The matrix has columns:

```
Prompter   Repo  Prod  Dev   R=P R=D P=D   CfgDrift                 P↔D-settings
```

- `Repo/Prod/Dev` — body char counts after scaffold strip.
- `R=P` `R=D` `P=D` — body equality, marked `✓` / `✗` / `·` (`·` = one side missing, comparison skipped).
- `CfgDrift` — `model` / `reasoningEffort` / `verbosity` mismatches between `src/config.js` and Braintrust [Production].
- `P↔D-settings` — which settings differ between BT Prod and BT Dev (model / params / tools / `scaffold`, the template shape — promoting dev would change prod's layout).

Rows with drift expand into a detail block below the matrix.

## Step 4: Decide what to do for each drift

For every drift row, ask the operator one question:

> Drift on `<prompter>`: `<one-line summary>`. Fix now, file a ticket, or skip?

The fix paths depend on the drift kind:

| Drift kind | Fix |
|---|---|
| **repo↔prod body** drifts | Chain into `eng:update-intelligence-prompt` Mode A with the Production prompt UUID — the skill will pull the BT body into the `.txt`, open a PR, and post-merge no BT resync is needed (BT is already canonical). |
| **repo↔prod config** drifts (`model` / `reasoningEffort` / `verbosity` differ between `config.js` and BT Prod) | Chain into `eng:update-intelligence-prompt` Mode B naming the prompter + the specific keys to align. The validator (E1–E5 + N1 chat-latest auto-strip + verbosity+JSON warning) gates the write. |
| **prod↔dev body or settings** drift | Can't be fixed automatically — print a one-liner telling the operator either to *promote* Dev to Prod on Braintrust (POST `/v1/prompt` with the Dev body to the Prod slug — see Tools/Braintrust → "POST creates a commit, PATCH does not") or to revert Dev to match Prod. This is a deliberate manual decision because Dev-Prod divergence is usually intentional iteration. |
| **repo↔prod tool text** (`tool:<name>.text`) | The repo `src/tools/<name>.json` is what production sends, so Braintrust follows it. Show the operator the before/after text, then `PATCH /v1/function/{id}` with the repo `description`, and/or the `function_schema` with only the drifted property texts changed. Keep `returns`, `$schema`, `additionalProperties` and the code (`function_data`) as they are. Re-fetch and re-run the audit to confirm. A function is shared by every prompt that references it, so name those prompts in the confirmation. |
| **No `[Production]` prompt at all** | Surface as `NO_BT_PROD`. Don't treat as drift. Several prompters are dormant (zero OpenAI spend) and this is expected. The skill should still call them out so the operator can decide whether to publish a BT prompt or remove the prompter. |

When chaining into `eng:update-intelligence-prompt`, pass the operator-facing context inline so the next skill doesn't have to re-derive what's drifting. Example invocation message:

> Release the `<prompter>` prompt from Braintrust prompt UUID `<prod-uuid>` (`<Project>`, slug `<slug>`, display name "`<name>`"). The repo's `src/prompters/<file>` is `<delta>` chars behind/ahead of Braintrust [Production] per the audit on `<date>`. Mode A.

Or for config-only:

> Update the `<prompter>-prompter` config: set `<key>` to `<value>`. Reason: audit on `<date>` found config.js drifts from BT [Production].

For Linear cards: the operator may want a parent ticket for the batch (e.g. `PROD-2160 Bugs - Cycle 29`). Ask once at the start of the fix loop and reuse it for every card.

## Step 5: AI Knowledge inventory sync

After the Braintrust drift matrix (Step 3), check whether the `Tools/OpenAI → Full prompter inventory` table in AI Knowledge is current. This catches new prompters that shipped without a corresponding table update — a common gap when a developer merges an IntelligenceService PR without running this audit.

```bash
python3 scripts/sync_ai_knowledge.py --repo "$IS_DIR"
```

`scripts/sync_ai_knowledge.py` parses the same `src/config.js` that `audit.py` uses and diffs it against the live Notion table. It reports four categories:

- **Missing** — prompters in `config.js` but absent from the table. These need a new row.
- **Stale model** — table rows whose `Model` column doesn't match `config.js`. These need a cell update.
- **Stale params** — table rows whose `Reasoning`, `Verbosity` or `Tier` column doesn't match. Reported, never patched in place; the fix is a rebuild (below).
- **Orphan** — table rows for prompters no longer in `config.js`. Likely deprecated; surface for operator decision (manual removal if agreed).

Auth: `NOTION_TOKEN` must be set. The script degrades gracefully if the token is absent — it prints a warning and exits without crashing the audit flow.

If changes are proposed, present them to the operator and ask for approval. For missing rows, fill in the **Role** and **Trigger** columns from the IntelligenceService job files (`src/jobs/`) and `src/config.js` context before proposing — don't leave both columns as placeholders if the information is available. On approval, apply:

```bash
python3 scripts/sync_ai_knowledge.py --repo "$IS_DIR" --apply
```

The `--apply` flag appends new rows to the table and patches stale model cells. New rows are added with the `Model`, `Reasoning`, `Verbosity` and `Tier` columns filled from `config.js`; `Role` and `Trigger` columns that couldn't be auto-derived are inserted as `— (fill in)` placeholders for the operator to complete at `https://app.notion.com/p/OpenAI-34c75e23af20812cbac8e0fcb0eefe1e`.

This step is independent of the Braintrust drift fix loop — run it even if Step 3 found no BT drift.

### Rebuilding the table (`--regenerate`)

`--apply` edits cells in an existing table. When the table's *shape* is wrong — a config field with no column of its own, so its value has nowhere durable to live — use `--regenerate` instead:

**Run the plan, show it to the operator, and wait for an explicit yes before applying.** This is the more destructive of the two write paths — `--apply` mis-edits a cell, `--regenerate --apply` deletes a table — so it takes the stricter gate, not a looser one. Never chain the two commands in a single step:

```bash
python3 scripts/sync_ai_knowledge.py --repo "$IS_DIR" --regenerate     # 1. plan — always
```

Paste the plan back and ask. Read out the three lines that decide whether it is safe: the column and row counts, any `- dropped` line (that prose is gone once applied), and the commit being stamped. Only after approval:

```bash
python3 scripts/sync_ai_knowledge.py --repo "$IS_DIR" --regenerate --apply   # 2. rebuild
```

It rewrites the table as `Prompter | Model | Reasoning | Verbosity | Tier | Role | Trigger / gating`, deriving the first five columns from `config.js` and carrying the hand-written **Role** and **Trigger** cells over verbatim (annotations and links included). Existing row order is preserved — it is hand-curated by prompter family, so alphabetising would destroy it — and new prompters are appended at the end with `— (fill in)` prose.

**The `Tier` column** is the OpenAI service tier (`config.prompt.<key>.serviceTier`), and it carries a decision rule rather than just a value: **flex ⇔ the prompter is reachable only from a cron job or an un-awaited side-effect path; standard ⇔ it is awaited inside a request handler, so a user is blocked on it.** Flex is billed at ~50% but queues, with the elevated `OPENAI_FLEX_TIMEOUT` (900s default), so putting a request-path prompter on flex can hang a user-facing call.

Its default differs from the other generated columns. An **absent** `serviceTier` means the OpenAI default tier, so the column renders `standard` — not the em dash used for a missing `reasoningEffort` / `verbosity`. Rendering it as `—` would read as "no tier decided", the opposite of what the config means, and would make every standard-tier row look like a gap to fill in.

Three things about how it works, because each one is a trap:

- **The table is deleted and recreated.** Notion rejects a `table_width` change on an existing table (`400`), so widening it has no in-place form. That moves the block id, which is why the script resolves the table from the `Full prompter inventory & trigger model` heading rather than the hardcoded `PROMPTER_TABLE_ID`.
- **Dropping a row discards its prose.** A prompter deleted from `config.js` loses its Role/Trigger text with the row. The plan names them before `--apply`; read that line.
- **The table is resolved within its section, not "the next table on the page".** `Tools/OpenAI` carries several tables, and `--regenerate --apply` *deletes* whichever one it resolves — so the search stops at the next heading of the same or higher rank. A missing inventory table is a hard error, never a silent adoption of the neighbouring section's.
- **It refuses to run against a dirty `src/config.js`.** A commit sha only describes the file if the working copy matches it — otherwise the stamp would attest to content that was never committed. `--allow-dirty` overrides and marks the stamp accordingly.

The script owns the paragraph directly below the table and rewrites it each run with the commit sha, the date `config.js` was last touched, and the prompter count. **Model / Reasoning / Verbosity / Tier are generated — never hand-edit them in Notion**; a hand edit is silently reverted on the next rebuild, and until then the page disagrees with production. Role and Trigger are the hand-written columns.

Rebuild whenever the diff mode reports stale params, or after any `config.js` change that moves a model or tuning knob — through the same plan-then-confirm gate above, every time. A rebuild is routine, but it is never automatic.

## Step 6: Re-run after fixes land

After the operator merges + releases the fixes, re-run `python3 scripts/audit.py` to confirm zero remaining drift. The skill's exit code is `0` if no drift, `1` if drift present (use this in `--strict` mode for CI).

## Pitfalls — common false drift signals to ignore

- **Trailing `{{metadata.contextInstructions}}` scaffold.** Already stripped from both Braintrust sides by the script. If you see ~33–39 char drift on repo↔dev or repo↔prod, that's almost always the scaffold and a bug in your local strip regex — see [`references/strip-scaffold.md`](references/strip-scaffold.md).
- **`tool_choice: 'auto'`.** Braintrust UI adds this by default; IntelligenceService never writes it. The audit script drops it before comparing.
- **Tools array order.** The script sorts by tool name. If you see a "tools differ" drift, look at the *set*, not the order.
- **Trailing newlines.** Both sides are `.rstrip("\n")`-normalised before equality.
- **CRLF on Windows.** The script forces `\n` line endings on both sides.

## Pitfalls — things that look like config drift but aren't

- **`temperature: 1` not set on Braintrust.** Most BT prompts don't include `temperature` in params (OpenAI default is 1, which matches what IS sets). The script treats absence as `1`.
- **`reasoningEffort` on chat-latest models.** Per Tools/OpenAI (and `eng:update-intelligence-prompt`'s N1 rule), `*-chat-latest` models ignore `reasoning_effort`. If `config.js` has it set on a chat-latest model and BT doesn't, that's a config bug — but it's also what the N1 rule auto-strips on the next release. Surface as drift; operator will run Mode B without the key.

## Out of scope / See also

- **Releasing a new prompt version** — `eng:update-intelligence-prompt` (Mode A from a BT prompt, Mode B for config-only). This skill *invokes* it per drift; it doesn't duplicate it.
- **Local config inventory snapshot** — `eng:report-intelligence-prompters` reads `config.prompt.*` and prints a table. No Braintrust comparison.
- **QA-chatting with a deployed prompt** — `qa:chat-with-aurie` after a Mode A release.
- **Posting activity comments on Braintrust** — the Braintrust comments API requires user-session auth, not API key. The `eng:update-intelligence-prompt` skill prints the comment template; the operator pastes it manually. This audit skill does not generate activity comments.

## References

- [`references/strip-scaffold.md`](references/strip-scaffold.md) — the trailing `{{metadata.contextInstructions}}` scaffold and why both BT Prod and BT Dev need stripping.
- [`references/inventory.md`](references/inventory.md) — snapshot of prompter → BT project mapping as of 2026-06-04. The script re-discovers from `src/config.js` at runtime; this is only a human-readable hint table.
- [Tools/Braintrust](../../../../) → "POST creates a commit, PATCH does not", "Multi-project layout", "Prompt page URLs — UUID, not slug" — background on the BT side of the audit.
