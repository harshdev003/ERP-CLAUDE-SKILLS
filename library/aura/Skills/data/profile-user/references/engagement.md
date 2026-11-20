# Tenure & engagement volume — field catalog and rules

Reference for **SKILL.md Step 3d**, which calls `GET /ai/usage/engagement/:userId`
once on **every** route — `full_profile`, `conversation_only`, and `journal_only`
alike, and gated by neither `sources` nor route. Canonical endpoint doc:
`Tools/Aura API` → Usage → Engagement (pre-fetched via `knowledge.reads`).

On `full_profile` the response renders as section 2b; on the two thin-wrapper
routes it renders as the preface's `Tenure & engagement` line. Both surfaces are
required, because they are what downstream reply composers
(`data:investigate-user-rating`, the `run-*-analysis-cadence` skills) copy.

## Precondition — never build the path from an empty id

**`uid` must be non-empty before the URL is formed.** An empty value interpolates to
`…/ai/usage/engagement/` — a bare trailing slash — which Express matches against the
**self-serve** `/engagement` route rather than the privileged `/engagement/:userId`
one. Until Intelligence Service 1.21.5 that returned `200` with the *token owner's*
engagement record: a complete, well-formed payload for a user nobody asked for, which
reached a public Slack reply as the rater's own tenure. Since 1.21.5 the service
rejects it with `400 REQUEST_PATH_EMPTY_SEGMENT` ([PROD-6300](https://linear.app/aura-health/issue/PROD-6300)),
but the caller still must not issue the request — the id is known-missing before the
call, and a 400 costs a wasted turn.

```python
uid = (uid or "").strip()
if not uid:
    # do not call — render `unavailable — no user_id supplied`
    ...
```

**A present-but-blank id is an absent id.** The failure is live and recurring rather
than hypothetical: a k8bot `*PROFILE RATED*` message that exceeds Slack's
4,000-character limit is truncated, the footer carrying `uid:` is what gets cut, and
`fetch_ratings.py` then parses an empty `uid`.

## The call

```bash
curl -A "AuraClaude/$(printf %s "${AINATIVE_UA_WHO:-$(git config --global user.email || git config --global user.name || id -un)}" | sed -E 's/@.*//; s/^[0-9]+\+//; s/[^A-Za-z0-9._-]+/-/g; s/^[-.]+|[-.]+$//g' | grep . || echo unknown)/data:profile-user (session ${AINATIVE_SESSION_NAME:-adhoc})" -fsS -H "Authorization: Bearer $AURA_API_TOKEN" \
  https://services.aurahealth.io/ai/usage/engagement/<UID>
```

Privileged endpoint (roles `admin` / `agent` / `service` / `system`), shipped in
Intelligence Service 1.6.1 (2026-07-30). **One call — do not stitch this from
`GET /users/:id` + `POST /ai/dialogue/count/:userId`.**

## What to take from the response

| Field | Use |
|---|---|
| `tenure.createdAt` / `daysSinceCreated` | **Aura** sign-up (the whole account, all products). **Also carries bulk stamps — apply the sentinel rule below.** |
| `tenure.aiCreatedAt` / `daysSinceAiCreated` | **Aurie** join. **Frequently not a real date — apply the sentinel rule below before rendering.** |
| `tenure.aiInstalledAt` | First Aurie install. Carries the same bulk stamp as `aiCreatedAt`. |
| `conversations.total` | Lifetime **completed** conversations — see the completed-conversation rule. |
| `conversations.daysCount` | Distinct days with a conversation. |
| `conversations.streak` / `streakMax` | Current / best consecutive-day conversation streak. |
| `conversations.lastAt` | Last conversation timestamp — pairs with the behavioural report's recency read. |
| `messages.total` / `totalExcludingOnboarding` | Lifetime messages; prefer the excluding-onboarding figure when the two differ. |
| `messages.daily` / `weekly` | Recent message volume (rate, not lifetime). |
| `voice.daily` / `weekly` | Voice duration in **milliseconds** — convert to minutes before rendering. |
| `feed.daysCount` / `streak` / `streakMax` | Feed engagement, for the any-surface picture. |
| `overall.dayCount` / `streak` / `streakMax` | Activity across all Aurie surfaces (conversations + feed). |

Counts default to `0` and timestamps to `null` rather than being omitted, so a `0`
means zero — not "not returned". Render zeroes as zeroes; never infer "missing
data" from one.

## Tenure rendering — the single owner of these rules

**Every surface that shows an Aura sign-up or an Aurie join resolves its wording
here and does not restate the rules.** That means section 2 (Identity), section
2b, the PMF tenure check, the engagement-call failure fallback, and every
downstream copy (Slack reply, Notion row, forwarded report). When a rule
changes, it changes in this section only.

**Do not copy these rules into a call site.** Point at this section instead. A
rule restated in N places has to be changed in N places, and the copy that gets
missed contradicts the others silently — the reader has no way to tell which one
is authoritative.

**Both clocks carry bulk stamps, and they are independent.** A user can have a
real Aura sign-up with a stamped Aurie join, the reverse, or both stamped.
Classify each on its own, then render.

### Classify

| Clock | Trusted when | Stamped / unusable when |
|---|---|---|
| **Aura** — `tenure.createdAt` | neither stamp matches | date is `2016-06-19` or `2023-05-10` |
| **Aurie** — `tenure.aiCreatedAt` | both guards clear and non-null | Guard A or Guard B fires, or the value is `null` |

Let **`bound` = `max(daysSinceAiCreated, overall.dayCount)`**.

### Render

| State | Rendered form |
|---|---|
| Aura trusted | `Aura sign-up <YYYY-MM-DD> (<daysSinceCreated>d)` |
| Aura stamped | `Aura sign-up on or before <YYYY-MM-DD> (stamped, exact date unknown)` |
| Aurie trusted | `Aurie join <YYYY-MM-DD> (<daysSinceAiCreated>d)` |
| Aurie — Guard A fired | `Aurie join at least <bound>d ago — joined before Feb 2026, exact join date unknown` |
| Aurie — Guard B only | `Aurie join at least <bound>d ago — exact join date unknown` |
| Aurie — `null` | `Aurie join not recorded — at least <overall.dayCount>d of activity` |

Three things the table encodes that are easy to get wrong:

- **"joined before Feb 2026" belongs to Guard A alone.** A Guard-B trip says the
  stored date contradicts observed activity — that is not evidence of *when* the
  user joined.
- **The `null` row still carries a bound.** The activity floor is real even when
  the date is missing; a bare "not recorded" throws away the only tenure signal
  left, in the one case where it is the only signal.
- **A stamped Aura date is the more seductive error.** "3693 days on Aura" off a
  `2016-06-19` stamp is a fabricated decade, and the number looks authoritative
  in a way a hedged line does not.

Never print `aiCreatedAt` as a join date, or `daysSinceAiCreated` as tenure, in
any state below "trusted". **Stop at the bound — do not go looking for the real
date.** No Mixpanel first-AI-login query, no read of the `aurie_user_ai_created`
BigQuery table, no composing `eng:backfill-aurie-user-ai-created`. For a
qualitative single-user reading the exact join date almost never changes the
conclusion, and it isn't worth adding a Mixpanel or `GCP_KEY` / `bq.py`
dependency to a skill that has neither. Honest imprecision beats false precision.

### The guards, defined

Users who were on Aurie before the field was instrumented were bulk-stamped by a
backfill, so a large share of accounts carry a synthetic join date.

- **Guard A — bulk stamp.** `aiCreatedAt` starts with `2026-02-11T12:00:00`.
  `eng:backfill-aurie-user-ai-created` solves the same problem and pins that
  value as `SENTINEL_PREFIX` in
  [`scripts/backfill.py`](../../../../eng/skills/backfill-aurie-user-ai-created/scripts/backfill.py)
  — **cross-check the constant there rather than trusting the date written
  here**, and match it the same way, as a prefix. Keep the two in step: a guard
  looser than the backfill's would mislabel a genuine join, and one tighter would
  miss stamped users the backfill already treats as unknown. `aiInstalledAt`
  equal to `aiCreatedAt` is strong confirmation. That script is **reference
  only** — read it to confirm the constant and how it separates a sentinel from a
  real value; never import from it, compose the skill, or replicate its Mixpanel
  fallback path (Architecture principle 1, self-contained skills).
- **Guard B — arithmetic impossibility.** `overall.dayCount >
  tenure.daysSinceAiCreated + 2`. A user cannot have meaningfully more distinct
  active days than days since joining. This guard depends on no constant, so it
  also catches any future bulk stamp.

  **The `+ 2` is the real bound, not slack** — it absorbs two independent
  off-by-ones, and the two fields are not measured the same way:

  1. **Inclusive endpoints.** `daysSinceAiCreated` is *elapsed* days, so a user
     who joined `N` days ago and has been active every day since — counting both
     the join day and today — legitimately has `dayCount = N + 1`.
  2. **Calendar days vs elapsed days.** `dayCount` counts distinct *calendar*
     days on a fixed US offset, while `daysSinceAiCreated` is elapsed time in
     UTC. The two disagree by up to one more day at the boundary, and the gap
     persists rather than washing out.

  The guard was `+ 1` until [ENG-1041](https://linear.app/aura-health/issue/ENG-1041),
  which covered (1) but not (2), so it fired constantly on ordinary accounts:
  a user who signed up at 23:18 UTC and returned the next calendar day reads
  `dayCount: 2` against `daysSinceAiCreated: 0` and was rendered "joined at
  least 2d ago" at ~22 hours old — directly contradicting the trusted Aura
  clause on the same line. One session logged 22 instances in a day. Evidence
  for (2) specifically: a 2-day-old account read `dayCount` 3 at 04:21 UTC and 4
  at 06:06 UTC, crossing no midnight in UTC or US Pacific but one in UTC-5; drift
  measured `+2` over 13 days on an unbroken-streak account.

  `+ 2` still catches what the guard is for — a bulk stamp puts a recent date on
  a long-active account, so a stamp dated yesterday against 50 active days trips
  it comfortably. What it stops doing is firing on every fully-engaged recent
  account, which inverted exactly the tenure read this section exists to get right.

  Use `overall.dayCount`, **not** `conversations.daysCount`. The two are not
  interchangeable: `overall` spans every Aurie surface (conversations *and*
  feed), so it is always ≥ `conversations.daysCount` and therefore fires on
  strictly more sentinel cases. A feed-heavy user with few conversations is
  exactly the account the conversation-only count would miss.

**The Aura stamps** — `2016-06-19` and `2023-05-10` — are documented in the same
skill's Notes & caveats. Confirm both lists at their documented home rather than
trusting the dates copied here; the never-import rule applies to that skill as a
whole.

## Completed-conversation rule

`conversations.total` inherits the conversation model's default `list` filters — a
conversation counts only once it has a title *and* a summary, both written at
conversation end. Label it **completed conversations** on every surface (report
body, Slack reply, Notion row); "conversations started" would be the wrong label.
It is therefore a floor, and a user mid-conversation can legitimately show
`total: 0` alongside a non-zero `messages.total`.

## Edge cases

**No Aurie activity.** All-zero counts with `null` timestamps is a valid answer,
not an error. Render the section with the zeroes and say plainly "no Aurie
activity on record" — against a real `tenure.createdAt` that *is* the signal
(signed up for Aura, never engaged with Aurie). Never drop the section to hide
the zeroes.

**Call fails** (401/403, 404, network, Cowork env strip): don't block the run.
Render the section with an "unavailable" note and the reason, fall back to
`createdAt` from the behavioural report's Identity section for Aura tenure only,
and record the miss in **Sources**.
