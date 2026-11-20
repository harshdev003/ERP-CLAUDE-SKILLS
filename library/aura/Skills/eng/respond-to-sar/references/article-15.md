# Article 15: what a SAR response must contain

This is an operational checklist, not legal advice. For a contested case (a chargeback or dispute, a
complaint, a lawyer's letter) privacy counsel reviews the final pack before it is sent.

## The law in one table

| Regime | Applies when | Regulator the reply names |
|---|---|---|
| EU GDPR, Art. 15 | The requester is in the EU/EEA | That country's authority. For Ireland: the Data Protection Commission, <https://www.dataprotection.ie> |
| UK GDPR, Art. 15, with the Data Protection Act 2018 | The requester is in the UK | Information Commissioner's Office, <https://ico.org.uk> |

Requests often cite both, as in "Article 15 GDPR and the UK Data Protection Act 2018". Name the
regulator for the requester's country and mention the other only if they cited it.

## Deadlines and conditions (Art. 12)

- **One month from receipt** (Art. 12(3)). Count to the same date in the next month. A request
  received on 29 Sep is due on 29 Oct. If that date does not exist, the deadline is the last day of
  the month.
- **Extension:** up to two more months for complex or numerous requests. We must tell the requester
  **within the first month** and say why.
- **Free of charge** (Art. 12(5)). A fee, or a refusal, is allowed only for manifestly unfounded or
  excessive requests. That is rare, so escalate to counsel first.
- **Identity** (Art. 12(6)): if we reasonably doubt who is asking, we may ask for more information.
  Ask only for what we need, for example a reply from the account email address or the last 4
  digits of the card. Never ask for a passport for a routine request. Under UK practice the clock
  pauses until the identity check is answered.
- **Format** (Art. 15(3)): a request made electronically gets its answer "in a commonly used
  electronic form". CSV and PDF inside a zip qualifies.

## What must be in the response

The requester's list of bullets **does not narrow** these obligations. Every formal Art. 15 response
covers all of the following, even the items they did not ask about.

| Art. | Item | Where it comes from at Aura |
|---|---|---|
| 15(1) | Confirmation that we process their data | Always "yes" if we found a user row |
| 15(3) | **A copy of the personal data** | The `export/` CSVs, plus any PDFs from sibling skills |
| 15(1)(a) | Purposes of processing | Privacy Policy: providing the service, billing, analytics, marketing measurement, security and fraud prevention |
| 15(1)(b) | Categories of personal data | Derived from the pack: identity and contact, account, device and network, usage, payment, marketing attribution |
| 15(1)(c) | **Recipients, named where we can.** Hashing is pseudonymisation, not anonymisation, so a hashed identifier sent to a third party still counts. The CJEU ruled in C-154/21 (2023) that the actual recipients must be named when they are known, not only their categories. | Data map: Stripe, Mixpanel, Meta (Conversions API), Google Cloud, Iterable, OpenAI (Aurie), Apple/Google (in-app purchases). List only those that actually received this user's data. |
| 15(1)(d) | Retention period, or the criteria that set it | Privacy Policy retention section, plus the real limits: for example, GCP logs have a finite retention window and Mixpanel keeps raw events |
| 15(1)(e) | Rights to rectification, erasure, restriction and objection | Fixed wording in the template |
| 15(1)(f) | Right to complain to a supervisory authority | Regulator table above |
| 15(1)(g) | Source of data not collected from them | IP-derived location, ad attribution (for example a Google Ads click, the UTM campaign), and data from app stores or Stripe |
| 15(1)(h) | Automated decision-making, including profiling, with legal or similarly significant effects | Content recommendations are profiling, but they have no legal effect. Say what exists and state "no decisions with legal or similarly significant effects" **only after confirming** with the owner. Card fraud screening by Stripe Radar is the processor's own processing, so mention it. |
| 15(2) | Safeguards for transfers outside the EEA/UK | The US vendors are covered by Standard Contractual Clauses and/or the EU-US Data Privacy Framework. Confirm per vendor with counsel and use the wording from the Privacy Policy. |
| 15(4) | The copy must not harm the rights of others | Drop staff identities, other users' data and internal security detail. Aura's internal IPs, hostnames and staff support lookups stay out of `export/`. |

**Don't only link to the policies.** Put a short, explicit Article 15 information section in the reply
itself, and link to the policies as well.

## Wording rules

The privacy review on ENGSUP-157 produced these:

- **Hashing is not encryption.** Write "cryptographically hashed before transmission". Say which
  fields were hashed and which were not; IP address and user agent are sent unhashed to Meta CAPI.
- **Confirm who made a change before attributing it.** If we don't yet know who or what changed a
  record (a cancellation, a refund flag), describe what the system shows: *"Stripe records that the
  subscription status changed to cancelled at {time}. We are verifying the source of that change."*
  Never write "you cancelled" or "cancellation requested" until the actor is confirmed.
- **Don't claim more than the data shows.** Write "Later records are limited to billing and
  cancellation events", not "every later entry is automatic", unless every entry's cause is known.
- **Refunds.** Only Stripe's `amount_refunded` counts. An internal analytics event named
  "Subscription Refunded" is not evidence of a refund.
- **Times.** Give every time in the requester's local timezone and label it (IST, BST, CET and so
  on). Support drafts often copy times from a dashboard set to a US timezone.

## Delivery

- Send only after the identity check is satisfied: the reply goes to the email address on the
  account.
- The zip holds IPs, payment references, device IDs and logs. Send it through a secure channel, or
  password-protect it and send the password separately (for example by SMS or a second email).
- Keep an internal record of what was sent and when. The README in the pack serves as that record.
  Never attach the internal `raw/` folder.
