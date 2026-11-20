#!/usr/bin/env python3
"""Fetch the live Aurie AI catalogue: packages, package prices, and products.

Calls three Aura API endpoints and prints a combined snapshot:

  POST /ai/packages/list       {"includeUnavailable": true}
  POST /ai/packagePrices/list  {"includeUnavailable": true}   (no packageId/provider filter)
  POST /products/list          {"type": "ai", "limit": 0}

With --experiments, also fetches the active package experiments needed for the
same-package-field conflict check (V3) when planning a package experiment:

  POST /ai/packages/experiments/list  {}  (authorized list endpoint; returns
                                 active rules — deletedAt IS NULL — by default)

That endpoint may not be deployed yet. When --experiments is passed it is
fetched best-effort: a 404 / not-found is reported as unavailable rather than
aborting the whole snapshot, so the conflict check can be flagged "could not
run" instead of silently skipped.

Auth: Bearer $AURA_API_TOKEN.

Usage:
  fetch_catalogue.py [--json] [--experiments] [--base-url URL]
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


DEFAULT_BASE_URL = "https://services.aurahealth.io"

ENDPOINTS = [
    ("packages", "/ai/packages/list", {"includeUnavailable": True}),
    ("packagePrices", "/ai/packagePrices/list", {"includeUnavailable": True}),
    # limit 0: every product, not PaymentService's default page of 100.
    ("products", "/products/list", {"type": "ai", "limit": 0}),
]


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CONTEXT = _ssl_context()


def _post(base_url: str, path: str, token: str, body: dict[str, Any]) -> Any:
    req = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": user_agent(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:500]
        if e.code in (401, 403):
            raise SystemExit(
                f"{path} -> {e.code}: auth rejected. Confirm AURA_API_TOKEN is set "
                "correctly in the env block of ~/.claude/settings.json."
            )
        raise SystemExit(f"{path} -> HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"{path} -> network error: {e.reason}")


def _get_experiments(base_url: str, token: str) -> Any:
    """Best-effort fetch of active package experiments.

    POSTs an empty filter body to the authorized list endpoint, which returns
    active rules (deletedAt IS NULL) by default. Returns the parsed rows on
    success, or a {"unavailable": reason} marker when the endpoint isn't
    deployed / is unreachable — never aborts the run, so the caller can flag
    the V3 conflict check as "could not run".
    """
    path = "/ai/packages/experiments/list"
    req = urllib.request.Request(
        base_url + path,
        data=json.dumps({}).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": user_agent(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CONTEXT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (404, 405, 501):
            return {"unavailable": f"HTTP {e.code} — endpoint not deployed"}
        detail = e.read().decode("utf-8", errors="replace")[:300]
        return {"unavailable": f"HTTP {e.code}: {detail}"}
    except urllib.error.URLError as e:
        return {"unavailable": f"network error: {e.reason}"}


def _rows(payload: Any) -> list[dict[str, Any]]:
    """Normalise a list response that may arrive bare or wrapped in a key."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for v in payload.values():
            if isinstance(v, list):
                return v
    return []


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    present = [c for c in columns if any(c in r for r in rows)]
    extras = sorted({k for r in rows for k in r} - set(present))
    cols = present + extras
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(_fmt(r.get(c)) for c in cols) + " |")
    return "\n".join(out)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print raw JSON payloads")
    parser.add_argument(
        "--experiments",
        action="store_true",
        help="also fetch active package experiments for the V3 conflict check",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AURA_API_BASE_URL", DEFAULT_BASE_URL),
        help="Aura API base URL (default: $AURA_API_BASE_URL or production)",
    )
    args = parser.parse_args()

    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        raise SystemExit(
            "AURA_API_TOKEN not set. Add it to the env block of "
            "~/.claude/settings.json and retry."
        )

    snapshot = {
        name: _post(args.base_url, path, token, body)
        for name, path, body in ENDPOINTS
    }
    if args.experiments:
        snapshot["packageExperiments"] = _get_experiments(args.base_url, token)

    if args.json:
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
        return

    sections = [
        (
            "Packages",
            _rows(snapshot["packages"]),
            ["id", "name", "rank", "isAvailable", "isDefault", "features",
             "displayConfig", "description"],
        ),
        (
            "Package prices",
            _rows(snapshot["packagePrices"]),
            ["id", "packageId", "productId", "provider", "isAvailable"],
        ),
        (
            "AI products",
            _rows(snapshot["products"]),
            ["id", "name", "provider", "revenue", "period", "duration",
             "trial", "active", "aiPlan"],
        ),
    ]
    for title, rows, columns in sections:
        print(f"## {title} ({len(rows)})\n")
        print(_table(rows, columns) if rows else "_no rows_")
        print()

    if args.experiments:
        experiments = snapshot["packageExperiments"]
        if isinstance(experiments, dict) and "unavailable" in experiments:
            print("## Active package experiments\n")
            print(
                f"_unavailable: {experiments['unavailable']}._ "
                "The same-package-field conflict check (V3) cannot run — flag it "
                "as could-not-run in the plan and require a manual conflict check.\n"
            )
        else:
            rows = _rows(experiments)
            print(f"## Active package experiments ({len(rows)})\n")
            columns = ["id", "experimentId", "variant", "action",
                       "controlPackageId", "variantPackageId", "deletedAt"]
            print(_table(rows, columns) if rows else "_no active experiments_")
            print()


if __name__ == "__main__":
    main()
