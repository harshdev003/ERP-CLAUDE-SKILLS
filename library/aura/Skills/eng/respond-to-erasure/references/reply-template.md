# Reply template

One letter covers both halves when the person asked for access and erasure. Drop sections that
don't apply. Times are the person's local time, labelled. Leave
`[COUNSEL TO CONFIRM: …]` only for facts nobody has established, and resolve every one before
sending.

## Regime table

| Regime | Deadline | Cite | Regulator |
|---|---|---|---|
| GDPR | 1 month (Art. 12(3)) | Art. 15 (access), Art. 17 (erasure) | The person's EU supervisory authority |
| UK GDPR | 1 month | Same articles, plus Data Protection Act 2018 | ICO, https://ico.org.uk |
| PIPEDA (Canada) | 30 days (s. 8(3)) | Schedule 1 Principles 4.9 (access), 4.3.8 and 4.5.3 (withdrawal, destruction) | Office of the Privacy Commissioner of Canada, https://www.priv.gc.ca |
| CCPA (California) | 45 days | Right to know, right to delete | California Privacy Protection Agency, https://cppa.ca.gov |

## Letter

```
Subject: Your request to access and delete your personal information ({regime}), received {date}

Hi {first name},

Thank you for your request under {regime}, which we received on {date}. You asked us to {tell you
what personal information we held about you, and then to} close your account and delete it. We
have done {both}. This letter covers each part.

All times below are {zone name}.

1. What we held about you                     ← access requested
   Account history in one paragraph: created when and where, sign-in method, what they used.
   Then a list:
   - Account and profile: the fields we held
   - Sign-in record: the provider
   - Purchases and billing: or "We held no payment information … because you never made a purchase."
   - Content you created: Aurie conversations, journal, if any
   - Messages we sent: push / email history, with counts and date ranges
   Sources. How we used it.
   Who received it: each recipient this person's data reached (data-map.md), with its role.
     Name a staff-lookup Stripe customer honestly: created while reviewing the request, deleted.
     Vendors we can't look up: "may have received …; we hold no identifier that would let us
     find any such record."
   "We did not find any records about you in {stores checked and empty}. We did not sell your
   information."

2. What we deleted
   On {date} we closed your account and permanently deleted: one line per store, in plain words.
   Include "The records our own systems created while deleting your account."
   Marketing: "You have been removed from all marketing emails and notifications."

3. What we keep, and why
   - Financial records kept for {period} because {law}, with your identifying details removed.
     Or: "Nothing in our active systems. You had no purchases …"
   - If an email unsubscribe existed: "We keep your email address on our do-not-email list, so
     that we never send you email again."
   - If donation records existed: name them, kept as records of the donations made.
   - Server logs are kept for 30 days and then deleted automatically.
   - Backups: kept for a limited period for disaster recovery and then overwritten; never used
     to restore your account or your information.

4. What only you can do
   - Facebook / Apple / Google sign-in: how to remove the app connection
   - App-store subscriptions: cancel in the store settings
   - Uninstall the app

Questions: reply to this email. Complaint: {regulator, url}.

Kind regards,
Aura Health Privacy Team
```

## Variant: our first reply was wrong

Use when the person was already told we hold no account, and a deeper search (usually userDB by
email, soft-deleted rows included) found one. Replace the opening paragraph of the letter with
this, then keep sections 1–4 as usual:

```
Hi {first name},

Thank you for your request under {regime}, which we received on {date}. In our earlier reply
we told you that we could not find an account for your email address. That was wrong, and we
apologise. Our first search covered only our active systems. A deeper search found an account
created on {created date}, which was closed on {closed date} but whose records we still held.

We have now deleted those records. This letter sets out what we found, what we deleted, and
what we keep.
```

- Say plainly that the first answer was wrong; don't soften it into "incomplete".
- Give the dates from the records, not a guess at why the account was closed.
- The deadline still runs from the original received date.

## Wording rules

- Say "deleted" only for what step 8 verified. "No records found" is not "never existed".
- Don't attribute a change to the person or to staff until someone has confirmed who made it.
- Hashing is not encryption: "cryptographically hashed before transmission".
- No vendor internals, ticket ids or table names in the letter.

## Delivery

- Support sends it from the support inbox to the account's email address.
- When posting the draft to the Support thread, replace the first name with `{first name}`.
  Slack is an external surface under `Compliance/Privacy`. The letter carries no email address.
- A letter longer than Slack's 4,000 characters is split by `slack_post.py`. Post it as plain text,
  not inside a code fence, so a split doesn't break the fence.
