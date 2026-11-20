#!/usr/bin/env python3
"""Look up PaymentService receipts for one or many Aura user IDs.

Usage:
  check_receipts.py <user-id> [--json]
  check_receipts.py --batch <uids-file> [--filter-missing-product SKU]
                    [--concurrency N] [--csv <out-file>]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import ssl
import sys
import threading
import urllib.error
import urllib.request
import pathlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
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
ENDPOINT_PATH = "/receipts/checkUser/{uid}"


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context that works across macOS Python distributions.

    Python.org installers on macOS don't always populate the default CA bundle.
    Prefer `certifi` when available so the script runs out-of-the-box, then fall
    back to the system default.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CONTEXT = _ssl_context()


def fetch_receipts(uid: str, token: str) -> tuple[int, dict[str, Any] | str]:
    url = AURA_API_HOST + ENDPOINT_PATH.format(uid=uid)
    req = urllib.request.Request(
        url,
        method="POST",
        data=b"",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": user_agent(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            body = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"

    try:
        return status, json.loads(body)
    except json.JSONDecodeError:
        return status, body


def normalize_receipts(payload: dict[str, Any], uid: str) -> list[dict[str, Any]]:
    """PaymentService returns {<userId>-<suffix>: {...}} per receipt."""
    receipts: list[dict[str, Any]] = []
    for key, value in payload.items():
        if not key.startswith(uid):
            continue
        if not isinstance(value, dict):
            continue
        record = dict(value)
        record["_key"] = key
        receipts.append(record)
    return receipts


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def render_table(receipts: list[dict[str, Any]], uid: str) -> str:
    if not receipts:
        return f"No receipts found for `{uid}`."

    receipts.sort(
        key=lambda r: parse_iso(r.get("purchaseAt")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    now = datetime.now(timezone.utc)
    lines: list[str] = []
    lines.append(f"## Receipts for `{uid}`")
    lines.append("")
    lines.append(
        "| Receipt | Product | Provider | Paid | Auto-renew | Purchase | Expires | Transaction | Notes |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for r in receipts:
        receipt_id = r.get("receiptId", "")
        receipt_short = (receipt_id.split("-")[0] + "…") if receipt_id else "—"
        product = r.get("productId", "—")
        provider = r.get("provider", "—")
        paid = "✓" if r.get("paid") else "✗"

        autorenew = r.get("autoRenew")
        autorenew_product = r.get("autoRenewProductId")
        if autorenew and autorenew_product:
            autorenew_cell = f"✓ → {autorenew_product}"
        elif autorenew:
            autorenew_cell = "✓"
        else:
            autorenew_cell = "✗"

        purchase_at = r.get("purchaseAt", "—")
        expiration_at = r.get("expirationAt", "—")
        transaction_id = r.get("transactionId", "—")

        notes: list[str] = []
        exp_dt = parse_iso(expiration_at)
        if r.get("paid") and exp_dt and exp_dt < now:
            notes.append("expired")

        lines.append(
            f"| {receipt_short} | {product} | {provider} | {paid} | {autorenew_cell} "
            f"| {purchase_at} | {expiration_at} | {transaction_id} | {', '.join(notes) or '—'} |"
        )

    lines.append("")
    lines.append(f"_{len(receipts)} receipt{'s' if len(receipts) != 1 else ''}._")
    return "\n".join(lines)


def summarize_for_csv(uid: str, status: int, payload: Any) -> dict[str, Any]:
    """Reduce a single response to one CSV row."""
    row: dict[str, Any] = {
        "userId": uid,
        "status": status,
        "hasReceipt": False,
        "productIds": "",
        "paid": "",
        "autoRenew": "",
        "purchaseAt": "",
        "expirationAt": "",
        "error": "",
    }
    if status == 0:
        row["error"] = str(payload)[:200]
        return row
    if status >= 400:
        row["error"] = f"HTTP {status}"
        return row
    if not isinstance(payload, dict):
        row["error"] = "non-dict response"
        return row

    receipts = normalize_receipts(payload, uid)
    if not receipts:
        return row

    row["hasReceipt"] = True
    row["productIds"] = ";".join(sorted({r.get("productId", "") for r in receipts if r.get("productId")}))
    row["paid"] = ";".join("true" if r.get("paid") else "false" for r in receipts)
    row["autoRenew"] = ";".join("true" if r.get("autoRenew") else "false" for r in receipts)
    row["purchaseAt"] = ";".join(r.get("purchaseAt", "") for r in receipts)
    row["expirationAt"] = ";".join(r.get("expirationAt", "") for r in receipts)
    return row


def run_batch(
    uids: list[str],
    token: str,
    concurrency: int,
    filter_missing_product: str | None,
    csv_path: str | None,
) -> int:
    """Fetch receipts for every UID in parallel, write CSV, return non-zero on errors."""
    rows: list[dict[str, Any]] = [None] * len(uids)  # type: ignore[list-item]
    completed = 0
    errors = 0
    lock = threading.Lock()
    total = len(uids)

    def work(idx: int, uid: str) -> None:
        nonlocal completed, errors
        status, payload = fetch_receipts(uid, token)
        row = summarize_for_csv(uid, status, payload)
        rows[idx] = row
        with lock:
            completed += 1
            if row["error"]:
                errors += 1
            if completed % 50 == 0 or completed == total:
                print(
                    f"  {completed}/{total} done ({errors} errors)",
                    file=sys.stderr,
                    flush=True,
                )

    print(
        f"Fetching receipts for {total} users (concurrency={concurrency})...",
        file=sys.stderr,
    )
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(work, i, uid) for i, uid in enumerate(uids)]
        for _ in as_completed(futures):
            pass

    fieldnames = [
        "userId",
        "status",
        "hasReceipt",
        "productIds",
        "paid",
        "autoRenew",
        "purchaseAt",
        "expirationAt",
        "error",
    ]
    if csv_path:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        print(f"Wrote {len(rows)} rows to {csv_path}", file=sys.stderr)

    # Summary stats
    no_receipt = [r for r in rows if not r["hasReceipt"] and not r["error"]]
    with_receipt = [r for r in rows if r["hasReceipt"]]
    print("", file=sys.stderr)
    print("=== Summary ===", file=sys.stderr)
    print(f"  Total UIDs:       {total}", file=sys.stderr)
    print(f"  With receipts:    {len(with_receipt)}", file=sys.stderr)
    print(f"  No receipts:      {len(no_receipt)}", file=sys.stderr)
    print(f"  Errors:           {errors}", file=sys.stderr)

    if filter_missing_product:
        missing = [
            r for r in rows
            if not r["error"] and filter_missing_product not in r["productIds"]
        ]
        print(
            f"  Missing `{filter_missing_product}`: {len(missing)} users",
            file=sys.stderr,
        )
        print("", file=sys.stderr)
        print(f"=== Users without a `{filter_missing_product}` receipt ===")
        for r in missing:
            kind = "no-receipt" if not r["hasReceipt"] else "different-product"
            print(f"{r['userId']}\t{kind}\t{r['productIds']}")

    return 1 if errors and not csv_path else 0


def run_single(uid: str, token: str, as_json: bool) -> int:
    status, payload = fetch_receipts(uid, token)

    if status == 0:
        print(f"Network error reaching PaymentService: {payload}", file=sys.stderr)
        return 3

    if status >= 400:
        print(f"PaymentService returned HTTP {status}.", file=sys.stderr)
        if isinstance(payload, (dict, list)):
            print(json.dumps(payload, indent=2), file=sys.stderr)
        else:
            print(payload, file=sys.stderr)
        return 4

    if not isinstance(payload, dict):
        print(f"Unexpected response shape (HTTP {status}):", file=sys.stderr)
        print(payload, file=sys.stderr)
        return 5

    if as_json:
        print(json.dumps(payload, indent=2))
        return 0

    receipts = normalize_receipts(payload, uid)
    print(render_table(receipts, uid))
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("uid", nargs="?", help="Aura user ID (omit when using --batch)")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON response body instead of rendered table (single-user mode only)",
    )
    parser.add_argument(
        "--batch",
        metavar="UIDS_FILE",
        help="Read one user ID per line from a file and fetch receipts in parallel",
    )
    parser.add_argument(
        "--filter-missing-product",
        metavar="SKU",
        help="In batch mode, list users without a receipt whose `productIds` contain this SKU (substring match)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Parallel requests in batch mode (default 10)",
    )
    parser.add_argument(
        "--csv",
        metavar="OUT_FILE",
        help="In batch mode, write a CSV summary (one row per user) to this path",
    )
    args = parser.parse_args()

    if args.batch and args.uid:
        parser.error("Provide either a single <user-id> or --batch, not both")
    if not args.batch and not args.uid:
        parser.error("Provide a <user-id> or use --batch <uids-file>")

    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        print(
            "AURA_API_TOKEN is not set. Add it under the `env` block in "
            "~/.claude/settings.json (see /CLAUDE.md → Secrets and environment) "
            "and restart Claude Code.",
            file=sys.stderr,
        )
        return 2

    if args.batch:
        with open(args.batch, encoding="utf-8") as f:
            uids = [line.strip() for line in f if line.strip()]
        if not uids:
            print(f"No UIDs found in {args.batch}", file=sys.stderr)
            return 6
        return run_batch(
            uids,
            token,
            concurrency=args.concurrency,
            filter_missing_product=args.filter_missing_product,
            csv_path=args.csv,
        )

    return run_single(args.uid, token, args.json)


if __name__ == "__main__":
    sys.exit(main())
