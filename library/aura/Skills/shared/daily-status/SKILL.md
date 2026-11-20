---
name: daily-status
description: >
  Generate daily status based on Linear cards

metadata:
  version: "3.5.0"
knowledge:
  reads:
    - Guide/Index                          # baseline routing aid (universal)
    - Team                                 # poster Slack identity (handle + Slack ID column) to attribute the standup as the user
    - Tools/Linear                         # API-first GraphQL bucket queries (LINEAR_API_KEY) — see "Pull from Linear"
    - Tools/Notion                         # Team-page roster reads + Slack-ID write-back via Notion REST (NOTION_TOKEN)
    - Tools/Slack                          # #standup channel ID + activity search/sweep (do NOT add a Post-format footer — see "No skill-written footer" below)
    - Procedures/Engineering/Estimation    # FAST story-point scale — see "Verify estimates on Completed cards"
    - Tools/GitHub                      # gh CLI auth + PR conventions
  writes:
    - Team          # write a resolved Slack ID back to the Team page when its cell was empty
---

Determine lookback window

Working days are Monday through Friday. On Monday, the lookback covers Friday, Saturday, and Sunday. On all other weekdays, the lookback covers just yesterday.

Pull from Linear

**API-first by necessity for Linear bulk queries** (see `/CLAUDE.md` → "Tokens are soft requirements"). This skill deviates from the MCP-first default because `mcp__linear__list_issues` silently truncates on heavy completion days — output spills to a file and entire buckets get dropped. The Linear GraphQL API (`LINEAR_API_KEY`) paginates cleanly via `pageInfo.endCursor`, doesn't truncate, and exposes the `updatedAt` / `completedAt` filter fields the MCP doesn't. Fall back to MCP only when `LINEAR_API_KEY` isn't set.

Run each bucket as its own query (not one big query):

1. Done            — assignee=me, state=Done,            orderBy=updatedAt, filter.updatedAt.gte=lookback_start, includeArchived=false
2. To Be Released  — assignee=me, state=To Be Released,  same window
3. In Progress     — assignee=me, state=In Progress
4. In Review       — assignee=me, state=In Review
5. Eng In Progress — assignee=me, state=Eng In Progress
6. Blocked         — assignee=me, state=Blocked
7. Ready for Eng   — assignee=me, state=Ready for Eng, same window as Done; keep only data design cards (see "Data design cards" below)

**Paginate every bucket to completion.** Query each of the seven buckets with `first: 100` and follow `pageInfo.endCursor` in a loop until `pageInfo.hasNextPage` is false, concatenating the nodes across pages. A single page is never the whole bucket — taking only the first page drops the rest silently, with no error, and under-reports the window.

Sort Done by completedAt desc; stop including rows when `completedAt < lookback_start`.

**Wide windows (weekly / post-sweep).** When the lookback spans a week or more — or follows a bulk operation like `eng:sweep-ended-experiments` — bucket counts routinely run into the hundreds, so paginating to completion matters most here. Bulk-generated, self-assigned cards (e.g. the "Remove experiment: …" sub-issues a sweep fans out) can dominate the raw card count and point total while representing one scripted action, not hundreds of hand-done tasks. Collapse each such family into a single summarized line (e.g. "Removed ~240 concluded experiments from the Aura mobile codebase") rather than listing every sub-issue, and lead the readout with the substantive engineering work so the auto-card volume doesn't bury it.

**Fetch the parent chain for every issue.** Grouping is by topmost parent (see "Group by topmost parent" below), so each issue's field selection must include its ancestor chain. Linear nests `parent` directly — request it several levels deep so the root is reachable in one query:

```graphql
parent {
  id identifier title
  parent {
    id identifier title
    parent {
      id identifier title
      parent { id identifier title }
    }
  }
}
```

Four levels covers the team's deepest trees today; if the deepest `parent` returned is still non-null (a chain deeper than the selection), follow up with a `get_issue`/node query on that id to walk the rest. The topmost parent is the last non-null `parent` in the chain.

If an API call is rate-limited (HTTP 429), wait and retry.

_MCP fallback (only when `LINEAR_API_KEY` is unset):_ Linear descriptions can blow past the MCP response cap on a heavy completion day, and the failure is silent — output spills to a file and the bucket gets dropped. If any bucket is truncated / spilled to a file: retry with limit=15. Never proceed with the truncated bucket dropped.

Filter issues

Completed: completedAt within the lookback window OR status is "To Be Released" with updatedAt within the lookback window OR a data design card in "Ready for Eng" with updatedAt within the lookback window. Also include issues completed today (before standup time) if they were In Progress during the lookback window — this catches same-day completions of yesterday's work.
In Progress: status is "In Progress", "In Review", or "Eng In Progress" (exclude archived)
Blocked: status is "Blocked"

**Data design cards.** A data design card is one whose title starts with "Data Design" / "Data design", or contains "data design" anywhere (e.g. `` `abc:customCheckoutCC`: Checkout page data design ``) and has no children in Linear — the `Data Design/Review [Cycle N]` umbrella itself is not one. For these, "Ready for Eng" means the design is finished and handed to engineering, so it counts as Completed. "Ready for Eng" on any other card is not a completion — drop those rows.

Exact Linear state names (case-sensitive): "Done", "To Be Released", "Ready for Eng", "Eng In Progress", "In Review", "In Progress", "Blocked". Use these exact strings when querying to prevent mismatches.

Verify estimates on Completed cards

Run this once the Completed bucket is filtered, before grouping or computing point totals. The goal is the same sanity pass a human would do before reporting velocity: every completed card should carry a point value, and that value should reflect the actual scope shipped — not be a rubber-stamped guess from when the card was created.

Pull the scale from AI Knowledge (`Procedures/Engineering/Estimation`, pre-fetched via this skill's `knowledge.reads` — this is the same FAST rubric `shared:add-linear-issues` / `shared:update-linear-issues` use) rather than hardcoding it. Three things from that page matter most here, in this order:

- **Release floor — a card that shipped is 3 or more.** The scale's release gate is a floor, not a suggestion: 3 is the smallest size for work that needs a release, so **1 and 2 are reserved for work that ships nothing at all.** Two shapes get sized below the floor and shouldn't be — a **one-line** change that ships, and a **test-only** PR with no production code. Both are 3: what the points buy is the review-and-release cycle, not the diff.

  Decide whether a card shipped from the linked PR attachments this step already fetches — a merged PR into a deployed repo ships. `AuraHealth/AINative` counts (the plugin version-bump gate re-ships every `plugins/**` change into the install cache), as do the mobile app repos (app release), `AuraHealth/AuraInfra` (a manifest change needs a `kubectl apply`), and `AuraHealth/IntelligenceService` (service deploy). When the card's state history is at hand, a transition through **To Be Released** confirms it directly.

  Flag every Completed card below 3 that shipped, naming the release vehicle in the reason so the user can judge it.
- **Missing estimate — size it and apply it.** Any Completed card with no estimate gets one, written to Linear before totals are computed, so no card silently counts as 0. Size it with the same inputs as the two bullets either side: the release floor (a shipped card starts at 3), then the PR file/line footprint and cross-team need below; a card with no PR is sized by the support-estimation table. Leave unestimated any card that has children in Linear — an epic, a `Monthly Tasks for …` umbrella, a recurring `[Cycle N]` umbrella — whether or not those children fall inside the lookback; their points live on the children. Decide this from Linear, not from the report: add `children(first: 1) { nodes { id } }` to the Done / To Be Released field selection and skip any card whose `children.nodes` is non-empty.
- **Estimate vs. actual scope.** This sizes a card *above* the floor. For each Completed card that has at least one linked PR attachment, sum `changedFiles` / `additions` / `deletions` across its PR(s) via `gh pr view <number> --repo <owner/repo> --json changedFiles,additions,deletions`. Weigh that against the remaining FAST gates: does it need another team (the 5/8 line), and does the file count land near the ~10-file (21) or ~25-file (32) split-warning thresholds. **Judge by file/line footprint and cross-team need, not by wall-clock turnaround** — the convention explicitly sizes effort/complexity to ship, not hours, and a card that shipped in 20 minutes can still be a legitimate 21 if it touched 40 files (confirmed 2026-07-16: a same-hour PR touching 40 files was correctly at 21, while several multi-file/multi-PR cards sized at 2-3 were undersized once the actual diff was checked).
- Cards with no linked PR (investigation-only cards, data/ops runs, decision/spike cards) ship nothing, so the release floor doesn't apply and the file-count check is skipped — just confirm they have *an* estimate, sized by the support-estimation table's research/investigation guidance on that same page.

**Fill missing estimates; ask before changing existing ones.** Apply every missing-estimate value directly via the Linear API (`save_issue` / the `linear_api.py` update path), with no confirmation, and list what was applied (card, value, one-line reason) alongside the drafted standup so the user can correct any of them. A card someone already sized is different: changing it overrides a human's number, so collect each suggested correction (old → new value and the one-line reason: release floor, file/line count, cross-team need) and show the list to the user alongside the drafted standup, before posting — the same side-effect gate as the post itself. Apply only the corrections the user confirms. All writes land before computing point totals and bucket subtotals, so the numbers in the post reflect them.

**When nobody is there to confirm.** An unattended run — a scheduled task, a cron tick — has no one to answer that gate, so waiting for confirmation parks every flag in a run summary nobody reads, and the estimate stays wrong permanently because a Done card is never revisited. The caller declares that its run is unattended; what that implies per flag class is decided here, and a caller must not restate it (a scheduler body executes the prompt frozen at arming time while reading this skill at fire time, so a restated rule silently outlives the edit that supersedes it):

| Flag | Unattended | Why |
|---|---|---|
| Missing estimate | **Apply it** (sized as above) | Same as an attended run — filling a missing estimate never needs confirmation. |
| Card **carrying an estimate of 1 or 2** that shipped | **Apply it** (→ 3) | The rubric fixes the value. A card that shipped cannot be a 1 or a 2, so there is exactly one correct edit and no judgement in making it. |
| Estimate vs. actual scope | **Leave it** | Sizing above the floor is a judgement off file/line footprint; applied unattended, a heuristic rewrites velocity history. |

An unattended run posts a comment on each card whose estimate it **filled** (the value and the reason, marked as auto-sized), so a later reader can tell an agent's sizing from a human's, and on each card whose correction it **left** (the current estimate, the suggested value and the reason) — durable, and where whoever acts on it is already looking. **Comment once per card per flag:** read the card's existing comments first and skip any flag already recorded there, since a `To Be Released` card stays in the Completed bucket across runs and a same-day re-run repeats the whole sweep. **The standup posts either way.** Gating the deliverable on a pending correction reads exactly like a clean run, so the two decisions stay independent: corrections are held, the post is not.

**Degrade gracefully.** If `gh` isn't installed/authenticated, or a card's PR was deleted/inaccessible, skip the file-count check for that card (fill a missing estimate from the release floor and the card's scope as described in Linear) and note in the readout that estimate verification was partial this run — don't block the standup on it.

Sanity checks before posting

- If Completed = 0, fetch the previous post in #standup (channel CPTFXHNP2) and verify every card that was In Progress in that standup either still appears In Progress today or shows up in today's Completed bucket. Any silent disappearance means a Done-bucket miss — re-run the Done query with a smaller limit.
- If Completed = 0 AND the user posted >3 work-related Slack messages during the lookback window, treat as suspicious; surface to the user before posting rather than publish [0].

Group by topmost parent

Issues are bucketed by their **topmost parent** — walk each issue's `parent` chain (fetched above) all the way to the root. Every issue that rolls up to the same root lands in the same bucket, regardless of project or label.

- **Bucket key** = the root ancestor's `id`. **Bucket label** = the root's title in **italics — not bold, not a link, and with no point/velocity number** (`_Aurie multi-tier pricing_`). Bold is reserved for the section headers (Completed / In Progress / Blocked); bucket sub-headers are italic so they read as a level below. The issue-number links on each line carry the navigation.
- **A mid-tree parent** (an issue that has children of its own but still has a parent above it) buckets under the root at the top of its chain — not under itself.
- **A root issue with no parent of its own** is its own root:
  - If other issues in the report roll up to it (it has children here), it heads its own named bucket — use the root's own title/link as the header; don't list the root itself as a separate line.
  - If it's a leaf (no children in the report) **and it's an experiment** — i.e. the title carries an experiment name like `` `ab:fooBar` `` / `` `abc:fooBar` `` — give it **its own heading named after the experiment** (the token after `ab:`/`abc:`, e.g. `fooBar`), not the raw card title. The heading is italic, not a link, like any other bucket sub-header.
  - If it's a leaf and not an experiment, it goes into a single shared bucket titled **Other** (no link).
- **Data design cards get a fixed `_Data Design_` bucket.** Every data design card (see "Data design cards" above) lands in one `_Data Design_` bucket per section, whatever its topmost parent — an experiment root (`abc:bookmarkShareRead`), a feature epic, or the `Data Design/Review [Cycle N]` tracker all fold into it. Pull these cards out before bucketing the rest by root, so they never appear under the experiment's heading.
- **Strip any `[Cycle N]` suffix from bucket header labels** — "Bugs [Cycle 30]" renders as "Bugs", "AI Native [Cycle 30]" as "AI Native". The cycle tag is noise in a standup heading.
- Buckets exist independently within each section — the same root can appear under Completed, In Progress, and Blocked if it has issues in each.

Order buckets within a section by **category tier first**, then by descending point subtotal within a tier (ties broken by issue count, then alphabetically by label) — the subtotal is used for sorting only and is never printed in the header:

1. **Experiments & features** — the `_Data Design_` bucket (always first in this tier, ahead of any data analysis bucket), product experiments (any bucket headed by an `ab:`/`abc:` experiment) and new user-facing capabilities (pricing/paywall/subscription work, new flows, new product surface).
2. **Bugs, support & tech quality** — the Bugs tracker, the Support queue, and tech-quality work (cleanup, refactors, experiment *removal*, infra, internal tooling/skills, docs).

The **Other** bucket always comes last, regardless of points. Classify a bucket by its root issue; when a bucket could fit either tier, prefer tier 1 only if it ships or tests something user-facing, otherwise tier 2. The point of the ordering is that the reader sees what moved the product before the keep-the-lights-on work.

Pull from Slack

Surface work done in Slack within the lookback window that isn't captured in a Linear card. Look for:

- Discussions in public channels that resulted in decisions, action items, or new work not captured in Linear
- Cross-functional conversations (CRM reviews, cost discussions, process proposals, support escalations)
- Threads where I was actively involved with external stakeholders (coaches, vendors, partners)

Skip: standup posts, Linear bot notifications, simple acknowledgments ("ok", "sg", "back"), and anything already covered by a Linear card.

**Source the activity by the best available path, in this order — each rung is optional; degrade to the next when it's absent:**

1. **User-token search via `shared:post-to-slack` — preferred.** Run the user-token `search.messages` by **invoking [`shared:post-to-slack`](../post-to-slack/SKILL.md)**'s `search` with `query=from:@<handle> after:<lookback_start>` (the resolved poster handle; `after:` takes `YYYY-MM-DD`). This is the only path that sees the user's **DMs and private channels** — `search.messages` runs *as the authorizing user* and is **user-token-only** (a bot token returns `not_allowed_token_type`, so it can't ride `SLACK_BOT_TOKEN`). The `SLACK_USER_TOKEN` is **personal and optional** (see `/CLAUDE.md` → token inventory); not every runner has one, so never hard-require it.
2. **Slack MCP search — when connected.** Use the Slack MCP search tool if it's available in the session. Ranked/capped results, no DM coverage, but no token to manage.
3. **Bot-token channel sweep via `shared:read-slack` — fallback.** List the channels the bot belongs to (`users.conversations`), then for each channel pull its history by **invoking [`shared:read-slack`](../read-slack/SKILL.md)** with `oldest` (and `latest`) set to the standup lookback window — pass the bounds so `history` doesn't paginate far past the lookback and waste time / hit rate limits — filtering to the poster's Slack id. To catch **thread replies under older parents** (which channel history won't return, since it keys on parent ts), widen the window and inspect each thread parent's `reply_users` + `latest_reply`, then use `shared:read-slack`'s thread (`replies`) fetch on the threads the poster replied in within the window. The slack skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback). Blind to channels the bot isn't a member of, and to DMs.
4. **Linear-only — last resort.** If none of the above is available, source from Linear alone and **note the gap** in the readout ("Slack activity not searched — no user token / MCP this run") rather than implying full coverage.

DMs are out of reach on any rung below #1 — if the user mentions non-Linear, non-public work (1:1 coaching, onboarding help) that no available path could see, ask them to supply it rather than silently omit.

Combine Linear + Slack

Linear cards are the primary source — every completed/in-progress/blocked card gets a line
Slack adds context to existing Linear items (e.g., audit details, root cause findings) — append to the same line, don't create a duplicate
Slack surfaces non-Linear work as new lines (e.g., vendor cost reviews, process proposals, ad-hoc support) — add these under the appropriate category without a Linear link
Combine related cards

Group related issues into a single line with a summary description. Examples: monthly payouts, sim skill updates, notification fixes, k8s infra work.

Plain language for Completed

The Completed section is read by people across the company — product, design, ops, leadership — not just engineers. Describe **what was done**, in language anyone can follow. The test: someone non-technical should understand the progress from the line alone.

- State plainly what was done. The *type* of work is fine to name — "refactored", "migrated", "fixed", "investigated" all describe real work and are fine when they're the clearest summary. The goal isn't to force a user-facing outcome onto every line; it's to make the line readable.
- Drop the deep technical detail that only an engineer on that code would parse: internal system/service names, code-level specifics, file/function names, acronyms. Say "moved the payout data to the new pipeline", not "migrated the payout aggregation query to the v2 GraphQL endpoint".
- Where there *is* a user-facing or business outcome, lead with it ("Coaches can now see their monthly payout breakdown") — but a line that's genuinely internal work (a refactor, a migration, an investigation) can just describe that work in plain terms.
- Translate, don't transcribe, the Linear title — the raw card title is often engineer-shorthand. Keep it to one clear sentence per line. Still end the line with the Linear card link(s) per "Link format" — the link carries the technical detail for anyone who wants to drill in.

This plain-language rule applies to **Completed only**. In Progress and Blocked can stay terser/closer to the card title, since they're status markers rather than a record of delivered progress.

Link format

Each line ends with Linear card numbers as clickable links in parentheses, using just the number portion of the ID as the link text. Slack format: (<url|number>, <url|number>)

**Use the issue's canonical `url` field from the Linear API as the link target** — the full `https://linear.app/<workspace>/issue/<ID>/<slug>` value each query returns. Do NOT hand-construct a bare `https://linear.app/<workspace>/issue/<ID>` URL by dropping the slug: those don't reliably resolve when clicked and produce dead links (observed 2026-06-11). Always fetch `url` in the issue field selection and pass it through verbatim.

Point totals

Each Linear card may have an estimate (points; treat a missing estimate as 0, and Slack-only items as 0). Completed-bucket estimates should already be verified per "Verify estimates on Completed cards" above by this point — missing ones filled and confirmed corrections applied before totals are computed.

- **Section headers DO show the total.** Sum the estimates of every card in the section and show it in square brackets after the section name: `*Completed [93]*`, `*In Progress [77]*`, `*Blocked [0]*`.
- **Bucket sub-headers do NOT show a number.** Compute each topmost-parent bucket's subtotal **only** to order buckets within the section by descending subtotal (see "Group by topmost parent"), but never print it — the sub-header is just `_<parent title>_`. The section total is still the sum of the (hidden) bucket subtotals.

Resolve poster Slack identity

The standup must be attributed to the user, not to a bot. Resolve the user's Slack identity (user id + display name + avatar) before posting:

1. **Read from AI Knowledge → Team.** The `Team` page (id `35075e23-af20-8155-bd68-f0b35263b362`) holds **several roster tables — one per function** (Leadership, Engineering, Design, Data & Analytics, Coach & Content Ops, Operations & HR, …), each with columns `Name | Slack | Slack ID | Role | Focus`. **Scan every `table` block on the page** — fetch all of the page's block children, then in each roster table look for the row whose `Slack` handle (or `Name`) matches the user (e.g. `@jerome`) and read its `Slack ID` cell — a Slack user id like `UEW6170R3`. The match can be in any table (engineers sit in the Engineering table, which is several tables down), so **do not stop at the first table** — that misses an already-populated id and falls through to the Slack lookup needlessly. **REST-first by necessity** — `notion-fetch` can silently truncate table-heavy pages without a marker, so read the page's blocks via the Notion REST API (`GET /blocks/<id>/children`, paginated, recursing into each `table` block) so every `table_row` comes back intact. Fall back to Notion MCP only when `NOTION_TOKEN` is unset, and re-validate the row count.
2. **Fall back to Slack when the `Slack ID` cell is empty.** Resolve via `SLACK_BOT_TOKEN` (needs the `users:read` scope on the app):
   - `users.list`, matching on `name` (the handle without `@`) or `profile.real_name`; or
   - `users.info?user=<candidate-id>` to confirm.
   (`users.lookupByEmail` needs the extra `users:read.email` scope — don't depend on it.)
3. **Write the resolved id back to the Team page** so future runs skip the Slack lookup, then invalidate the `Team` cache entry. (See "Storing the Slack ID" below — the Notion table API can't widen an existing table, so the column is maintained by recreating the Engineering table.)
4. From `users.info` grab `profile.image_512` (or `image_192`) — the avatar URL used below.

Post to #standup (channel CPTFXHNP2) as one thread

**Everything for one day's standup lives in one thread:** a single top-level main message, with every other post (the main message's overflow, then the In Progress reply) as replies under it. Nothing else goes top-level in #standup.

Post **as the user**, not as a generic bot:

- Preferred — genuine user (real name + avatar, no "APP" badge): **invoke [`shared:post-to-slack`](../post-to-slack/SKILL.md)** with **`--as-user`**. It posts as the authorizing user — MCP-first via the user's Slack connector when connected, else `SLACK_USER_TOKEN` (a user token on `chat.postMessage`). **Do not pass `--footer`** (see **No skill-written footer** below).
- Fallback (no user token / connector): **invoke [`shared:post-to-slack`](../post-to-slack/SKILL.md)** with `username` = the user's `real_name` (e.g. "Jerome Curlier") and `icon_url` = the avatar from identity step 4 (the `chat:write.customize` bot path) — same `--footer` rule (omit it). App posts always carry a small "APP" badge — unavoidable on the bot path, which is why `--as-user` is preferred.

**Suppress link previews.** Link previews MUST be off so the Linear card links don't expand into preview cards and clutter the channel. `shared:post-to-slack` defaults `unfurl_links/media:false` on both the `--as-user` and bot paths, so previews are suppressed automatically — no extra flags needed.

**No skill-written footer.** Unlike other Slack-posting skills, daily-status does NOT append the `Tools/Slack` Post-format footer. The Slack integration that posts on the user's behalf auto-appends a "Sent using @Claude" signature, so a skill-written footer produces a duplicated tail (observed: `Sent using @ClaudeSent using @Claude`). End the message body at the last content line — no `_Generated by ..._` footer, no trailing italic line, nothing.

Main message: Standup — [status date] with the Completed section (grouped into topmost-parent buckets, Other last) + Blocked section. The section header (Completed / Blocked) is bold and **shows the section point total** (`*Completed [93]*`); render each bucket as an italic sub-header (`_<parent title>_`, NOT bold, NOT a link, **no point number**, `[Cycle N]` stripped) with its lines underneath; the navigation comes from the issue-number links on each line.

**Skip the Blocked section entirely when nothing is blocked.** If the Blocked bucket has zero issues, omit the header and the section — do not post an empty `*Blocked [0]*` with a "None" line. Only render Blocked when there's at least one blocked card.

**Always pass `--thread-overflow` on the main message.** A main message over 4,000 characters is split by `shared:post-to-slack`, and without the flag chunks 2..N land as stray **top-level** messages in #standup. With it they post as replies under chunk 1, so they sit in the thread ahead of the In Progress reply. The split starts a chunk at a section header (`*Blocked [N]*`) or a bucket header where it can, and never ends one on a header. On 2026-09-25 a 4.4k main message split right after `*Blocked [10]*`, which left an empty-looking Blocked section in message 1 and its cards in a second top-level message. The Slack connector does not chunk, so **a main message over 4,000 characters goes through the script path** (`--as-user`, or the bot fallback) even when the connector is available.

Thread reply: In Progress section (bold header with the section point total, e.g. `*In Progress [77]*`), same topmost-parent bucketing — post via the **same path as the main message** (`--as-user`, or the `username`/`icon_url` bot fallback) by **invoking `shared:post-to-slack`**, using the main message's `ts` (the **first** chunk, which `post` returns as `ts`) as `thread_ts`. Post it only after the main message, overflow included, has landed. Any overflow of this reply stays in the same thread automatically.

Avoid double-counting across same-day standups

The lookback can overlap a standup the user already posted earlier the same day (e.g. a Tuesday-afternoon catch-up post, then a Wednesday-morning run). Before composing Completed, fetch the user's most recent prior standup in #standup and note its post time; drop any card already listed there (a card whose `completedAt` precedes that post) so it isn't reported twice. Start the effective Completed window at the prior standup's timestamp, not midnight.

Storing the Slack ID (Team page maintenance)

The Notion API cannot add a column to an existing `table` block (`table_width` is fixed at creation). To maintain the `Slack ID` column, the Engineering table is recreated: build a new `table` block one column wider with the same rows plus the `Slack ID` values, append it after the old table, verify, then delete the old table block. Seed known ids rather than re-resolving every run.
