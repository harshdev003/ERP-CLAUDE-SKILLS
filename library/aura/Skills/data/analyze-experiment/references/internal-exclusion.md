# Excluding internal / comped accounts

Internal, staff and comped accounts are not organic behaviour and must be removed from every
experiment readout **before** any statistic is computed. They are few but can be individually
heavy: in the `aiLiveKitIntegration` experiment one excluded account carried **20 voice sessions**
in a 150-user arm.

`scripts/screen_internal.py` implements everything below. Prefer running it over re-deriving the
logic — it is deterministic, cached, and returns the per-net split the readout has to report.

Both caches carry a **7-day TTL** (`--max-age-days`, or `--refresh` to ignore them outright).
Internal accounts appear continuously — a new employee, a freshly issued tester code — and a user
can redeem an internal code *after* being cached as clean, so a verdict is a snapshot with a shelf
life rather than a permanent fact. A cache written by an older version of the script carries no
timestamp and is treated as stale, so it is re-fetched rather than trusted indefinitely.

Each cached code verdict also carries the **matching rule** that produced it (a short hash of the
rule itself, `CODE_RULE`). Widening the rule therefore invalidates every verdict written under the
narrower one, instead of letting a `clean` decided by the old rule stand until its TTL expires — the
failure that hides a widening is a stale *negative*, and nothing about the cache entry would
otherwise reveal it.

## Per-user JQL pass

Step 5's insights/segmentation queries return **aggregate** per-variant uniques — there is no UID
list in them and individual users cannot be subtracted. Screening therefore needs one JQL query
(Tier 3 on the access ladder) returning a row per user, which also becomes the basis for the Step 6
rates.

Key on the **`UserID`** profile property. Never `distinct_id` — ~92% of those are anonymous
`$device:` ids that join to nothing.

```javascript
function main() {
  return join(
    Events({from_date: "<create date>", to_date: "<end date>",
            event_selectors: [{event: "<SUL event>"}, {event: "<Trial Start event>"},
                              {event: "<Paid event>"}, {event: "<Session event>"}]}),
    People({user_selectors: [{selector: 'defined(user["Experiment: <name>"])'}]}),
    {type: "inner"}
  )
  .groupByUser(function(state, rows) {
    state = state || {uid: null, variant: null, events: {}, sessionTimes: []};
    for (var i = 0; i < rows.length; i++) {
      var e = rows[i].event;
      state.events[e.name] = (state.events[e.name] || 0) + 1;
      // Session-start timestamps, so Step 6 can merge restarts into conversations.
      // `AI Conversation Session Started` covers TEXT chats too, so a voice metric
      // filters the mode here — without it the count is voice plus text.
      if (e.name === "<Session event>" &&
          e.properties["Current Conversation Mode"] === "Voice Mode") {
        state.sessionTimes.push(e.time);
      }
      state.variant = rows[i].user.properties["Experiment: <name>"];
      state.uid = rows[i].user.properties["UserID"];
    }
    return state;
  })
  .map(function(r) {
    return {uid: r.value.uid, variant: r.value.variant, events: r.value.events,
            sessionTimes: r.value.sessionTimes.sort(function(x, y) { return x - y; })};
  });
}
```

`sessionTimes` is what makes the Step 6 conversation merge possible — aggregate segmentation
returns no timestamps, so without it the only available engagement figure is a raw session-start
count. Collapse starts closer together than the merge window before counting:

```python
def conversations(times, window_min=5.0):
    # Session-starts merged into conversations; `times` ascending, epoch-ms.
    if not times:
        return 0
    count, prev = 1, times[0]
    for t in times[1:]:
        if (t - prev) / 60000.0 >= window_min:
            count += 1
        prev = t
    return count
```

Drop the `Current Conversation Mode` filter only for an experiment whose engagement question
genuinely spans both modes — and then say the figure covers voice and text, because the event
name does not.

Collapse multiple `distinct_id`s onto one `UserID` before counting — a single user can appear under
several device ids. Write the resulting UIDs one per line for `--uids-file`, then compute Step 6
rates from the rows that survive screening.

Reconcile the screened denominators against the Step 5 aggregates: they should differ by about the
drop count. A materially larger gap means the two queries disagree about the population — resolve
that before trusting either number.

## Three nets, unioned

No net is a superset of the others, so all three are required. In `aiLiveKitIntegration` the
cohorts missed 1 account the code scan caught, and the code scan missed 3 the cohorts caught. In
`aiLiveKitIntegration4` a staff account was invisible to both and caught only by the third.

### 1. Mixpanel exclusion cohorts

| id | name |
|---|---|
| `5954114` | `Code Redeemed is Aurie-Tester/Employee/Creator + Email Contains 'test'` |
| `5972972` | `Internal is True` |

Fetch members from the Engage API with `filter_by_cohort={"id": <id>}`, paging on `session_id`.
Read the **`UserID`** profile property — never `distinct_id`, which is ~92% anonymous `$device:`
ids and joins to nothing.

This net catches two things the code scan cannot: **historical redeemers** (redemption is an
*event*, so an account can carry no live code today and still be internal) and accounts whose
**email contains `test`**.

### 2. Per-subscription code scan

A user holds **several** subscriptions — `type` is `content`, `ai`, or `community` — and **each
carries its own redemption `code`**. Treat the user as internal when **any** subscription has an
internal code.

```
GET /subscriptions/{userId}      Authorization: Bearer $AURA_ADMIN_TOKEN
```

Three endpoint details worth knowing:

- **`AURA_API_TOKEN` returns 403 here (wrong role).** That is the service token the rest of the Aura skills use
  for `/users/:id`, so it is the natural guess and it does not work. Use `AURA_ADMIN_TOKEN` or
  `AURA_USER_TOKEN` — both are verified against this endpoint. With only the service token present,
  every lookup fails and every user comes back `unscreened` from *this* net; net 3 still runs.
- `GET /users/:id/subscriptions` is a **404**.
- `GET /subscriptions?userId=<uid>` returns **fewer rows** than the path form. Use the path form.

**The user-level `code` on `GET /users/:id` is not usable for this.** It reflects only one
subscription. A known internal account exposed `AURA-TESTER` there — its *content* subscription —
while `AURIE-TESTER` on its *ai* subscription was invisible.

### 3. Email domain + role

```
GET /users/{userId}      Authorization: Bearer $AURA_ADMIN_TOKEN
```

A user is staff when **any** of these holds:

| signal | internal when |
|---|---|
| `email` | domain is `aurahealth.io` |
| `role` | one of `admin`, `agent`, `system` |
| `internal` | `true` |

**Why this net is needed.** The experiment service refuses to assign only the roles it treats as
internal, and that list covers bot roles rather than people (`internalRoles: ['agent','system']`
in its config). A staffer or an `admin` whose account carries no redemption code is therefore
bucketed into an arm and stamped into experiment analytics exactly like a real user — `code`,
`aiCode`, `internal` and `role` all read empty, so nets 1 and 2 cannot see them. The email domain
is the only signal that can. Two `@aurahealth.io` accounts sat undetected in one arm of
`aiLiveKitIntegration4`, both non-converting, biasing that arm's conversion rate downward.

**Only the domain is read, and only the domain is ever reported** — never the local part. See
`Compliance/Privacy`.

**This net's token differs from net 2's.** `/users/{userId}` accepts `AURA_API_TOKEN` — the
service token most Aura skills carry — while `/subscriptions/{userId}` rejects it. Each net
therefore probes the endpoint it actually reads, and the staff net keeps running in an
environment where `AURA_API_TOKEN` is the only token present (cadence, notably) even though the
code net cannot. Probing the wrong endpoint fails a net whose own lookups would have succeeded.

The service also protects itself through a code override that forces code-holders into one
variant and returns them as `ended: true`, so the client never stamps a super property. That
path is sound when it fires, but it matches its code list **exactly**, so a suffixed variant of
a listed code slips past it — the substring rule below is deliberately wider than the service's
and catches those. Two related service behaviours are worth knowing when reading a population:
test accounts carry an `aiJoinedVersion` far above any real release so they clear every
`minVersion` gate, and `admin` is absent from the service's internal-role list. All three are
upstream defects; this net screens around them rather than depending on them being fixed.

## Matching codes

Codes are inconsistently formatted, so normalise first: upper-case, and collapse underscores and
whitespace to hyphens. Both brands are live — **`AURA-`** on the content subscription,
**`AURIE-`** on the ai one.

**Any code containing `AURIE` is internal**, wherever it appears in the string. Every Aurie-side
code issued to date is a staff, tester or comp grant, and the 14 live ones take four different
shapes — so neither a literal list nor a prefix anchor reaches them all:

| shape | live codes |
|---|---|
| prefixed | `AURIE-TESTER`, `AURIE-CREATOR`, `AURIE-EMPLOYEE`, `AURIE-FRIEND`, `AURIE_TESTER_UNLIMITED`, `AURIE_TESTER_NO_EXPERIMENT_UNLIMITED` |
| named one-offs | `AURIE-DESTINY`, `AURIE-LEAH` |
| person + year suffix | `JERRY2026-AURIE`, `MARIE2026-AURIE`, `NEIL2026-AURIE`, `SHANNON2026-AURIE`, `SUSAN2026-AURIE` |
| embedded | `QA_AURIE_SIM_TEST_1MONTH` |

`AURA` gets no such rule: it is the consumer brand, so an acquisition code may legitimately carry
it. Those grants stay enumerated (`AURA-TESTER`, and the `EMPLOYEE` / `CREATOR` / `FRIEND` variants),
alongside a `QA-` prefix.

Applied after normalisation: the substring `AURIE`, or the pattern

```
^(QA-|AURA-(TESTER|EMPLOYEE|CREATOR|FRIEND))
```

**Genuine acquisition codes stay in the analysis.** Affiliate, podcast, socialite and seasonal
discount codes identify real acquired users. Only staff/tester/comp codes are internal. An
Aurie-*branded* marketing code would be swept up by the substring rule and needs an explicit
carve-out in `screen_internal.py`.

## Failure handling

The guiding rule: **a degraded screen must never look like a clean one.** The script encodes this,
and the readout has to carry it through.

- **The nets are independent.** One failing leaves the others running. The output's `nets` field
  records each net's state and `complete` is `false` whenever anything degraded.
- **Cohort fetch failure** falls back to a past-TTL cohort cache when one exists (reported as
  stale) rather than screening nobody. Mixpanel being briefly unreachable is not a reason to
  publish unscreened rates.
- **A failed re-check of a stale *internal* verdict keeps excluding that UID.** A verdict a few
  days out of date is far better than readmitting a tester into the rates. The reverse does not
  hold: a stale *clean* verdict is not trusted, because the account may have redeemed an internal
  code since — asserting clean from an expired record is the error that matters.
- **Otherwise a failed lookup is `unscreened`** — neither confirmed internal nor confirmed clean.
  `unscreened` and `excluded` never overlap: a UID any net already confirmed internal has a
  known status, so another net's failed lookup for it does not make it unknown.
- **Tokens are probed, not just read.** A set-but-rejected `AURA_ADMIN_TOKEN` would otherwise
  shadow a working `AURA_USER_TOKEN` and send every lookup down the failure path. Rejection shows
  up as 401 *or* 403.
- **A net raises rather than exits.** A missing Mixpanel credential or an unusable Aura token
  degrades only its own net; the other still runs. (`SystemExit` is not an `Exception`, so exiting
  from inside a net would silently bypass the independence handler.)
- **A stale cohort snapshot is degraded-but-usable, not failed.** It still screens most internal
  accounts, so it must not be discarded as if it screened nobody.
- **The script exits non-zero only when EVERY net failed** — the one case where no exclusion at all
  was applied and a drop count would be a fiction.

## Reporting requirements

- Always print the **per-arm drop count** and the **per-net split**, so the exclusion is visible
  in the readout rather than an invisible upstream filter. The script reports per-net membership
  in `by_net`, plus `caught_by_one_net_only` — the accounts a single net was solely responsible
  for, which is the evidence that each net still earns its place.
- A **0/0 drop count is suspicious, not reassuring** — check for a stale cache or a missing
  `UserID` before trusting it. This applies per net: a net contributing zero across a whole
  population is a signal to check it, not evidence the population is clean.
- A failed subscription lookup makes a user **unscreened**, never "clean". Surface those UIDs.
- Follow the `Compliance/Privacy` rule when naming accounts: UID **prefix** only (8 chars), email
  **domain** only. Never a full email, given name, or family name.

## Materiality

Exclusion is usually small on the headline but must still be applied and shown. For
`aiLiveKitIntegration` (voice sessions per user, arm `a` vs `b`):

| | delta | p |
|---|---|---|
| no exclusion | −42.8% | 0.0072 |
| cohorts only | −42.4% | 0.0086 |
| codes only | −42.8% | 0.0087 |
| **union (correct)** | **−42.3%** | **0.0102** |

Each net has been solely responsible for at least one account: on a 220-user `aiLiveKitIntegration4`
population the three nets excluded 3 accounts between them, one of which only the staff net saw.
