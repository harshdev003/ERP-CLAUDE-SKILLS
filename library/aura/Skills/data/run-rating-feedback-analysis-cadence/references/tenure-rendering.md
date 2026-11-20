# Tenure & engagement — field catalog and rendering rules

Reference for this skill's `GET /ai/usage/engagement/<uid>` engagement call. Canonical
endpoint doc: `Tools/Aura API` → Usage → Engagement.

**Transport: Python `requests` only.** Both consuming skills' Prerequisites forbid
`curl` / `urllib` — they fail with `SSL: CERTIFICATE_VERIFY_FAILED` in this environment,
so a `curl` snippet would break the fetch rather than merely diverge in style.

```python
# requests only — the consuming skill's Prerequisites forbid curl/urllib (CERTIFICATE_VERIFY_FAILED here)
import os, requests

uid = (uid or "").strip()
if not uid:
    raise ValueError("no user_id — render `unavailable` rather than calling")

r = requests.get(
    f"https://services.aurahealth.io/ai/usage/engagement/{uid}",
    headers={"Authorization": f"Bearer {os.environ['AURA_API_TOKEN']}"},
    timeout=60,
)
```

> **Canonical owner of the tenure-rendering rules:** `data:profile-user` →
> `references/engagement.md` → "Tenure rendering". This file is a **copy**, carried
> here because skills are self-contained (Architecture principle 1) and a cadence
> agent cannot read a sibling skill's reference at runtime. Keep the two in step —
> that file wins on any disagreement. Drift check in [`/CLAUDE.md`](../../../../../CLAUDE.md).

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

## What to take from the response

| Field | Use |
|---|---|
| `tenure.createdAt` / `daysSinceCreated` | **Aura** sign-up (whole account, all products). Carries bulk stamps — classify below. |
| `tenure.aiCreatedAt` / `daysSinceAiCreated` | **Aurie** join. Frequently not a real date — classify below. |
| `tenure.aiInstalledAt` | First Aurie install. Carries the same stamp as `aiCreatedAt`. |
| `conversations.total` | Lifetime **completed** conversations — counted only once a conversation has both a title and a summary. |
| `conversations.daysCount` | Distinct days with a conversation. |
| `conversations.streak` / `streakMax` | Current / best consecutive-day conversation streak. |
| `conversations.lastAt` | Last conversation timestamp. |
| `messages.total` / `totalExcludingOnboarding` | Lifetime messages — prefer the excluding-onboarding figure when they differ. |
| `messages.daily` / `weekly` | Recent message rate, not lifetime. |
| `voice.daily` / `weekly` | Voice duration in **milliseconds** — convert to minutes before rendering. |
| `feed.daysCount` / `streak` / `streakMax` | Feed engagement. |
| `overall.dayCount` / `streak` / `streakMax` | Activity across all Aurie surfaces (conversations + feed). |

Counts default to `0` and timestamps to `null` rather than being omitted, so a `0`
means zero — **never** "not returned". Render zeroes as zeroes; on a paying or
long-tenured account the zeroes *are* the finding.

## Tenure rendering

**Both clocks carry bulk stamps, and they are independent.** A user can have a real
Aura sign-up with a stamped Aurie join, the reverse, or both stamped. Classify each
on its own, then render.

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

Three things that table encodes and are easy to get wrong:

- **"joined before Feb 2026" belongs to Guard A alone.** A Guard-B trip says the
  stored date contradicts observed activity — that is not evidence of *when* the
  user joined.
- **The `null` row uses `overall.dayCount`, not `bound`.** When the date is missing
  there is no `daysSinceAiCreated` to max against, so folding null into the
  Guard-A/B lower-bound clause would invent a figure. The activity floor is still
  real and must render — a bare "not recorded" throws away the only tenure signal
  left, in the one case where it is the only signal.
- **A stamped Aura date is the more seductive error.** "3693 days on Aura" off a
  `2016-06-19` stamp is a fabricated decade, and the number looks authoritative in
  a way a hedged line does not.

Never print `aiCreatedAt` as a join date, or `daysSinceAiCreated` as tenure, in any
state below "trusted". **Stop at the bound — do not go looking for the real date.**
No Mixpanel first-AI-login query, no read of the `aurie_user_ai_created` BigQuery
table, no composing `eng:backfill-aurie-user-ai-created`. For a single-entry
qualitative read the exact join date almost never changes the conclusion, and
honest imprecision beats false precision.

### The guards, defined

Users who were on Aurie before the field was instrumented were bulk-stamped by a
backfill, so a large share of accounts carry a synthetic join date.

- **Guard A — bulk stamp.** `aiCreatedAt` starts with `2026-02-11T12:00:00`. Match
  it as a **prefix**. `aiInstalledAt` equal to `aiCreatedAt` is strong confirmation.
  `eng:backfill-aurie-user-ai-created` pins the same value as `SENTINEL_PREFIX` in
  its `scripts/backfill.py` — that script is **reference only**; never import from
  it or replicate its Mixpanel fallback.
- **Guard B — arithmetic impossibility.** `overall.dayCount > daysSinceAiCreated + 2`.
  A user cannot have meaningfully more distinct active days than days since joining.
  This guard depends on no constant, so it also catches any future bulk stamp.

  **The `+ 2` is the real bound, not slack** — it absorbs two independent off-by-ones,
  because the two fields are not measured the same way:

  1. **Inclusive endpoints.** `daysSinceAiCreated` is *elapsed* days, so a user who
     joined `N` days ago and has been active every day since — counting both the join
     day and today — legitimately has `dayCount = N + 1`.
  2. **Calendar days vs elapsed days.** `dayCount` counts distinct *calendar* days on
     a fixed US offset, while `daysSinceAiCreated` is elapsed time in UTC. The two
     disagree by up to one more day at the boundary, and the gap persists rather than
     washing out.

  The guard was `+ 1` until [ENG-1041](https://linear.app/aura-health/issue/ENG-1041),
  which covered (1) but not (2), so it fired constantly on ordinary accounts: a user
  who signed up at 23:18 UTC and returned the next calendar day reads `dayCount: 2`
  against `daysSinceAiCreated: 0` and was rendered "joined at least 2d ago" at ~22
  hours old — contradicting the trusted Aura clause on the same line. One session
  logged 22 instances in a day; drift measured `+2` over 13 days on an unbroken-streak
  account. `+ 2` still trips on a real bulk stamp (a date of yesterday against 50
  active days), it just stops firing on every fully-engaged recent account.

  Use `overall.dayCount`, **not** `conversations.daysCount`. `overall` spans every
  Aurie surface (conversations *and* feed), so it is always ≥ the conversation-only
  count and fires on strictly more sentinel cases. A feed-heavy user with few
  conversations is exactly the account the conversation-only count would miss.

**The Aura stamps** — `2016-06-19` and `2023-05-10` — are documented in
`eng:backfill-aurie-user-ai-created`'s Notes & caveats.

## Degradation

A non-2xx, a missing `AURA_API_TOKEN`, or a network error renders the reply's line as
`*Tenure & engagement:*  unavailable — <reason>`; the analysis continues on the
transcript/artifact alone. **Never drop the line silently** — a reader cannot tell an
omitted section from an account with no history.
