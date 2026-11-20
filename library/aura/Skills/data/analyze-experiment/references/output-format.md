# Output-format & message templates

Reference material for the `data:analyze-experiment` skill: the over-stamping query templates and reporting blocks (Step 5.5), the disagreement block (Step 6.5), and the user-facing routing prompts + `record-knowledge` update payload (Step 8). The skill's workflow steps point here for the exact verbatim shapes.

## Over-stamping query templates (Step 5.5)

### The crucial filter primitive: event-level, not user-level

```yaml
filters:
  - { type: string, propertyName: "Experiment: <name>", propertyType: string, resource: event, operator: "is set" }
```

`resource: user` matches any event from a user who *eventually* had the property — including events that fired *before* the SDK attached it. `resource: event` only counts events whose payload physically contained the property at firing time. The two diverge by design — and the divergence is exactly what reveals over-stamping.

### a) Exposure-event check (preferred)

If a dedicated event for the experimental surface exists (e.g. `Onboarding Results Screen Viewed`, `<Surface> Seen`, `<Surface> Rendered` — search the lexicon via `Get-Events` with `query="<Surface>"`), this is a one-query check:

```yaml
project_id: 963821
report_type: insights
report:
  metrics:
    - eventName: "<exposure event>"
      measurement: { type: basic, math: unique }
      filters:
        - { type: string, propertyName: "Experiment: <name>", propertyType: string, resource: event, operator: "is set" }
    - eventName: "<earliest pre-exposure event, e.g. AI Sign Up Start>"
      measurement: { type: basic, math: unique }
      filters:
        - { type: string, propertyName: "Experiment: <name>", propertyType: string, resource: event, operator: "is set" }
  dateRange: { type: absolute, from: "<createDate>", to: "<endDate>" }
```

If `<earliest pre-exposure event>` count is much greater than zero, the property is attaching before exposure → over-stamping. Compute the **over-stamping factor** = (events with property at any pre-exposure point) / (exposure-event count).

### b) Funnel walk (when no dedicated exposure event exists)

Often the case — the lexicon for `onboardingPromptResults` has no dedicated exposure event. Walk the funnel: list 4–6 candidate events spanning the user journey from app open to deep-funnel, and run an insights query with each as a metric, all event-level filtered. The first event with a non-trivial count (not 0–10 stragglers) is where the SDK starts attaching the property.

> See [`references/examples.md`](examples.md) → "Over-stamping funnel worked example" for the `onboardingPromptResults` candidate list and output.

## Over-stamping reporting blocks (Step 5.5)

Always include an **Exposure quality** block in the per-experiment readout (Step 7), beside the `## Result` block:

```
Exposure quality
- Property first attaches at: <event> (~<N> events with property attached)
- Exposure event:             <event name, or "no dedicated event in lexicon">
- Over-stamping factor:       <pre-exposure stamped N> / <exposed N> ≈ <ratio>x
- Verdict:                    <clean | over-stamped | unverifiable>
```

## Internal-account screening block

Always include a **Screening** block beside `Exposure quality`, so the Step 5.6 exclusion is
visible in the readout rather than an invisible upstream filter:

```
Screening (internal/comped accounts excluded)
- Dropped:      a=<N>  b=<N>   (of <checked> screened)
- By net:       Mixpanel cohort <N> · subscription code <N> · both <N>
- Net state:    cohort <ok | stale cache | failed> · code <ok | failed>
- Unscreened:   <N> (subscription lookup failed — neither confirmed internal nor clean)
```

When the screener reports `complete: false` — any net degraded, or any `unscreened` UIDs — the
readout must state that the exclusion was **incomplete**, not imply a fully screened population.

Name accounts by UID **prefix** only (8 chars) and email **domain** only, per `Compliance/Privacy`.

A `Dropped: a=0 b=0` line is suspicious rather than reassuring — when it appears, state that the
cache freshness and `UserID` coverage were checked, or flag the readout as unverified on this axis.

When over-stamping is detected, also add a `⚠ Methodology — over-stamping` block (separate from the Slack disagreement block in Step 6.5):

```
⚠ Methodology — over-stamping
- Property registered at: <where> (~<N> events stamped pre-exposure)
- Exposure:               <where the experimental surface actually renders, ~<N> users>
- Over-stamping factor:   ~<ratio>x
- Recommended fix:        register `Experiment: <name>` as a super-property at exposure
                          (when the experimental surface renders), or fire a dedicated
                          `<Surface> Viewed` event and use it as the cohort denominator.
```

The over-stamping factor also gets one sentence in `## Learning` so future readers know how much to trust the raw super-property cut. Status is still set from Step 6's verdict — over-stamping is a methodology caveat, not a verdict reversal — but the verdict text should explicitly note it (e.g. *"Won on exposure cohort; raw cut is flat due to over-stamping factor ≈10×; reconcile before sizing follow-ups"*).

## Step 7 readout template (per-experiment + batch sections)

Produce for each experiment:

- **What was tested** — one sentence (from Linear).
- **Result** — a small metrics block (code-fenced ASCII table, Slack-friendly).
- **Exposure quality** — always include the block defined in Step 5.5 (where the
  property first attaches, exposure event, over-stamping factor, verdict). When
  over-stamping is detected, also include the `⚠ Methodology — over-stamping`
  block per Step 5.5.
- **Screening** — always include the internal-account block defined above (per-arm
  drop count, per-net split, unscreened count) from Step 5.6.
- **Learning** — 2–3 sentences of interpretation, grounded in **our** Step 6
  analysis (Mixpanel numbers, computed lifts, guardrail checks). When Step 6.5
  surfaced a disagreement with Slack, add 1 sentence stating the divergence and
  our reasoning ("Slack called this a Win on a 4% trial-start lift; we read it
  as Inconclusive because the CSS guardrail collapsed and `N=180/arm` is below
  our threshold"). When Step 5.5 detected over-stamping, add 1 sentence calling
  out the over-stamping factor and what it means for sizing the next iteration
  ("over-stamping factor ≈10×; raw super-property cut is dilution-dominated, the
  exposure-cohort lift is the real signal, and `<follow-up experiment>` should
  not be sized off the raw cut"). Use brand-voice priors from Step 0:
  - **Aurie:** prefer engagement/conversation framing ("did users actually talk to Aurie?") over pure trial-rate framing for paywall experiments.
  - **Aura:** prefer content-consumption and retention framing ("did users complete a meditation / return within 7 days?") — Aura is a library-and-coaching platform, not a conversation product. Avoid projecting conversation-session language onto Aura results.
- **Follow-up experiments** — 2–4 concrete ideas the result suggests. Each
  idea must include: a one-line hypothesis, the variant(s) to build, and the
  primary metric it would move. Prefer ideas that:
    - Probe a tension the result exposed (e.g. hardPaywall lifted trials but
      tanked engagement → test a softer hard-paywall that appears only after
      first completed session)
    - Isolate a confound the current test couldn't resolve (e.g. archetype
      DAU lift: was it the archetype or the copy? → test archetype-with-old-copy)
    - Extend a winner to an adjacent surface (e.g. tutorial won → test
      tutorial variants for voice-first users)
    - Reverse a loser with a smaller change (e.g. two-step paywall lost → test
      a single-step paywall with the two-step's better copy)

When analyzing more than one experiment, add two final sections:

**Cross-cutting learnings** — themes across the batch:
- Engagement vs. trial-start trade-offs
- Friction that buys something for the user vs. pure tax
- Cheap framing experiments vs. expensive structural ones
- Experiment hygiene issues observed (a/b inversion, missing super-properties,
  traffic skew)
- Statistical power warnings

**Portfolio follow-up ideas** — 3–5 higher-level experiments the *batch*
suggests (beyond the per-experiment follow-ups). Examples: "paywall changes
should be judged on paid retention, not trial start — propose a 4-week
retention-gated readout template"; "every new onboarding experiment should
include a D7 return cohort as a guardrail."

## Chat output format

Default to posting a structured response in chat, with:

1. A header listing the experiments analyzed and the date range.
2. One section per experiment: Linear link, variant definition, metrics table,
   learning, and 2–4 follow-up experiment ideas.
3. A cross-cutting learnings section if N > 1.
4. A portfolio follow-up ideas section if N > 1 (3–5 batch-level experiments).

After presenting the report, always ask the user if they want to route it
anywhere (see Step 8). Report back URLs for any Slack posts, Linear tickets
created, or AI Knowledge pages updated.

## Following individual users (Step 5.5, when needed)

The aggregate event-level filter from the exposure-event check / funnel walk tells you the same thing a per-user walk would, at population scale. If you still want to confirm by following a specific user:

- **Mixpanel UI** — Users view, filter `Experiment: <name> is set`, click into a user's activity feed; the first event with the property in its payload is where the SDK first registered it.
- **`Get-User-Replays-Data`** — works only when you already have a `distinct_id` (e.g. from a Slack thread referencing a specific user). Useful when a stakeholder asks "what did *this* person experience?" — not for systematic checks.
- **MCP toolkit limitation today.** There's no raw per-user event listing exposed via the Mixpanel MCP. The aggregate event-level approach is the practical default for this skill, and it answers the same question: "where in the funnel does the property first attach?"

## Slack posting details (Step 8, options 2 & 3)

**Footer** (options 2 and 3). Pass `--footer data:analyze-experiment` to
`shared:post-to-slack` so every post ends with the canonical attribution footer per
`Tools/Slack` → Post format: `` _Generated by `data:analyze-experiment:<version>` · <YYYY-MM-DD>_ `` (the helper auto-resolves `:<version>` from `metadata.version` — don't hand-type it).
For an optional trailing source link to the analysed `Learnings/Experiments` row,
pass it via `--footer-extra`. The slack skill owns the connection — don't call Slack
directly here.

**Slack formatting rules** for options 2 and 3: no markdown tables (they fail
validation — use code blocks for metric tables), use `*bold*`, `_italic_`,
backticks for event and property names. Always set `thread_ts` when replying.

## Disagreement block (Step 6.5)

When our verdict differs from Slack's, add a `⚠ Disagreement` block to the readout for that experiment:

```
⚠ Our analysis disagrees with Slack
- Slack verdict: <quote from #wins post / AuraBot end-note, with link>
- Our verdict:   <Won / Lost / Inconclusive>
- Why we differ: <one sentence — e.g. "lift is within noise once we exclude returning users"
                                    / "guardrail metric (CSS) collapsed by 12%, primary win is hollow"
                                    / "N=37 in the treatment arm — too small to call a winner">
```

## Step 8 disagreement-resolution prompt

If Step 6.5 surfaced one or more `⚠ Disagreement` blocks, ask the user how to handle each before routing the readout. Offer:

> Our analysis disagrees with Slack on `<experimentName>`:
>
> - Slack verdict: `<their call>`
> - Our verdict: `<our call>` — `<why we differ>`
>
> How would you like to handle it?
>
> 1. **Keep our analysis.** File our verdict to `Learnings/Experiments`. The
>    `## Learning` section documents the divergence and our reasoning so the
>    audit trail is intact. Slack stays untouched.
> 2. **Keep the Slack analysis.** Override our call with Slack's verdict for
>    `Status` and the headline of `## Learning`, but record our analysis as a
>    "Minority view" footnote at the bottom of the page body — we don't erase
>    our reasoning, just defer to the team's call.
> 3. **Reply in Slack with questions / critique.** Draft a threaded reply
>    under the original `#wins` post (preferred) or the `#support-bot` end-note,
>    raising the specific points where we disagree (e.g. "the win seems driven
>    by returning users — were they excluded from variant assignment?", or
>    "CSS dropped 12% in the treatment arm — is the team comfortable trading
>    engagement for trials here?"). Draft it, show the user for approval, then
>    post the threaded reply by **invoking `shared:post-to-slack`** (`thread_ts` =
>    the original post's ts).
>
> Pick any combination — e.g. "1 + 3" files our analysis AND posts a question
> in Slack.

## Step 8 option 4b — `record-knowledge` update payload

Invoke `shared:record-knowledge` with `action: update`, passing the existing `page_id` resolved in Step 1c. Payload shape:

```yaml
action: update
path: Learnings/Experiments
page_id: <from Step 1c>
fields:
  Status: Won|Lost|Inconclusive|Turned off    # inferred from #support-bot end-note; see experiments.md heuristics
  Hypothesis: "..."                           # only if the row's was blank
body_replace_sections:
  "## Result": |
    <Mixpanel variant table + significance, from Step 6>
  "## Learning": |
    <2–3 sentences from Step 7, prefer #wins analysis prose if available>
  "## Follow-up experiments": |
    <2–4 ideas from Step 7>
body_append_sources:
  - { label: "Mixpanel — Aurie Experiments", url: "..." }
  - { label: "Slack — #wins analysis", url: "..." }  # if found in Step 2
```

Per [record-knowledge Recipe C](../../../../shared/skills/record-knowledge/SKILL.md):
- `## What was tested` is never overwritten — it preserves the source-sheet framing from bulk import
- Sources append-only; dedup by URL so re-runs don't produce duplicates

## Step 8 routing options — full mechanics

After resolving any disagreements, ask what they'd like to do with the
report. Offer these options (multiple can be picked):

1. **Nothing — keep it in chat.** Default if the user doesn't respond.
2. **Post to `#ai-product`** (`C04R4L67676`) as a parent `[experiment][analysis]`
   message with threaded replies per experiment + final summary reply covering
   cross-cutting learnings and portfolio follow-ups. Post the parent by **invoking
   `shared:post-to-slack`** (channel `C04R4L67676`; ask it for the returned `ts`),
   then post each per-experiment reply and the final summary reply by invoking it
   again with `thread_ts` = that parent ts.
3. **Reply in `#support-bot` or `#data-experiment`** to the original
   `Experiment ended: \`<name>\`` AuraBot message for each experiment,
   threading the per-experiment section (learning + follow-up ideas) under
   the corresponding end-note. Either channel works since they mirror —
   prefer the one with more active human engagement (`#data-experiment`
   tends to attract more analysis discussion). Post each by **invoking
   `shared:post-to-slack`** with `thread_ts` = the `message_ts` captured in
   Step 1. Keep each reply self-contained. Pass `--footer data:analyze-experiment`
   (and optional `--footer-extra` source link) for the canonical attribution footer
   — see "Slack posting details (Step 8, options 2 & 3)" above.
4. **File learnings to `Learnings/Experiments`** *(create mode default)* — invoke `shared:record-knowledge`
   once per experiment to create a new row in `Learnings/Experiments` with the
   [Experiments schema](../../../../shared/skills/record-knowledge/references/schemas/experiments.md) fields:
   `Title`, `App`, `Platform`, `Experiment Type`, `Status`, `Start Date`, `End Date`,
   `Hypothesis`, `Variants`, plus page body sections
   `## What was tested`, `## Result`, `## Learning`, `## Follow-up experiments`, and
   a `# Sources` block. Let `record-knowledge` handle validation and the `# Sources` section.

   **Title dedup.** Before creating, check whether a row with the same `Title` already
   exists in `Learnings/Experiments` (via `retrieve-knowledge`). If it does, refuse
   to create a duplicate — switch to option 4b below. This happens when the user
   ran the skill in create mode but the row was already bulk-imported.

4b. **Update existing `Learnings/Experiments` row** *(enrich mode default)* — invoke
   `shared:record-knowledge` with `action: update`, passing the existing `page_id`
   resolved in Step 1c. The payload sets `Status` (and `Hypothesis` only if blank),
   `body_replace_sections` for `## Result` / `## Learning` / `## Follow-up experiments`,
   and `body_append_sources` for Mixpanel / #wins links (see "Step 8 option 4b
   — `record-knowledge` update payload" above for the verbatim shape). The Linear ticket is
   named inline in `## Learning`, never linked in `# Sources` — see the schema's validation
   rule 6.

   Per [record-knowledge Recipe C](../../../../shared/skills/record-knowledge/SKILL.md):
   - `## What was tested` is never overwritten — it preserves the source-sheet framing from bulk import
   - Sources append-only; dedup by URL so re-runs don't produce duplicates

   This option is the **default** when the input mode was a Notion URL or DB query (enrich mode) — the user saw an already-imported row and asked for enrichment, not duplication.

5. **Create Linear tickets for follow-up experiments** — invoke
   `shared:add-linear-issues` with the generated follow-up ideas (one issue
   per idea) so they land in the backlog for triage.

## Step 8 mode-specific routing asks

Phrase the ask as a single multi-select question, adapted to the mode.

**Create mode:**

> Want to do anything with this? Options: (1) nothing, (2) post to `#ai-product`,
> (3) reply in `#support-bot` under the end-experiment notes, (4) file learnings
> to `Learnings/Experiments` in AI Knowledge (new rows), (5) create Linear
> tickets for follow-ups. Pick any combination.

**Enrich mode** (input was a Notion URL / DB query):

> Want to route this? Options: (1) nothing, (2) post to `#ai-product`,
> (3) reply in `#support-bot`, (4b) **update the existing `Learnings/Experiments`
> row(s)** in AI Knowledge — refresh `## Result`, `## Learning`, `## Follow-up
> experiments`, and `Status`; append Mixpanel / #wins sources *(default)*,
> (5) create Linear tickets for follow-ups. Pick any combination.
