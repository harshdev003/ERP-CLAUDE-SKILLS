# Sources and queries

The queries are IntelligenceService's own, from its 2026-11-05 onset analysis (ENGBACK-2365, ENGBACK-2318,
ENGBACK-2277). All of them return counts, never a reply's text or a uid.

## Reply length: BigQuery `aura-main.llm.llm_call`

The table is partitioned on `createdAt`, and the filter must be a literal range. `prompterName =
'coach-prompter'`; `platform = 'livekit'` is voice, `mobile` is chat. `model` stores only the alias
requested (`chat-latest`), not the snapshot served, so a model change can't be read here. chat-prompter has
single-digit daily volume and companion-prompter none, so only the coach is covered.

```sql
SELECT DATE(createdAt) d, IF(platform='livekit','voice','chat') mode, COUNT(*) n,
  APPROX_QUANTILES(outputTokens,100)[OFFSET(50)] out_med,
  APPROX_QUANTILES(outputTokens,100)[OFFSET(90)] out_p90
FROM `aura-main.llm.llm_call`
WHERE createdAt >= TIMESTAMP('<start>') AND createdAt < TIMESTAMP('<end>')
  AND prompterName='coach-prompter'
GROUP BY 1,2
```

About 16 MB scanned for 8 days. GCP_KEY holds dataViewer + jobUser in aura-main.

## Emoji share: BigQuery `aura-main.llm.llm_call`

IntelligenceService stores `emojiCount`, `replyChars` and `userChars` on every coach, chat and companion
row since 2026-09-25 (ENGBACK-2367/2368). They're null only where no reply was stored: incognito or a
failed call. A retry writes a second row for the same turn, so count the last row of each dialogue:

```sql
SELECT d, COUNT(*) n, ROUND(100 * COUNTIF(e > 0) / COUNT(*), 1) pct FROM (
  SELECT FORMAT_DATE('%Y-%m-%d', DATE(createdAt)) d, emojiCount e
  FROM `aura-main.llm.llm_call`
  WHERE createdAt >= TIMESTAMP('<start>') AND createdAt < TIMESTAMP('<end>')
    AND prompterName = 'coach-prompter' AND replyChars IS NOT NULL
  QUALIFY ROW_NUMBER() OVER (PARTITION BY dialogueId ORDER BY createdAt DESC) = 1)
GROUP BY d
```

Checked against the Postgres share for 2026-09-25 to 11-05: the largest difference is 0.2 points
(10-07 25.8%, 10-08 55.5%, 11-04 18.8% in both).

**Fallback, IntelligenceService Postgres `intelligenceDB`,** for a day BigQuery has no `emojiCount` for,
and only where `INTELLIGENCE_DB_URL` (a read-only connection) is set. It is read-only
(`PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=280000'`), one day per query, and
never selects `message`, only the share:

```sql
SELECT count(*) n,
  round(100.0*count(*) FILTER (WHERE message ~ '[\U0001F300-\U0001FAFF☀-➿]')/count(*),1) pct_emoji
FROM dialogue
WHERE "createdAt" >= '<d>' AND "createdAt" < '<d+1>'
  AND role='assistant' AND prompt->>'name'='coach-prompter'
```

## Incomplete, double-failure and empty replies: Cloud Logging, `firebase-auratech16`

Container `intelligence-service`, one count per UTC day. These lines exist only since about 2026-10-28
(#734 ENGBACK-2042, then #811), so a 0 before that means not logged, not none.

- **Incomplete:** `jsonPayload.reason="max_output_tokens"` and `jsonPayload.attempt=1`. That covers both
  "Retrying an incomplete response with no output" and "OpenAI response incomplete" (an incomplete that
  already had output, so it wasn't retried).
- **Double failure, which the user sees:** `jsonPayload.reason="max_output_tokens"` and
  `jsonPayload.attempt>=2`. A second voice retry (ENGBACK-2366) can reach attempt 3, which this counts.
- **Empty completed reply:** `jsonPayload.message` is "Retrying an empty completed response" or "OpenAI
  response completed empty". `llm_call` holds almost no `outputTokens = 0` rows, so it can't be used
  for this.

Live check, 2026-11-04 and 11-05: incomplete 253 and 253, double 1 and 2, empty 0 and 4. That matches
IntelligenceService's own counts.

## Our own changes in the window

From GitHub: the IntelligenceService tags dated in the window, and default-branch commits whose headline
names the coach (prompt changes under `src/prompters/coach*`). A step with none of these in the window is
likely drift in `chat-latest`, which the coach runs on and which can't be pinned.

## History the thresholds were checked against

| Day | What happened | Daily flag |
|---|---|---|
| 2026-10-07 | Emoji step from about 20Z (not ours: coach.txt unchanged) | 10-08: emoji ~55% vs ~17% |
| 2026-11-03 | Length step from about 05Z, v1.46.7 still serving | 11-03: chat median +14%, voice +15% |
| 2026-11-04 | Emoji drop from our PROD-8232 (v1.47.0, 11-03 20:41Z); incomplete onset 11-03 23:47Z | 11-04: emoji, incompletes, a double failure |

The 10-07 emoji step started late that day, so a daily rollup flags it the next day. Intraday detection
would need an hourly series; that isn't part of this check.
