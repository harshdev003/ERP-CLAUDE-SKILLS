# Reply template

Fill it from the pack. Drop any section whose data does not exist; a mobile-only user has no Stripe
section. Keep section 6 (the Article 15 information) in every reply. Leave `[COUNSEL TO CONFIRM: …]`
where no fact has been established; never fill it with a guess. Times are the requester's local
time, labelled.

```
Subject: Your Subject Access Request (Article 15 {GDPR | UK GDPR}), received {date received}

Hi {first name},

Thank you for your Subject Access Request under Article 15 of the {GDPR / UK GDPR and the Data
Protection Act 2018}, which we received on {date}. Below is a summary of the personal data we hold
about you. The full records are in the attached files.

All times are {zone name} ({abbr}, UTC{offset}).

**1. Account and profile data** (attachment 01)
- Name, email, sign-in method, account created {time} on {web | iOS | Android}
- Device and browser; IP address at account creation {ip} ({network owner})
- Approximate location, derived from the IP address: {city, country}
- Answers you gave during onboarding: {list}
- How you arrived: {ad / referrer / organic}

**2. Sign-up and consent** (attachment 02)         ← for requests about a subscription or consent
- {time}: step … (one line for each step in 02_timeline.csv that shows consent: plan viewed, plan
  selected, payment confirmed, with the IP Stripe recorded, trial started, confirmation shown)
- If there are two IPs from the same mobile carrier, one sentence explaining address rotation.

**3. Transaction and billing history** (attachments 03 and 04)
- Plan, price, trial, subscription reference
- Payment method (brand, funding, last 4, wallet); "We do not store your card number. Your card is
  stored as a payment token by {Stripe | Apple | Google}."
- One line per invoice or charge, with the statement descriptor
- Current state, described neutrally (article-15.md → Wording rules), e.g. "Stripe records that
  the subscription status changed to cancelled at {time}. We are verifying the source of that
  change. Stripe shows no refund; the charge remains under dispute."

**4. Activity** (attachments 05 and 06)
- What 05 and 06 contain; the first and last recorded activity; "Later records are limited to
  {billing / cancellation} events" if that is what the data shows.

**5. Content you created**                          ← only if in scope and present
- Aurie conversations (PDF), journal entries, reflections

**6. Information about how we process your data (Article 15(1) and (2))**
- Purposes: {service and account, payment, analytics, advertising measurement, security}
- Categories: {identity/contact, account, device/network, usage, payment, marketing attribution, content}
- Sources: {provided by you / your device / derived from IP / advertising link / Stripe / app store}
- Recipients: {each recipient from data-map.md that received this person's data, with its role and
  what it received; say which fields were sent unhashed and which were "cryptographically hashed
  before transmission"}
- Retention: [COUNSEL TO CONFIRM: period or criteria for account, billing, analytics and log data]
- Transfers outside the EEA/UK: [COUNSEL TO CONFIRM: safeguard for each provider]
- Automated decision-making: [COUNSEL TO CONFIRM: no decisions with legal or similarly significant
  effects made by solely automated means; mention Stripe fraud screening if there was a card payment]

**Attachments**
{numbered list of every file in the zip except reply.md}

You have the right to ask us to rectify or erase your data, to restrict or object to its processing,
and to receive this information free of charge. You also have the right to complain to {regulator,
url}. More detail is in our Privacy Policy (https://www.aurahealth.io/privacy-policy).

{If a dispute or complaint is open: "If you would like us to erase your data once this request and
the {dispute} are resolved, just reply to this email."}

Kind regards,
{Support agent}
Aura Health Support
```

## Summary for the internal thread

This goes to Slack, not to the requester: what changed from any earlier draft, the IPs, the
activity finding, and **decisions needed** (unknown actors, recipients to confirm, counsel review).
The attachments go to the human; the skill never uploads them itself.
