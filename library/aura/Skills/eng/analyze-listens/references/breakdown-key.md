# Breakdown keys for `Meditation done single`

Confirmed with Yawar (Data Analyst) in `#engineering-mixpanel` on 2026-05-13. The event splits across four buckets using **two** Mixpanel properties — `Source App` (Aurie vs Aura) plus `Browser` (mobile vs web within Aura).

## Canonical bucketing rule

| `Source App` | `Browser` | Canonical bucket |
| --- | --- | --- |
| `aurie mobile` | * (any) | **Aurie (mobile)** |
| `aurie desktop` | * (any) | **Aurie (web)** |
| _not set_ | _not set_ | **Aura (mobile)** |
| _not set_ | _set_ (any value) | **Aura (web)** |

### Why two properties

- `Source App` was only added for the Aurie product. For Aura events it's null — the event came from "the Aura app" but the property isn't populated.
- Within the Aura bucket, `Browser` being set/unset is the canonical way to know mobile vs web. (`Browser` alone isn't enough across the board — it can't distinguish Aurie mobile from Aura mobile because both leave it unset.)

### Edge cases

- Any `Source App` value the formatter doesn't recognise (e.g. a hypothetical future `aurie tv`) routes to the **Other / unknown** bucket so nothing is silently dropped. When a new value appears, update the `BUCKET_RULES` table in `scripts/format_report.py` and add a row to the canonical table above.
- The Mixpanel string for "not set" in a breakdown is typically `$non_property_value` or `(not set)` — the formatter normalises both (plus a few other sentinels) to the canonical "not set" value before bucketing.

## Querying via the API (`$`-prefix gotcha)

The Mixpanel UI shows the second property as **Browser**, but that's the display name. In the Insights / Run-Query API the property is `$browser` — Mixpanel system properties (auto-collected by the SDKs) are stored under a `$`-prefixed raw name and the API requires the raw name. Passing `"Browser"` returns a query that succeeds but produces a single `undefined` row for the dimension (silently empty breakdown). Use `$browser` in API payloads; `Source App` is a custom property and keeps the same name in both the UI and the API.

The general rule, plus a lookup table for the most common `$`-prefixed system properties (`$browser`, `$browser_version`, `$os`, `$city`, `$country_code`, `$device`, `$referrer`, …), lives at [`Tools/Mixpanel`](https://www.notion.so/34c75e23af20817baddaf32213eba8d5) → "System property naming (`$`-prefix gotcha)". When in doubt, call `Get-Properties` with the display name to confirm the raw name before retrying the query.

## How this was discovered

1. Drafted a question in `#engineering-mixpanel` (per the routing rule — never DM individuals for taxonomy questions).
2. Yawar confirmed `Source App` is the right key for Aurie/Aura split and that Aura events leave it null.
3. Jerome added the `Browser` rule for mobile vs web within Aura, which Yawar confirmed.
4. Steve asked whether `Source App` reflects where the event was fired or where the user joined; Yawar confirmed: **where the event was fired**, but Aura events aren't tagged.

Source thread: [#engineering-mixpanel 2026-05-13](https://auramobile.slack.com/archives/C030BQ2H3QS/p1778697768929349).

## If the instrumentation changes

If a future Aura release starts tagging `Source App` (e.g. `aura mobile` / `aura web`), the bucketing logic still works — the formatter will route those values to "Other / unknown" until `BUCKET_RULES` is extended. At that point, replace the "not set" rows above with explicit `aura mobile` / `aura web` values and simplify the formatter accordingly.

## Cross-reference

This rule also lives at [`Tools/Mixpanel`](https://www.notion.so/34c75e23af20817baddaf32213eba8d5) → "App-surface bucketing" section, so any event split (not just `Meditation done single`) can reuse it.
