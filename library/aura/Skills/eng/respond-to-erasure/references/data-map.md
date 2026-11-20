# Erasure data map

Where one person's data can live, how to tell whether this person reached it, how to find
them there, and how to erase it. Step 3 of SKILL.md maps the footprint onto this table. Step 7
works through the vendor rows the footprint reached, and only those.

**Rule for every row: look up first, act only on what exists.** A write to a store that never held
the person can *create* a record. Iterable unsubscribe and the Stripe lookup both do this; see
traps.md.

## What the account delete does and does not do

`DELETE /users/{uid}` (UserService `deleteUser`) runs, in order:

| Part | Effect | Verified by |
|---|---|---|
| Payment `cancelAll` | Cancels **Stripe** subscriptions only. Apple and Google can't be cancelled server-side. Errors are swallowed. | Stripe subscriptions read |
| Firebase Auth | Deletes the login whose UID equals the profile id. **Sibling logins sharing the email survive.** | Identity Toolkit `accounts:lookup` by UID and by email |
| RTDB `/users/{uid}` | Removed. FirebaseDenormalizer then removes 69 user nodes, paging heavy ones (`/userDonations` is commented out there, and kept on erasure by decision, Jerome 2026-11-19), and writes the `/deletedUsers/{uid}` tombstone. About 18 more per-user nodes (`userEvents`, `customPlaylist`, `downloadedTracks`, `familyPlanInvites`, `referralFriends`, …) are never touched. | `scripts/check_rtdb_gone.py`: one batch of exact-path reads (Admin SDK, `rtdb_get.js`) of the 90 per-user nodes except the kept `/userDonations`; `null` = gone. List nodes the audit and error lines can't vouch for are not read (`present_per_log` or `unverified`), and untouched list nodes go to the Firebase owner (`for_firebase`). The tombstone is reported apart and removed last (step 7). Exists / gone only, never the value. traps.md has the full rule. |
| Postgres | `"user".deletedAt` is set in **every** service DB (soft delete). Only `token` rows are hard-deleted. Each service's `UserDeleted` listener soft-deletes its own copy. | Step 6 removes the rows |
| Iterable | IterableService calls `DELETE /api/users/byUserId` per project (a delete, not a forget) | Iterable lookup |
| OneSignal | `deleteUserByExternalId` | (no key in the skill env; report as done by the flow) |
| Mixpanel | `people.delete_user` 15 s later: **profile only**, events stay. A later track can re-create the profile. | `collect.py mixpanel` (export lags a few hours) |
| BusinessService | Hard-deletes `businessMember` and `businessUser` | Step 6 sweep |
| **Side effect** | Emits `UserUpdated`, `UserDeleted` and permission events, which the Ingestor writes to **BigQuery `aura-main.firebase.raw_events`** | Step 7 BigQuery row |

A 204 means the flow ran, not that every part succeeded. A second call returns
`USER_ALREADY_DELETED`, so the delete cannot be retried. Verify each part.

## Postgres (aura-db-prod, one server)

`SELECT datname FROM pg_database WHERE NOT datistemplate` lists them all. Sweep **every** DB except
the sample `postgres` DB; the account delete leaves a soft-deleted `"user"` row in each, and an
old account keeps them for years (ENGSUP-171: 13 rows from a 2019 delete). Count by uid **and** by
every email-valued column. User-bearing DBs: business, coach, content, email, emotion, experiment,
feed, intelligence, iterable, notification, payment, report, support, user (search has no user
columns).

Each DB's owner runs its own statements, on a go typed where it works; a go relayed by anyone
else is refused. Give one go line per owner:

| Owner (service) | DBs |
|---|---|
| UserService | user |
| EmailService | email |
| PaymentService | payment |
| ContentService | content |
| CoachService | coach |
| IntelligenceService | intelligence, emotion |
| Backend (other services) | business, experiment, feed, iterable, notification, report, support |

Things the person may have, beyond the `"user"` row in each DB:

| DB | Tables worth knowing |
|---|---|
| paymentDB | `receipt` (+`receiptBackup`, `receiptBackup-02042022`), `charge`, `refund`, `subscription`, `subscriptionEvent`, `subscriptionChanged`, `aiSubscription`, `gift`, `redemption`, `referral` (referrer/referree), `code.referrer`, `donation`, `ledger`, `coachSubscription`, `communitySubscription`, `userContentListen/Play` (+Backup) |
| userDB | `journal` (`userId`, `coachId`), `homework`, `health`, `emailUser` (by email), `token` |
| intelligenceDB (Aurie) | `conversation`, `dialogue`, `dialogueScore`, `memory`, `journal`, `mood`, `emotion`, `insight`, `activity`, `affirmation`, `bookmark`, `questionnaire`, `feed`, `feedSchedule`, `notification`, `tts`, `survey`, `usage`, `llmCall`, `openaiConversationMapping`, `meeting`, `meetingSession`, `userPackageConfigHistory` |
| emailDB | **`emailBackup`**: emails sent through SendGrid (recipient, template, delivery/open times) |
| notificationDB | **`notificationBackup`**: push titles and messages, OneSignal id, delivery/open times |
| feedDB | `feed`, **`feedBackup`** (30 GB; `userId`, `actorId`), `userFollower/Following`, `userChallenge`, `userChannelSubscription`, `userCommunity`, `userExperiment` |
| iterableDB | `"user"` holds email and phone; `synchedAt` says whether Iterable ever had the profile |

The account delete never touches the `*Backup` tables. The discovery sweep in sql.md finds them
by column name.

**Billing rows are the one exception to deletion.** Keep `receipt`, `charge`, `refund`,
`subscriptionEvent`, `ledger` and the Stripe invoices for the tax retention period, anonymised:
PII columns nulled and `userId` replaced. If there are none, say so in the reply ("no financial
records we are required to keep").

## Other stores we run

| Store | Find | Erase |
|---|---|---|
| Firebase Storage | `profilePictures/{uid}/`, `chat/{uid}/` (Aurie attachments) | Delete the prefixes |
| BigQuery `aura-main.firebase.raw_events` | `STRPOS(TO_JSON_STRING(r), '{uid}') > 0`. Always holds the delete's own events. | DML delete **after the streaming buffer flushes** (`table.streaming_buffer.oldest_entry_time` is later than the rows). Partitions expire after 45 days. |
| BigQuery `firebase.events`, `dev_events`, `user_following*` | `userId = '{uid}'` | DML delete |
| BigQuery `aurie_cost.*` | `user_id = '{uid}'` (Aurie users only) | DML delete |
| BigQuery `aura-main.llm.llm_call` (the IntelligenceService `llmCall` ledger, ENG-1657) | `createdAt >= TIMESTAMP('2000-01-01') AND userId = '{uid}'` (Aurie users only; the table requires a `createdAt` filter) | DML delete with the same predicate, once the streaming buffer holds none of the rows. Rows expire after 396 days. Read the `openaiId`s first: the OpenAI row below needs them |
| GCP Cloud Logging | `collect.py gcp` chunked sweep | Not editable. 30-day retention, no exports. Say so in the reply. |
| GCS `aura-ai-tts` | Keyed by text hash, so it can't be found per user | Lifecycle only. Say so. |
| Slack k8bot posts, Notion `Learnings/Conversations` | Full UID in text or the `User ID` column (Aurie users) | Delete the messages and rows |

## Vendors

| Vendor | Reached when | Find | Erase |
|---|---|---|---|
| **Iterable** (Aura project) | Any account; `iterableDB."user".synchedAt` set | `GET /api/users/getByEmail?email=` | Profile exists: unsubscribe, then `POST /api/users/forget {email}`. No profile: **no call**. |
| **Iterable** (Aurie project) | Aurie user | `GET /api/users/byUserId/{uid}`. Emails are non-unique, so email calls return 400. | Same rule, keyed by `userId` |
| **SendGrid** | Got any email (`emailBackup` rows), or any address we may have mailed, account or not | Global Unsubscribes `GET /v3/suppression/unsubscribes?email=` (ENGSUP-172's only hit), `/v3/suppression/{bounces,spam_reports,blocks,invalid_emails}/{email}`, `/v3/asm/suppressions/{email}` | Delete only the other entries that exist. **Keep a global unsubscribe** (Jerome, 2026-11-19): it stops us ever emailing the address again. Activity expires in ≤30 days. We only send through the API: no Marketing Campaigns contacts, new or legacy. |
| **Stripe** | `user.stripeId` set or a customer with the email | `collect.py stripe`; `customers/search` (header `Stripe-Version: 2024-06-20`); list by email | Delete the customer (the restricted key is read-only, so a human uses the dashboard). Keep invoices for tax when payments exist. |
| **Mixpanel** | Any app user | `collect.py mixpanel` (events + profile) | Events exist: GDPR Data Deletion API. Nothing found: no call. |
| **OneSignal** | Legacy app push | Account delete handles it | (verify needs a key) |
| **OpenAI** | Aurie user | `openaiId`s from BigQuery `llm.llm_call` (Postgres `llmCall` until ENGBACK-1998 drops it), `openaiConversationMapping`, `metadata.user` | Per project key: `DELETE /v1/responses/{id}`, `/v1/chat/completions/{id}`, `/v1/conversations/{id}` |
| **Braintrust** | Aurie user | BTQL on `metadata.userId` | Delete API to confirm |
| **ElevenLabs ConvAI** | Aurie voice (legacy arm) | conversation ids from Aura conversation records | `DELETE /v1/convai/conversations/{id}` |
| **LiveKit** | Aurie voice | identity = UID | Only if recording is on; 30-day expiry |
| **Bugsnag** | App user (`user.id`, email, name; Aurie text in metadata) | `eng:search-bugsnag-by-user` | Per-user deletion request |
| **Meta** | Facebook Login (`facebook.com` provider), web purchase (CAPI), legacy app events | Firebase provider; GCP `pixel event` lines | Meta is the controller for Login and app events. Tell the person how to remove Aura in Facebook settings. |
| **AppsFlyer, Branch** | Legacy RN app installs; mobile purchases (AppsFlyer S2S) | Device id / `customer_user_id` | AppsFlyer OpenGDPR when an id exists. Otherwise "may have received" in the reply. |
| **Apple / Google** | Mobile receipts | `eng:check-user-receipts` | Controllers for billing. The person cancels and deletes in their store account. |
| Coaching (Zoom, Sendbird, OnSched) | CoachService rows | appointment, chat, videoConference | Per vendor; out of scope until a coaching case comes up |

## Footprint signals

| Signal | Means |
|---|---|
| Aura API `aiCreatedAt` null, intelligenceDB has only `user` | Never used Aurie: skip OpenAI, Braintrust, ElevenLabs, LiveKit |
| Identity Toolkit provider `facebook.com` | Meta Login disclosure |
| `stripeId` set; `customer.created` **before** the request | Web purchase: Stripe, Meta CAPI, TikTok, Impact |
| `customer.created` **after** the request arrived | Created by a staff lookup (traps.md). Delete it, and explain it in the reply. |
| Receipts with provider apple/google | AppsFlyer, Appstack, Google Ads, X |
| `version` 3.x, iOS/RN, `deviceOS` set | Legacy app: OneSignal, Branch, FB app events |
| `emailBackup` / `notificationBackup` rows | SendGrid / OneSignal recipients |
