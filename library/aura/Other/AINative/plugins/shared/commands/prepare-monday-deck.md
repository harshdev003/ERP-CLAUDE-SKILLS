---
description: Prepares the AI team slides for the Monday All-Hands deck. Fetches User Impact (Slack), Velocity (Linear), Weekly Focus (Linear Product/Design) and Retro (Notion) in parallel, then reads PR metrics out of the Review Loop artifact. Generates a PPTX presentation matching the company's All-Hands Google Slides deck style if user has node env setup.
---

# Prepare Monday All-Hands Deck (AI Team Slides)

Gathers all content needed for the AI team's slides in the weekly All-Hands deck, then generates a styled PPTX presentation file.

## Output Format

Before starting, determine whether the user wants a **PPTX file** or **text output** by checking the user's message for natural language cues:

- **Text output** if the user says anything like: "text", "text only", "just text", "no ppt", "no presentation", "no slides", "skip the ppt", "don't generate ppt", "without ppt", "without the deck", "without slides"
- **PPTX output** (default) for everything else, including: "ppt", "deck", "presentation", "slides", or no preference stated

Set a mental flag (`outputFormat = "pptx"` or `outputFormat = "text"`) and carry it through to the PPTX Generation step.

---

## Prerequisites

### MCP Servers

The following MCP servers must be enabled and authenticated before running:


| MCP Server | Used for                          |
| ---------- | --------------------------------- |
| **Slack**  | User Impact                       |
| **Linear** | Velocity, Weekly Focus              |
| **Notion** | What Worked / Didn't Work         |


If any required server is unavailable, stop and ask the user to authenticate it.

PR metrics come from the **Review Loop artifact**, refreshed from GitHub through the **`gh` CLI** — not an MCP server, since the repo's MCP-only rule covers Linear and Figma. Check `gh auth status` rather than looking for a GitHub MCP server. The step is reads only; nothing about the deck writes to GitHub.

### Reading Linear insights views (browser)

The **Velocity (WoW)** and **Effort by Label** slides read Linear **saved-view insights**, which Linear MCP does not expose. Fetch those two with Claude in Chrome: navigate to the view URL, then call `get_page_text` — it returns the insights data table as text, including every row behind the graph. Screenshots often fail on these pages; the page text is the reliable read.

Both URLs are in [`references/insights-views.md`](references/insights-views.md), along with the assignee roster behind each filter and the label include-list. Take them from there rather than asking.

This is a **scoped exception** to the MCP-only rule for Linear, on the same footing as the one `report-linear-updates` holds. It covers saved-view insights only — every other Linear read in this command goes through MCP.

### Tool Setup (for PPTX generation)

Before generating the presentation, ensure the Node.js environment is ready. If the user has no node/npm, fall back to text output:

1. Run `npm init -y` in the working directory (if no `package.json` exists)
2. Run `npm install pptxgenjs` to install the PptxGenJS library locally

`soffice` (LibreOffice) is the preferred visual-QA converter but is often absent; the **Visual QA** section of [`references/monday-deck-layout.md`](references/monday-deck-layout.md) carries the fallback that needs no extra install.

### Charting guidance

Every chart uses the house Google Sheets style defined in [`references/monday-deck-layout.md`](references/monday-deck-layout.md) → *House chart style*. Before writing any chart code, read the `dataviz` skill and validate the categorical palette with its `scripts/validate_palette.js`. That script is an ES module whose CLI guard requires a filename ending in `validate_palette.js` — copy it next to a `package.json` containing `{"type":"module"}` to run it.

### PPTX Skill

Read the PPTX skill at `/mnt/.claude/skills/pptx/SKILL.md` (and `pptxgenjs.md` beside it) when that path exists, for API patterns and QA workflow. Outside the sandbox it is absent — the layout spec in [`references/monday-deck-layout.md`](references/monday-deck-layout.md) is then the source of truth.

## Slides to produce


| Slide                     | Source                                                                       |
| ------------------------- | ---------------------------------------------------------------------------- |
| User Impact               | Slack (#wins, #releases + the private QA channels)                           |
| Weekly Focus              | Linear — Product/Design team, current cycle                                  |
| Team metrics in focus     | Product Delivery + Bugs/Quality artifacts (Artifact `read`) + Review Loop figures from Code Review Metrics |
| Code Review Metrics       | Review Loop artifact (`build-pr-metrics-dashboard`, in AuraHealth/OKRs)      |
| Velocity (WoW)            | Linear insights view — weekly effort by assignee (browser) + Product Delivery dashboard — product features released / handed off per week |
| Effort by Label           | Linear insights view — last week's effort by label (browser)                 |
| What Worked / Didn't Work | Notion retro doc                                                             |


**Skip**: The OKR slide, and the "What Worked / Didn't Work" slide unless a retro entry is found for last week.

Velocity is told **once**, on the Velocity (WoW) slide, from the insights view. The Code Review Metrics slide carries no cycle-velocity block — cycle points and issues-closed measure a different scope (Mobile cycle scope) from the insights Effort figure, and showing both invites a contradiction from the floor.

---

## Parallel execution

Launch the following **4 subagents simultaneously** using the Agent tool (all `general-purpose` type). Do not wait for one to finish before starting the others.

**Paginate every Linear read.** On each query that lists issues, follow `pageInfo.endCursor` until `hasNextPage` is false, and never treat a page limit as proof the set is complete. One page looks like a full answer, so a truncated read shows up as a plausible slide that is quietly missing work.

---

### Subagent 1 — User Impact (Slack)

**Goal**: Find Aura and Aurie release announcements from the past 7 days.

TestFlight build announcements are posted to **private** channels, so every Slack search and channel read in this subagent must include private channels (set channel types to both `public_channel` and `private_channel`). Resolve each channel's ID by searching channels with private channels included. The relevant channels are:
  - `#wins` (public) — production release callouts and experiment wins
  - `#releases` (public) — production release notes
  - `#qa-aurie-mobile` (**private**) — the active Aurie TestFlight channel
  - `#qa-aura-mobile` (**private**) — Aura mobile QA / TestFlight builds
  - `#qa-aura-web` (**private**) — Aura web QA; often only site reports, no builds
  - `#qa-aurie-web` (**private**) — Aurie web QA; frequently empty for the window

Resolve these by search rather than assuming ids, and treat the QA channel set as **per-app** (`aura` vs `aurie`) — a single `#qa-mobile` or `#qa-web` does not exist.

1. Run these Slack searches **over both public and private channels** (more reliable than reading channels directly):
  - `"New Release" after:YYYY-MM-DD` — production release notes (#releases, #wins)
  - `"New Testflight Build" after:YYYY-MM-DD` — TestFlight builds (the private QA channels)
  - `uploaded TestFlight after:YYYY-MM-DD` — QA upload announcements
  - Replace `YYYY-MM-DD` with the date 7 days ago.
2. Also read `#wins`, `#releases` and each private QA channel directly for the past 7 days as a fallback.
3. Classify each build by where it appears, following the release process — a build ships to **TestFlight first, then to release**:
  - **Production**: any build whose version appears in a release channel (`#releases` or `#wins`). If the same build appears in **both** a QA channel **and** a release channel, it counts as **Production** — list it under Production only, never under TestFlight.
  - **TestFlight**: builds that appear only in a QA channel and have not yet been promoted to a release channel.
  - The TestFlight post and the release post for the same build may carry slightly different build numbers (e.g. TestFlight `v1.21.1 (1)` vs release `v1.21.0`); match builds by their feature/content set, and use the release version number once the build is classified as Production.
4. Return two lists with key highlights for each version where available:
  - **Production**: versions released to production
  - **TestFlight**: versions still only in TestFlight / internal testing
5. Also create an overall summary with:
  - One-liner totals: "X experiments, Y major features, Z minor improvements, N bug fixes"
  - Then a breakdown listing each item under its category:
    - *Experiments*: name + one-line description of what it tests
    - *Major features*: name + one-line description
    - *Minor improvements*: name + one-line description
    - *Bug fixes*: name + one-line description

---

### Subagent 2 — Velocity (Linear)

**Goal**: Establish the previous-cycle window on the Mobile team, and report that cycle's completion stats as a cross-check.

**These figures are not slide content.** Velocity reaches the deck from the insights view on the Velocity (WoW) slide, and `deck-content.js` carries no key for cycle points or issue counts. Everything this subagent returns — the cycle window and its completion stats — goes into the summary handed to the user as a **cross-check**, never onto a slide: cycle scope and insights Effort measure different things, and putting both in front of a room is the contradiction the split removed. The window is what lets the reader confirm the insights slides and the cycle cover the same days.

1. Use Linear MCP to find the **Mobile team** (team name: "Mobile")
2. Get the **previous cycle** for the Mobile team
3. Report:
  - Cycle name and date range
  - Points completed vs total scope
  - Issues closed vs total issues in cycle

**Take every number from the cycle's history arrays** — the final value of `completedScopeHistory`, `scopeHistory`, `completedIssueCountHistory` and `issueCountHistory`. Do **not** compute the denominator by querying issues in the cycle: this command runs on Monday, after the cycle closed and rollover already moved incomplete issues into the new cycle, so an issue query sees only what remained and reports a far higher completion rate than the cycle actually achieved. Cross-check the *completed* side against the issues still in the cycle (their estimates should match `completedScopeHistory`), and let any mismatch stand as a flag rather than replacing the history figure. A gap here is usually the snapshot cadence rather than bad data: the history arrays hold one sample per day, so anything completed after the final sample — or any estimate raised after it — lands in the issue query but not in `completedScopeHistory`. Report the cause alongside the number so it can be answered from the floor, and say which side is the larger.

**Report the cycle by `title`, not `number`.** Mobile's cycle `number` runs ahead of its `title` (title "Cycle 39" carries number 41), and the title is what everyone sees in the Linear UI. Other teams' titles and numbers agree, so the title is also what keeps the slides consistent across teams.

Return a short summary: e.g. "Cycle 19 (Mar 23–30) — 274 pts / 326 scope, 40/63 issues", plus the raw history values used so they can be verified.

---

### Subagent 3 — Weekly Focus (Linear)

**Goal**: List current cycle issues for the Product/Design team, grouped into **In Progress** and **Planned**, sorted by priority.

1. Use Linear MCP to find the **Product/Design team** (team name: "Product/Design")
2. Get the **current cycle** for that team
3. Fetch all issues in that cycle (include subissues). Capture each issue's labels — the **"Major Feature"** label is used in step 5. **Then exclude the recurring generic operational tracker umbrellas before building either group below.** These are the standing per-cycle catch-all buckets — the **bugs/fixes**, **support**, and **analytics** trackers (e.g. "Aura Bugs and Fixes [Cycle N]", "Support [Cycle N]", "Analytics [Cycle N]") — which add routine noise rather than signal; the slide should foreground product work. To qualify for exclusion a card must be a **top-level umbrella** (no `parentId` — a standing operational bucket, typically the recurring `[Cycle N]` tracker) **whose subject is bugs/fixes, support, or analytics**; drop it together with all of its descendants. **Scope guards — match on the umbrella's role, not a substring:** never drop a feature, experiment, or optimization card just because its title contains "bug", "fix", "support", or "analytics" (e.g. "Fix onboarding paywall" or an analytics-instrumentation feature stays). When unsure whether a card is a generic operational umbrella versus real product work, keep it.
4. Build the **In Progress** group so each piece of work appears **once, at the highest in-progress level**, with descendant work rolled up:
   - An issue is **In Progress** when its status is an engineering status: Ready for Eng, Eng In Progress, In Review, or Blocked (or any status containing "ready for eng", "eng in progress", "in review", "blocked").
   - **Display rule**: list an In Progress issue as its own item **only if none of its ancestors are also In Progress**. Walk the full `parentId` chain, not just the immediate parent — this applies at any depth, so a mid-level card (e.g. a sub-tracker) is displayed when its own parent is not In Progress, and is rolled up when its parent is.
   - **Roll-up rule**: an In Progress issue that **does** have an In Progress ancestor is **not** listed separately — it rolls up silently under its nearest displayed ancestor. Leave the ancestor's title exactly as it reads in Linear — no descendant count, no suffix, no other roll-up marker.
   - Never list both a card and one of its descendants as separate items.
5. **Sub-categorize the In Progress group by the "Major Feature" label**: split it into two sub-sections — **Major Features** (displayed cards carrying the "Major Feature" label) listed first, then **Other** (the remaining displayed cards). If no In Progress card carries the label, omit the Major Features sub-section and list everything under In Progress directly.
6. Build the **Planned** group: filter to **parent issues only** (exclude any issue that has a `parentId`) whose status is one of — Backlog, Todo, Design & PRD In Progress (or any status containing "design", "prd", "todo", "backlog"). Exclude issues with status Done or Cancelled.
7. Within each group (and within each In Progress sub-section), sort by priority ascending (Urgent → High → Normal → Low → None)
8. Return just the issue titles, verbatim (no IDs, no status labels, no priority labels, no roll-up counts):
   - **In Progress** — a **Major Features** sub-section (when present) followed by an **Other** sub-section
   - **Planned** — a clean bullet list

---

### Subagent 4 — What Worked / Didn't Work (Notion)

**Goal**: Check the Product & Eng retro doc for last week's entry.

1. Fetch the Notion page: `https://www.notion.so/aurahealth/Retros-2025-26-Product-Eng-29675e23af20807aaeacfdf661f1b335`
2. Look for a retro section dated within the past 7 days (e.g. a heading like "3/30/26", "3/23/26", or similar)
3. If a matching retro is found:
  - The retro doc is organized per-person. **Consolidate across individuals into thematic bullets — do not group or attribute by person.** Merge points that describe the same theme into one bullet, and drop the names.
  - Return **What Worked Well** as consolidated, theme-based bullet points (no names)
  - Return **What Didn't Work Well** as consolidated, theme-based bullet points (no names)
4. If no retro for last week exists, return: "No retro found for last week — skip this slide."

---

### Code Review Metrics (sequential, after subagents)

The figures come from the [Review Loop artifact](https://claude.ai/code/artifact/4e997ead-1408-44ba-81de-e0c88631eaf2),
owned by the `build-pr-metrics-dashboard` skill in [AuraHealth/OKRs](https://github.com/AuraHealth/OKRs). Refresh it (that
skill's steps 1–5), then read the numbers off the built page — do not recompute them.

The whole procedure — refresh, the two `javascript_tool` reads, the section→slide mapping,
the per-figure direction rules and the caveats that must survive onto the slide — is in
[`references/pr-metrics-from-artifact.md`](references/pr-metrics-from-artifact.md). Read it
before touching this step; several of its rules fail silently when missed.

There is no approval gate on the metrics themselves, and nothing is written to **this**
repo. The refresh does write to the OKRs repo, though: `data/pr/` there is the durable
store, so a clean run commits and pushes the refreshed history to its `main`. Publishing the
artifact is a separate, gated step inside `build-pr-metrics-dashboard`; the deck does not
depend on it, though publishing in the same session keeps the slide and the shared link in
agreement.

#### What goes on the slide

The artifact carries far more than a projected slide can hold — time to first approval, PR
size vs approval time, review load, repo activity, PRs opened / reviewed / changes requested,
open PRs, "ready but nobody was asked", "waiting more than a day for a human". Porting all of
it would produce a slide nobody can read from the back of a room. The slide takes **five
figures and two charts**, and links out for the rest:

- **Five KPI tiles**, latest complete week with its week-over-week delta: *time to merge*,
  *time to merge — human-reviewed*, and the three coverage shares (*reviewed by a human*,
  *reviewed by AI only*, *no review at all*).
- **Two line charts, last 8 complete weeks**: coverage over time, with all **three** shares
  on one chart, and time to first review with the **human and AI series on one chart**. Both
  are single charts on purpose — on coverage the three shares move against each other, and on
  timing the gap between human and AI *is* the finding; splitting either one hides it.

The two timings are the pair that decide how the merge process actually behaves; coverage is
what says whether the process happened at all. Time to first approval is deliberately left
off: approval is close to optional on this data, so it describes a narrower population than
the room will assume.

Everything dropped from the slide is still one click away. Put the artifact link in the
summary handed to whoever presents, so a question from the floor has an answer.

#### Caveats to carry

- **Exclude the partial week.** The final week in the data runs to the refresh date, not to
  Sunday. Tiles read the latest *complete* week; charts take the 8 complete weeks before it.
- **Keep the `≥`.** It marks a figure resting on PRs that are still waiting — a floor, not a
  value. Carry it verbatim and put the still-waiting count in the footer.
- **State the basis, and only compare like with like.** These are cohort figures on
  Monday-start UTC weeks over every active repo — the subtitle says so. A slide built on a
  different basis is not a comparison: where the deck's previous PR figures did not come from
  this artifact, the change between the two is measurement, not performance, and must be said
  once rather than read as a step change. The rule is in the reference.

### Team metrics in focus (sequential, after Code Review Metrics)

Five targets drawn from three OKR dashboards — QA → Release and Releases/week from Product
Delivery, No review at all and Time to first human review from the Review Loop, Closure ratio
from Bugs/Quality. The slide carries a value, its percentage change against the week prior,
and a status of on track / at risk / off track. No charts: it is the scorecard that the three
metric slides after it then detail.

The Product Delivery and Bugs/Quality figures come from the **published artifacts**, read with
the Artifact tool's `read` action and decoded from the fact table each page embeds. A browser
cannot read them (the page renders in a sandboxed iframe), and a local re-render of the OKRs
dashboards is only the fallback, for when the Artifact tool is unavailable or returns only a
summary.

The whole procedure — which dashboard owns which figure, the artifact URLs, the read and the
extraction, the fallback re-render, where each value sits in the fact table, the
week-alignment rule, the `generated` gate, the status definitions and the caveats to hand on —
is in [`references/team-metrics-in-focus.md`](references/team-metrics-in-focus.md). Read it
before touching this step.

**Run this step after the Code Review Metrics step above, not in the slide's own position.**
Two of its five rows are the Review Loop figures that step reads, so running it earlier either
leaves those rows empty or reads the PR page before the refresh — which is exactly how one deck
ends up showing two different values for *no review at all*. The slide sits at position 6 in the
deck; the gathering happens here.

Three further rules that decide whether the slide is honest, repeated here because they fail quietly:

- **A figure whose page was generated on or before the reported week's last day is not
  printed.** The reference makes the `generated` stamp a gate: show that row as unavailable and
  name the dashboard that needs republishing, rather than a number nothing downstream can tell
  apart from a current one.

- **Every figure reports the same latest complete week**, and it is the same week the Code
  Review Metrics slide names. Both the product and the bug fact table carry a trailing partial
  week; excluding it is what keeps the row comparable.
- **Reuse the Review Loop figures already read** for the Code Review Metrics slide rather than
  reading that page a second time. *No review at all* is the third `kpis-cov` tile and *time to
  first human review* the first `kpis-eff` tile, both already in hand from that step.

This step only reads. It never republishes an artifact and never runs an OKRs fetch script.
The fallback re-render writes only to the gitignored `build/` inside the OKRs checkout.

### Linear insights views (sequential, after subagents)

Two slides come from saved Linear insights read in the browser (see Prerequisites). Both are the
**same saved view** — `all-issues-270a13596592`, the workspace "All issues" view — opened twice
with different `filter=` and `insights=` query params. Neither configuration is what the view
holds by default, so both slides are driven from a URL rather than from the view as saved.

Decode the `filter=` and `insights=` params (base64 JSON) before reading the table, and state the
scope back — it determines how the slide must be captioned. Their scopes:

| Slide | `insights` | `filter` |
|-------|-----------|----------|
| Velocity (WoW) | `m=effort, d=completedAt, dateAgg=week, s=assignee` | Done · created since Feb 2026 · 5 named assignees · **excluding** the `AI First` and `Tech Debt` labels |
| Effort by Label | `m=effort, d=label` | Done · `completedAt` within `-P1W` · 5 named assignees · **including** a 15-label list |

The view's own stored insights config slices by **assignee**, not by completed week, so opening it
without the `insights=` param gives an all-time per-person total and no weekly trend.

**Both URLs, verbatim, are in [`references/insights-views.md`](references/insights-views.md)**,
with the assignee roster behind each filter and the 15-label include list written out. Read them
from there. The two filters carry different assignee sets, so a name present on one slide and
absent from the other is expected — that file says which. Ask the user only if a URL there no
longer loads, and never reconstruct the label view's include list by hand: it is not derivable
from the category mapping, which is a 19-label superset.

**Resolve any assignee sitting at zero before treating it as a finding** — a deactivated Linear
account left in a saved view reads exactly like a member who shipped nothing. The reference
carries the check and the current roster.

**Velocity (WoW)** — insights measure `effort`, sliced by completed week, segmented by assignee.

1. `get_page_text` on the view returns a per-week table: a row total followed by one column per assignee.
2. **Reconcile before using it**: each row's assignee columns must sum to that row's total, and the per-week totals must sum to the grand total the page reports. State the result — a mismatch means the table was misread.
3. Take the **last 13 complete weeks** (about three months) for the chart — long enough that a slow drift reads as a trend rather than a bounce. If the view holds fewer complete weeks, chart what it has and say so. **Exclude the current partial week** — this command runs on the Monday a cycle opens, so that bucket holds about one day and would read as a collapse.
4. Compute: latest complete week, its change vs the prior week, and its distance from the trailing 4-week average. Also compute each member's week-over-week change.
5. Note any member sitting at zero across the whole window — that is a real signal, not a gap to quietly drop from the Insight line.
6. Label each week as its Monday–Sunday range, `"M/D - M/D"` — the chart's category axis reads these verbatim.

**Features released and handed off** — the slide's other two charts come from the Product Delivery dashboard, not from Linear insights, and run over the **same 13 weeks** as the velocity chart.

1. Read them from the Product Delivery fact table the Team metrics step decoded from the published artifact ([`references/team-metrics-in-focus.md`](references/team-metrics-in-focus.md) steps 1–2; read the artifact now if that step was skipped). On the fallback path it is the re-rendered `build/product/okr.json`. Read two series per measure — product features, which the charts plot and the insight lines and takeaway measure, and engineering features, which appear only as a count in the insight lines:

   | Measure | Product (charted) | Engineering |
   |---|---|---|
   | Features released | `s_type.product.features.weekly[<week>]` | `s_type.engineering.features.weekly[<week>]` |
   | Features handed off | `s_type.product.ready.weekly[<week>]` | `s_type.engineering.ready.weekly[<week>]` |

   The dashboard types every feature as product or engineering, so the two counts together are all features — which is why the slide never needs the all-features series under `s`. The keys are Monday ISO dates: map each `"M/D - M/D"` velocity week to its Monday and read that key. A week older than the dashboard's first week is `null`.
2. **Check the weeks line up before charting.** The dashboard's latest complete week is `weeks[-2]` (`weeks[-1]` is the partial week holding the day the page was generated); it must be the last velocity week. When it is one week older, the page was generated before the reported week ended: read the earlier weeks as usual and treat the reported week as gated (step 3). Any other mismatch, stop and say so rather than shifting one series — a row of charts whose frames disagree about the week is worse than a missing chart.
3. **Gate the reported week on the page's `generated` stamp**, as the Team metrics step does (on the fallback path, on `prod_last_run` in `data/product/state.json` instead). When it falls on or before the reported week's last day, that week's feature values are `null` — a gap in both feature charts — and both feature insight lines read `— (page generated {date} — republish to report)` in place of the figures.
4. For each feature chart compute, on the **product** series, the reported week's count, its change vs the prior week and the trailing 4-week average, on the same windows as step 4 above; then add the reported week's engineering count beside it. The product-released average is also what the takeaway in step 5 measures against. **Report the change as an absolute count, not a percentage**: weekly feature counts sit in single or low double digits, where one feature moves a percentage by ten points or more.
5. **Write the takeaway as one test: is the team's velocity turning into product features?** Compare velocity with **product features released** (`s_type.product.features.weekly`), not all features: engineering features count in all, so a week of infrastructure work can lift both velocity and the all-features count while no product work ships. Measure each against its **own trailing 4-week average** — the four complete weeks before the reported week, the same four for both — not against the prior week, which is too noisy on counts this small. Compute the average from the `.weekly` series; the dashboard's `.l4` window includes the reported week itself. Express both as a percentage above or below that average.
   - **Gated week** → when step 3 nulled the reported week, there is nothing to compare: the takeaway reads *"Product features for {week} are not in the published dashboard yet (generated {date}) — republish to compare against velocity."* Skip the rest of this step.
   - **Place each figure in one of three bands**: *above* (more than 10% over its average), *at* (within ±10%), *below* (more than 10% under).
   - **Same band** → they correspond. Say so with both figures: *"Velocity 13% above its 4-week average and product releases 41% above theirs — the extra effort shipped as product features."*
   - **Different bands** → they diverge, including when only one of them has left *at*. The takeaway names the reason, and which list to check depends on which side is ahead. Check the list in order against the reported week in the same fact table, and state the first reason the data supports, with its figure. A reason that holds but points the wrong way does not count.
     - **Velocity ahead of product** (velocity in a higher band than product releases): effort that did not ship as product features.
       1. **Engineering features.** Check this first. Effort spent on engineering features — infrastructure, reliability, security, tech quality — raises velocity without moving the product count at all. It holds when points on released engineering features, `s_type.engineering.points.weekly[<week>]`, sit above their 4-week average. Then **name the one or two largest by points from the `released[<week>]` rows with `type: "engineering"`, saying in a few words what each is about** — from its title, with no issue id: *"Velocity 18% above its 4-week average while product releases held at theirs: the effort went to engineering features, chiefly the voice-transcription callback rework."*
       2. **Larger product features.** `s_type.product.complexity.weekly` (points per released product feature) above its 4-week average: the effort went into fewer, bigger product features.
       3. **Non-feature work.** The `Features` share of the week's effort — `effort.weekly.Features[<week>] ÷ effort.total[<week>]` — below its 4-week average: name the category that grew most (`Bugs`, `Support`, `Tech quality & debt`, …).
     - **Product ahead of velocity** (product releases in a higher band than velocity): product features shipped without matching effort that week.
       1. **Effort booked earlier.** Long-running product features in `released[<week>]` (`type: "product"`, high `days`) were built in earlier weeks and only reached Done in this one.
       2. **Smaller product features.** `s_type.product.complexity.weekly` below its 4-week average: more features shipped for the same effort.
     - **Nothing in the list explains it** → say the dashboard does not explain the gap. Two causes are possible, and the takeaway names them as possible rather than as the finding. Effort counts in the week each sub-issue closes but a feature only in the week it reaches Done, so a velocity-ahead week can be building features that have not shipped. On that side, name the one or two largest open features from `wip.buckets.eng`. That list is a snapshot taken at the run, not as of the reported week, and is rarely empty, which is why it never counts as a reason that holds. The velocity view also covers only five named assignees, so work outside that roster can explain either side.
   - **Handoffs** go in a short closing clause only when they change the reading: handed off well above released fills the engineering queue, and since handoffs lead releases by a cycle or two, a handoff spike sets up a later rise in releases.
   - **Never divide effort by features released.** The velocity view covers five named assignees and excludes two labels, while the feature counts cover every Product/Design feature, so a ratio of the two mixes populations.

**Effort by Label** — insights measure `effort`, sliced by label, over the last 7 days.

1. `get_page_text` returns a label→effort table. Capture **every** label, not just the ones on the graph.
2. **Labels overlap.** An issue carries several labels, so each of its estimate points is counted once per label and the totals sum to more than the week's real effort. Verify this on the data before framing anything, as a subtraction rather than an assertion: sum the label rows, compare that total against the grand total the page reports for the same filter, and state both numbers and the gap. The label sum is the larger of the two, and at least one small label is usually the same issues as a larger one. Consequently the effort mix is a set of **per-theme totals**, never shares of a whole, and the form is bars — a pie would assert a whole that does not exist.
3. Group every label into categories and confirm they account for all of them. **Three categories always appear**, whatever their size, so the mix stays comparable week to week:
   - **Product, Growth & Infrastructure** — `Aura App Improvements`, `Core UX & Activation`, `Growth & Monetization`, `Character & Emotional Bonding`, `Coaching & Processing Efficacy`, `Engagement & Retention`, `Marketing & Distribution`, `Platform Expansion`, `Major Feature`, `Infrastructure, Reliability & Performance`
   - **Bugs & Support** — `Bugs`, `Support`
   - **Tech Quality & Debt** — `Tech Quality`, `Tech Debt`
   Each label above is backticked because one of them contains a comma. Match names **exactly as
   Linear spells them** — `Infrastructure, Reliability & Performance` is a single label, and a
   near-miss silently drops the week's largest theme into the residual instead of the category it
   belongs to. (The bar chart shortens it to `Infra, Reliability &
   Performance` to fit the gutter; that is a display string, not the match key. `Platform
   Reliability & Performance` is a different label and is not in the filter at all.)

   Every label not in those three lists is a residual. **Any residual label whose share exceeds 20% is promoted to its own row**, so an unusually large theme is named rather than buried; everything still left folds into **Other**. A label outside every list is a residual too — say which one, so the mapping can be extended next week. Report each row as a percentage of total label-attributions, and round with largest-remainder so the rows total exactly 100%.
4. **Order: the three standing categories in the fixed order above, then any promoted row, then "Other" last** in medGray. The residual never leads, even when it outranks a named category, and the standing three hold their positions even when a promoted row is larger — a fixed order is what makes the rows scannable week to week.
   Absorbing a large label into a named category makes the 20% promotion unlikely to fire; that is the intended effect, and the rule stays as a safety net for a theme the mapping does not yet name.
5. Say what the denominator is when reporting the percentages, so the number can be traced.

Neither Effort measure is story points — divide the grand total by the issue count to see the scale before writing any axis title, and label the axis **Effort**. The two views' Effort figures are also **not on the same scale as each other** (the weekly view runs an order of magnitude higher per issue than the label view), so never carry a number from one slide to the other or invite the room to compare them.

---

## PPTX Generation

**If `outputFormat = "text"`**: Skip this entire section. Instead, output the slide content as structured markdown — one section per slide with a bold heading and the relevant bullet points. No script, no file, no QA steps needed.

**If `outputFormat = "pptx"`** (default): After all data is gathered, generate a `.pptx` presentation using PptxGenJS (Node.js). Write a `create-deck.js` script and run it with `node create-deck.js`. If the user does not have a Node.js environment set up, fall back to text output as described above.

### Script shape

Split the generator in two so the week's content is editable without touching layout:

- **`deck-content.js`** — a plain module exporting one key per slide (team roster, `userImpact`, `weeklyFocus`, `focusAreas`, `prMetrics`, `velocityTrend`, `labelEffort`, `retro`). Set `retro: null` when no retro exists.
- **`create-deck.js`** — requires that module and owns all positioning. Guard the optional slides on their content (`if (C.retro)`) so a missing section drops its slide rather than emitting an empty one.

Include a `QA_SLIDE` env hook that stubs `addSlide` for every slide but the requested one, so a single slide can be rendered for review. The stub must supply **`addText`, `addShape`, `addTable` and `addChart` noops** — a missing `addChart` throws on any slide carrying a chart.

The `deck-content.js` key contract, the design tokens and helpers, the per-slide layout spec,
the PptxGenJS chart rules and the whole visual-QA workflow live in
[`references/monday-deck-layout.md`](references/monday-deck-layout.md). Read it before writing
either file — several of its rules fail silently when missed.

## Final Output

**If `outputFormat = "pptx"`**:

1. The `.pptx` is already saved to `{cwd}/reports/prepare-monday-deck/{today's date}.pptx`
2. Share with a computer:// link: `[View your deck](computer://{cwd}/reports/prepare-monday-deck/{today's date}.pptx)`

**If `outputFormat = "text"`**:

1. Output the full slide content inline as structured markdown (already done in the PPTX Generation step above)

### Manual step — animate in Google Slides

The **User Impact** (Slide 2) and **AI Consumer App – Weekly Focus** (Slide 3) slides land a lot of content at once, which is hard to digest when it all appears together. After the content is in the Google Slides All-Hands deck, apply a per-section entrance animation (in Google Slides: select the section's text box → **Insert → Animation** → effect "Appear", start condition "On click") so each section reveals one at a time as the presenter advances. **Animating per section is preferred; per line works too.** On Slide 3 the In Progress **Major Features** and **Other** sub-sections — and on the What Worked / Didn't Work slide the **What Worked Well** and **What Didn't Work Well** sections — are emitted as separate text boxes whenever both are present, so each can be selected and animated as its own block.

Surface this as an explicit reminder to whoever assembles the deck — it is manual **by necessity**: neither PptxGenJS nor the Google Slides API exposes any way to set animations or transitions, so the skill cannot apply them programmatically.

---

## Notes

- Do not include Linear issue IDs, Slack channel names, or PR numbers/URLs in any slide content. Titles pulled verbatim from Linear sometimes carry an issue reference — strip it.
- "Last week" = the 7 days ending yesterday. "This week" = the current Linear cycle.
- Today's date is available in the system context as `currentDate`.
- The Code Review Metrics change convention: green = improvement, red = regression. Which direction counts as an improvement differs per figure — falling for the timings and for "no review at all", rising for "reviewed by a human". **"Reviewed by AI only" has no fixed direction** and stays black; a rise there can be PRs moving up out of no review or down out of human review, and the tile alone cannot say which. Every delta carries a ▲/▼ glyph, so direction is never colour-only.
- The `build-pr-metrics-dashboard` skill answers PR-metric questions outside the deck too, but it lives in [AuraHealth/OKRs](https://github.com/AuraHealth/OKRs) and is not installed by this plugin — run it from a checkout of that repo rather than reaching for the Skill tool.
- The Team metrics slide and the standalone one-pager read the **same** `focusAreas` block, so a figure is edited in one place. Its status labels are text first — colour reinforces the label and is never the only cue.
- **Prefer figures over prose on the two insights slides.** Their job is a handful of numbers a room can absorb at a glance; a long bullet list is more reading than a projected slide supports.
- **Report what the numbers are, and are not.** The insights measure is Effort, not story points, and the label totals overlap. Where a caveat is dropped from a slide, keep it in the summary handed to the user so they can answer for the figure.
- The subagents surface judgment calls (an ambiguous operational tracker, an assignee sitting at zero, a source that disagrees with the brief). Pass those to the user rather than resolving them silently — they usually reflect something real about the week.
