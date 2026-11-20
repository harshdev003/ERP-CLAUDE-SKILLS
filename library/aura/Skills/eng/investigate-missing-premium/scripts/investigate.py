#!/usr/bin/env python3
"""Investigate why an Aura user may be missing premium access.

Cross-checks the canonical subscription array against the user record's
`premium` (Aura content) and `premiumAI` (Aurie) flags, then renders a
per-type verdict table.

Usage:
  investigate.py <user-id> [--json] [--quiet]
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
import pathlib
import re
from datetime import datetime, timezone
from typing import Any


def _ua_who() -> str:
    """Login name of whoever runs this, for the user agent: AINATIVE_UA_WHO, else the global git email's local part, git user.name, the OS account, or "unknown"."""
    who = re.sub(r"^\d+\+", "", os.environ.get("AINATIVE_UA_WHO", "").strip().partition("@")[0])
    if not who:
        if not hasattr(_ua_who, "found"):
            import getpass, subprocess
            found = ""
            for key in ("user.email", "user.name"):
                try:
                    val = subprocess.run(["git", "config", "--global", key], capture_output=True, encoding="utf-8", errors="replace", timeout=5).stdout.strip()
                except (OSError, subprocess.SubprocessError):
                    val = ""
                found = re.sub(r"^\d+\+", "", val.partition("@")[0]) if key == "user.email" else val
                if found:
                    break
            if not found:
                try:
                    found = getpass.getuser()
                except Exception:
                    found = ""
            _ua_who.found = found
        who = _ua_who.found
    return re.sub(r"[^A-Za-z0-9._-]+", "-", who).strip("-.") or "unknown"


def user_agent() -> str:
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls to RTDB, GCP and the Aura API.

    Same format as gcloud_sa.user_agent(). A label, not proof: anyone can send it.
    """
    skill = pathlib.Path(__file__).resolve().parents[1]
    root = skill.parents[1]
    try:
        plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["name"]
    except (OSError, ValueError, KeyError, TypeError):
        plugin = root.name
    try:
        front = (skill / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
        version = re.search(r'version:\s*"([^"]+)"', front).group(1)
    except (OSError, IndexError, AttributeError):
        version = "unknown"
    session = os.environ.get("AINATIVE_SESSION_NAME") or (os.environ.get("CLAUDE_CODE_SESSION_ID") or "")[:8]
    session = re.sub(r"[^A-Za-z0-9._-]+", "-", session).strip("-") or "none"
    return f"AuraClaude/{_ua_who()}/{plugin}:{skill.name}@{version} (session {session})"


AURA_API_HOST = "https://services.aurahealth.io"
SUBSCRIPTIONS_PATH = "/subscriptions/list/{uid}"
USERS_PATH = "/users/{uid}"
# limit=0: every product. With no limit, PaymentService returns one page (100); Stripe has 200+.
PRODUCTS_PATH = "/products/{provider}?limit=0"

# Map subscription `type` → user-record flag.
# Aurie (`ai`) and Aura content (`content`) are the only types that mirror to
# a user-record flag today. Other types (coaching, business, community,
# coach) are shown for visibility but not verdicted.
TYPE_TO_FLAG = {
    "ai": "premiumAI",
    "content": "premium",
}


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CONTEXT = _ssl_context()


def _request(
    url: str,
    method: str,
    *,
    token: str | None = None,
    body: bytes | None = None,
) -> tuple[int, Any]:
    headers = {"Accept": "application/json", "User-Agent": user_agent()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


def fetch_subscriptions(uid: str, token: str) -> tuple[int, Any]:
    return _request(
        AURA_API_HOST + SUBSCRIPTIONS_PATH.format(uid=uid),
        method="POST",
        token=token,
        body=b"",
    )


def fetch_user(uid: str) -> tuple[int, Any]:
    return _request(
        AURA_API_HOST + USERS_PATH.format(uid=uid),
        method="GET",
    )


def fetch_products(provider: str, token: str) -> tuple[int, Any]:
    return _request(
        AURA_API_HOST + PRODUCTS_PATH.format(provider=provider),
        method="GET",
        token=token,
    )


def product_in_catalog(catalog: list[dict[str, Any]], product_id: str) -> bool:
    """Catalog rows use `name` for the provider-side SKU."""
    return any(p.get("name") == product_id for p in catalog)


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def classify(sub: dict[str, Any] | None, flag_value: Any, now: datetime) -> str:
    """Return a per-type verdict given the active sub (or None) and flag."""
    flag_true = bool(flag_value)

    if sub is None:
        return "CORRECTLY-NOT-PREMIUM" if not flag_true else "STALE-PREMIUM"

    expiration = parse_iso(sub.get("expirationAt"))
    is_lifetime = bool(sub.get("lifetime"))
    is_active = is_lifetime or (expiration is not None and expiration > now)

    if not is_active:
        return "CORRECTLY-NOT-PREMIUM" if not flag_true else "STALE-PREMIUM"

    return "OK" if flag_true else "LOCKED-OUT"


def pick_relevant_sub(subs: list[dict[str, Any]], sub_type: str) -> dict[str, Any] | None:
    """Pick the most-recent non-deleted subscription of the given type."""
    candidates = [
        s for s in subs
        if s.get("type") == sub_type and not s.get("deletedAt")
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda s: parse_iso(s.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return candidates[0]


def _sub_in_catalog(
    sub: dict[str, Any] | None,
    catalogs: dict[str, list[dict[str, Any]]],
) -> bool | None:
    """Return True/False/None — None when we can't tell (no catalog for provider)."""
    if not sub:
        return None
    provider = sub.get("provider")
    product_id = sub.get("productId")
    if not provider or not product_id:
        return None
    catalog = catalogs.get(provider)
    if catalog is None:
        return None
    return product_in_catalog(catalog, product_id)


def render_report(
    uid: str,
    subs: list[dict[str, Any]],
    user: dict[str, Any],
    catalogs: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Return (markdown_report, overall_verdict, missing_products_records)."""
    lines: list[str] = []
    lines.append(f"## Premium investigation: `{uid}`")
    lines.append("")
    lines.append(
        "| Type | Sub state | Provider | Product | In catalog | Paid | Expires | User flag | Flag value | Verdict |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    verdicts: list[str] = []
    handled_types = set()
    missing_products: list[dict[str, Any]] = []

    def in_catalog_cell(sub: dict[str, Any] | None) -> str:
        result = _sub_in_catalog(sub, catalogs)
        if result is None:
            return "—"
        return "✓" if result else "**✗ MISSING**"

    # 1) Verdict for each type that has a user-record flag mirror.
    for sub_type, flag_name in TYPE_TO_FLAG.items():
        sub = pick_relevant_sub(subs, sub_type)
        flag_value = user.get(flag_name)
        verdict = classify(sub, flag_value, now)
        verdicts.append(verdict)
        handled_types.add(sub_type)

        if sub:
            expiration = parse_iso(sub.get("expirationAt"))
            sub_state = (
                "lifetime" if sub.get("lifetime")
                else "active" if expiration and expiration > now
                else "expired"
            )
            in_cat = _sub_in_catalog(sub, catalogs)
            if in_cat is False:
                missing_products.append({
                    "type": sub_type,
                    "provider": sub.get("provider"),
                    "productId": sub.get("productId"),
                    "revenue": sub.get("revenue"),
                    "period": sub.get("period"),
                    "duration": sub.get("duration"),
                })
            row = (
                f"| {sub_type} | {sub_state} | {sub.get('provider', '—')} "
                f"| {sub.get('productId', '—')} | {in_catalog_cell(sub)} "
                f"| {'✓' if sub.get('paid') else '✗'} "
                f"| {sub.get('expirationAt', '—')} | `{flag_name}` "
                f"| {'✓' if flag_value else '✗'} | **{verdict}** |"
            )
        else:
            row = (
                f"| {sub_type} | none | — | — | — | — | — | `{flag_name}` "
                f"| {'✓' if flag_value else '✗'} | **{verdict}** |"
            )
        lines.append(row)

    # 2) Visibility rows for non-verdicted types (coaching/business/community/coach/...).
    other_types = sorted({s.get("type") for s in subs if s.get("type") not in handled_types})
    for sub_type in other_types:
        sub = pick_relevant_sub(subs, sub_type)
        if not sub:
            continue
        expiration = parse_iso(sub.get("expirationAt"))
        sub_state = (
            "lifetime" if sub.get("lifetime")
            else "active" if expiration and expiration > now
            else "expired"
        )
        in_cat = _sub_in_catalog(sub, catalogs)
        if in_cat is False:
            missing_products.append({
                "type": sub_type,
                "provider": sub.get("provider"),
                "productId": sub.get("productId"),
                "revenue": sub.get("revenue"),
                "period": sub.get("period"),
                "duration": sub.get("duration"),
            })
        lines.append(
            f"| {sub_type} | {sub_state} | {sub.get('provider', '—')} "
            f"| {sub.get('productId', '—')} | {in_catalog_cell(sub)} "
            f"| {'✓' if sub.get('paid') else '✗'} "
            f"| {sub.get('expirationAt', '—')} | — | — | _(no user-flag mirror)_ |"
        )

    # 3) Overall verdict + suggested next step.
    if "LOCKED-OUT" in verdicts:
        overall = "LOCKED-OUT"
    elif "STALE-PREMIUM" in verdicts:
        overall = "STALE-PREMIUM"
    elif "OK" in verdicts:
        overall = "OK"
    else:
        overall = "CORRECTLY-NOT-PREMIUM"

    lines.append("")
    lines.append(f"**Overall verdict:** {overall}")

    # When products are missing from the catalog, that's the PROD-1981 root
    # cause directly — surface the SQL fix before the GCP-logs suggestion.
    if missing_products:
        lines.append("")
        lines.append("### Root cause: product(s) missing from PaymentService catalog")
        lines.append(
            "The user's subscription references a `productId` that doesn't "
            "exist in the `product` table — entitlement sync fails silently. "
            "This is the PROD-1981 pattern. Add the missing row(s) and the "
            "next entitlement-sync tick will resolve the lockout:"
        )
        lines.append("")
        lines.append("```sql")
        for mp in missing_products:
            provider = mp["provider"]
            name = mp["productId"]
            revenue = mp.get("revenue") if mp.get("revenue") is not None else "NULL"
            period = mp.get("period") or "month"
            duration = mp.get("duration") if mp.get("duration") is not None else 1
            # The catalogue represents "not this kind of plan" as NULL, and
            # PaymentService filters read it as `IS NULL`. Only name the plan
            # flag when it applies; an explicit false hides the row from them.
            columns = ['"id"', '"provider"', '"name"', '"revenue"', '"period"', '"duration"']
            values = [
                f"'{provider}-{name}'",
                f"'{provider}'",
                f"'{name}'",
                f"{revenue}",
                f"'{period}'",
                f"{duration}",
            ]
            if mp["type"] == "ai":
                columns.append('"aiPlan"')
                values.append("true")
            lines.append(f"INSERT INTO \"product\" ({', '.join(columns)})")
            lines.append(f"VALUES ({', '.join(values)});")
        lines.append("```")

    if overall in {"LOCKED-OUT", "STALE-PREMIUM"}:
        lines.append("")
        lines.append("### Suggested next step")
        if missing_products:
            lines.append(
                "Apply the SQL above first. If the lockout persists after the "
                "next entitlement-sync tick, dig into recent backend activity "
                "via `eng:investigate-gcp-logs`:"
            )
        else:
            lines.append(
                "Investigate recent backend activity for this user via "
                "`eng:investigate-gcp-logs`. Sample filter:"
            )
        lines.append("")
        lines.append("```")
        lines.append(f'jsonPayload.userId="{uid}"')
        lines.append('severity>=WARNING')
        lines.append('timestamp>="<last-7-days>"')
        lines.append("```")
        lines.append("")
        if overall == "LOCKED-OUT":
            lines.append(
                "Look for entitlement-sync failures, `product not found` errors on "
                "receipt validation, or 5xx responses on PaymentService routes around "
                "the user's `purchaseAt`."
            )
        else:
            lines.append(
                "STALE-PREMIUM means the user no longer has an active subscription but "
                "the user-record flag is still true. Check for missed cancellation / "
                "refund / expiration webhooks around the subscription's `expirationAt`."
            )

    return "\n".join(lines), overall, missing_products


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uid", help="Aura user ID")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw payloads (subscriptions + user) as JSON instead of the verdict table",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the markdown report; print only the overall verdict word (OK / LOCKED-OUT / STALE-PREMIUM / CORRECTLY-NOT-PREMIUM)",
    )
    args = parser.parse_args()

    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        print(
            "AURA_API_TOKEN is not set. Add it under the `env` block in "
            "~/.claude/settings.json (see /CLAUDE.md → Secrets and environment) "
            "and restart Claude Code.",
            file=sys.stderr,
        )
        return 2

    # Subscriptions
    status, subs_payload = fetch_subscriptions(args.uid, token)
    if status >= 400:
        print(f"/subscriptions/list returned HTTP {status}.", file=sys.stderr)
        if isinstance(subs_payload, (dict, list)):
            print(json.dumps(subs_payload, indent=2), file=sys.stderr)
        else:
            print(subs_payload, file=sys.stderr)
        return 4
    if not isinstance(subs_payload, list):
        print(
            f"Unexpected /subscriptions/list response shape (HTTP {status}):",
            file=sys.stderr,
        )
        print(subs_payload, file=sys.stderr)
        return 5

    # User record
    status_u, user_payload = fetch_user(args.uid)
    if status_u >= 400:
        print(f"/users/{{uid}} returned HTTP {status_u}.", file=sys.stderr)
        if isinstance(user_payload, (dict, list)):
            print(json.dumps(user_payload, indent=2), file=sys.stderr)
        else:
            print(user_payload, file=sys.stderr)
        return 6
    if not isinstance(user_payload, dict):
        print(
            f"Unexpected /users/{{uid}} response shape (HTTP {status_u}):",
            file=sys.stderr,
        )
        print(user_payload, file=sys.stderr)
        return 7

    # Product catalogs — fetch once per provider that appears in the
    # subscription array. Used to detect the PROD-1981 root cause
    # (subscription references a product that doesn't exist in the catalog).
    providers = sorted({s.get("provider") for s in subs_payload if s.get("provider")})
    catalogs: dict[str, list[dict[str, Any]]] = {}
    for provider in providers:
        status_p, catalog = fetch_products(provider, token)
        if status_p >= 400 or not isinstance(catalog, list):
            # Not fatal — the verdict still works, just without the
            # in-catalog column for this provider. Warn and continue.
            print(
                f"warning: /products/{provider} returned HTTP {status_p}, "
                f"skipping in-catalog check for that provider.",
                file=sys.stderr,
            )
            continue
        catalogs[provider] = catalog

    if args.json:
        print(
            json.dumps(
                {
                    "subscriptions": subs_payload,
                    "user": user_payload,
                    "catalogs": catalogs,
                },
                indent=2,
            )
        )
        return 0

    now = datetime.now(timezone.utc)
    report, overall, _missing = render_report(
        args.uid, subs_payload, user_payload, catalogs, now
    )

    if args.quiet:
        print(overall)
    else:
        print(report)

    return 0 if overall in {"OK", "CORRECTLY-NOT-PREMIUM"} else 1


if __name__ == "__main__":
    sys.exit(main())
