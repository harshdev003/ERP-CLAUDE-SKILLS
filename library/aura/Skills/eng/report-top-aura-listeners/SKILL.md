---
name: report-top-aura-listeners
description: >
  Condensed, Slack-ready top-N Aura listener cost ranking — defaults to yesterday Pacific,
  fits on one screen, designed for a daily #eng-budget post. Renders a headline (total
  listens, coach payout, the top listener's cost, share of spend and multiple of the
  population mean), a compact top-N table (rank, UID prefix, listens, cost, share, cumulative
  share), and an auto-detected outliers paragraph flagging concentration, power-law dominance,
  binge-listening or music-heavy days only when those patterns are real. No distribution stats
  or window-wide tier table — those are the analyze sibling's job. Costs use the production
  payout-rate logic, so figures are real dollars. Listener-side mirror of
  `report-top-aura-coaches`. Trigger on "top aura listeners", "yesterday's biggest spenders",
  "daily aura cost report", "report top aura listeners", "biggest aura users", "who cost us
  the most yesterday", or "top costly listeners today". Do NOT use for full distribution
  analysis (`eng:analyze-aura-cost-per-user`).

metadata:
  version: "3.1.0"
knowledge:
  reads:
    - Guide/Index         # baseline routing aid (universal)
    - Tools/Aura API      # PaymentService endpoint reference + auth conventions
    - Tools/Slack         # channel directory + Post format footer spec for #eng-budget post
    - Compliance/Privacy  # UID truncation rule in rendered output; UIDs are PII-adjacent
---

# Report Top Aura Listeners

Condensed daily Slack-ready ranking of Aura listeners by cost (= coach payout they drove). Listener-side mirror of [`eng:report-top-aura-coaches`](../report-top-aura-coaches/SKILL.md); the "fast-take" companion to [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md).

Family siblings (cost skill family — share [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) PaymentService API):

| | **Deep dive** (`analyze-*`) | **Daily condensed** (`report-top-*`) |
|---|---|---|
| **Listeners** | [`analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md) | **this skill** |
| **Coaches** | [`analyze-aura-cost-per-coach`](../analyze-aura-cost-per-coach/SKILL.md) | [`report-top-aura-coaches`](../report-top-aura-coaches/SKILL.md) |

Plus the cross-cutting macro [`analyze-aura-cost`](../analyze-aura-cost/SKILL.md) (aggregate spend).

## Compliance

This skill reads Firebase UIDs (PII-adjacent — they're not emails but they're user-identifiable in the Aura DB). Per [`Compliance/Privacy`](https://www.notion.so/34a75e23af20813a943fdb6f389b8e6e), rendered output truncates each UID to the first 8 chars + `…`. Full UIDs are preserved in `--json` output for downstream joins; that file should never be shared publicly.

The underlying PaymentService endpoint may include `givenName` / `familyName` / `email` joined from the `"user"` table. The formatter never prints those — `_safe_row()` whitelists only the 8 documented data fields. Slack-post target defaults to `#eng-budget` (`C05PC18APLH`, private — canonical ID in `Tools/Slack`) — **never public channels without explicit user confirmation**.

## Step 0 — Check prerequisites

- `AURA_API_TOKEN` must be set in `~/.claude/settings.json` under `env`. Admin scope on PaymentService.
- `python3` on the path (stdlib only).
- For Slack posts: the post is delegated to [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md), which owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) — no direct Slack prerequisite here; the destination is `#eng-budget` (`C05PC18APLH`, private — canonical ID in `Tools/Slack`).

## Step 1 — Resolve the time window

Default for daily report: `yesterday-pt`. Same UTC presets as the rest of the family are available, plus the two PT-aligned presets:

- **`yesterday-pt`** (default) — Pacific calendar's previous day, returned as the corresponding UTC half-open range (DST-safe via `zoneinfo`)
- **`today-pt`** — Pacific calendar's today midnight → now
- **`today` / `wtd` / `mtd` / `prev-month` / `last-7d` / `last-30d`** — UTC presets shared with the family
- **Custom range** — explicit `--from YYYY-MM-DD --to YYYY-MM-DD` (UTC, half-open)

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-aura-listeners/scripts/resolve_window.py --window yesterday-pt
# → {"from": "2026-05-18", "to": "2026-05-19", "from_iso": "2026-05-18T07:00:00Z", "to_iso": "2026-05-19T07:00:00Z", "label": "yesterday Pacific (2026-05-18)", "days_in": 1}
```

> **Sync note.** The `yesterday-pt` and `today-pt` modes live in this skill's copy of `resolve_window.py` only — the canonical copy in `eng:analyze-listens/scripts/resolve_window.py` doesn't have them yet (same status as the coach-side mirror in `report-top-aura-coaches`). When you next touch the canonical, propagate these. Z-suffix normalization on `from_iso` / `to_iso` was also added in this generation of skills — PaymentService accepts both `+00:00` and `Z` but the family uses `Z`.

## Step 2 — Fetch the per-listener ranking

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-aura-listeners/scripts/call_aggregate.py \
  --since <from_iso> \
  --until <to_iso> \
  --group-by userId \
  --limit 10 \
  --output /tmp/aura_top_listeners.json
```

`--limit` should match (or slightly exceed) the `--top` you'll pass to format_report.py. Default 10 is the standard daily report size.

See [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md#step-2--fetch-the-aggregate-from-paymentservice) for the full `call_aggregate.py` flag table — same script, same flags, only `--group-by userId` is enforced for this skill.

## Step 3 — Format the report

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/report-top-aura-listeners/scripts/format_report.py \
  --input /tmp/aura_top_listeners.json \
  --label "<label from Step 1>"
```

Common variants:

```bash
# Compact: top-3, no outliers paragraph
... format_report.py --input /tmp/aura_top_listeners.json --label "..." --top 3 --no-outliers

# Headline-suppressed (for compositions inside larger reports)
... format_report.py --input /tmp/aura_top_listeners.json --label "..." --no-headline

# Structured payload (full UIDs)
... format_report.py --input /tmp/aura_top_listeners.json --label "..." \
    --json /tmp/aura_top_listeners_full.json
```

Flags:

| Flag | Default | Effect |
|---|---|---|
| `--top N` | 10 | Top-N cutoff |
| `--no-outliers` | off | Suppress the auto-detected outliers paragraph |
| `--no-headline` | off | Suppress the one-line headline |
| `--json PATH` | n/a | Write structured payload (full UIDs) to disk |

## Step 4 — Show inline (and optionally post to Slack)

Render the markdown inline. If the user wants to share:

- Send the rendered output to `#eng-budget` (`C05PC18APLH`, canonical ID in `Tools/Slack`) by **invoking [`shared:post-to-slack`](../../../shared/skills/post-to-slack/SKILL.md)**; that skill owns the connection (MCP-first, `SLACK_BOT_TOKEN` script fallback) and the unfurl-off default — don't call Slack directly here. The condensed shape fits in a single Slack message.
- For ad-hoc audiences other than `#eng-budget`, ask the user which channel before posting; **never default to public channels** — the report contains UIDs that haven't been agreed for public sharing. Pass the chosen channel through to `shared:post-to-slack`.
- Post via `shared:post-to-slack` with `--footer eng:report-top-aura-listeners --footer-extra prod` — the helper stamps the versioned `Tools/Slack` footer (`_Generated by `eng:report-top-aura-listeners:<version>` · <YYYY-MM-DD> · prod_`) from `metadata.version` automatically. Don't hand-type the footer in the body.

The skill itself never auto-posts — Slack posting is an explicit follow-up step the model takes after the user confirms.

## What the report renders

1. **Headline (one line + a second line)** — total qualifying listens, coach payout, distinct users; then the top listener + their listens + cost + share-of-spend + Nx the population mean.
2. **Top-N table** — rank, short UID, listens, cost, share, cumulative share. Compact — no per-row tier breakdown.
3. **Outliers paragraph** *(when patterns fire)* — auto-detected:
   - Top-5 cumulative share ≥ 1% → "Top 5 listeners account for X% of total spend."
   - Top listener ≥ 10× the population mean → "Top listener cost Nx the average user — power-law tail."
   - Top listener's spend ≥ 70% from a single non-music tier → "binging a hit" or "concentrated in one popularity band"
   - Window-wide music share ≥ 10% of total payout → "high low-rate share"

Thresholds are tuned for the **listener distribution**, which is much steeper than the coach distribution: ~17× more users than coaches, so top-5 share is naturally smaller and the power-law multiplier is larger. (Coach-side thresholds in [`report-top-aura-coaches`](../report-top-aura-coaches/SKILL.md) are 25% / 5× respectively.)

## JSON shape (`--json` output)

```json
{
  "window": { "label": "yesterday Pacific (2026-05-18)" },
  "summary": { "total_listens": ..., "total_cost_cents": ..., "groups": ..., "tier_breakdown": { ... }, "...": "..." },
  "top_users": [
    { "key": "<uid>", "listen_count": ..., "cost_cents": ..., "music_listens": ..., "non_music_listens": ..., "tier_breakdown": {...}, "distinct_coaches": ..., "distinct_content": ... }
  ],
  "users_all": [ "... full enumerated list, same row shape as top_users ..." ]
}
```

UIDs in markdown are truncated to `first8…`; JSON keeps the full UID for downstream joins.

## Per-listen $ logic

Same as the rest of the family — see [`eng:analyze-aura-cost-per-user`](../analyze-aura-cost-per-user/SKILL.md#per-listen--logic-from-paymentservice) for the full table. Summary: tiered rates `lt500=6¢ / lt2000=10¢ / lt5000=13¢ / other=17¢ / music=5¢`, with daily-cap (6 per user-coach pair) and premium-paid-only exclusions.

## Notes & edge cases

- **Same numbers as the deep-dive sibling and the cross-cutting macro.** This skill, `analyze-aura-cost-per-user`, `analyze-aura-cost`, and `analyze-aura-cost-per-coach` all hit the same endpoint with the same window bounds. Total listens / total payout will always match across the family for a given window.
- **Yesterday PT vs yesterday UTC.** `yesterday-pt` is the Pacific calendar day (07:00 → next-day 07:00 UTC during PDT; 08:00 → next-day 08:00 during PST). Slightly different counts than UTC `yesterday` because the day boundary is offset.
- **Empty windows** render `No qualifying listens in this window.` and exit 0.
- **Outliers paragraph is silent on normal days.** If no patterns fire, the section is dropped entirely — no false alarms.
- **No AI premium tier annotation in v1.** The sibling `analyze-openai-cost-per-user` annotates top-N with the user's last-seen AI premium tier via Aura PaymentService `POST /subscriptions/list/:userId`. Worth adding to this skill as v2 — particularly "how many top spenders are Premium Free" is a useful signal. Out of scope for v1.
- **Cowork sandboxes** strip host env (PROD-1305). Paste-export `AURA_API_TOKEN` per session per CLAUDE.md → "Cowork constraints → Workaround".

## References

- Endpoint: [PROD-1961](https://linear.app/aura-health/issue/PROD-1961) — `POST /payouts/listens/aggregate`.
- This card: [PROD-1975](https://linear.app/aura-health/issue/PROD-1975).
- Family parent: [PROD-1928](https://linear.app/aura-health/issue/PROD-1928).
- Pattern template: [`eng:report-top-conversation-users`](../report-top-conversation-users/SKILL.md) — same daily-Slack-ready shape for OpenAI + ElevenLabs.
- Coach-side mirror: [`eng:report-top-aura-coaches`](../report-top-aura-coaches/SKILL.md).
- PaymentService home: `~/Development/Aura/Services/PaymentService`.
