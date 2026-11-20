# create-iap-subscription

Create a new App Store subscription product via the ASC REST API.

## When to invoke

When the user types `/create-iap-subscription` or asks to create a new IAP subscription product.

## Script location

All API calls go through `scripts/asc_api.py` in this skill directory. Run it as:

```bash
python3 .claude/skills/create-iap-subscription/scripts/asc_api.py <subcommand> [flags]
```

Credentials are loaded automatically from `fastlane/.env` (no flags needed in most cases).

## Interactive workflow

Follow these steps in order.

### Step 1 — List subscription groups

Run:
```bash
python3 .claude/skills/create-iap-subscription/scripts/asc_api.py list-groups
```

Display the full output to the user. It shows all groups and their existing products with product IDs, ref names, display names, periods, and states.

### Step 2 — Select group

Ask the user which group to add the product to. Accept the group number from the list (e.g. "1") or the group name. Extract the group's `id` from the listing — you'll need it for `--group-id`.

### Step 3 — Collect inputs

Ask for any details not already provided. Required fields:

| Field | Flag | Description |
|-------|------|-------------|
| Product ID | `--product-id` | The SKU string (e.g. `ai_yearly_9999_7days`). Use the naming convention `ai_{period}_{price}_{trial}` where price is in cents (e.g. `9999` = $99.99). |
| Ref name | `--ref-name` | Internal label shown in ASC (e.g. `"Yearly $99.99 – 7 day trial"`). |
| Display name | `--display-name` | Localized name shown to users in the App Store (e.g. `"Aura Premium – Yearly"`). |
| Description | `--description` | Optional. Localized description shown on the App Store subscription detail page. Defaults to `"<Period> subscription for full access to the Aurie app"` (e.g. `"Yearly subscription for full access to the Aurie app"`). |
| Period | `--period` | One of: `ONE_WEEK`, `ONE_MONTH`, `TWO_MONTHS`, `THREE_MONTHS`, `SIX_MONTHS`, `ONE_YEAR` |
| Trial duration | `--trial` | Optional. One of: `THREE_DAYS`, `SEVEN_DAYS`, `TWO_WEEKS`, `ONE_MONTH`, `TWO_MONTHS`. Sets the intro offer for the anchor territory only — configure other territories' intro offers in the ASC UI. Requires `--price` on `create` (ASC rejects intro offers on unpriced products). Omit for no intro offer. |
| Price (USD) | `--price` | Optional. USD price (e.g. `69.99`). Sets the price for the anchor territory via API. **Other territories must be reviewed and set manually in App Store Connect** — Apple's tier system maps USD amounts to local currencies using standard conversion rates that may not match your intended local price (e.g. the tier for $69.99 USD maps to $89.99 CAD, not $99.99 CAD). Omit to set all pricing manually in the ASC UI. |
| Territory | `--territory` | Optional. Territory code for the anchor price *and* intro offer (default: `USA`). |

### Step 4 — Confirm

Present a summary to the user before creating anything:

```
Group:        <group name> (id: <group id>)
Product ID:   <product_id>
Ref name:     <ref_name>
Display name: <display_name>
Description:  <description (default: "<Period> subscription for full access to the Aurie app")>
Period:       <period>
Trial:        <trial or "none">
Price (USD):  <price or "set manually in ASC UI">
Territory:    <territory (default USA)>

Proceed?
```

Wait for explicit confirmation ("yes", "y", "go", etc.) before continuing.

### Step 5 — Dry run

Run with `--dry-run` to show the request bodies without sending them:

```bash
python3 .claude/skills/create-iap-subscription/scripts/asc_api.py create \
  --group-id <id> \
  --product-id <product_id> \
  --ref-name "<ref_name>" \
  --display-name "<display_name>" \
  [--description "<description>"] \
  --period <period> \
  [--trial <trial>] \
  [--price <price>] \
  [--territory <territory>] \
  --dry-run
```

Show the output to the user. Ask: "Does the request look correct? Confirm to create."

### Step 6 — Create

Run without `--dry-run`:

```bash
python3 .claude/skills/create-iap-subscription/scripts/asc_api.py create \
  --group-id <id> \
  --product-id <product_id> \
  --ref-name "<ref_name>" \
  --display-name "<display_name>" \
  [--description "<description>"] \
  --period <period> \
  [--trial <trial>] \
  [--price <price>] \
  [--territory <territory>]
```

Report success or failure.

### Step 7 — Register product with the Aura backend

After the Apple-side product is created successfully, register it on the Aura backend via `POST https://services.aurahealth.io/products`. This is what makes the product known to the rest of the Aura stack.

**Auth**: bearer token in the `AURA_API_TOKEN` environment variable. Resolve in this order:

1. Shell env: `$AURA_API_TOKEN`
2. Claude settings: read `env.AURA_API_TOKEN` from `~/.claude/settings.json`

If neither is set, stop and ask the user to set `AURA_API_TOKEN` before continuing — do not prompt for the token inline and do not hardcode it.

**Body construction** — derive each field from the inputs already collected in Step 3:

| Field | Value | Derivation |
|-------|-------|------------|
| `provider` | `"apple"` | constant for this skill |
| `name` | the product ID | `--product-id` from Step 3 |
| `revenue` | USD price as a number | `--price` from Step 3 (e.g. `19.99`). If `--price` was omitted, ask the user for the USD revenue figure to send to the backend — it must not be guessed |
| `period` | `"week"` / `"month"` / `"year"` | map from `--period`: `ONE_WEEK`→`week`, `ONE_MONTH`/`TWO_MONTHS`/`THREE_MONTHS`/`SIX_MONTHS`→`month`, `ONE_YEAR`→`year` |
| `duration` | integer | `ONE_WEEK`→1, `ONE_MONTH`→1, `TWO_MONTHS`→2, `THREE_MONTHS`→3, `SIX_MONTHS`→6, `ONE_YEAR`→1 |
| `trial` | trial length in days | `THREE_DAYS`→3, `SEVEN_DAYS`→7, `TWO_WEEKS`→14, `ONE_MONTH`→30, `TWO_MONTHS`→60. Omit `--trial`→`0` |
| `dynamicTrial` | `false` | constant |
| `active` | `true` | constant |
| `aiPlan` | bool | inferred — see "Plan flag inference" below |
| `businessPlan` | bool | inferred |
| `coachPlan` | bool | inferred |
| `coachingPlan` | bool | inferred |
| `communityPlan` | bool | inferred |
| `contentPlan` | bool | inferred |
| `familyPlan` | bool | inferred |
| `maxFamilyPlanMembers` | int | `0` unless `familyPlan` is true — then ask the user for the member cap |
| `videoPlan` | bool | inferred |
| `videoCredit` | int | `0` unless `videoPlan` is true — then ask the user for the credit count |

**Plan flag inference** — split the product ID on `_` and check the resulting tokens (matching against the whole token, not substrings, so `coach` doesn't false-match inside `coaching`):

| Flag | True when tokens contain |
|------|--------------------------|
| `aiPlan` | `ai` |
| `businessPlan` | `business` |
| `coachPlan` | `coach` |
| `coachingPlan` | `coaching` |
| `communityPlan` | `community` |
| `contentPlan` | `content` |
| `familyPlan` | `family` |
| `videoPlan` | `video` |

Examples:
- `ai_yearly_9999_7days` → `aiPlan: true`, all others `false`
- `family_monthly_1499` → `familyPlan: true` (ask user for `maxFamilyPlanMembers`)
- `coach_yearly_4999` → `coachPlan: true`
- `coaching_monthly_2999` → `coachingPlan: true` (token is `coaching`, not `coach`)

If no plan flag matches, stop and ask the user which plan flag(s) to set — never default silently.

**Confirm the body with the user before calling**. Print the full JSON body and ask:

```
Backend registration body:

{
  "provider": "apple",
  "name": "<product_id>",
  "revenue": <price>,
  ...
}

Send to POST https://services.aurahealth.io/products? (y/n)
```

Wait for explicit confirmation. Allow the user to override any field before sending.

**Call the API** with curl. Read the token from env in the same shell invocation — never inline the token value:

```bash
curl -sS -X POST https://services.aurahealth.io/products \
  -H "Authorization: Bearer $AURA_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "apple",
    "name": "<product_id>",
    "revenue": <price>,
    "period": "<period>",
    "duration": <duration>,
    "trial": <trial_days>,
    "dynamicTrial": false,
    "active": true,
    "aiPlan": <inferred>,
    "businessPlan": <inferred>,
    "coachPlan": <inferred>,
    "coachingPlan": <inferred>,
    "communityPlan": <inferred>,
    "contentPlan": <inferred>,
    "familyPlan": <inferred>,
    "maxFamilyPlanMembers": <0 or user-provided cap>,
    "videoPlan": <inferred>,
    "videoCredit": <0 or user-provided credits>
  }'
```

If `AURA_API_TOKEN` is only set in `~/.claude/settings.json` (not the shell), export it for the curl call:

```bash
AURA_API_TOKEN=$(python3 -c "import json; print(json.load(open('$HOME/.claude/settings.json')).get('env',{}).get('AURA_API_TOKEN',''))")
[ -n "$AURA_API_TOKEN" ] || { echo "AURA_API_TOKEN not set"; exit 1; }
```

Report the HTTP status and response body. On a non-2xx, stop and surface the error — do not proceed to Step 8.

### Step 8 — Next steps

After a successful create, always remind the user:

1. **Review and set pricing for all territories in App Store Connect UI** — if `--price` was provided, the USD (or anchor territory) price was set via API. All other territories still need to be reviewed and saved manually. Go to [App Store Connect](https://appstoreconnect.apple.com) → Your App → Subscriptions → find the new product → Price.

   > **Why other territories need manual review:** Apple's price point system uses numbered tiers where each tier maps $X USD to a _different_ local-currency price in each territory. For example, the tier for $69.99 USD maps to $89.99 CAD — not $99.99 CAD. The API sets one territory's price; the ASC UI lets you review every territory's inferred price and override individual markets before saving.

   If `--price` was **not** provided, set all pricing manually in the ASC UI.

2. **Add the product ID to Swift** — open [`AuraConsumerApp/Services/Subscription/Providers/Apple/ApplePaymentProvider+StoreKit.swift`](AuraConsumerApp/Services/Subscription/Providers/Apple/ApplePaymentProvider+StoreKit.swift) and add `"<product_id>"` to the `productIDs` array at line 17.

## Credentials

The script auto-discovers `fastlane/.env` by walking up the directory tree from the script's location. If credentials aren't found automatically, pass them explicitly:

```bash
python3 scripts/asc_api.py list-groups \
  --key-path /path/to/AuthKey_XXXXXXXXXX.p8 \
  --key-id XXXXXXXXXX \
  --issuer-id xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Or set the environment variables:
- `APP_STORE_CONNECT_API_KEY` — path to the `.p8` file
- `APP_STORE_CONNECT_KEY_ID` — the 10-character key ID
- `APP_STORE_CONNECT_ISSUER_ID` — the issuer UUID

## Error handling

- **401 / JWT errors**: The `.p8` key file may be missing or expired. Check `fastlane/.env` points to the correct path.
- **409 Conflict**: The product ID already exists in this group. Check the `list-groups` output.
- **422 Unprocessable**: The group ID may be wrong, or a required field is missing. Inspect the error body printed by the script.
- **`cryptography` not installed**: Run `pip3 install cryptography` and retry.
