#!/usr/bin/env python3
"""Fetch wind-down inputs for one Aurie package experiment.

Combines a snapshot of the canonical catalogue with the experiment's
packageExperiment rows and the ExperimentService experiment config:

  POST /ai/packages/list           {"includeUnavailable": true}
  POST /ai/packagePrices/list      {"includeUnavailable": true}
  POST /products/list              {"type": "ai", "limit": 0}
  POST /ai/packages/experiments/list  {"experimentId": <name>}
       (returns active rules — deletedAt IS NULL — by default; pass
        includeDeleted=true to also surface soft-deleted rows)
  GET  /experiments/<name>

The packageExperiment list endpoint may not be
deployed yet. If it returns 404, this script reports the dependency on stderr
and continues with the rest of the snapshot rather than hard-failing — so the
caller can still see the catalogue and gather the rows another way.

Auth: Bearer $AURA_API_TOKEN.

Usage:
  fetch_experiment.py --experiment <name> [--json] [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
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


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CONTEXT = _ssl_context()


def _request(
    base_url: str,
    path: str,
    token: str,
    *,
    method: str,
    body: dict[str, Any] | None = None,
    soft_404: bool = False,
) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": user_agent(),
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url + path, data=data, method=method, headers=headers)
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
        if e.code == 404 and soft_404:
            print(
                f"WARN {path} -> 404: endpoint not deployed. The "
                "packageExperiment list endpoint may not be deployed yet. Gather "
                "the experiment's rows another way (read-only DB query) before "
                "winding down.",
                file=sys.stderr,
            )
            return None
        if e.code == 404 and path.startswith("/experiments/"):
            raise SystemExit(
                f"{path} -> 404: experiment not found. Check the experiment name "
                "— the ExperimentService config lookup failed, so the experiment "
                "does not exist (or is mistyped). Do not wind down."
            )
        raise SystemExit(f"{path} -> HTTP {e.code}: {detail}")
    except urllib.error.URLError as e:
        raise SystemExit(f"{path} -> network error: {e.reason}")


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
    parser.add_argument("--experiment", required=True, help="ExperimentService experiment name")
    parser.add_argument("--json", action="store_true", help="print raw JSON payloads")
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

    name = args.experiment
    quoted = urllib.parse.quote(name, safe="")

    snapshot = {
        "packages": _request(
            args.base_url, "/ai/packages/list", token, method="POST",
            body={"includeUnavailable": True},
        ),
        "packagePrices": _request(
            args.base_url, "/ai/packagePrices/list", token, method="POST",
            body={"includeUnavailable": True},
        ),
        "products": _request(
            args.base_url, "/products/list", token, method="POST",
            # limit 0: every product, not PaymentService's default page of 100.
            body={"type": "ai", "limit": 0},
        ),
        "packageExperiments": _request(
            args.base_url, "/ai/packages/experiments/list", token,
            method="POST", body={"experimentId": name}, soft_404=True,
        ),
        "experiment": _request(
            args.base_url, f"/experiments/{quoted}", token, method="GET",
        ),
    }

    if args.json:
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
        return

    print(f"# Wind-down snapshot for experiment: {name}\n")

    exp = snapshot["experiment"]
    if isinstance(exp, dict):
        ended = exp.get("endedAt")
        deleted = exp.get("deletedAt")
        live = ended is None and deleted is None
        print("## Experiment config\n")
        print(f"- endedAt: {_fmt(ended)}")
        print(f"- deletedAt: {_fmt(deleted)}")
        print(f"- active: {_fmt(exp.get('active'))}")
        print(f"- targetApp: {_fmt(exp.get('targetApp'))}")
        if live:
            print("\n**WARNING: experiment appears still LIVE (endedAt and "
                  "deletedAt are both null). Confirm intent before winding down.**")
        print()
    else:
        print("## Experiment config\n\n_not found — see warning above_\n")

    pe = snapshot["packageExperiments"]
    pe_rows = _rows(pe) if pe is not None else []
    print(f"## packageExperiment rows ({len(pe_rows)})\n")
    if pe is None:
        print("_endpoint not deployed — supply rows another way_\n")
    elif pe_rows:
        print(_table(pe_rows, [
            "id", "experimentId", "variant", "action",
            "controlPackageId", "variantPackageId",
            "name", "description", "displayConfig", "features", "deletedAt",
        ]))
        print()
    else:
        print("_no rows for this experiment — nothing to wind down_\n")

    sections = [
        ("Packages", _rows(snapshot["packages"]),
         ["id", "name", "level", "isAvailable", "isDefault", "features",
          "displayConfig", "description"]),
        ("Package prices", _rows(snapshot["packagePrices"]),
         ["id", "packageId", "productId", "provider", "isAvailable"]),
        ("AI products", _rows(snapshot["products"]),
         ["id", "name", "provider", "revenue", "period", "duration",
          "trial", "active", "aiPlan"]),
    ]
    for title, rows, columns in sections:
        print(f"## {title} ({len(rows)})\n")
        print(_table(rows, columns) if rows else "_no rows_")
        print()


if __name__ == "__main__":
    main()
