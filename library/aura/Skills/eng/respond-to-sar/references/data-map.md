# Aura personal-data map

Where one person's data lives, how to get it, and which part of a request it answers. Scope a request
against this table (SKILL.md step 1), then collect only the rows in scope. A generic request, such as
"all the personal data you hold about me", puts **every row** in scope.

| # | Source | What it holds about the person | How to collect | Request themes |
|---|---|---|---|---|
| 1 | **Postgres `user` row** (PaymentService DB) | Name, email, gender, age group, created date, last IP, user agent, city/region/country from IP, initial referrer (UTM), device OS, locale, timezone, `stripeId` | The **human** runs the SQL in SKILL.md step 2. Never query the DB yourself. | Account, profile, identifiers |
| 2 | **Other Postgres tables** | `receipt` (Apple/Google/Stripe purchases), `subscriptionEvent`, `charge`, `refund`, `gift`, `referral`, `redemption`, `aiSubscription`, `userContentListen` / `userContentPlay` (listening history, ~40 days retained) | The human runs `SELECT * FROM "<table>" WHERE "userId" = '{uid}'` for each table in scope. Check the column name in `database/create.sql`. | Purchases, gifts, referrals, listening history |
| 3 | **Stripe** (web purchases only) | Customer (email, country, locale), saved card (brand, last 4, wallet), subscriptions (plan, trial, metadata with campaign), invoices, PaymentIntents and charges (dispute flag, refunds) | `collect.py stripe`. The restricted key can't read charges, events, disputes, Checkout Sessions or tokens directly; the probe lists what was unreadable. | Payment, consent, billing agreement, tokens |
| 4 | **App-store purchases** (iOS/Android) | Receipts: product, transaction id, purchase and expiry dates | `eng:check-user-receipts {uid}`. Apple and Google hold the card data, not us. | Payment for mobile subscribers |
| 5 | **Mixpanel events** | Every tracked step: onboarding answers, page views, checkout steps, server-side subscription events, attribution properties | `collect.py mixpanel` (by user id, then by device id; deduplicated). Don't use the BigQuery `mixpanel` dataset: it stops in Oct 2022. | Activity and audit logs, consent steps, preferences |
| 6 | **Mixpanel profile** | People properties: name, email, city, experiment cohorts, attribution | `collect.py mixpanel` also saves `mixpanel_profile.json` | Profile, profiling |
| 7 | **GCP Cloud Logging** (prod cluster) | Request logs with client IP and user agent, sign-up and payment steps, pixel sends | `collect.py gcp --signup` (±10 min text search) and `--from/--to` (a per-day sweep of user-facing services). Retention is finite, so collect early. | Audit logs, IP at sign-up and payment |
| 8 | **Meta Conversions API** (`src/services/pixel.js` in PaymentService) | Registration, trial and purchase events with IP and user agent (unhashed) plus hashed email, name, gender, city, state, country, external id, fbp/fbc, IDFA | Visible in GCP as `Sent '<Event>' pixel event` with `serverEvent._user_data`. We keep no other copy. | **Recipients (Art. 15(1)(c))**. Always disclose if any send is found. |
| 9 | **Aurie conversations** | Everything the person said to Aurie and its replies | `eng:export-user-conversations {uid}` makes a PDF; drop it into `export/` | Content they created |
| 10 | **Aura API user record** (Firebase-backed) | App profile, preferences, sign-in providers, journal and reflection records | `eng:call-aura-api` → `/users/{uid}` and related routes (Tools/Aura API) | Account, user content |
| 11 | **Iterable** (lifecycle email/push) | Email address, subscription status, sent and opened email events | Iterable console or API. Include only if the person received lifecycle messages. | Marketing, recipients |
| 12 | **Support conversations** | Emails and chats with Support, including the SAR itself | Support exports these from the helpdesk | Communications |
| 13 | **AI call log** (IntelligenceService `llmCall`, BigQuery `aura-main.llm.llm_call`) | One row per AI call made for the person: time, prompter, model, token counts, platform, app version, conversation id. No message text (that is row 9) | `collect.py llm-calls --uid {uid}` (`GCP_KEY`). Rows expire after 396 days | Profiling, AI processing |

## Recipients to list (Art. 15(1)(c))

Name only the recipients this person's data actually reached:

| Recipient | Role | When to list |
|---|---|---|
| Stripe | Payment processor (web) | A Stripe customer exists |
| Apple / Google | App-store billing | A mobile receipt exists |
| Mixpanel | Product analytics | Any Mixpanel event |
| Meta | Advertising measurement (Conversions API) | Any `pixel event` log line |
| Google Cloud | Hosting and logs | Always |
| Iterable | Lifecycle messaging | Any Iterable profile |
| OpenAI / Anthropic | AI processing of Aurie conversations and recommendations | The person used Aurie or AI features |
| Braintrust | LLM tracing of Aurie conversations (names and email masked) | The person used Aurie |
| ElevenLabs / LiveKit | Aurie voice conversations | The person used Aurie voice |
| OneSignal | Push notifications | Rows in notificationDB `notificationBackup`, or a legacy-app push token |
| SendGrid | Transactional and marketing email delivery | Rows in emailDB `emailBackup` |
| Facebook (Meta) Login | Sign-in; Meta holds its own record of the app connection | Firebase provider `facebook.com` |
| AppsFlyer / Branch | Install and link attribution (legacy app SDKs; AppsFlyer server events on mobile purchases) | Legacy app user, or a mobile purchase. Say "may have received" when no identifier exists |

## Traps found on ENGSUP-160

- **Never open the requester in the admin/support tool.** Its billing panel calls
  `getCustomerId`, which creates a Stripe customer when none exists. Collect with the read-only
  paths only (Aura API `GET /users/{uid}`, the human-run SELECTs, `collect.py`). If a Stripe
  customer's `created` is after the request date, a staff lookup made it: explain it in the reply.
- **`*Backup` tables hold personal data** (emailDB `emailBackup`, notificationDB
  `notificationBackup`). Data-map row 2 lists PaymentService only, so sweep every DB on the server.
- **The GCP day sweep can time out (504).** Re-run in 6-hour windows that split on failure, and
  record any window that still fails as incomplete.

## Traps found on ENGSUP-157

- **Mixpanel export times are in the project timezone**, not UTC (America/Los_Angeles, so UTC−7 in
  summer). `build_pack.py --mixpanel-tz` converts them. Check with `Tools/Mixpanel` if the project
  timezone changes.
- **Two IPs a few seconds apart is normal on mobile data.** Check with whois: carrier pools such as
  Three Ireland `H3G-IE` rotate public addresses.
- **The IP Stripe recorded** on a card or Apple Pay token appears in `user-web` logs as
  `token.client_ip`. It is the best answer to "the IP address when I signed up for the subscription".
- **Staff lookups appear in the user's logs.** Admin and agent routes (`/getCustomerId/{uid}`,
  `/subscriptions/list/{uid}`, `/receipts/checkUser/{uid}`) are our Support team, not the person.
  `build_pack.py` drops them. Don't count them as the person's activity. Services now log the route
  pattern (`/receipts/checkUser/:userId`), and only on the request's `Ending transaction` line, so
  `collect.py gcp` fetches those lines (`raw/gcp_endings.jsonl`) and every line of a staff
  transaction is dropped by joining on `jsonPayload.transaction`. Older logs carry the raw id on
  `Starting transaction`; both forms match.
- **"Subscription Refunded" in Mixpanel is not a refund.** Check Stripe `amount_refunded`.
- **A cancellation or change with no known actor** gets described neutrally (see article-15.md →
  Wording rules) until someone confirms who made it.
