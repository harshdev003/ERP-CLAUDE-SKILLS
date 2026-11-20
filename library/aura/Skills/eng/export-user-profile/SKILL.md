---
name: export-user-profile
description: >
  Exports the profile Aurie keeps about one Aura user, plus their account details, to a readable PDF
  (and Markdown), so Support can send a user a copy for a data request. Verbatim: an account page
  (allow-listed account fields and the plan with its dates), the stored profile document, the
  compiled insights document Aurie's prompts read, the Profile-tab cards that are on, and the
  onboarding conversation. Each part is labelled "Written by Aurie", "Written by you" or "Your
  account"; no LLM reads the content. Read-only: it renders the profile file from
  `eng:query-user-export-data` and reads the account page through allow-lists. Trigger on "export
  this user's profile", "the profile Aurie built about the user", "data request for profile", or
  "export {uid} profile". Do NOT use for the journal, conversations, insights, affirmations or
  memory exports, or for analysing a user (`data:profile-user`).
metadata:
  version: "1.0.0"
knowledge:
  reads:
    - Guide/Index
    - Compliance/Privacy    # a data-subject export: the user's own data, back to the user
    - Tools/Aura API        # UserService user row and PaymentService subscription list (allow-listed)
---

# Export User Profile

Produce a PDF of the profile Aurie keeps about one user, with their account details on the first
page: the last document of a data-request export, in the same layout as the journal,
conversations, insights, affirmations and memory exports.

## Privacy: read this before running

The same boundary as `eng:export-user-journal` (read its Privacy section).

- **Never post the PDF, or anything from it,** to Slack, Notion, Linear, GitHub or any other surface.
- **Only the requesting user's own uid, from a trusted source,** never an email lookup.
- **Allow-lists, never whole responses.** The user row also holds credentials, device tokens, ip and
  staff notes, and a subscription row holds revenue, receipt and transaction ids and cancel feedback.
  The script keeps only the listed fields the moment each response arrives.
- **Files are private:** 0600 in a 0700 folder. The intermediate HTML is deleted unless kept.
- **A hold on a request is a hold on running it.**

## What goes in the document

| Part | Source | Label |
|---|---|---|
| Your account | UserService `GET /users/:userId`: email, first and last name, member since, language, timezone, country, gender (a field the record lacks prints no row; the age-group code is never shown). PaymentService `POST /subscriptions/list/:userId`: every subscription in force, one per product (lifetime, or an expiry still to come; a cancelled or lapsed row is never shown as current), with its type, period, flags, renews on or ends on, and where it was bought (App Store, Google Play or Web) | Your account |
| Your profile, as Aurie wrote it | `user.profile` (the ingested profile document) | Written by Aurie |
| What Aurie has learned about you | `user.insights` (the compiled document Aurie's prompts read) | Written by Aurie |
| Your profile cards | `user.insightsRenderers`, only cards with `display` on, as the app shows them | Written by Aurie |
| Your onboarding answers | the onboarding quiz: each question with the answer the user gave, numbered; Aurie's welcome and closing shown apart | Written by you |
| Previous profile feedback | the `profileRatings` file: each rating the user gave the Profile tab or a card, with its date, the card's name, the rating and the text. Never the rated version of the profile; a note says the profile has been updated since. Printed only when there is feedback | Written by you |

Values are shown as stored (a language code stays a code), and nothing is mapped or invented. A
stored document's own Markdown headings are escaped, so they can't take over the document outline.

## Step 1: Get the profile file

The profile part comes from **`eng:query-user-export-data`**, run by the IntelligenceService database
owner in one read-only SQL transaction. It writes `profile` as a JSON file (`{version, entity,
generatedAt, userIdPrefix, rows}` with one row, 0600 in a 0700 folder). Never paste its content into chat.

## Step 2: Run the export

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/export-user-profile/scripts/export_profile.py \
  --uid "$(cat <trusted uid file>)" --from-file <the profile file> --ratings-file <the profileRatings file> \
  --format both --out <private folder>/<ticket>-profile.pdf
```

| Flag | Purpose |
|---|---|
| `--uid` | Required. The user's Firebase UID, from a trusted source; the file's uid prefix must match it. |
| `--from-file` | Required. The `profile` file from `eng:query-user-export-data`. Another version, section or user is refused, as is a file without exactly one row. |
| `--ratings-file` | The `profileRatings` file from the same run, for the "Previous profile feedback" section. |
| `--format` | `pdf` (default), `md`, or `both`. |
| `--out` | Output path. Default `./aurie-profile-<uid prefix>.pdf`; the extension is swapped per format. |
| `--timezone` | Override the timezone (default: the user row's timezone). |
| `--html-only` / `--keep-intermediates` | Debugging only: the HTML holds the profile. |

`AURA_API_TOKEN` (agent role) is required for the account page. If either read fails, the script
stops rather than print an account page with gaps.

The script prints one summary line: the uid prefix, the parts written, and whether a plan is in
force. Quote that line when reporting back.

## Related

- `eng:query-user-export-data`: writes the profile file.
- `eng:export-user-journal`, `eng:export-user-conversations`, `eng:export-user-insights`,
  `eng:export-user-affirmations`, `eng:export-user-memory`: the other parts of a data request.
