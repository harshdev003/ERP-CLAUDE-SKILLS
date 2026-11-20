#!/usr/bin/env python3
"""
App Store Connect API helper for managing in-app subscription products.

Subcommands:
  list-groups   List all subscription groups and their products
  create        Create a new subscription product

Credentials (in priority order):
  1. CLI flags: --key-path, --key-id, --issuer-id
  2. Environment variables: APP_STORE_CONNECT_API_KEY, APP_STORE_CONNECT_KEY_ID, APP_STORE_CONNECT_ISSUER_ID
  3. Auto-discovered fastlane/.env walking up from this script's location
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BUNDLE_ID = "com.aurahealth.AuraConsumerApp"
ASC_BASE = "https://api.appstoreconnect.apple.com"

VALID_PERIODS = ["ONE_WEEK", "ONE_MONTH", "TWO_MONTHS", "THREE_MONTHS", "SIX_MONTHS", "ONE_YEAR"]
VALID_TRIALS = ["THREE_DAYS", "SEVEN_DAYS", "TWO_WEEKS", "ONE_MONTH", "TWO_MONTHS"]


# ---------------------------------------------------------------------------
# Credential loading
# ---------------------------------------------------------------------------

def _find_dotenv():
    """Walk up from this file's directory to find fastlane/.env."""
    here = Path(__file__).resolve().parent
    for directory in [here, *here.parents]:
        candidate = directory / "fastlane" / ".env"
        if candidate.exists():
            return candidate
    return None


def _parse_dotenv(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_credentials(args):
    """Return (key_path, key_id, issuer_id) from args > env > .env file."""
    key_path = getattr(args, "key_path", None)
    key_id = getattr(args, "key_id", None)
    issuer_id = getattr(args, "issuer_id", None)

    if not all([key_path, key_id, issuer_id]):
        key_path = key_path or os.environ.get("APP_STORE_CONNECT_API_KEY")
        key_id = key_id or os.environ.get("APP_STORE_CONNECT_KEY_ID")
        issuer_id = issuer_id or os.environ.get("APP_STORE_CONNECT_ISSUER_ID")

    if not all([key_path, key_id, issuer_id]):
        dotenv_path = _find_dotenv()
        if dotenv_path:
            dotenv = _parse_dotenv(dotenv_path)
            key_path = key_path or dotenv.get("APP_STORE_CONNECT_API_KEY")
            key_id = key_id or dotenv.get("APP_STORE_CONNECT_KEY_ID")
            issuer_id = issuer_id or dotenv.get("APP_STORE_CONNECT_ISSUER_ID")

    missing = [name for name, val in [("key_path", key_path), ("key_id", key_id), ("issuer_id", issuer_id)] if not val]
    if missing:
        sys.exit(
            f"Missing credentials: {', '.join(missing)}\n"
            "Set APP_STORE_CONNECT_API_KEY, APP_STORE_CONNECT_KEY_ID, APP_STORE_CONNECT_ISSUER_ID "
            "in the environment or fastlane/.env, or pass --key-path / --key-id / --issuer-id."
        )

    return key_path, key_id, issuer_id


# ---------------------------------------------------------------------------
# JWT generation (ES256, no third-party libs)
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(key_path: str, key_id: str, issuer_id: str) -> str:
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
    except ImportError:
        sys.exit(
            "The 'cryptography' package is required for JWT signing.\n"
            "Install it with:  pip3 install cryptography\n"
            "  or (Homebrew):  brew install python && pip3 install cryptography"
        )

    with open(key_path, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    now = int(time.time())
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + 1200,  # 20 minutes
        "aud": "appstoreconnect-v1",
    }

    header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()

    signature = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))

    # DER → raw r||s (64 bytes for P-256)
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    r, s = decode_dss_signature(signature)
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return f"{header_b64}.{payload_b64}.{_b64url(raw_sig)}"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _request(method: str, path: str, token: str, body=None, ignore_codes=()):
    url = f"{ASC_BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code in ignore_codes:
            return {}
        error_body = e.read().decode(errors="replace")
        sys.exit(f"ASC API error {e.code} {method} {path}:\n{error_body}")


def _get(path: str, token: str):
    return _request("GET", path, token)


def _post(path: str, token: str, body: dict, ignore_codes=()):
    return _request("POST", path, token, body, ignore_codes=ignore_codes)


# ---------------------------------------------------------------------------
# App ID resolution
# ---------------------------------------------------------------------------

def get_app_id(token: str) -> str:
    resp = _get(f"/v1/apps?filter[bundleId]={BUNDLE_ID}", token)
    apps = resp.get("data", [])
    if not apps:
        sys.exit(f"No app found with bundle ID {BUNDLE_ID}")
    return apps[0]["id"]


def _find_sub_by_product_id(product_id: str, app_id: str, token: str):
    """Return the ASC subscription id for the given product SKU, or None if not found."""
    resp = _get(f"/v1/apps/{app_id}/subscriptionGroups?include=subscriptions&limit=50", token)
    for item in resp.get("included", []):
        if item["type"] == "subscriptions" and item["attributes"].get("productId") == product_id:
            return item["id"]
    return None


# ---------------------------------------------------------------------------
# list-groups subcommand
# ---------------------------------------------------------------------------

def cmd_list_groups(args):
    key_path, key_id, issuer_id = load_credentials(args)
    token = _make_jwt(key_path, key_id, issuer_id)
    app_id = get_app_id(token)

    # ASC does not support nested includes — fetch groups+subscriptions first,
    # then fetch localizations per subscription in a second pass.
    resp = _get(
        f"/v1/apps/{app_id}/subscriptionGroups?include=subscriptions&limit=50",
        token,
    )

    groups = resp.get("data", [])
    included = resp.get("included", [])

    # Index included subscription resources by id
    by_id = {item["id"]: item for item in included}

    if not groups:
        print("No subscription groups found.")
        return

    for i, group in enumerate(groups, 1):
        gid = group["id"]
        gname = group["attributes"].get("referenceName", "(unnamed)")
        print(f'\nGroup {i}: "{gname}" (id: {gid})')

        sub_ids = [
            rel["id"]
            for rel in group.get("relationships", {})
            .get("subscriptions", {})
            .get("data", [])
        ]

        if not sub_ids:
            print("  (no products)")
            continue

        for sub_id in sub_ids:
            sub = by_id.get(sub_id)
            if not sub:
                print(f"  (sub {sub_id} — data not included)")
                continue

            attrs = sub["attributes"]
            state = attrs.get("state", "UNKNOWN")
            product_id = attrs.get("productId", "")
            ref_name = attrs.get("name", "")
            period = attrs.get("subscriptionPeriod", "")

            # Fetch en-US localization in a separate request
            display_name = ""
            loc_resp = _get(f"/v1/subscriptions/{sub_id}/subscriptionLocalizations", token)
            for loc in loc_resp.get("data", []):
                if loc["attributes"].get("locale", "").startswith("en"):
                    display_name = loc["attributes"].get("name", "")
                    break

            print(f"  [{state:<12}]  {product_id}")
            if ref_name:
                print(f"               ref:    {json.dumps(ref_name)}")
            if display_name:
                print(f"               name:   {json.dumps(display_name)}")
            if period:
                print(f"               period: {period}")

    print()


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------

def _get_all_territory_ids(app_id: str, token: str) -> list[str]:
    """Return all territory IDs available for this app by copying from an existing subscription."""
    groups = _get(f"/v1/apps/{app_id}/subscriptionGroups?include=subscriptions&limit=1", token)
    subs = [item for item in groups.get("included", []) if item["type"] == "subscriptions"]
    if not subs:
        return []
    ref_sub_id = subs[0]["id"]
    territories = []
    url = f"/v1/subscriptionAvailabilities/{ref_sub_id}/availableTerritories?limit=200"
    while url:
        resp = _get(url, token)
        territories.extend(t["id"] for t in resp["data"])
        raw_next = resp.get("links", {}).get("next", "")
        url = raw_next.replace(ASC_BASE, "") if raw_next else None
    return territories


# ---------------------------------------------------------------------------
# Pricing helpers — single territory only (default: USA)
# ---------------------------------------------------------------------------
#
# We only set pricing for one territory via the API. Here's why we don't do all 130:
#
# Apple's price point IDs encode {"s": sub_id, "t": territory, "p": tier} in base64.
# The "p" tier is a global index into Apple's price table. Tier N maps to a DIFFERENT
# local-currency price in each territory — e.g. tier 10387 = $69.99 USD but only
# $89.99 CAD, NOT $99.99 CAD. Setting one tier across all territories gives Apple's
# standard currency conversion, which is often not the price you actually want.
#
# After running this script, always open App Store Connect to:
#   • Confirm the USD price was applied correctly
#   • Review and override any territory prices that need to differ
#   App Store Connect → Your App → Subscriptions → <product> → Price
#
# Intro offers can only be created after a price exists for the product in that
# territory, so pricing must be set before intro offers in the creation sequence.
# ---------------------------------------------------------------------------


def _find_price_point(sub_id: str, price: str, territory: str, token: str):
    """Return the price point ID whose customerPrice matches price in territory."""
    price_float = float(price)
    next_url = f"/v1/subscriptions/{sub_id}/pricePoints?filter[territory]={territory}&limit=200"
    while next_url:
        resp = _get(next_url, token)
        for pp in resp.get("data", []):
            cp = pp["attributes"].get("customerPrice", "")
            try:
                if abs(float(cp) - price_float) < 0.005:
                    return pp["id"]
            except (ValueError, TypeError):
                pass
        raw_next = resp.get("links", {}).get("next")
        if not raw_next:
            break
        next_url = raw_next.replace(ASC_BASE, "") if raw_next.startswith(ASC_BASE) else raw_next
    return None


def _set_price(sub_id: str, price: str, territory: str, token: str, dry_run: bool):
    if dry_run:
        print(
            f"\n=== DRY RUN: GET /v1/subscriptions/<SUB_ID>/pricePoints"
            f"?filter[territory]={territory} ==="
        )
        print(f"  (would find price point for ${price} in {territory})")
        price_body = {
            "data": {
                "type": "subscriptionPrices",
                "attributes": {"preserveCurrentPrice": False},
                "relationships": {
                    "subscription": {"data": {"type": "subscriptions", "id": "<SUB_ID>"}},
                    "subscriptionPricePoint": {
                        "data": {"type": "subscriptionPricePoints", "id": "<PRICE_POINT_ID>"}
                    },
                },
            }
        }
        print("\n=== DRY RUN: POST /v1/subscriptionPrices ===")
        print(json.dumps(price_body, indent=2))
        return

    print(f"Looking up ${price} price point for {territory}...")
    pp_id = _find_price_point(sub_id, price, territory, token)
    if not pp_id:
        print(
            f"  Warning: no price point found for ${price} in {territory}. "
            "Set pricing manually in ASC UI."
        )
        return

    price_body = {
        "data": {
            "type": "subscriptionPrices",
            # Omit startDate entirely for the initial price (null is rejected by ASC)
            "attributes": {"preserveCurrentPrice": False},
            "relationships": {
                "subscription": {"data": {"type": "subscriptions", "id": sub_id}},
                "subscriptionPricePoint": {
                    "data": {"type": "subscriptionPricePoints", "id": pp_id}
                },
            },
        }
    }
    print(f"  Setting ${price} in {territory} (price point: {pp_id})...")
    _post("/v1/subscriptionPrices", token, price_body)
    print(f"  Price set. Remember to review other territory prices in App Store Connect.")


def _set_intro_offer(sub_id: str, trial: str, territory: str, token: str, dry_run: bool):
    """Create a free-trial intro offer for a single territory.

    Single-territory by design, mirroring `_set_price`. Setting an intro offer
    requires that a price already exists for the same (subscription, territory)
    pair — ASC rejects intro offers on unpriced products with a 422. Since the
    script only sets pricing for one territory via the API, the matching intro
    offer must also be limited to that same territory. Other territories' intro
    offers should be configured in the ASC UI after their prices are set.
    """
    body = {
        "data": {
            "type": "subscriptionIntroductoryOffers",
            "attributes": {
                "offerMode": "FREE_TRIAL",
                "duration": trial,
                "numberOfPeriods": 1,
                "startDate": None,
                "endDate": None,
            },
            "relationships": {
                "subscription": {"data": {"type": "subscriptions", "id": sub_id}},
                "territory": {"data": {"type": "territories", "id": territory}},
            },
        }
    }

    if dry_run:
        print(f"\n=== DRY RUN: POST /v1/subscriptionIntroductoryOffers ({territory}) ===")
        print(json.dumps(body, indent=2))
        return

    print(f"Creating {trial} free trial in {territory}...")
    _post("/v1/subscriptionIntroductoryOffers", token, body)
    print(f"  Intro offer set in {territory}. Configure other territories' intro offers in ASC UI.")


# ---------------------------------------------------------------------------
# create subcommand
# ---------------------------------------------------------------------------

# Map period enum to a human-readable adjective for the default description.
PERIOD_LABEL = {
    "ONE_WEEK": "Weekly",
    "ONE_MONTH": "Monthly",
    "TWO_MONTHS": "Two-month",
    "THREE_MONTHS": "Quarterly",
    "SIX_MONTHS": "Six-month",
    "ONE_YEAR": "Yearly",
}


def _default_description(period):
    """Default localization description, e.g. 'Yearly subscription for full access to the Aurie app'."""
    label = PERIOD_LABEL.get(period, period.replace("_", " ").title())
    return f"{label} subscription for full access to the Aurie app"


def cmd_create(args):
    key_path, key_id, issuer_id = load_credentials(args)
    token = _make_jwt(key_path, key_id, issuer_id)
    app_id = get_app_id(token)

    # Validate inputs
    if args.period not in VALID_PERIODS:
        sys.exit(f"Invalid --period '{args.period}'. Valid values: {', '.join(VALID_PERIODS)}")
    if args.trial and args.trial not in VALID_TRIALS:
        sys.exit(f"Invalid --trial '{args.trial}'. Valid values: {', '.join(VALID_TRIALS)}")
    # ASC rejects intro offers on unpriced products. A newly-created product has no
    # price yet, so requesting a trial without --price would create the subscription,
    # localization, and availability, then fail for all ~130 territories during intro
    # offer creation — leaving the product in a partially configured state. Force the
    # caller to supply a price up front, or set the trial later via the `update`
    # subcommand once pricing has been configured manually in the ASC UI.
    if args.trial and not args.price:
        sys.exit(
            "--trial requires --price on create (ASC rejects intro offers on unpriced products).\n"
            "Either pass --price <USD>, or omit --trial here and add the trial later "
            "via `asc_api.py update --product-id ... --trial ...` once pricing is set."
        )

    description = args.description or _default_description(args.period)

    # Fetch territory list once — reused for availability, intro offers, and pricing.
    # Skipped in dry-run mode (no real API calls should be made).
    territory_ids = []
    if not args.dry_run:
        print("Fetching territory list from existing subscriptions...")
        territory_ids = _get_all_territory_ids(app_id, token)
        print(f"  {len(territory_ids)} territories found.")

    # 1. POST /v1/subscriptions
    sub_body = {
        "data": {
            "type": "subscriptions",
            "attributes": {
                "productId": args.product_id,
                "name": args.ref_name,
                "subscriptionPeriod": args.period,
                "reviewNote": "",
                "familySharable": False,
            },
            "relationships": {
                "group": {
                    "data": {"type": "subscriptionGroups", "id": args.group_id}
                }
            },
        }
    }

    if args.dry_run:
        print("=== DRY RUN: POST /v1/subscriptions ===")
        print(json.dumps(sub_body, indent=2))
    else:
        print(f"Creating subscription '{args.product_id}'...")
        sub_resp = _post("/v1/subscriptions", token, sub_body)
        sub_id = sub_resp["data"]["id"]
        print(f"  Created subscription id: {sub_id}")

    # 2. POST /v1/subscriptionLocalizations
    loc_body = {
        "data": {
            "type": "subscriptionLocalizations",
            "attributes": {
                "locale": "en-US",
                "name": args.display_name,
                "description": description,
            },
            "relationships": {
                "subscription": {
                    "data": {"type": "subscriptions", "id": "<SUB_ID>" if args.dry_run else sub_id}
                }
            },
        }
    }

    if args.dry_run:
        print("\n=== DRY RUN: POST /v1/subscriptionLocalizations ===")
        print(json.dumps(loc_body, indent=2))
    else:
        print("Creating en-US localization...")
        _post("/v1/subscriptionLocalizations", token, loc_body)
        print("  Localization created.")

    # 3. POST /v1/subscriptionAvailabilities (required before pricing can be set)
    # Mirror the territory list from an existing subscription in the same app.
    if args.dry_run:
        avail_body = {
            "data": {
                "type": "subscriptionAvailabilities",
                "attributes": {"availableInNewTerritories": True},
                "relationships": {
                    "subscription": {"data": {"type": "subscriptions", "id": "<SUB_ID>"}},
                    "availableTerritories": {"data": ["<130 territory objects>"]},
                },
            }
        }
        print("\n=== DRY RUN: POST /v1/subscriptionAvailabilities ===")
        print(json.dumps(avail_body, indent=2))
    else:
        avail_body = {
            "data": {
                "type": "subscriptionAvailabilities",
                "attributes": {"availableInNewTerritories": True},
                "relationships": {
                    "subscription": {"data": {"type": "subscriptions", "id": sub_id}},
                    "availableTerritories": {
                        "data": [{"type": "territories", "id": t} for t in territory_ids]
                    },
                },
            }
        }
        print("Creating subscription availability...")
        _post("/v1/subscriptionAvailabilities", token, avail_body)
        print("  Availability created.")

    # 4. POST /v1/subscriptionPrices (only if --price provided)
    # Pricing must be set before intro offers — ASC rejects intro offers on unpriced products.
    if args.price:
        _set_price(
            "<SUB_ID>" if args.dry_run else sub_id,
            args.price,
            args.territory,
            token,
            args.dry_run,
        )

    # 5. POST /v1/subscriptionIntroductoryOffers for the anchor territory only.
    # Single-territory by design — see _set_intro_offer for the rationale.
    if args.trial:
        _set_intro_offer(
            "<SUB_ID>" if args.dry_run else sub_id,
            args.trial,
            args.territory,
            token,
            args.dry_run,
        )

    if args.dry_run:
        print("\n[Dry run complete — no changes made]")
    else:
        print(f"\nDone. Product ID: {args.product_id}")
        print("Next steps:")
        if not args.price:
            print("  1. Set pricing in App Store Connect UI (--price was not provided):")
            print("     appstoreconnect.apple.com → Your App → Subscriptions → find product → Price")
        else:
            print("  1. Review territory prices in App Store Connect:")
            print("     appstoreconnect.apple.com → Your App → Subscriptions → find product → Price")
            print("     (USD was set via API — other territories use Apple's tier conversion by")
            print("      default and may need manual overrides, e.g. CAD, GBP, AUD.)")
        step = 2
        print(f"  {step}. Add '{args.product_id}' to productIDs in")
        print("     AuraConsumerApp/Services/Subscription/Providers/Apple/ApplePaymentProvider+StoreKit.swift")


# ---------------------------------------------------------------------------
# update subcommand
# ---------------------------------------------------------------------------

def cmd_update(args):
    """Set price and/or intro offer on an existing subscription for the anchor territory."""
    key_path, key_id, issuer_id = load_credentials(args)
    token = _make_jwt(key_path, key_id, issuer_id)
    app_id = get_app_id(token)

    if args.trial and args.trial not in VALID_TRIALS:
        sys.exit(f"Invalid --trial '{args.trial}'. Valid values: {', '.join(VALID_TRIALS)}")
    if not args.trial and not args.price:
        sys.exit("Nothing to update — provide at least one of --price or --trial.")

    if not args.dry_run:
        print(f"Looking up subscription '{args.product_id}'...")
        sub_id = _find_sub_by_product_id(args.product_id, app_id, token)
        if not sub_id:
            sys.exit(f"No subscription found with product ID '{args.product_id}'.")
        print(f"  Found subscription id: {sub_id}")

        print("Fetching territory list from existing subscriptions...")
        territory_ids = _get_all_territory_ids(app_id, token)
        print(f"  {len(territory_ids)} territories found.")

        # Ensure availability exists — 409 means it was already created, which is fine.
        avail_body = {
            "data": {
                "type": "subscriptionAvailabilities",
                "attributes": {"availableInNewTerritories": True},
                "relationships": {
                    "subscription": {"data": {"type": "subscriptions", "id": sub_id}},
                    "availableTerritories": {
                        "data": [{"type": "territories", "id": t} for t in territory_ids]
                    },
                },
            }
        }
        print("Ensuring subscription availability exists...")
        result = _post("/v1/subscriptionAvailabilities", token, avail_body, ignore_codes=(409,))
        print("  Availability ready." if result == {} else "  Availability created.")
    else:
        sub_id = "<SUB_ID>"
        territory_ids = []
        print(f"=== DRY RUN: update '{args.product_id}' ===")

    # Pricing must be set before intro offers
    if args.price:
        _set_price(sub_id, args.price, args.territory, token, args.dry_run)

    if args.trial:
        _set_intro_offer(sub_id, args.trial, args.territory, token, args.dry_run)

    if args.dry_run:
        print("\n[Dry run complete — no changes made]")
    else:
        print(f"\nDone. '{args.product_id}' updated.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def add_credential_args(parser):
    parser.add_argument("--key-path", help="Path to .p8 private key file")
    parser.add_argument("--key-id", help="App Store Connect API key ID")
    parser.add_argument("--issuer-id", help="App Store Connect issuer ID")


def main():
    parser = argparse.ArgumentParser(
        description="App Store Connect subscription management helper"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list-groups
    p_list = sub.add_parser("list-groups", help="List subscription groups and products")
    add_credential_args(p_list)

    # create
    p_create = sub.add_parser("create", help="Create a new subscription product")
    add_credential_args(p_create)
    p_create.add_argument("--group-id", required=True, help="Subscription group ASC ID")
    p_create.add_argument("--product-id", required=True, help="Product ID (e.g. ai_yearly_9999_7days)")
    p_create.add_argument("--ref-name", required=True, help="Internal reference name shown in ASC")
    p_create.add_argument("--display-name", required=True, help="Localized display name shown to users")
    p_create.add_argument(
        "--description",
        default=None,
        help="Localized description (default: '<Period> subscription for full access to the Aurie app')",
    )
    p_create.add_argument(
        "--period",
        required=True,
        choices=VALID_PERIODS,
        help="Subscription period",
    )
    p_create.add_argument(
        "--trial",
        choices=VALID_TRIALS,
        default=None,
        help="Free trial duration (omit for no intro offer)",
    )
    p_create.add_argument(
        "--price",
        default=None,
        help="Customer price in USD (e.g. 69.99) — sets pricing for the anchor territory only",
    )
    p_create.add_argument(
        "--territory",
        default="USA",
        help="Territory to set price in (default: USA). Other territories must be set in ASC UI.",
    )
    p_create.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request bodies without making API calls",
    )

    # update
    p_update = sub.add_parser("update", help="Set price/intro offer on an existing subscription")
    add_credential_args(p_update)
    p_update.add_argument("--product-id", required=True, help="Existing product SKU (e.g. ai_yearly_6999_3days)")
    p_update.add_argument(
        "--trial",
        choices=VALID_TRIALS,
        default=None,
        help="Free trial duration to set for all territories",
    )
    p_update.add_argument(
        "--price",
        default=None,
        help="Customer price in USD (e.g. 69.99) — sets pricing for the anchor territory only",
    )
    p_update.add_argument(
        "--territory",
        default="USA",
        help="Territory to set price in (default: USA). Other territories must be set in ASC UI.",
    )
    p_update.add_argument(
        "--dry-run",
        action="store_true",
        help="Print request bodies without making API calls",
    )

    args = parser.parse_args()

    if args.command == "list-groups":
        cmd_list_groups(args)
    elif args.command == "create":
        cmd_create(args)
    elif args.command == "update":
        cmd_update(args)


if __name__ == "__main__":
    main()
