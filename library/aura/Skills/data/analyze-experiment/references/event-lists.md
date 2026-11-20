# Mixpanel event catalog & query mechanics

Reference material for the `data:analyze-experiment` skill: the per-app Mixpanel event names, the Tier-2 REST segmentation template, the per-experiment insights query template, canonical IDs, and the variant/notes gotchas. The skill's Step 5 / Step 5.5 workflow points here.

## Enrich-mode pre-populated row fields (Step 1c)

For each resolved `Learnings/Experiments` row, extract the **pre-populated context** — things we don't need to re-fetch because the row already has them from bulk import:

- `Title` → experiment name (use for Mixpanel filter + Slack search + Linear lookup)
- `Variants` → human-readable variant breakdown (skips the need to parse Description from Slack create-message)
- `Start Date` / `End Date` → Mixpanel query date range (skips the `#support-bot` create-message lookup entirely)
- `Experiment Type` / `App` / `Platform` → metadata used in the final readout framing
- `Hypothesis` → if populated, skip Linear ticket lookup for hypothesis; if blank, still fetch Linear
- `Status` / body `## Learning` — helpful prior context for judging whether re-analysis is warranted

Capture the Notion `page_id` per row — Step 8 option 4b needs it for the update call.

## Mixpanel access ladder (Step 5)

The Mixpanel MCP tools (`Run-Query`, `Get-Events`, etc.) are the preferred
path but **aren't always reachable** — they're absent from Cowork sandboxes
(MCP servers don't forward into `mcp__workspace__bash` per
[PROD-1305](https://linear.app/aura-health/issue/PROD-1305)) and occasionally
unloaded from host sessions. Fall through to the REST API when MCP is
unavailable:

**Tier 1 — Mixpanel MCP (preferred when loaded).** Use the `Run-Query` insights template below for full filter/breakdown control, and `Get-Events` for lexicon lookups during the Step 5.5 over-stamping check.

**Tier 2 — Mixpanel REST `/api/2.0/segmentation` (API-token fallback).** See "Tier-2 Mixpanel REST segmentation — worked template" below.

**Tier 3 — Mixpanel JQL** (`/api/2.0/jql`) — fallback when segmentation can't express the query (e.g. ratio-of-uniques requiring a join). Heavier; reach for it only when Tier 2 isn't enough.

The Tier-2 segmentation path is the one used in the 2026-05-29 sweep that
analysed 27 experiments in parallel (~10s wall-clock for all per-variant
counts across SUL/Trial/CSS/Paid).

## Assigned vs `N` — the population count (Step 5)

**`N` is a denominator, not the population.** Step 6 sets `N` = the `SUL` (sign-up/login)
unique-user count from the Step 5 insights query, so it counts only assigned users who
signed in **inside the window**. An assigned user who was quiet is invisible to it. Every
rate divides by `N`, so reporting `N` as the population hides how much of the population
those rates never covered.

`/api/2.0/engage` counts **profiles**, not events, so it answers "how many users carry this
experiment property at all?" — which is the population.

### `N` is already deterministic — do not invent a union

The Step 5 query returns a **separate** unique count per metered event (`SUL`, Trial, Paid,
Session). There is no single "fired any of them" scalar in that response, and you should not
try to synthesise one:

- **A union is not the denominator.** `SUL` is the funnel entry point; Trial / Paid / Session
  are downstream of it, so they are **numerators measured against `SUL`** (that is exactly how
  Step 6 computes every rate). Unioning them would put part of the numerator into the
  denominator and flatten the rates it is meant to measure.
- **A union is not cheaply computable anyway.** Uniques do not add — overlapping sets need a
  JQL join (Tier 3) or a funnel query, not arithmetic on the Step 5 output.

So the comparison is **`assigned` vs `N`**, both single scalars, both already produced. Nothing
here needs a new query beyond the engage call below.

### Three denominators, kept apart on purpose

| Term | Means | Comes from |
| -- | -- | -- |
| `assigned` | carries the experiment property at all, ever — **see the tense warning below before using this on an ended experiment** | `/api/2.0/engage` profile count (below) |
| `N` | signed in (`SUL`) inside the window | the Step 5 insights `Run-Query` |
| **exposed** | reached the **experimental surface** | Step 5.5's event-level exposure check |

`assigned` ≥ `N` and `assigned` ≥ `exposed`. `N` and `exposed` are **not** ordered with respect
to each other and are not interchangeable — a user can sign in all week without ever rendering
the surface under test. Step 5.5 owns *exposed*; this section owns *assigned* and `N`.

Do not label `N` "active": a user quiet on `SUL` may have used the app daily on events this
query does not meter.

### "Ever" is the wrong tense for an ended experiment

**An ended experiment does not stop stamping users.** `endedAt` records that the
readout happened; it does not stop the flag resolving, and after a rollout there
is only one variant left to stamp. So the profile population keeps growing, all
into the surviving arm, indefinitely.

Measured 2026-09-18, months after each ended:

| experiment | ended | real population (its own end-note) | engage returns | ratio |
|---|---|---:|---:|---:|
| `aurieEnvironment2` | 2026-06-02 | 279 | **1,623** | 5.8× |
| `pricingNewMoneyBackGuaranteeCard` | 2026-05-01 | 57 | **580** | 10× |
| `pricingHighlightPaymentInput` | 2026-05-02 | 70 | **580** | 8.3× |

`assigned >= N` still holds, so nothing above fires. The failure is that the
**gap** — the quantity this section tells you to investigate — becomes an
artefact. On `aurieEnvironment2` the recipe reports `N` covering 16% of the
population and 1,344 users unaccounted for, and then directs you to widen the
metered event list to find them. They do not exist; they were assigned after the
experiment was over.

**So, for an experiment with `endedAt` set:**

- Prefer the contemporaneous **`[N users]` figure from the `#data-experiment`
  end-note**. It is measured at the time and cannot drift.
- If you must query engage, **bound it by the experiment window** — filter on a
  profile timestamp inside `createdAt`…`endedAt` — or report it as *"property
  holders today"*, which is a different quantity from the population and must
  not be differenced against `N`.
- Never report an unexplained coverage gap for an ended experiment without first
  checking this. It is the most likely explanation and it is not about coverage.

**The invariant that catches it: for an ended experiment, `assigned` is a
historical fact and must be stable.** Run the query twice a month apart; if the
number grew, it is not measuring the experiment's population. A count that
changes after the thing it counts has finished is measuring something else.

Root cause and the service-side fix: [PROD-6196](https://linear.app/aura-health/issue/PROD-6196).

### The recipe

```bash
curl -sS -u "$MIXPANEL_SERVICE_ACCOUNT_USERNAME:$MIXPANEL_SERVICE_ACCOUNT_SECRET" \
  -d project_id="$MIXPANEL_PROJECT_ID" \
  --data-urlencode 'where=properties["Experiment: <name>"] == "a"' \
  --data-urlencode 'output_properties=["UserID","$last_seen"]' \
  https://mixpanel.com/api/2.0/engage
```

- **`total`** is the assigned profile count for that variant. Repeat per variant.
- **Cross-check** with `where=defined (properties["Experiment: <name>"])` for the all-variants
  total; the per-variant totals must sum to it. If they don't, there is a variant value you
  didn't enumerate (a multi-arm experiment, or a stray value).
- Same service-account Basic auth as the Tier-2 segmentation template — no new credential.
- The project id env var is **`MIXPANEL_PROJECT_ID`** (not `MIXPANEL_SERVICE_ACCOUNT_PROJECT_ID`;
  the CLAUDE.md shorthand `_PROJECT_ID` is a suffix on the prose, not the real name).
- `output_properties` returns the UIDs, which feed straight into the Step 5.6 internal-account
  screen (`scripts/screen_internal.py`) and any per-user join.

**Paginate before trusting the UID list.** `page_size` is 1000 and the first response is
`page: 0`; for a larger arm, re-POST with the returned `session_id` and `page: N+1` until you
have `total` rows. `total` is correct on the first call — the *UID list* is what truncates,
silently, which matters because it is the input to the internal screen.

### Reading the gap

`assigned − N` has **three** components, and `$last_seen` separates only the first from the
other two:

1. internal / QA accounts — Step 5.6 screens these,
2. users assigned so recently their events have not landed — recent `$last_seen`,
3. users active on events this query does not meter — **also** a recent `$last_seen`.

So a recent `$last_seen` does not prove write-lag. Do not report (3) as idleness; if that
bucket looks large, widen the metered event list rather than explaining the gap away.

**Worked gap.** `aiResponseAPI`, 2026-08-04, same project and window: the insights
`resource: user` breakdown returned **16**; engage returned **23** assigned (a=14, b=9). The
7-user gap was 3 internal/QA accounts plus 5 users assigned within the previous hour whose
session events hadn't landed. Because the gap was invisible, the first readout also
mis-estimated the accrual rate ~4× (~15/day vs ~60/day), which moved the power forecast from
"hopeless" to "borderline" for a Monday decision. This is a recurring class of error, not a
one-off.

**There is no Aura-backend path for this.** Assignment is write-only via
`POST /experiments/<name>/assign/<uid>`; every read endpoint probed returns 404
(`/assignments`, `/stats`, `/count`, `/users`). Mixpanel is the only source.

## Tier-2 Mixpanel REST segmentation — worked template

Uses HTTP Basic auth with the service-account creds (`MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `MIXPANEL_SERVICE_ACCOUNT_SECRET`). One call per event, but trivially parallelisable across events and experiments. Returns daily unique-user counts broken down by the property value.

**Two breakdown levels, for two different jobs. Pick deliberately.** They are one
parameter apart and the wrong one does not fail — it returns a well-formed number
against a population it never covered.

| `level` | `on` | Answers | Used by |
|---|---|---|---|
| `"user"` | `user["Experiment: X"]` | *Which arm was this user in?* Attributes every event to the arm the user was **assigned** to, whenever the stamp landed. | **Step 5** headline counts, **Step 6** rates — anything with `N` underneath it |
| `"event"` | `properties["Experiment: X"]` | *Did this event physically carry the variant when it fired?* | **Step 5.5** over-stamping check only |

Matching the Tier-1 `Run-Query` template, whose breakdown is `resource: user`.

```python
import base64, urllib.parse, urllib.request, ssl, certifi, json, os
auth = base64.b64encode(
    f"{os.environ['MIXPANEL_SERVICE_ACCOUNT_USERNAME']}:"
    f"{os.environ['MIXPANEL_SERVICE_ACCOUNT_SECRET']}".encode()
).decode()
ctx = ssl.create_default_context(cafile=certifi.where())

def per_variant_unique(event: str, experiment: str, from_d: str, to_d: str,
                       level: str = "user") -> dict:
    """{variant: unique-user-count} for an event, broken down by the experiment property.

    `level="user"` for every headline count (Step 5/6). `level="event"` ONLY for
    the Step 5.5 over-stamping check — see the table above.
    """
    prop = f"Experiment: {experiment}"
    on = f'user["{prop}"]' if level == "user" else f'properties["{prop}"]'
    params = {
        "project_id": "963821",
        "event": event,
        "from_date": from_d,                                       # "YYYY-MM-DD"
        "to_date": to_d,                                           # "YYYY-MM-DD"
        "on": on,                                                  # breakdown — see table
        "where": f'(defined (user["{prop}"]))',                     # filter: property exists
        "type": "unique",
        "unit": "day",
    }
    url = "https://mixpanel.com/api/2.0/segmentation/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
        data = json.loads(r.read())
    values = (data.get("data") or {}).get("values") or {}
    return {variant: sum(daily_counts.values()) for variant, daily_counts in values.items()}

# Per-app event list
AURIE_EVENTS = ["AI Sign Up Login", "AI Subscription Purchased", "AI Subscription Paid", "AI Conversation Session Started"]
AURA_EVENTS  = ["Sign up login", "Subscription Trial Started", "Subscription Paid"]
```

Returns `{variant_value: count}` — e.g. `{'a': 963, 'b': 902}`.

### The `undefined` bucket is a reading of the breakdown level, not of the data

At `level="event"` an event lands in `undefined` when its payload carried no
variant **at firing time**. For any event that fires *before* assignment that is
not an anomaly, it is the whole population — so the size of the bucket tells you
which level you are on, not how healthy the data is.

Measured on `hideIncognitoButton` (2026-04-02 → 2026-05-29), same window, same filter:

| `AI Sign Up Login` | a | b | undefined | total |
|---|---|---|---|---|
| `level="event"` | 0 | 1 | **1,864** | 1,865 |
| `level="user"` | **963** | **902** | — | 1,865 |

Same 1,865 users either way. Sign-up fires before the experiment stamps the
payload, so at event level the entire population is unattributed.

**The invariant: if `undefined` holds most of `N`, the breakdown level is wrong.**
It does not mean the data is missing, and it is not a small over-stamping
anomaly. Step 6 divides every rate by the per-variant `N`, so on the event-level
cut those rates divide by what little is left — `hideIncognitoButton` yields a
**Trial Start Rate of 3,300%** for variant b, `historyTooltip` 1,400%. Nothing
errors. A denominator that lands at 40 instead of 1,865 produces percentages that
look perfectly ordinary, which is the case this note exists to prevent.

So: **a user-proportion rate above 100% is a denominator bug, not a finding** —
stop and check the breakdown level before reading the number as a result.

The guard is on rates whose numerator counts *users*, so the value cannot exceed
the population: `Trial Start Rate`, `Paid Rate`. It does **not** apply to
`Session Rate`, which is merged conversations over `N` — a mean per user, not a
proportion of them, and routinely above 1 on Aurie (`hideIncognitoButton`: 2.71).
Reading that as broken would reject Aurie's own primary metric.

Rate-limit: ~60 requests/min per project. Batch across events with a `ThreadPoolExecutor` capped at ~8 workers; retry 429s with 2–5s backoff. A 31-experiment sweep is ~110 calls, which exceeds that in a burst — cap at 3 workers with 8s+ backoff, or it 429s partway and the failures land as zeroes.

## Per-app event config

| Metric alias | Aurie event name | Aura event name | Notes |
|---|---|---|---|
| `SUL` (sign-up/login — primary N) | `AI Sign Up Login` | `Sign up login` | Aura's "Sign up login" is the canonical registration completion event, equivalent to Aurie's SUL. |
| `Trial Start` | `AI Subscription Purchased` | `Subscription Trial Started` | First subscription trial. |
| `Paid` | `AI Subscription Paid` | `Subscription Paid` | First paid billing event. |
| `Session / Engagement` | `AI Conversation Session Started` | *(see note)* | Covers **text chats as well as voice** — filter `Current Conversation Mode == "Voice Mode"` for a voice metric. Fires again on reconnect, so merge starts under 5 minutes apart before counting, then drop husks with no backend turn (all three below). **Aura has no direct conversation-session equivalent.** Aura is a content platform, not a conversation one. Use `Subscription Trial Started` as the primary engagement proxy, or note the gap and report only SUL + Trial + Paid columns. Do not force a session metric into the Aura table — it will mislead. |

### Counting conversations, not session-starts

Three things separate this event from a conversation count, and all three inflate it. [`conversation-unit.md`](conversation-unit.md) holds the canonical unit these feed.

**It covers text chats.** `AI Conversation Session Started` fires for text conversations as well as
voice, so a voice metric filters `Current Conversation Mode == "Voice Mode"`. Unfiltered counts run
roughly 20-25% high against the agent-side voice figure and are not comparable to it.

**It fires on every reconnect,** so the raw count treats a dropped-and-resumed conversation as
several. Measured on aiLiveKitIntegration4 (671 consecutive-start
gaps): **33% fall within five minutes** of the previous start, 21% within sixty seconds. Merging at
five minutes removes ~24% of counted sessions.

Merge starts separated by less than **5 minutes**. A reconnect is rarely instant — the user reopens
the app and re-enters — so the window has to cover that recovery; a one-minute window leaves most
restarts counted twice. The trade is that a genuine second conversation begun inside five minutes
merges into the first, which is the rarer case.

The event carries no session or conversation id, so a time gap is the only signal available. The
`conversationId` the LiveKit agent logs is not a substitute — 23.8% of those ids serve more than one
session, 12.9% of same-id pairs are an hour or more apart, and the mobile client reuses an id well
beyond a single sitting, so merging on the id collapses genuinely separate conversations.

Because the merge needs per-user timestamps it belongs on the Step 5.6 per-user JQL pass; aggregate
segmentation cannot express it. When only aggregate numbers are available, report the figure as a
session-start count and say it includes restarts, rather than calling it a conversation count.

Whether restarts are symmetric across arms is itself worth checking before trusting a between-arm
engagement comparison: on aiLiveKitIntegration4 they were (LiveKit 31.9% vs ElevenLabs 34.5% of gaps
under five minutes, p=0.48), so the inflation cancelled — but an engine or client change that affects
reconnect rate biases the raw metric toward whichever arm drops more.

**It counts sessions that produced nothing.** A start that connected and yielded no exchange is a
husk, not a conversation, and the client cannot tell the two apart — it fires on the attempt. The
canonical unit therefore also requires at least **3 backend `coach-prompter` turns** inside the
session's window, which is what separates `Session Rate` computed here from a raw merged-start
count. Husk rate is worth reporting in its own right: on aiLiveKitIntegration4 it sat at 13-17% on
every arm. Definition, window and the ledger join: [`conversation-unit.md`](conversation-unit.md).

The user property convention is the same for both apps: `Experiment: <experimentName>` with variant values `a` / `b` (or more for multi-arm experiments).

## Query template

Run one `Run-Query` per experiment, substituting the correct event names from the table above:

```yaml
project_id: 963821
report_type: insights
report:
  name: "<experimentName> — by variant"
  metrics:
    # Aurie: use "AI Sign Up Login", "AI Subscription Purchased", "AI Subscription Paid", "AI Conversation Session Started"
    # Aura:  use "Sign up login", "Subscription Trial Started", "Subscription Paid"  (omit session — no equivalent)
    - { eventName: "<SUL event>",     measurement: { type: basic, math: unique } }
    - { eventName: "<Trial event>",   measurement: { type: basic, math: unique } }
    - { eventName: "<Paid event>",    measurement: { type: basic, math: unique } }
    - { eventName: "<Session event>", measurement: { type: basic, math: unique } }  # Aurie only
  breakdowns:
    - metric:
        type: property
        propertyName: "Experiment: <experimentName>"
        propertyType: string
        resource: user
  filters:
    - type: string
      propertyName: "Experiment: <experimentName>"
      propertyType: string
      resource: user
      operator: "is set"
  chartType: bar
  dateRange:
    type: absolute
    from: "<createDate>"
    to:   "<endDate or today>"
```

**Aurie web-only experiments:** include `AI Subscription Paid` but expect `AI Conversation Session Started` to be sparse — document the gap rather than dropping it.

**Aura infra / backend experiments** (e.g. `quickProcessingResponseAPI`): Mixpanel user-event metrics may not capture the signal (latency, error rate, etc.). Run the query anyway to confirm low N, then note "infra experiment — Mixpanel user metrics are not the primary signal; see Linear / server logs."

## Key IDs and references

- **Mixpanel project:** `963821` (Project Aura) — workspace `30768` — **same project for both Aurie and Aura apps**
- **Mixpanel dashboard (Aurie):** `11067377` (Aurie Experiments — Mobile)
- **Mixpanel REST API (Tier 2 fallback):** `https://mixpanel.com/api/2.0/segmentation/` — HTTP Basic auth via `MIXPANEL_SERVICE_ACCOUNT_USERNAME` / `MIXPANEL_SERVICE_ACCOUNT_SECRET`. See "Mixpanel access ladder" in Step 5.
- **Slack `#support-bot`:** `C02FQE15YGP` (AuraBot create/end experiment log)
- **Slack `#data-experiment`:** `G017L0Q12FL` (parallel AuraBot create/end experiment log — same content as `#support-bot`; AuraBot posts both `Experiment created:` and `Experiment ended:` to both. Read both as a defensive pattern.)
- **Slack `#ai-product`:** `C04R4L67676` (default post destination for Aurie)
- **Slack `#data` / `#growth`:** additional search targets for Aura-app experiment analyses
- **Event names — Aurie:** `AI Sign Up Login`, `AI Subscription Purchased`, `AI Subscription Paid`, `AI Conversation Session Started`
- **Event names — Aura:** `Sign up login`, `Subscription Trial Started`, `Subscription Paid` (no session-equivalent — omit that column)
- **App detection:** `targetApp == "ai"` → Aurie; `targetApp == null` → Aura (server-side default; confirmed 2026-04-27)
- **User property pattern:** `Experiment: <experimentName>` with variant values
  — usually `a` / `b`, but experiments may have **more than two variants**
  (`a` / `b` / `c` / ...). **By convention `b` is the control**, so treat `a`
  (and `c`, `d`, ...) as treatment arms unless the Slack end-experiment note
  says otherwise. Convention is the same for both Aurie and Aura apps.

## Notes and gotchas

- **Variant conventions:** the default is `b = control` and the other variants
  (`a`, `c`, `d`, ...) are treatments. Compute lift as `treatment vs b` unless
  the end-experiment note explicitly flips it (e.g. `twoStepPaywall` called out
  `a is control`). Always cite which variant is control in the learning.
- **More than two variants:** don't hard-code an `a`/`b` split. Iterate over
  every variant returned in the breakdown and report each against the control.
  The Mixpanel query above already returns all variants — no query change
  needed, just adapt the rate/lift math and the output table.
- **Small/zero arms:** variants sometimes get zero traffic (gradual rollout or
  mis-assignment). Skip arms with `N = 0` in the report but list them so the
  user sees the gap.
- **Super-property gaps:** older experiments may lack `Experiment: <name>` on
  client events, causing funnel queries to under-report. Flag when
  `$overall` count is suspiciously below `SUL(a) + SUL(b)`.
- **Small N:** paid conversion `N` is almost always in single digits at end
  time. Never declare a revenue winner from these queries alone — recommend a
  paid-retention follow-up 2–3 weeks post-end.
- **Web experiments:** trial-start rates are 3–4× lower than mobile. Don't
  compare web and mobile rates directly in the same learning.
- **Date range:** default window is the experiment's create date through its
  end date (inclusive). If the create message is older than the newest-100 read
  (Step 1b), fall back to Linear `createdAt` for the ticket, or to `last 30 days`
  as a last resort.
