#!/usr/bin/env python3
"""
Call PaymentService `POST /payouts/listens/aggregate` and write the JSON
response to stdout (or to a file via --output).

This is the data-fetch half of the Aura cost skill family
(`analyze-aura-cost`, `analyze-aura-cost-per-user`, `analyze-aura-cost-per-coach`,
future `report-top-aura-listeners`). The output is the authoritative payload
— computed server-side using the production payout-rate logic (tiered rates
by `contentListenedCount`, music vs non-music, daily cap, premium-paid only,
podcaster + deleted-coach exclusions). See PROD-1961 for the endpoint spec.

Canonical copy lives in eng:analyze-aura-cost-per-user/scripts/call_aggregate.py;
this is a sibling copy per the self-contained-skills rule (CLAUDE.md →
Architecture principles → 1). Sync edits across all copies in the same PR.

Auth: requires `AURA_API_TOKEN` in env. Falls back to a clear error message
if missing — never tries to call the endpoint unauthenticated.

Usage:
    python3 call_aggregate.py --since 2026-05-12T00:00:00Z --until 2026-05-19T00:00:00Z
    python3 call_aggregate.py --since ... --until ... --group-by coachId --limit 50
    python3 call_aggregate.py --since ... --until ... --no-music
    python3 call_aggregate.py --since ... --until ... --output /tmp/agg.json
    python3 call_aggregate.py --since ... --until ... --base-url https://services.dev.aurahealth.io

Stdlib only — uses `certifi` opportunistically if installed (a common transitive
dep) so the script works on stock macOS Python where the bundled CA store is
empty. Falls back to the system default otherwise.
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
ENDPOINT_PATH = "/payouts/listens/aggregate"
# Server-side accepts: "userId" | "coachId" | "premiumState" | null (passed as "none" on the CLI).
# When groupBy=premiumState the server forces requirePremiumPaid=false so non-paid listens
# appear in their own buckets — see PaymentService listenModel.aggregate.
GROUP_BY_CHOICES = ("userId", "coachId", "premiumState", "none")
DEFAULT_TIMEOUT_SECONDS = 60


def _build_body(args: argparse.Namespace) -> dict:
    """Construct the JSON body for the endpoint. See PROD-1961 for the spec."""
    body: dict = {
        "since": args.since,
        "until": args.until,
        "includeMusic": args.include_music,
        "includeNonMusic": args.include_non_music,
        "respectDailyCap": args.respect_daily_cap,
        "requirePremiumPaid": args.require_premium_paid,
        "limit": args.limit,
    }
    # groupBy: "none" → null, drops the rows array on the server side
    if args.group_by == "none":
        body["groupBy"] = None
    else:
        body["groupBy"] = args.group_by
    if args.users:
        body["users"] = args.users
    if args.coaches:
        body["coaches"] = args.coaches
    return body


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context that works on stock macOS Python.

    macOS-bundled Python (python.org installer) ships without CA certs loaded
    until the user manually runs `Install Certificates.command`. We work around
    that by using `certifi`'s bundle when the package is importable (it's a
    common transitive dep — installed by `requests`, `urllib3`, etc.) and
    falling back to the system default otherwise.
    """
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post_json(url: str, body: dict, token: str, timeout: int) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": user_agent(),
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Surface the server's error JSON when it's structured
        raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
        msg = f"HTTP {e.code} from PaymentService"
        try:
            err = json.loads(raw)
            err_key = err.get("error", {}).get("key") or err.get("key")
            err_msg = err.get("error", {}).get("message") or err.get("message")
            if err_key or err_msg:
                msg = f"{msg}: {err_key or ''} {err_msg or ''}".strip()
        except (json.JSONDecodeError, AttributeError):
            if raw:
                msg = f"{msg}: {raw[:200]}"
        sys.exit(msg)
    except urllib.error.URLError as e:
        sys.exit(f"Network error calling PaymentService: {e.reason}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Window
    p.add_argument(
        "--since", required=True,
        help="Inclusive ISO timestamp lower bound on createdAt, e.g. 2026-05-12T00:00:00Z",
    )
    p.add_argument(
        "--until", required=True,
        help="Exclusive ISO timestamp upper bound on createdAt, e.g. 2026-05-19T00:00:00Z",
    )
    # Grouping + limit
    p.add_argument(
        "--group-by", choices=GROUP_BY_CHOICES, default="userId",
        help=(
            "userId (listener spend, default) | coachId (coach earnings) | "
            "premiumState (split by paid/paymentPending/trial/free/nonPremium — "
            "server auto-disables requirePremiumPaid in this mode) | "
            "none (summary-only, rows array stays empty)"
        ),
    )
    p.add_argument(
        "--limit", type=int, default=100,
        help="Top-N to enumerate in rows (default 100, max 5000 per the API)",
    )
    # Filters
    p.add_argument(
        "--users", nargs="+", default=None,
        help="Restrict to a set of listener userIds",
    )
    p.add_argument(
        "--coaches", nargs="+", default=None,
        help="Restrict to a set of coachIds (content.authorId on the server side)",
    )
    # Production-rule overrides (defaults match the monthly payout job)
    music_group = p.add_mutually_exclusive_group()
    music_group.add_argument(
        "--include-music", dest="include_music", action="store_true", default=True,
        help="Include music/soundscape listens (default on)",
    )
    music_group.add_argument(
        "--no-music", dest="include_music", action="store_false",
        help="Exclude music/soundscape listens",
    )
    non_music_group = p.add_mutually_exclusive_group()
    non_music_group.add_argument(
        "--include-non-music", dest="include_non_music", action="store_true", default=True,
        help="Include non-music listens (default on)",
    )
    non_music_group.add_argument(
        "--no-non-music", dest="include_non_music", action="store_false",
        help="Exclude non-music listens",
    )
    cap_group = p.add_mutually_exclusive_group()
    cap_group.add_argument(
        "--respect-daily-cap", dest="respect_daily_cap", action="store_true", default=True,
        help="Cap at 6 listens per (user, coach) per day (production default)",
    )
    cap_group.add_argument(
        "--ignore-daily-cap", dest="respect_daily_cap", action="store_false",
        help="Count listens beyond the 6-per-(user,coach)-per-day cap",
    )
    premium_group = p.add_mutually_exclusive_group()
    premium_group.add_argument(
        "--require-premium-paid", dest="require_premium_paid", action="store_true", default=True,
        help="Only count premium-paid listens (production default — these are the $-bearing ones)",
    )
    premium_group.add_argument(
        "--include-free-listens", dest="require_premium_paid", action="store_false",
        help="Include free / trial / payment-pending listens (these earn $0 but inflate counts)",
    )
    # Transport
    p.add_argument(
        "--base-url", default=os.environ.get("AURA_API_BASE_URL", DEFAULT_BASE_URL),
        help=f"PaymentService base URL (default {DEFAULT_BASE_URL}, or $AURA_API_BASE_URL)",
    )
    p.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default {DEFAULT_TIMEOUT_SECONDS})",
    )
    p.add_argument(
        "--output", default=None,
        help="Write response JSON to this path instead of stdout",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the assembled request body to stderr and exit 0 — no network call",
    )
    args = p.parse_args()

    token = os.environ.get("AURA_API_TOKEN")
    if not token and not args.dry_run:
        sys.exit(
            "AURA_API_TOKEN not set. Add it to ~/.claude/settings.json under env "
            "(see CLAUDE.md → Secrets and environment) and restart Claude Code."
        )

    body = _build_body(args)

    if args.dry_run:
        print("Would POST to:", file=sys.stderr)
        print(f"  {args.base_url.rstrip('/')}{ENDPOINT_PATH}", file=sys.stderr)
        print("With body:", file=sys.stderr)
        print(json.dumps(body, indent=2), file=sys.stderr)
        return

    url = args.base_url.rstrip("/") + ENDPOINT_PATH
    payload = _post_json(url, body, token, args.timeout)

    serialized = json.dumps(payload, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.write("\n")
    else:
        print(serialized)


if __name__ == "__main__":
    main()
