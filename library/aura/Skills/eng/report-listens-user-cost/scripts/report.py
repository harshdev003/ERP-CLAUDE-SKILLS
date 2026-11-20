#!/usr/bin/env python3
"""
Report Aura content-listen coach-payout cost for ONE specific user.

Point-lookup counterpart to the rank-N `eng:analyze-aura-cost-per-user`
skill. Calls PaymentService's authoritative `POST /payouts/listens/aggregate`
endpoint with a `users=[<UID>]` server-side filter — same rate logic as
the monthly payout job (`src/jobs/payout.js`), not an estimate.

Naming note: this is "listens" (Aura content-listen payouts to coaches) —
the Aura consumer app's cost-of-listens model, distinct from Aurie's
OpenAI inference cost (`eng:report-openai-user-cost`) and ElevenLabs TTS
cost (`eng:report-elevenlabs-user-cost`) and LiveKit-path voice cost
(`eng:report-livekit-user-cost`). All four roll up to the parent
orchestrator `eng:report-user-costs`.

Env:
  AURA_API_TOKEN  PaymentService admin token. Required.

Usage:
  python3 report.py <UID>
  python3 report.py <UID> --window prev-month
  python3 report.py <UID> --from 2026-04-11 --to 2026-05-27
  python3 report.py <UID> --ignore-daily-cap
  python3 report.py <UID> --include-free-listens
  python3 report.py <UID> --by-coach
  python3 report.py <UID> --no-by-tier
  python3 report.py <UID> --json out.json
  python3 report.py <UID> --base-url https://services.dev.aurahealth.io   # staging
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
from pathlib import Path

# Allow `from resolve_window import resolve` regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_window import resolve  # noqa: E402


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
DEFAULT_TIMEOUT = 60


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post(url: str, body: dict, token: str) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": user_agent(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=DEFAULT_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")[:500]
        sys.exit(f"PaymentService {e.code}: {body_text}")


def _fmt_money(cents: int) -> str:
    if cents == 0:
        return "—"
    return f"${cents / 100:,.2f}"


def _tier_label(key: str) -> str:
    return {
        "lt500": "< 500",
        "lt2000": "500 – 1,999",
        "lt5000": "2,000 – 4,999",
        "other": "≥ 5,000",
        "music": "music",
    }.get(key, key)


def render_markdown(report: dict, show_by_tier: bool, by_coach: bool) -> str:
    lines: list[str] = []
    uid_short = report["user_id"][:8] + "…"
    label = report["window"]["label"]
    days_in = report["window"]["days_in"]

    if report["empty"]:
        lines.append(f"## Listens cost — `{uid_short}` ({label})")
        lines.append("")
        lines.append("No qualifying listens in this window.")
        if report.get("exclusion_notes"):
            lines.append("")
            for n in report["exclusion_notes"]:
                lines.append(f"- {n}")
        return "\n".join(lines) + "\n"

    total_cents = report["total"]["cost_cents"]
    total_listens = report["total"]["listen_count"]
    per_day = total_cents / 100 / max(days_in, 1)
    headline_pieces = [
        _fmt_money(total_cents),
        f"{total_listens} qualifying listen{'s' if total_listens != 1 else ''}",
        f"{_fmt_money(int(per_day * 100))}/day",
    ]
    lines.append(f"## Listens cost — `{uid_short}`  ({', '.join(headline_pieces)} over {days_in}d)")
    lines.append("")

    if show_by_tier and report.get("tier_breakdown"):
        lines.append("| Tier | Listens | Cost |")
        lines.append("|---|---:|---:|")
        # Stable tier order
        for k in ("lt500", "lt2000", "lt5000", "other", "music"):
            t = report["tier_breakdown"].get(k)
            if not t or (t.get("listen_count", 0) == 0 and t.get("cost_cents", 0) == 0):
                continue
            lines.append(f"| {_tier_label(k)} | {t['listen_count']:,} | {_fmt_money(t['cost_cents'])} |")
        lines.append("")

    if by_coach and report.get("by_coach"):
        lines.append("**By coach**")
        lines.append("")
        lines.append("| Coach | Listens | Cost |")
        lines.append("|---|---:|---:|")
        for r in report["by_coach"]:
            ck = (r.get("key") or "")[:8] + "…" if r.get("key") else "—"
            lines.append(f"| `{ck}` | {r['listen_count']:,} | {_fmt_money(r['cost_cents'])} |")
        lines.append("")

    if report.get("exclusion_notes"):
        lines.append("**Exclusions:**")
        for n in report["exclusion_notes"]:
            lines.append(f"- {n}")
    return "\n".join(lines).rstrip() + "\n"


def _row_for_user(rows: list[dict], uid: str) -> dict | None:
    for r in rows or []:
        if r.get("key") == uid:
            return r
    return None


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("user_id", help="Firebase UID")
    p.add_argument("--window", help="Preset: mtd | prev-month | last-7d | last-30d | wtd | today | yesterday-pt | today-pt")
    p.add_argument("--from", dest="since", help="Custom start (YYYY-MM-DD, UTC)")
    p.add_argument("--to", dest="until", help="Custom end (YYYY-MM-DD, UTC, exclusive)")
    p.add_argument("--by-tier", dest="by_tier", action="store_true", default=True,
                   help="Show per-tier breakdown table (default on)")
    p.add_argument("--no-by-tier", dest="by_tier", action="store_false",
                   help="Suppress per-tier breakdown; show total only")
    p.add_argument("--by-coach", dest="by_coach", action="store_true", default=False,
                   help="Add a per-coach breakdown of the user's listens")
    p.add_argument("--ignore-daily-cap", action="store_true",
                   help="Bypass the 6-listens-per-(user,coach)-per-day production cap")
    p.add_argument("--include-free-listens", action="store_true",
                   help="Include free / trial / payment-pending listens (zero-cost; inflates counts)")
    p.add_argument("--base-url", default=os.environ.get("AURA_API_BASE_URL", DEFAULT_BASE_URL),
                   help="PaymentService base URL (default: $AURA_API_BASE_URL or production)")
    p.add_argument("--json", dest="json_out", help="Write structured report JSON to this path")
    args = p.parse_args()

    token = os.environ.get("AURA_API_TOKEN")
    if not token:
        sys.exit("AURA_API_TOKEN not set — see CLAUDE.md → 'Token inventory'.")

    win = resolve(args.window, args.since, args.until)

    # Call 1: userId-grouped, filtered to just our user → tier breakdown row
    body_user = {
        "since": win["from_iso"],
        "until": win["to_iso"],
        "groupBy": "userId",
        "includeMusic": True,
        "includeNonMusic": True,
        "respectDailyCap": not args.ignore_daily_cap,
        "requirePremiumPaid": not args.include_free_listens,
        "users": [args.user_id],
        "limit": 1,
    }
    resp_user = _post(args.base_url + ENDPOINT_PATH, body_user, token)
    user_row = _row_for_user(resp_user.get("rows", []), args.user_id)
    summary = resp_user.get("summary") or {}

    exclusion_notes: list[str] = []
    excluded_cap = summary.get("excluded_by_daily_cap") or 0
    excluded_premium = summary.get("excluded_by_premium") or 0
    excluded_type = summary.get("excluded_by_content_type") or 0
    if excluded_cap:
        exclusion_notes.append(f"{excluded_cap:,} listen{'s' if excluded_cap != 1 else ''} dropped by the 6-per-(user,coach)-per-day cap.")
    if excluded_premium and not args.include_free_listens:
        exclusion_notes.append(f"{excluded_premium:,} free / trial / payment-pending listen{'s' if excluded_premium != 1 else ''} excluded (zero-cost). Pass `--include-free-listens` to count them.")
    if excluded_type:
        exclusion_notes.append(f"{excluded_type:,} listen{'s' if excluded_type != 1 else ''} excluded by content type (podcasts, deleted coaches, etc.).")
    if args.ignore_daily_cap:
        exclusion_notes.append("`--ignore-daily-cap` is set: cost above includes listens that would normally be capped out.")
    if args.include_free_listens:
        exclusion_notes.append("`--include-free-listens` is set: listen counts include free / trial / payment-pending listens (which are zero-cost).")

    # Optional Call 2: per-coach breakdown for this user
    by_coach_rows: list[dict] = []
    if args.by_coach:
        body_coach = dict(body_user)
        body_coach["groupBy"] = "coachId"
        body_coach["limit"] = 50
        resp_coach = _post(args.base_url + ENDPOINT_PATH, body_coach, token)
        by_coach_rows = sorted(
            resp_coach.get("rows", []),
            key=lambda r: -int(r.get("cost_cents", 0) or 0),
        )

    if not user_row:
        report = {
            "user_id": args.user_id,
            "window": win,
            "empty": True,
            "total": {"cost_cents": 0, "listen_count": 0},
            "tier_breakdown": {},
            "by_coach": by_coach_rows,
            "exclusion_notes": exclusion_notes,
        }
    else:
        report = {
            "user_id": args.user_id,
            "window": win,
            "empty": False,
            "total": {
                "cost_cents": int(user_row.get("cost_cents") or 0),
                "listen_count": int(user_row.get("listen_count") or 0),
            },
            "tier_breakdown": user_row.get("tier_breakdown") or {},
            "by_coach": by_coach_rows,
            "exclusion_notes": exclusion_notes,
        }

    print(render_markdown(report, show_by_tier=args.by_tier, by_coach=args.by_coach))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2))
        print(f"# Wrote structured report to {args.json_out}", file=sys.stderr)


if __name__ == "__main__":
    main()
