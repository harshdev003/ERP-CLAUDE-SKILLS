---
name: analyze-experiment
description: >
  End-to-end analysis for one or several A/B experiments across Aurie and Aura. Accepts
  explicit experiment names, "last N ended" from Slack, or a Learnings/Experiments row
  reference — the last enriches an existing row in place rather than duplicating it. Detects
  the app and routes Mixpanel queries and metric framing accordingly, resolves context from
  Slack, Linear and Mixpanel, then synthesises learnings plus cross-cutting takeaways and
  writes the Learnings row. Trigger on "analyze experiment {name}", "analyze the last N ended
  experiments", "what did we learn from {experiment}", "experiment learnings", "summarize
  experiment results", "enrich this experiment row", "enrich all blank-status experiments",
  "analyze all Running Aurie experiments", "sweep stale experiment rows", "pull Mixpanel data
  for {experiment}", "update Learnings/Experiments with the latest analysis", or an experiment
  name / Notion URL with a request for a readout.

metadata:
  version: "3.9.0"
knowledge:
  reads:
    - Guide/Index   # baseline routing aid (universal)
    - Compliance/Privacy           # PII rule for naming screened accounts (UID prefix + email domain only)
    - Products/Aurie          # brand voice for Aurie experiment learning phrasing
    - Products/Aura           # brand voice for Aura experiment learning phrasing
    - Identity/Audience            # vertical enum + ICPs
    - Learnings/Experiments        # source rows when enriching (in DB-row / DB-query input modes); also prior rows for cross-cutting patterns
    - Tools/Aura API               # auth + base URL conventions
    - Tools/Aura API/Experiments/List Experiments      # GET /experiments — pagination quirks, targetApp == null means Aura
    - Tools/Aura API/Experiments/Retrieve Experiment   # GET /experiments/:name to authoritatively check if/when an experiment ended
    - Tools/Mixpanel                    # service account auth + Engage/JQL API conventions (internal-account cohorts)
    - Tools/Mixpanel/Events             # canonical event names + descriptions for interpreting per-variant metrics
    - Tools/Mixpanel/Super Properties   # event-level context (Premium, Channel, Time, …) for correctly segmenting per-variant breakdowns
    - Tools/Slack                       # canonical Channel IDs (for skills) — resolves #ai-product → C04R4L67676 for the optional post-readout Slack reply
    - Tools/Linear                                   # Linear MCP / GraphQL access
  writes:
    - Learnings/Experiments        # create new rows OR update existing rows via record-knowledge action: update
---

# analyze-experiment

Analyze one or several Aurie experiments end-to-end. The skill connects Slack,
Linear, and Mixpanel, computes per-variant metrics, and produces learnings.

## When to use

Trigger when the user asks any of:

- "analyze experiment `<name>`"
- "analyze the last N ended experiments"
- "what did we learn from `<name>`"
- "experiment learnings for the last week"
- "summarize experiment results"
- "pull Mixpanel data for `<name>`"
- "enrich this experiment row" / pastes a `Learnings/Experiments` Notion URL
- "enrich all blank-status experiments" / "enrich all experiments where `Result` is blank"
- "update `Learnings/Experiments` with the latest analysis"

Also trigger when the user drops one or more experiment names in the context of a
readout, retro, or weekly update.

## Inputs

Accept any of:

- **One or more explicit experiment names** (e.g. `hardPaywall`, `twoStepPaywall`). — *create mode*
- **`last N ended`** — the most recent N experiments ended in `#support-bot`. — *create mode*
- **A date range** — all experiments ended in that window. — *create mode*
- **Notion URL or page ID of a `Learnings/Experiments` row** (e.g. `https://www.notion.so/34c75e23af208171afcad7210a898da0` or the bare UUID). — *enrich mode*, updates the specified row in place
- **DB query filter** (e.g. `Status is blank`, `Hypothesis is empty AND Result section is placeholder`, `End Date since 2026-03-01`) — *enrich mode*, iterates over matching rows and updates each in place
- **Sweep input** — N>10 experiments at once, or phrases like `all Running Aurie experiments`, `all Status: Running`, `every row with End Date blank`. — *sweep mode*, triggers the pre-classification pass in Step 1f before any deep analysis. Skips the full Slack/Linear/Mixpanel pipeline on rows that pre-classify as `NEVER_LAUNCHED` or `TOO_YOUNG`.

If the user didn't specify, default to `last 5 ended` in create mode.

### Skip-already-analyzed default

**Default behavior: do not re-run analysis on experiments whose `Learnings/Experiments` row is already enriched.** Before doing any Slack / Linear / Mixpanel work for an experiment, look up its row in `Learnings/Experiments` (by `Title`). Treat the row as **already analyzed** when **all** of these are true:

- `Status` is one of `Won`, `Lost`, `Inconclusive`, `Turned off` (i.e. not `Running` and not blank), AND
- Page body `## Learning` is non-placeholder (does not contain `awaiting analyze-experiment enrichment` or `_To be added_`), AND
- Page body `## Result` is non-placeholder (has actual metrics, not just dashboard captions like `Old Dashboard / New dashboard`).

For each input experiment, classify as **already-analyzed** or **needs-analysis**, then:

1. Report the split up front (e.g. `5 experiments → 3 already analyzed, 2 need analysis`).
2. Only run Steps 1–8 on the needs-analysis subset.
3. List the already-analyzed rows with their Notion URLs so the user can open them, but **do not re-fetch Mixpanel, Slack, or Linear for them**.

**Override:** the user can explicitly ask for a re-run — e.g. "re-analyze hardPaywall", "re-run analysis on the last 5 even if already done", "force refresh", or by passing the row's Notion URL directly in enrich mode (a direct row reference is treated as an explicit ask). When the user overrides, run the full workflow on every input regardless of row state.

This guard applies in **both** create mode (where the row may have been bulk-imported and already enriched) and enrich mode (where a DB-query input might match a mix of stale and already-enriched rows). The point is to avoid burning Mixpanel / Slack tool calls on experiments whose readout is already in the knowledge base.

### Create mode vs Enrich mode vs Sweep mode

This skill has three operating modes:

- **Create mode** — input is one or more experiment names / date ranges. The skill creates new rows in `Learnings/Experiments` at the end (Step 8 option 4). Use for experiments the team has just ended that haven't been tracked in the DB yet.
- **Enrich mode** — input is a Notion row reference or a DB query. The skill updates existing rows in place via `record-knowledge` `action: update` (Step 8 option 4b). Use when rows were bulk-imported from the source sheet and need enrichment with Mixpanel actuals + synthesised learnings.
- **Sweep mode** — input is a large list (N>10 experiments or a wildcard like `all Running Aurie`). The skill **pre-classifies** each row in Step 1f using cheap signals (Aura API, Mixpanel SUL, Slack mentions, Linear tickets) and only runs Steps 0–7 on rows classified `RUNNING_WITH_DATA`. `NEVER_LAUNCHED` and `TOO_YOUNG` rows get boilerplate close-outs without burning tokens on per-row Slack/Linear/Mixpanel fetches. Use for periodic catalog sweeps — validated 2026-05-29 on a 39-row Running-Aurie sweep where 11 were stubs and only 27 needed deep analysis.

All three modes share the same Slack / Linear / Mixpanel logic — they differ at the input resolution (Step 1), the pre-classification pass (Step 1f, sweep only), and the output routing (Step 8). When ambiguous, **sweep mode** wins if N>10, otherwise **enrich mode** wins if a matching row already exists in `Learnings/Experiments`.

## Workflow

### Step 0 — Pull AI Knowledge priors

Before any Slack/Linear/Mixpanel work, invoke `shared:retrieve-knowledge` to pre-load the paths declared in this skill's `knowledge.reads` frontmatter:

- `Products/Aurie` or `Products/Aura` — brand voice guidance for how to phrase learnings in Step 7. Pre-load **both** upfront (one may be a stub); select the relevant one once the experiment's app is determined in Step 1e.
- `Identity/Audience` — canonical vertical enum so the learning row's `vertical` field validates cleanly against [record-knowledge schema](../../../shared/skills/record-knowledge/references/base-schema.md#vertical)
- `Learnings/Experiments` — recent prior rows (last 20 or so). Surfaces cross-cutting patterns in Step 7 cross-cutting section — "is this the third paywall experiment that traded trials for engagement?"

Keep the fetched content in working memory through Steps 1–7; cite paths and URLs in the final readout.

If any path fails to resolve (taxonomy drift), note the miss and proceed — don't block the analysis.

### Step 1 — Resolve experiment list

Three paths depending on input mode:

**1a. Explicit experiment names (create mode).** Skip to Step 2. Each name gets resolved via Slack + Linear + Mixpanel from scratch.

**1b. Last N ended / date range (create mode).** AuraBot mirrors the same
`Experiment created/ended` post-stream into **two channels** that are
authoritative for this data:

- `#support-bot` (`C02FQE15YGP`)
- `#data-experiment` (`G017L0Q12FL`)

Read **both** as a defensive pattern — if the bot isn't a member of one
(invite-pending, renamed, archived), the other carries the same content.
Confirmed in the 2026-05-29 sweep: scanning `#data-experiment` returned 136
end-notes back to 2025-01-03; the subsequent `#support-bot` scan returned 0
new entries (perfect overlap).

Read the most recent 100 messages of each by **invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** with `--newest 100` — once per channel. `--newest` caps to the latest N by count (faithful to the old `limit=100`), so it works whether the channel is busy or quiet; that skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback).

```
shared:read-slack history --channel C02FQE15YGP --newest 100   # support-bot
shared:read-slack history --channel G017L0Q12FL --newest 100   # data-experiment
```

Merge the message stream by `ts`, dedup by `(name, ts)`, and the **format
to match is `Experiment ended: \`<name>\` [N users] - <result>`** — not
`end experiment <name>` (the latter is the user-typed slash-command shape
in `#support-bot`, which AuraBot then echoes into both channels with the
formal `Experiment ended:` shape).

Filter for `Experiment ended:` and take the most recent N. Capture for
each: experiment name, winner variant, full result notes, end timestamp.

**1c. Notion URL / DB query (enrich mode).** Invoke `shared:retrieve-knowledge` with `path=Learnings/Experiments`:

- **Notion URL / page ID** → fetch that single row (Recipe A). Resolves to one experiment.
- **DB query** (e.g. `Status is blank`, `Hypothesis is empty`) → fetch all matching rows (Recipe B) and iterate.

For each resolved row, extract the **pre-populated context** (Title, Variants, Start/End Date, Experiment Type / App / Platform, Hypothesis, Status / `## Learning`) so we don't re-fetch what bulk import already captured, and capture the Notion `page_id` per row — Step 8 option 4b needs it for the update call.

> See [`references/event-lists.md`](references/event-lists.md) → "Enrich-mode pre-populated row fields (Step 1c)" for what each field is used for and which downstream lookups it short-circuits.

**Short-circuit rule:** in enrich mode, skip `#support-bot` AuraBot create-note lookup entirely (row already has dates). Still run #support-bot end-note lookup (row may not yet have `Status` inferred from it) and #wins analysis search (richer narrative than bulk-import `## Learning`). Mixpanel always runs — the whole point is fresh metric data.

**1d. Confirm status via Aura API (all modes).** Once each experiment name is resolved, hit the Aura API to authoritatively check whether it has ended and which variant shipped — don't rely solely on Slack scraping.

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:analyze-experiment (session ${AINATIVE_SESSION_NAME:-adhoc})" -H "Authorization: Bearer $AURA_API_TOKEN" \
  https://services.aurahealth.io/experiments/<name>
```

Read these fields off the response (full schema in [`Tools/Aura API`](https://www.notion.so/34c75e23af208102b7dcd0d53a5776f7)):

- `endedAt: null` → **check `active` before calling it running.** A null `endedAt` does not mean live: when a variant ships to everyone the server collapses `active` to one letter instead of stamping an end ([PROD-6116](https://linear.app/aura-health/issue/PROD-6116)). More than one **distinct** letter → genuinely **still running**, mark Status `In flight` and treat Mixpanel numbers as interim. One distinct letter → the experiment has **stopped splitting**: do *not* write `In flight` (it is not running, and `sync-experiments`' reconcile flips the row back to `Stopped splitting` next pass — an oscillation that reads as drift). This is the case a person is resolving, so write the terminal verdict you came to write, or leave the row at `Stopped splitting` and say the window could not be established. **Do not infer the verdict from `active`** — a rollout, a variant forced before release, a release still pending and a permanent kill switch all look identical here; get it from `#wins`, `#support-bot`, or the owner. A repeated-letter `active` (`"bb"` — two characters, one distinct arm) is ambiguous and deliberately unresolved repo-wide: flag it rather than pick a reading.
- `endedAt: <ISO timestamp>` → experiment **ended at that time**. Use this as the authoritative end timestamp (overrides any earlier guess from Slack).
- `active` → which variant the app currently ships (`a`, `b`, …, or `null` if no variant is forced). When the experiment has ended, `active` is the *winning* variant. Cross-check against the `#wins` analysis post — disagreements are worth flagging in the readout.
- `notes` / `result` → human-written description and readout the team kept on the experiment record. **Read both before judging how an experiment was run** — they carry intent, which no metric does, and `result` often states *why* it ended. **An early stop with a clear negative is a decision, not a shortfall**: withdrawing something visibly doing harm needs no full sample, and `pricingWebHardPaywall`'s "(expected, hard paywall blocks free browsing)" is a predicted mechanism being sized, not a truncated test. The asymmetry to carry: **stopping early on a negative costs nothing; declaring a win early costs a shipped change and a retired question**, because a `Won` row feeds `product:learn-idea`'s prior-knowledge block, which treats a settled question as answered. Quote them in the final learning where they add context the numbers don't.
If the API call fails (404 = unknown name, 401/403 = token issue, network error), don't block — fall back to Slack-derived status and note the miss in the readout's Sources section.

**Cowork caveat.** Inside a Cowork session, `mcp__workspace__bash` does not inherit `AURA_API_TOKEN` from the host's `~/.claude/settings.json` (see [Known Cowork constraints](../../../../CLAUDE.md#known-cowork-constraints) and [PROD-1305](https://linear.app/aura-health/issue/PROD-1305)) — Step 1d's curl will return `Forbidden`. Treat that as a soft miss: rely on `#support-bot` end-notes and the `#wins` analysis post for status/winner, and note "Aura API unreachable from Cowork sandbox" in the Sources block. The Block Kit edge case (e.g. `voiceModeCC`'s end via Block Kit not ingested by AuraBot) is the known gap this fallback can't cover.

**When to skip 1d:** explicit "I just want Mixpanel numbers" requests, or when the experiment name doesn't follow the production naming convention (one-off / locally-tested experiments that never shipped to the catalog).

### Step 1e — Determine App (Aurie vs Aura)

Using the sources resolved in Steps 1a–1d, pin the app for each experiment. This drives Mixpanel event selection (Step 5), brand-voice priors (Step 0/7), and Slack search scope (Step 2).

**Priority order (first match wins):**

1. **Notion row `App` property** (enrich mode) — if the row has `App: Aurie` or `App: Aura` set, that is authoritative. The bulk-import derives `App` from `targetApp` and is always accurate.
2. **Aura API `targetApp` field** (Step 1d result) — `targetApp == "ai"` → **Aurie**; `targetApp == null` → **Aura**; any other value → log a warning, default to **Aura** and flag for human review.
3. **Fallback** — if neither source is available (API miss + no Notion row), default to **Aurie** (most recent experiments are Aurie) and note the assumption in the readout.

The resolved app then selects the event set and voice prior: `targetApp == "ai"` → Aurie (Aurie event set, `Products/Aurie`); `null`/unset → Aura (Aura event set, `Products/Aura`). Capture the resolved `app` value and carry it through all subsequent steps.

### Step 1f — Sweep mode pre-classification (sweep mode only)

In sweep mode, pre-classify every row from cheap signals first and run the full pipeline **only** on `RUNNING_WITH_DATA`. A row past 14 days that is still splitting with SUL < 20 is `REGISTERED_STILL_ASSIGNING`: **report it and write nothing** — closing it says a question is settled when it is not, and analysing it burns the pipeline on zero users.

Bucket definitions, the signals that decide each, and the boilerplate close-outs:
[`references/sweep-mode.md`](references/sweep-mode.md).

### Step 2 — Broader Slack search for context

Beyond the AuraBot create/end messages in `#support-bot`, search Slack for any
other discussion of the experiment — design debates, QA findings, interim
readouts, dashboard screenshots, or decisions captured in other channels
(e.g. `#ai-product`, `#growth`, `#data`, `#ai-team`, `#wins`).

Run the workspace search by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)** with its `search` (`query="<experimentName>"`). Workspace search runs `search.messages`, which needs a `SLACK_USER_TOKEN`; if the slack skill reports search isn't available, note the miss and lean on the Step 1b channel reads. That skill owns the connection — don't call Slack directly here.

Also search human-readable variants — for `hardPaywall`, try `"hard paywall"`
and `"hardPaywall"`. Note `search` is **message-only** (`search.messages`) — file
search (`content_types=files`) isn't exposed, so search dashboard/spec file *names*
as keywords in message text, or skip (file hits are optional context).

**Prioritize `#wins` analyses.** Search specifically for `"<name> — analysis"` or `"WIN — <name>"` — the richest prose learnings typically live in `#wins`, not in `#support-bot` thread replies. The `#wins` post usually has a full Mixpanel variant table, significance, and a 2–3 sentence framing that's good raw material for `## Learning`.

**Aura-app experiments.** Analysis culture for Aura experiments skews toward `#data` and `#growth` rather than `#wins`. When `app == Aura`, also search those channels explicitly — again by **invoking `shared:post-to-slack`**'s `search`, scoped per channel: `query="<experimentName> in:#data"`, then `query="<experimentName> in:#growth"`.

`#wins` is still worth checking (cross-team experiments get posted there too), but don't treat a `#wins` miss as definitive for Aura experiments.

For each hit that looks like real context (a QA finding, a design decision, an
interim metric, a stakeholder concern, a `#wins` analysis), read the full thread by
**invoking [`shared:read-slack`](../../../shared/skills/read-slack/SKILL.md)** (its `replies`
mode) for that hit's `channel_id` + `ts`. Summarize these findings alongside the AuraBot end-experiment
note in Step 7.

Skip trivial hits like "+1" or emoji reactions. Flag contradictions between
the AuraBot end-note and other Slack discussion (e.g. a QA thread found a bug
that might invalidate the result; or a revised analysis in `#wins` contradicts
a small-N call in `#support-bot`). **Capture every verdict you find, but do not
treat any of them as authoritative** — the team's Slack call is one input, not
the conclusion. Step 6 produces our own independent verdict from Mixpanel; Step
6.5 reconciles it with Slack.

### Step 3 — Linear context

For each experiment name, find the matching ticket(s):

```
list_issues(query="<experimentName>", limit=5)
```

Match on titles like `ab:<name>`, `Experiment <name>`, `<Name> Eng`. Use
`get_issue` for the best match to pull the full description — hypothesis, what
was built, variant definitions, related tickets.

Keep the top Linear ticket **id** per experiment, to name inline in `## Learning`. Do **not**
put the card URL in `# Sources` — `Guide` → Authoring rules bans Linear card links in AI
Knowledge content, `# Sources` included.

**Enrich-mode short-circuit.** If the DB row already has a non-blank `Hypothesis` property AND `## What was tested` in the page body, the Linear ticket lookup is still worth doing so the ticket id can be named inline in `## Learning` (never linked in `# Sources`), but skip parsing the ticket for hypothesis — the row already has it. If `Hypothesis` is blank, pull it from the Linear description as usual.

### Step 4 — Find the experiment create date

In the same `#support-bot` / `#data-experiment` read (Step 1b — read both,
they mirror), find the matching `Experiment created: \`<name>\`` AuraBot
message to capture the start date. Fall back to the Linear `createdAt` if
the create message is older than the newest-100 read (Step 1b) — bump `--newest`
higher for that channel if you need to reach further back.

**Enrich-mode short-circuit.** Skip this step entirely if the DB row already has `Start Date` populated. The row's dates are authoritative.

### Step 5 — Mixpanel data (per experiment)

**Both Aurie and Aura experiments live in the same Mixpanel project (`963821` — "Project Aura", workspace `30768`).** The only difference is the event names. Use the resolved `app` from Step 1e to pick the right event set.

#### Mixpanel access ladder

Prefer the Mixpanel MCP tools (`Run-Query`, `Get-Events`); they aren't always reachable (absent in Cowork sandboxes, occasionally unloaded), so fall through to the REST `/api/2.0/segmentation` API (Tier 2), and to JQL (Tier 3) only when segmentation can't express the query.

> See [`references/event-lists.md`](references/event-lists.md) → "Mixpanel access ladder" for the tier detail and rationale, and "Tier-2 Mixpanel REST segmentation" for the worked `per_variant_unique` template, the `AURIE_EVENTS` / `AURA_EVENTS` lists, the `undefined`-bucket note, and the rate-limit / batching guidance.

#### Per-app event config & query template

Pick the event set from the resolved `app` (Step 1e), then run one `Run-Query` per experiment. The `Experiment: <experimentName>` user property (variants `a` / `b`, or more for multi-arm) is the same for both apps.

> See [`references/event-lists.md`](references/event-lists.md) → "Per-app event config" and "Query template" for the metric-alias mapping table, the full insights `Run-Query` YAML, and the Aurie-web-only / Aura-infra caveats.

#### Population: `N` is a denominator, not the population

Step 6's `N` is the `SUL` unique count from the query above, so it sees only assigned users who
signed in **during the window** — every rate divides by it, and reporting it *as* the population
hides how much of the population those rates never covered. So also run a **profile**-based count
(`/api/2.0/engage`), and carry both per variant: `assigned` (the population) and `N` (unchanged).

Report both and the gap. `assigned − N` is not one thing — it mixes internal accounts,
assignments too recent for events to have landed, and users active on unmetered events. An
unexplained gap is itself a finding. **For an ended experiment, check the tense before reporting any gap** — it keeps stamping users into the surviving variant, so the engage count grows after it is over (`aurieEnvironment2`: 1,623 today against a real 279). Prefer the end-note's contemporaneous `[N users]`; `assigned` for an ended experiment is historical and must be stable ([`references/event-lists.md`](references/event-lists.md) → "Ever is the wrong tense").

> Recipe, why a union across metered events is *not* the right denominator, the
> `assigned`/`N`/`exposed` term table, the UID pagination rule, and the worked gap:
> [`references/event-lists.md`](references/event-lists.md) → "Assigned vs `N`".

### Step 5.5 — Detect over-stamping (super-property timing check)

Verify the experiment's `Experiment: <name>` property attaches **at exposure** (the moment the experimental surface renders), not earlier in the funnel. If the SDK registers it as a super-property at app/sign-up start, every subsequent event from that user carries the property attached — including from users who never reached the experimental surface. The result: the raw super-property cut from Step 5 is diluted by ineligible users, and the reported lift can diverge wildly from a properly-defined exposure cohort.

(See [`references/examples.md`](references/examples.md) → "Over-stamping concrete example (Step 5.5)" for the `onboardingPromptResults` case where a raw flat cut hid a +44% exposure-cohort lift.)

**Always run this step.** Cheap, catches a class of methodology bugs that compounds across experiments. Skip only for infra/backend experiments where Mixpanel user metrics aren't the primary signal anyway. **Step 5.7 is its other half** (exposure vs outcome); run both.

#### The crucial filter primitive: event-level, not user-level

Filter with `resource: event` (not `resource: user`) on `Experiment: <name>`. **This step is the one place event-level is correct** — Steps 5 and 6 use user-level for every headline count, and the Tier-2 helper takes `level=` for exactly this reason ([`references/event-lists.md`](references/event-lists.md) → "Two breakdown levels"). `resource: user` matches any event from a user who *eventually* had the property — including events that fired *before* the SDK attached it; `resource: event` only counts events whose payload physically contained the property at firing time. The two diverge by design — and the divergence is exactly what reveals over-stamping. See [`references/output-format.md`](references/output-format.md) → "The crucial filter primitive" for the exact filter snippet.

#### a) Exposure-event check (preferred)

If a dedicated event for the experimental surface exists (e.g. `Onboarding Results Screen Viewed`, `<Surface> Seen`, `<Surface> Rendered` — search the lexicon via `Get-Events` with `query="<Surface>"`), this is a one-query check: an insights query metering the exposure event and the earliest pre-exposure event, both event-level filtered on the experiment property.

> See [`references/output-format.md`](references/output-format.md) → "a) Exposure-event check" for the full `Run-Query` YAML.

If `<earliest pre-exposure event>` count is much greater than zero, the property is attaching before exposure → over-stamping. Compute the **over-stamping factor** = (events with property at any pre-exposure point) / (exposure-event count).

#### b) Funnel walk (when no dedicated exposure event exists)

Often the case — the lexicon for `onboardingPromptResults` has no dedicated exposure event. Walk the funnel: list 4–6 candidate events spanning the user journey from app open to deep-funnel, and run an insights query with each as a metric, all event-level filtered. The first event with a non-trivial count (not 0–10 stragglers) is where the SDK starts attaching the property.

> See [`references/examples.md`](references/examples.md) → "Over-stamping funnel worked example" for the Aurie-onboarding candidate list and the `onboardingPromptResults` output.

#### Reporting

Always include an **Exposure quality** block in the per-experiment readout (Step 7), beside the `## Result` block. When over-stamping is detected, also add a `⚠ Methodology — over-stamping` block (separate from the Slack disagreement block in Step 6.5).

> See [`references/output-format.md`](references/output-format.md) → "Over-stamping reporting blocks" for the verbatim `Exposure quality` and `⚠ Methodology — over-stamping` block formats.

The over-stamping factor also gets one sentence in `## Learning` so future readers know how much to trust the raw super-property cut. Status is still set from Step 6's verdict — over-stamping is a methodology caveat, not a verdict reversal — but the verdict text should explicitly note it (e.g. *"Won on exposure cohort; raw cut is flat due to over-stamping factor ≈10×; reconcile before sizing follow-ups"*).

The aggregate event-level filter from (a)/(b) answers the population-scale question directly; to confirm by following a specific user, see [`references/output-format.md`](references/output-format.md) → "Following individual users (Step 5.5, when needed)".

### Step 5.6 — Screen out internal / comped accounts

Internal, staff and comped accounts are excluded from the population the rates are computed on —
few, but individually heavy (one carried 20 voice sessions in a 150-user arm).

Step 5 returns **aggregate** per-variant uniques, which cannot have individual users subtracted. So
screening needs a **per-user pass** — a JQL query (Tier 3) returning one row per user with their
`UserID`, variant, and the event flags Step 6 needs — and the rates are then computed from the rows
that survive rather than from the Step 5 aggregates.

Three nets are unioned: Mixpanel exclusion cohorts, a per-subscription code scan, and the
account's email domain + role. The third catches staff the experiment service assigns anyway —
it refuses only bot roles, so a staffer holding no redemption code is bucketed and stamped like
a real user, invisible to the other two.

1. Run the per-user JQL query, keyed on the **`UserID`** profile property (never `distinct_id`).
2. Screen those UIDs and drop everything returned in `excluded`:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/analyze-experiment/scripts/screen_internal.py \
  --uids-file /tmp/experiment-uids.txt --json
```

3. Recompute Step 6 from the screened rows and reconcile against the Step 5 aggregates.

**When the per-user pass is skipped** (aggregate-only run, or JQL unavailable), the exclusion has
**not** been applied — say so in the readout rather than implying a screened population.

Carry into the readout (Step 7): the **per-arm drop count**, the **per-net split**, each net's
state, and any **`unscreened`** UIDs — failed lookups, so neither confirmed internal nor clean. When
the screener reports `complete: false`, say the exclusion was incomplete rather than implying a
screened population. A **0/0 drop count is suspicious, not reassuring**.

> The JQL template, reconciliation tolerance, cohort ids, the code taxonomy and its formatting
> traps, the staff domain/role rule and the service gaps behind it, the token that actually works,
> and why the user-level `code` field is unreliable:
> [`references/internal-exclusion.md`](references/internal-exclusion.md).

### Step 5.7 — Locate the exposure moment, then split metrics pre- vs post-exposure

**Always run this step.** Step 5.5 checks the property stamp against exposure; this checks exposure against the **outcome** — the half that produces a wrong *result* rather than a caveat.

**Find the assignment call site in code**, not the experiment config and not where the treatment renders; assignment often sits far deeper in the funnel than the surface it controls. Then label every metric: **pre-exposure** (can complete before assignment) is a covariate, reported as a balance check and never as an outcome; **post-exposure** is the only causal metric.

Four consequences for Step 6: a metric a user can no longer move is not an outcome (someone who converted before assignment belongs in the denominator of retention, not conversion); the denominator is the **eligible cohort**, stated inline, not the arm; the noise floor is computed on the post-exposure headline, since the all-time rate's arm gaps measure the randomisation draw; and if exposure sits after the primary metric the verdict is **Inconclusive by design**, not underpowered — more users never fix a metric the treatment could not have influenced, so recommend moving exposure earlier instead.

> The `aiLiveKitIntegration4` case this prevents — a paid "win" at p=0.012 that was a pre-treatment imbalance, retracted the same day — plus the `Exposure timing` and `⚠ Methodology — outcome precedes exposure` reporting blocks: [`references/examples.md`](references/examples.md) → "Outcome precedes exposure (Step 5.7)".

### Step 5.8 — Pin the unit of analysis, and prefer backend truth for cross-arm comparisons

**Define the unit once, in writing, before any arm comparison, and restate it in the output.** "Conversation", "session" and "active user" each have several defensible readings, and a comparison that switches between them reports a definition artifact as a finding. Fix the merge rule, the content floor and the denominator up front — Aurie's canonical voice definition, and the depth tiers to report beside it, are in [`references/conversation-unit.md`](references/conversation-unit.md).

**Client instrumentation is variant-asymmetric until proven otherwise; a backend record written by one shared code path is not.** Before reporting any client-measured arm gap, read the emitter's call site and confirm both arms reach it under the same guard conditions, then confirm the gap against a backend source — the `llmCall` ledger for conversation turns. Prefer a metric defined by the **absence of a success signal** over one defined by the presence of a failure signal: no classifier runs on an absence, so a classification asymmetry cannot manufacture it. On `aiLiveKitIntegration4` two findings died to this and the two that survived were both absence- or backend-defined. Both checks, the ledger reader, and the Mixpanel→UTC timezone shift the join needs: [`references/conversation-unit.md`](references/conversation-unit.md) → "Backend truth".

### Step 6 — Compute rates and lifts

Compute every rate below on the **screened** population from Step 5.6.

**State which denominator each rate uses.** Step 5 produces two population numbers — `assigned`
(profiles, from engage) and `N` (signed in during the window, from insights) — and Step 5.5
produces a third, `exposed` (reached the experimental surface). A rate is meaningless without
naming which one is underneath it. The rates below all use `N`. The rates below use the `SUL`-based `N` by
convention; report `assigned` alongside it so the reader can see how much of the population that
`N` covers. A rate whose denominator drifts between experiments is not comparable across them.

For each experiment, from the returned counts per variant (using the per-app event aliases from Step 5):

- `N` = `SUL` event unique users (sign-up/login)
- `Trial Start Rate` = `Trial Start` event / `N`
- `Paid Rate` = `Paid` event / `N`
- `Session Rate` = merged `Session` conversations / `N` — **Aurie only**; omit for Aura
  experiments. The event covers text chats too, so filter `Current Conversation Mode ==
  "Voice Mode"` for a voice metric, and merge restarts before counting (below).
- `Relative lift` — each treatment vs. control (`b` by default)
- Flag any variant with `N < 50` or paid `N < 10` as underpowered
- **A user-proportion rate above 100% is a denominator bug, never a finding** — `Trial Start Rate` and `Paid Rate` only (not `Session Rate`, which is conversations per user and legitimately exceeds 1 on Aurie). Stop and check the Mixpanel breakdown level: at event level every user assigned after the event fired lands in `undefined`, `N` collapses, and the rates divide by the remainder — `hideIncognitoButton` returned 3,300% that way. See [`references/event-lists.md`](references/event-lists.md) → "The `undefined` bucket is a reading of the breakdown level"
- Flag CSS > SUL (Aura or Aurie) as a date-range artifact — returning users who signed up before the experiment window inflate session counts

**Count conversations, not session-start events — and state which definition you used.** A voice conversation is one client voice start with restarts inside **5 minutes** merged, whose window holds at least **3 backend coach turns**. Merging alone removes ~24% of counted starts; the turn floor removes husks the client counts as conversations. Report the merge's effect (`raw starts -> conversations, N% removed`) and the husk rate beside the count. The merge runs on the Step 5.6 per-user JQL pass (it needs per-user timestamps), never on aggregate segmentation, and never keyed on the agent's `conversationId`. Both halves, the sensitivity sweep, and the backend join: [`references/conversation-unit.md`](references/conversation-unit.md).

**Make our own call on the winner — do not defer to the Slack/AuraBot verdict.** The team's verdict is captured separately in Step 6.5; here we form an independent view from the Mixpanel numbers.

Pick the verdict from one of:

- **Won** — treatment shows a meaningful lift on the primary metric AND `N` is sufficient to support the call (above the underpowered threshold) AND no countervailing guardrail signal (e.g. session rate or paid rate didn't collapse).
- **Lost** — treatment underperforms control on the primary metric beyond noise, OR it lifts the primary but tanks a guardrail metric (e.g. trial start ↑ but engagement ↓ on Aurie).
- **Inconclusive** — `N` is too small to call, rates are within noise, or the result is mixed in a way no single arm beats control.
- **Turned off** — experiment was killed mid-flight before powering up; usually evident from a very short create-to-end window in `#support-bot`.

Primary metric by app:

- **Aurie** — engagement (`AI Conversation Session Started`) is primary, `Trial Start` and `Paid` are secondary. Aurie's bet is "did users actually talk to Aurie?" — a paywall lift that costs engagement is not a clean win.
- **Aura** — `Trial Start Rate` is primary, `Subscription Paid` is the long-tail confirmation, content-consumption / D7-return is the guardrail.

Heuristic for "meaningful lift" when no formal stat-sig is available: **>5% relative lift with `N ≥ 200` per arm**, with no countervailing guardrail signal. Tighten the threshold for higher-stakes arms; loosen if `N` is well above 1000 per arm.

Record three things separately so Step 6.5 can compare them:

- `our_verdict` — our independent call from the numbers above.
- `slack_verdict` — what the AuraBot end-note in `#support-bot` reported (winner variant or "no winner / inconclusive").
- `wins_verdict` — what the `#wins` (or `#data` / `#growth` for Aura) analysis post concluded, if one exists.

### Step 6.5 — Cross-check against Slack analysis

Compare `our_verdict` against `slack_verdict` and `wins_verdict`. Three cases:

1. **Agreement.** Our call matches Slack's. Cite both in the readout's Sources block and proceed to Step 7 normally.
2. **No strong Slack call.** AuraBot end-note is neutral / no `#wins` post exists. Use our call; cite the AuraBot end-note as context.
3. **Disagreement.** Our verdict differs from Slack's. **Do not silently override either side.** Add a `⚠ Disagreement` block to the readout for that experiment (Slack verdict + link, our verdict, one-sentence "why we differ"). See [`references/output-format.md`](references/output-format.md) → "Disagreement block (Step 6.5)" for the verbatim format.

The row's `Status`, `## Result` table, and `## Learning` text always reflect **our** analysis. When there's a disagreement, the `## Learning` body must explicitly note the divergence and our reasoning so the audit trail is intact. Step 8 then asks the user how they want to handle the disagreement (keep ours, keep Slack's, or reply in Slack to challenge).

### Step 7 — Synthesize learnings and follow-up experiments

Produce for each experiment five parts: **What was tested** (one sentence from
Linear), **Result** (small code-fenced ASCII metrics table), **Exposure
quality** (the block from Step 5.5; add the `⚠ Methodology — over-stamping`
block when detected), **Learning** (2–3 sentences grounded in **our** Step 6
analysis, using the Step 0 brand-voice priors — Aurie = engagement/conversation
framing, Aura = content-consumption/retention framing — and adding a sentence
each for any Step 6.5 disagreement and any Step 5.5 over-stamping factor), and
**Follow-up experiments** (2–4 concrete ideas, each with a one-line hypothesis,
the variant(s) to build, and the primary metric it moves).

When analyzing more than one experiment, add two batch sections: **Cross-cutting
learnings** (themes across the batch) and **Portfolio follow-up ideas** (3–5
higher-level experiments the batch suggests).

> See [`references/output-format.md`](references/output-format.md) → "Step 7 readout template" for the full per-field guidance, the brand-voice detail, the "prefer ideas that…" follow-up heuristics with worked examples, and the cross-cutting / portfolio example lists.

### Step 8 — Ask what to do with the report

Offer the routing options and wait for an explicit choice — every destination
writes somewhere durable, so none is a default.

The four options, the create-vs-update-in-place rule, and the per-destination
requirements: [`references/routing.md`](references/routing.md).

## Output format

Default to posting a structured response in chat: a header (experiments + date
range), one section per experiment (Linear link, variant definition, metrics
table, learning, 2–4 follow-up ideas), plus cross-cutting learnings and
portfolio follow-up sections when N > 1. After presenting, always ask the user
whether to route it anywhere (see Step 8), and report back URLs for any Slack
posts, Linear tickets, or AI Knowledge pages touched.

> See [`references/output-format.md`](references/output-format.md) → "Chat output format" for the itemised structure.

## Key IDs and references

> See [`references/event-lists.md`](references/event-lists.md) → "Key IDs and references" for the canonical Mixpanel project / dashboard / REST IDs, Slack channel IDs, per-app event names, app-detection rule, and the user-property / variant convention.

## Notes and gotchas

> See [`references/event-lists.md`](references/event-lists.md) → "Notes and gotchas" for the variant-convention, multi-variant, small/zero-arm, super-property-gap, small-N, web-experiment, and date-range caveats.

## Example invocations

> See [`references/examples.md`](references/examples.md) → "Example invocations" for end-to-end walkthroughs of create mode, enrich mode (single row + batch DB query), and sweep mode.
