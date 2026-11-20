---
name: report-top-aura-coaches
description: >
  Condensed, Slack-ready top-N Aura coach earnings ranking — defaults to yesterday Pacific,
  fits on one screen, designed for a daily #eng-budget or #creator-economy post. Renders a
  headline (total listens, total payout, the top coach's earnings, share of payout and
  multiple of the average coach), a compact top-N table (rank, coach ID prefix, listens,
  earned, share, cumulative share), and an auto-detected outliers paragraph flagging
  concentration, power-law dominance, tier-mix bias or music-heavy days only when those
  patterns are real. No distribution stats or window-wide tier table — those are the analyze
  sibling's job. Earnings use the production payout-rate logic, so figures are real dollars.
  Trigger on "top aura coaches", "yesterday's biggest coach earners", "daily aura coach payout
  report", "report top aura coaches", "biggest aura coaches", "who earned the most yesterday",
  or "top earning coaches today". Do NOT use for full distribution analysis
  (`eng:analyze-aura-cost-per-coach`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/Aura API      # PaymentService endpoint reference + auth conventions
    - Tools/Slack         # channel directory + Post format footer spec for #eng-budget / #creator-economy post
    - Compliance/Privacy  # coach ID truncation rule in rendered output
---

# Report Top Aura Coaches

Condensed daily Slack-ready ranking of Aura coaches by earnings. Coach-side mirror of [`eng:report-top-aura-listeners`](../report-top-aura-listeners/SKILL.md); the "fast-take" companion to [`eng:analyze-aura-cost-per-coach`](../analyze-aura-cost-per-coach/SKILL.md).

Family siblings (cost skill family — share [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) PaymentService API):

| | **Deep dive** (`analyze-*`) | **Daily condensed** (`report-top-*`) |
|---|---|---|
| **Listeners** | [`analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md) | [`report-top-aura-listeners`](../report-top-aura-listeners/SKILL.md) |
| **Coaches** | [`analyze-aura-cost-per-coach`](../analyze-aura-cost-per-coach/SKILL.md) | **this skill** |

Plus the cross-cutting macro [`analyze-aura-cost`](../analyze-aura-cost/SKILL.md) (aggregate spend).

## Compliance

Coach IDs are **public-facing creator identifiers** but still treated as sensitive for consistency with the rest of the family. Rendered output truncates each coach ID to the first 8 chars + `…`; full IDs are preserved only in `--json` output.

The underlying PaymentService endpoint may include `givenName` / `familyName` / `email` joined from the `"user"` / `"coach"` tables. The formatter never prints those — `_safe_row()` whitelists only the 8 documented data fields. Slack-post target defaults to `#eng-budget` (`C05PC18APLH`, private — canonical ID in `Tools/Slack`) — never public channels without explicit user confirmation.

## Step 0 — Check prerequisites

- `AURA_API_TOKEN` must be set in `~/.claude/settings.json` under `env`. Admin scope on PaymentService.
- `python3` on the path (stdlib only).
- For Slack posts: the post is delegated to [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md), which owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) — no direct Slack prerequisite here; the destination is `#eng-budget` (`C05PC18APLH`, private — canonical ID in `Tools/Slack`).

## Step 1 — Resolve the time window

Default for daily report: `yesterday-pt`. Same UTC presets as the rest of the family are also available; this skill adds two PT-aligned presets:

- **`yesterday-pt`** (default) — Pacific calendar's previous day, returned as the corresponding UTC half-open range (DST-safe via zoneinfo)
- **`today-pt`** — Pacific calendar's today midnight → now
- **`today` / `wtd` / `mtd` / `prev-month` / `last-7d` / `last-30d`** — UTC presets shared with the rest of the family
- **Custom range** — explicit `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, half-open)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-aura-coaches/scripts/resolve_window.py --window yesterday-pt
# → {"from": "2026-05-18", "to": "2026-05-19", "from_iso": "2026-05-18T07:00:00Z", "to_iso": "2026-05-19T07:00:00Z", "label": "yesterday Pacific (2026-05-18)", "days_in": 1}
```

> **Sync note.** The `yesterday-pt` and `today-pt` modes live in this skill's copy of `resolve_window.py` only — the canonical copy in `eng:analyze-listens/scripts/resolve_window.py` doesn't have them yet. When you next touch the canonical (e.g. adding another window mode, fixing a bug), propagate these too so all sibling copies converge. Also normalized `from_iso` / `to_iso` to Z-suffix form (RFC 3339 canonical) — PaymentService's `parseISODate` accepts both `+00:00` and `Z` but the rest of the family was already sending `Z`, so this copy matches.

## Step 2 — Fetch the per-coach ranking

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-aura-coaches/scripts/call_aggregate.py \
  --since <from_iso> \
  --until <to_iso> \
  --group-by coachId \
  --limit 10 \
  --output /tmp/aura_top_coaches.json
```

`--limit` should match (or slightly exceed) the `--top` you'll pass to format_report.py so the renderer has enough rows. Default 10 is the standard daily report size.

See [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md#step-2--fetch-the-aggregate-from-paymentservice) for the full `call_aggregate.py` flag table — same script, same flags, only `--group-by coachId` is enforced for this skill.

## Step 3 — Format the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-aura-coaches/scripts/format_report.py \
  --input /tmp/aura_top_coaches.json \
  --label "<label from Step 1>"
```

Common variants:

```bash
# Compact: top-3 with no outliers paragraph
... format_report.py --input /tmp/aura_top_coaches.json --label "..." --top 3 --no-outliers

# Headline-suppressed (for compositions inside larger reports)
... format_report.py --input /tmp/aura_top_coaches.json --label "..." --no-headline

# Structured payload (full coach IDs)
... format_report.py --input /tmp/aura_top_coaches.json --label "..." \
    --json /tmp/aura_top_coaches_full.json
```

Flags:

| Flag | Default | Effect |
|---|---|---|
| `--top N` | 10 | Top-N cutoff |
| `--no-outliers` | off | Suppress the auto-detected outliers paragraph |
| `--no-headline` | off | Suppress the one-line headline |
| `--json PATH` | n/a | Write structured payload (full coach IDs) to disk |

## Step 4 — Show inline (and optionally post to Slack)

Render the markdown inline so the user sees it immediately. If the user wants to share it:

- Send the rendered output to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)**; that skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here. The condensed shape is designed to fit in a single Slack message — no truncation needed.
- For ad-hoc audiences other than `#eng-budget`, ask the user which channel before posting; don't default to public channels. Pass the chosen channel through to `shared:post-to-slack`.
- Post via `shared:post-to-slack` with `--footer eng:report-top-aura-coaches --footer-extra prod` — the helper stamps the versioned `Tools/Slack` footer (`_Generated by `eng:report-top-aura-coaches:<version>` · <YYYY-MM-DD> · prod_`) from `metadata.version` automatically. Don't hand-type the footer in the body.

The skill itself never auto-posts — Slack posting is an explicit follow-up step the model takes after the user confirms.

## What the report renders

1. **Headline (one line)** — total qualifying listens, total payout, distinct coaches; then a second line naming the top coach + their earnings + share-of-payout + Nx the average coach.
2. **Top-N table** — rank, short coach ID, listens, earned, share, cumulative share. Compact — no per-row tier breakdown (that's the deep-dive sibling's job).
3. **Outliers paragraph** *(when patterns fire)* — auto-detected:
   - Top-5 cumulative share ≥ 25% → "concentrated at the top"
   - Top coach ≥ 5× the average coach → "power-law tail"
   - Top coach's earnings ≥ 70% from a single non-music tier → "binge listening on a hit" / "concentrated in one popularity band"
   - Window-wide music share ≥ 10% of total payout → "high low-rate share"

Show the script's stdout verbatim. Don't paraphrase the numbers.

## JSON shape (`--json` output)

```json
{
  "window": { "label": "yesterday Pacific (2026-05-18)" },
  "summary": { "total_listens": ..., "total_cost_cents": ..., "groups": ..., "tier_breakdown": { ... }, "...": "..." },
  "top_coaches": [
    { "key": "<coachId>", "listen_count": ..., "cost_cents": ..., "music_listens": ..., "non_music_listens": ..., "tier_breakdown": {...}, "distinct_coaches": 1, "distinct_content": ... }
  ],
  "coaches_all": [ "... full enumerated list, same row shape as top_coaches ..." ]
}
```

Coach IDs in markdown are truncated to `first8…`; JSON keeps the full ID for downstream joins. `distinct_coaches` is always 1 in coach-side mode (each row = one coach) — kept in the shape for consistency with the listener-side skill.

## Per-listen $ logic

Same as the rest of the family — see [`eng:analyze-aura-cost-per-coach`](../analyze-aura-cost-per-coach/SKILL.md#per-listen--logic-from-paymentservice) for the full table. Summary: tiered rates `lt500=6¢ / lt2000=10¢ / lt5000=13¢ / other=17¢ / music=5¢`, with daily-cap (6 per user-coach pair) and premium-paid-only exclusions.

## Notes & edge cases

- **Same numbers as the deep-dive sibling.** This skill and `analyze-aura-cost-per-coach` hit the same endpoint with the same `groupBy=coachId`. The data is identical; only the rendering differs. Cross-check across the family: total listens / total payout for a given window will always match `analyze-aura-cost` (aggregate) and the listener-side ranking skills for the same time bounds.
- **Yesterday PT vs yesterday UTC.** `yesterday-pt` is the Pacific calendar day (07:00 → next-day 07:00 UTC during PDT; 08:00 → next-day 08:00 during PST). It will produce slightly different counts than UTC `yesterday` because the day boundary is offset — that's correct, just call out which boundary you're reporting on if the consumer asks.
- **Empty windows** render `No qualifying listens in this window.` and exit 0.
- **Outliers paragraph is silent on normal days.** If no patterns fire, the section is dropped entirely — no false alarms.
- **Cowork sandboxes** strip host env (PROD-1305). Paste-export `AURA_API_TOKEN` per session per CLAUDE.md → "Cowork constraints → Workaround".

## References

- Endpoint: [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) — `POST /payouts/listens/aggregate`.
- This card: [PROD-1999](https://linear.app/aura-health/issue/PROD-1999).
- Family parent: [PROD-1928](https://linear.app/aura-health/issue/PROD-1928).
- Pattern template: [`eng:report-top-conversation-users`](../report-top-conversation-users/SKILL.md) — same daily-Slack-ready shape for OpenAI + ElevenLabs.
- PaymentService home: `~/Development/Aura/Services/PaymentService`.
