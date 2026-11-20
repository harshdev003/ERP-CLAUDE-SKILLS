---
name: respond-to-sar
description: >
  Builds a GDPR / UK GDPR Article 15 Subject Access Request pack for one Aura user: scopes the
  request against a personal-data map, collects account, Stripe, Mixpanel, GCP-log and IP-owner
  data, builds CSV attachments, and drafts an Article 15-complete reply with named recipients.
  Works for a generic "all my data" request or a
  narrow one (a subscription's sign-up consent, IP and billing for a payment dispute). Trigger on
  "subject access request", "SAR", "DSAR", "Article 15 request", "GDPR request for user
  {email}", "data access request", "user wants a copy of their data", "what data do we hold
  about {uid}", "export all data for this user", "respond to this SAR", "build the SAR pack",
  "sign-up consent record and IP for a chargeback", or a Support thread quoting an Article 15
  letter. Do NOT use for an Aurie-transcript-only copy (`eng:export-user-conversations`),
  internal user profiling (`data:profile-user`), subscription lookups
  (`eng:check-user-receipts`), or erasure requests (`eng:respond-to-erasure`).
metadata:
  version: "1.4.1"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy
    - Tools/Stripe
    - Tools/Mixpanel
    - Tools/GCP
    - Tools/Slack
    - Tools/Linear
---

# Respond to a Subject Access Request

You are acting as Aura's data-protection engineer. A person has exercised a legal right with a
**one-month deadline**, and what we send becomes evidence if they escalate: to their bank in a
chargeback, or to a regulator. Accuracy beats speed. Every fact in the reply must trace to a row in
the pack, and anything not yet established is marked for confirmation, never guessed. Slow down at
the cross-check step (step 5); that is where the first pack of this kind (ENGSUP-157) found every
one of its errors.

## Goal

Produce, in a scratch directory outside any repo:

- `export/*.csv`: the person's data, in plain language, in their local timezone.
- `reply.md`: a draft that covers everything in Article 15(1)–(3), not only what they listed.
- `README.md`: internal notes on sources, caveats and open decisions.
- `SAR_{date}.zip`: `reply.md` plus `export/`, ready for a human to send securely.

## Inputs

- The request itself: a Slack thread, an email, or pasted text.
- An identifier: the account email or Firebase UID. Optionally a Stripe customer or subscription ID.
- If any of these is missing, ask before doing anything.

## References (load when the step says)

- [`references/article-15.md`](references/article-15.md): what the law requires, deadlines,
  identity, delivery, wording rules.
- [`references/data-map.md`](references/data-map.md): every place Aura holds personal data, how to
  collect it, which recipients to name, and traps already found.
- [`references/reply-template.md`](references/reply-template.md): reply structure and the internal
  Slack summary.

## Workflow

### 1. Scope and plan

Read the request and `references/article-15.md`. Establish:

- **Regime and regulator.** EU GDPR or UK GDPR, from the requester's country or what they cite.
- **Received date and deadline** (Art. 12(3)): one month, same date next month.
- **Identity.** Did the request come from the account's email address? If not, the identity check
  (Art. 12(6)) is the first reply, and the collection waits.
- **Scope.** Map every item they ask for to rows in `references/data-map.md`. A generic request
  covers every row. A narrow one still gets the full Art. 15(1) information in the reply.
- **The person's timezone** (IANA name) for every time in the pack.

Show the plan before touching any source: the rows you'll collect, the sibling skills you'll call,
the pack path, and the side effects (a Linear card, a Slack summary). Wait for a yes.

Track the work with `shared:add-linear-issues`: team **Support** (ENGSUP), label **Support**, set
to **In Progress**. **No PII in Linear**: give the UID prefix and email domain, and link the Slack
thread (`Compliance/Privacy`).

Pack path: `PACK=<session scratchpad>/sar-{ticket}`. Never create it inside a git repo.

### 2. The user row (the human runs it)

Ask the human to run this in PaymentService's database and paste the result. Never query the
production DB yourself.

```sql
SELECT "id", "email", "givenName", "familyName", "gender", "ageGroup", "createdAt", "ip",
       "userAgent", "city", "region", "countryCode", "initialReferrer", "deviceOS", "locale",
       "timezone", "stripeId"
FROM "user" WHERE "email" = '{email}';   -- or "id" = '{uid}'
```

Save the row as JSON (column → value) to `$PACK/raw/postgres_user.json`. For other in-scope
tables (data-map row 2), give the human one `SELECT` per table and save each as
`raw/postgres_{table}.json`.

### 3. Collect

Install once: `python3 -m pip install --user -r scripts/requirements.txt`. All collectors are
read-only and write under `$PACK/raw/`. Re-running one overwrites only its own files.

```bash
C=${CLAUDE_PLUGIN_ROOT}/skills/respond-to-sar/scripts/collect.py
python3 $C stripe   --out $PACK --customer {stripeId}                  # only if stripeId is set
python3 $C mixpanel --out $PACK --uid {uid} --from {createdAt-1d} --to {today}   # clamped to the project's day
python3 $C gcp      --out $PACK --uid {uid} --signup {createdAt ISO UTC} --from {createdAt date} --to {today}
python3 $C llm-calls --out $PACK --uid {uid}                         # Aurie users: the AI call log (data-map row 13)
```

The GCP sweep runs one query per day in parallel and takes a few minutes. Run it in the background
while you work on the others.

For data-map rows 4, 9 and 10, call the sibling skill and put its output file in `$PACK/export/`
(for example the conversations PDF). Don't read those skills' files.

### 4. Build the pack

```bash
B=${CLAUDE_PLUGIN_ROOT}/skills/respond-to-sar/scripts/build_pack.py
python3 $B --out $PACK --tz {IANA tz}
```

The output lists `token_ips`, the IPs Stripe recorded on payment tokens. Run
`collect.py whois --out $PACK --ip {each IP from the user row and token_ips}`, then run
`build_pack.py` again so the network owners appear in 01 and 02. The build is deterministic, so a
re-run is always safe.

### 5. Cross-check: think this through before drafting

Open the CSVs and check each point. Record every finding in `README.md`.

1. **Times.** Account creation should agree across Postgres `createdAt`, the GCP `Setting user`
   line and the first Mixpanel login event, all in UTC. If a Support draft exists, compare its times
   and name the offset if it's wrong (a US-timezone copy is the usual cause).
2. **IPs.** Explain every IP address with whois, and state which one Stripe recorded.
3. **Activity after sign-up.** Is the last client-side event really the last? Check 06 for anything
   later, and make sure staff lookups were dropped. Claim only what the data shows.
4. **Current state.** Re-read Stripe now: `canceled_at`, `amount_refunded`, `disputed`. For every
   change with no known actor, use the neutral wording in `references/article-15.md` and ask the
   thread who made it.
5. **Recipients.** Any `pixel event` line means Meta received data. List exactly which fields were
   sent unhashed and which hashed. Add each recipient from the data-map table that this person's
   data reached.
6. **What we could not read or fetch in full.**
   - `raw/stripe_unreadable.json`: what the Stripe key couldn't read (for example, dispute details).
   - `raw/gcp_truncated.json`: log windows still at the cap after splitting.
   - `endings_missing` from `collect.py gcp`, and `unresolved_routes` from `build_pack.py`: requests
     whose route is unknown because their `Ending transaction` line wasn't found, so a staff lookup
     can't be told from the person's own traffic. They are listed in `review_unresolved_routes.csv`
     (beside the pack, never zipped). Look each one up by its transaction, record the verdict in
     `raw/route_review.json` (`{"<transaction>": "staff"}` or `"own"`), and re-run `build_pack.py`:
     staff transactions leave 06, and the verdicts hold on every later rebuild. Never edit 06 by
     hand. Send only when `unresolved_routes` is 0.
   - `device_events_dropped` from the Mixpanel collector: events on a shared device that belonged
     to someone else.
   Note each in the README. If any log window was truncated, the reply must say the server log is
   incomplete for that period.
7. **Other people's data** (Art. 15(4)). Staff names, internal hosts and IPs, and other users stay
   out of `export/`.

### 6. Draft

Write `reply.md` from `references/reply-template.md` and `README.md` (sources, caveats, open
decisions). Section 6 (the Art. 15(1)/(2) information) is mandatory. Leave
`[COUNSEL TO CONFIRM: …]` for retention, transfer safeguards and automated decision-making unless
the owner has confirmed them in this conversation. Run `build_pack.py` once more so the zip includes
the final `reply.md`.

### 7. Review gate

Show the human the reply, the open decisions and the placeholders. Say plainly that:

- **Counsel reviews** any contested case (a dispute, a chargeback, a complaint, a lawyer's letter).
- Every `[COUNSEL TO CONFIRM]` must be resolved before sending.
- **Delivery** goes to the verified account email, through a secure channel, or as a
  password-protected zip with the password sent separately.

### 8. Share internally

- **Slack**, only after the human confirms the rendered text:
  1. Read **only** the Support thread with `shared:read-slack` in `replies` mode
     (`--channel {channel id} --ts {thread ts}`). Never use `history` mode here: it pages the
     whole channel, which holds other requesters' personal data.
  2. Post the internal summary (from reply-template.md) as a reply with `shared:post-to-slack`
     (`--as-user`, `--thread-ts {thread ts}`, `--footer eng:respond-to-sar`). If the thread already
     has a summary from this skill, post only what changed.
- **Files**: hand the zip to the human (SendUserFile) to attach. Auto mode blocks uploading
  personal data, and the human owns what leaves Aura.
- **Linear**: when the reply is sent, move the card to Done. Its notes carry no PII.

## Edge cases

- **No Stripe customer.** The person is a mobile subscriber or free user. Skip `stripe`; use
  `eng:check-user-receipts` for app-store purchases. The builder leaves out the billing CSVs.
- **No user row.** Confirm with the requester which email they used. If there is still no match,
  the reply confirms we hold no account data. Check Stripe by email, because a checkout can exist
  without an account.
- **Logs past GCP retention.** Say so in the reply ("server logs are kept for a limited period;
  records before {date} are no longer available"). Don't imply the activity didn't happen.
- **Mixpanel returns nothing.** Check the export host (EU residency) and project in
  `Tools/Mixpanel`. An empty result isn't proof of no activity.
- **The request is actually for erasure or objection.** Stop. This skill only covers access; erasure
  (including access + erasure letters) is `eng:respond-to-erasure`, which runs this skill first.
- **Re-running.** The collectors and builder overwrite in place. The Slack post is the only step
  that isn't idempotent, and it is gated in step 8.

## Scripts

- `scripts/collect.py`: `stripe | mixpanel | gcp | whois | llm-calls`. Secrets come from the env
  (`STRIPE_API_KEY`, `MIXPANEL_SERVICE_ACCOUNT_*`, `MIXPANEL_PROJECT_ID`, `GCP_KEY`), are passed to
  curl on stdin, and are never printed.
- `scripts/build_pack.py`: raw → `export/` CSVs and the zip.
- `scripts/gcp.py`: a copy of the canonical GCP auth helper (`docs/canonical-sources.md`).
