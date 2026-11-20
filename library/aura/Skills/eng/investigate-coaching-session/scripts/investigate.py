#!/usr/bin/env python3
"""Diagnose a user's coaching session state across CoachService.

Correlates subscription, credits, coach bookability, and recent appointment
status into a single verdict word and (where fixable) a copy-paste fix command.

Usage:
  investigate.py <userId> <coachId> [--appointment-id <id>] [--json] [--quiet]

Verdicts (priority order):
  COACH_NOT_BOOKABLE   coach.bookable=false OR no resourceId
  NO_SUBSCRIPTION      no coaching subscription found for this user/coach pair
  OUT_OF_CREDITS       subscription active but 0 credits remaining
  APPOINTMENT_UNCONFIRMED  most recent (or named) appointment is pending/unconfirmed
  CANCELLED            most recent (or named) appointment is cancelled
  HEALTHY              all signals green
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

COACH_PATH = "/scheduling/coaches/{coachId}"
SUBSCRIPTION_PATH = "/coaching/subscriptions/{userId}/{coachId}"
CREDITS_LIST_PATH = "/coaching/subscriptions/credits/list"
CREDITS_HISTORY_PATH = "/coaching/subscriptions/credits/history/list"
APPOINTMENTS_LIST_PATH = "/scheduling/appointments/list"
APPOINTMENT_PATH = "/scheduling/appointments/{appointmentId}"

# Appointment statuses that map to fixable verdicts.
_UNCONFIRMED_STATUSES = {"pending", "unconfirmed", "awaiting_confirmation"}
_CANCELLED_STATUSES = {"cancelled", "canceled", "declined"}


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL = _ssl_context()


def _request(
    url: str,
    method: str,
    *,
    token: str,
    body: bytes | None = None,
) -> tuple[int, Any]:
    headers: dict[str, str] = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": user_agent(),
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL) as resp:
            raw = resp.read().decode("utf-8")
            status = resp.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, f"network error: {exc.reason}"
    try:
        return status, json.loads(raw)
    except json.JSONDecodeError:
        return status, raw


# ---------------------------------------------------------------------------
# Individual fetchers
# ---------------------------------------------------------------------------

def fetch_coach(coach_id: str, token: str) -> tuple[int, Any]:
    url = AURA_API_HOST + COACH_PATH.format(coachId=coach_id)
    return _request(url, "GET", token=token)


def fetch_subscription(user_id: str, coach_id: str, token: str) -> tuple[int, Any]:
    url = AURA_API_HOST + SUBSCRIPTION_PATH.format(userId=user_id, coachId=coach_id)
    return _request(url, "GET", token=token)


def fetch_credits(user_id: str, token: str) -> tuple[int, Any]:
    url = AURA_API_HOST + CREDITS_LIST_PATH
    body = json.dumps({"userId": user_id}).encode()
    return _request(url, "POST", token=token, body=body)


def fetch_credits_history(user_id: str, token: str) -> tuple[int, Any]:
    url = AURA_API_HOST + CREDITS_HISTORY_PATH
    body = json.dumps({"userId": user_id}).encode()
    return _request(url, "POST", token=token, body=body)


def fetch_appointments(user_id: str, coach_id: str, token: str) -> tuple[int, Any]:
    url = AURA_API_HOST + APPOINTMENTS_LIST_PATH
    body = json.dumps({"userId": user_id, "coachId": coach_id}).encode()
    return _request(url, "POST", token=token, body=body)


def fetch_appointment(appointment_id: str, token: str) -> tuple[int, Any]:
    url = AURA_API_HOST + APPOINTMENT_PATH.format(appointmentId=appointment_id)
    return _request(url, "GET", token=token)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _most_recent_appointment(appointments: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not appointments:
        return None
    return max(
        appointments,
        key=lambda a: _parse_iso(a.get("createdAt")) or datetime.min.replace(tzinfo=timezone.utc),
    )


def _credits_remaining(credits_payload: Any) -> int | None:
    """Extract remaining credits from the credits/list response.

    The response shape is inferred from the endpoint semantics. Try common
    field names; return None if the shape is unrecognised (caller degrades
    gracefully rather than crashing).
    """
    if isinstance(credits_payload, dict):
        for key in ("remaining", "balance", "credits", "sessionsRemaining", "total"):
            if key in credits_payload:
                val = credits_payload[key]
                if isinstance(val, (int, float)):
                    return int(val)
    # Credits might be a list of records (one per coach).
    if isinstance(credits_payload, list):
        total = 0
        for item in credits_payload:
            if isinstance(item, dict):
                for key in ("remaining", "balance", "credits", "sessionsRemaining"):
                    if key in item:
                        val = item[key]
                        if isinstance(val, (int, float)):
                            total += int(val)
        return total if credits_payload else None
    return None


def _last_credit_event(history_payload: Any) -> str:
    """Return a human-readable summary of the most recent credit history event."""
    if not isinstance(history_payload, list) or not history_payload:
        return "—"
    events = sorted(
        history_payload,
        key=lambda e: _parse_iso(e.get("createdAt") if isinstance(e, dict) else None)
                      or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    evt = events[0]
    if not isinstance(evt, dict):
        return "—"
    adj = evt.get("adjustment", evt.get("delta", "?"))
    when = evt.get("createdAt", "?")[:10] if evt.get("createdAt") else "?"
    reason = evt.get("reason", evt.get("note", ""))
    return f"{adj:+} on {when}" + (f" ({reason})" if reason else "")


# ---------------------------------------------------------------------------
# Verdict + report
# ---------------------------------------------------------------------------

def determine_verdict(
    *,
    coach: dict[str, Any] | None,
    coach_status: int,
    sub: dict[str, Any] | None,
    sub_status: int,
    credits_remaining: int | None,
    appointment: dict[str, Any] | None,
) -> str:
    # 1. Coach not reachable / not bookable.
    if coach_status == 404:
        return "COACH_NOT_BOOKABLE"
    if coach_status < 400 and isinstance(coach, dict):
        if not coach.get("bookable", True) or not coach.get("resourceId"):
            return "COACH_NOT_BOOKABLE"

    # 2. No subscription.
    if sub_status == 404 or sub is None:
        return "NO_SUBSCRIPTION"

    # 3. Active sub but no credits.
    if credits_remaining is not None and credits_remaining == 0:
        return "OUT_OF_CREDITS"

    # 4. Appointment-level issues (only if we have one to inspect).
    if appointment and isinstance(appointment, dict):
        status = (appointment.get("status") or "").lower()
        if status in _UNCONFIRMED_STATUSES:
            return "APPOINTMENT_UNCONFIRMED"
        if status in _CANCELLED_STATUSES:
            return "CANCELLED"

    return "HEALTHY"


def _fix_block(
    verdict: str,
    user_id: str,
    coach_id: str,
    appointment: dict[str, Any] | None,
) -> str | None:
    """Return a markdown fix block for actionable verdicts, or None."""
    lines: list[str] = []

    if verdict == "OUT_OF_CREDITS":
        lines += [
            "### Fix — refund one session credit",
            "",
            "Run after confirming the credit was incorrectly consumed:",
            "",
            "```bash",
            "curl -X POST https://services.aurahealth.io/coaching/subscriptions/credits/adjust \\",
            "  -H \"Authorization: Bearer $AURA_API_TOKEN\" \\",
            "  -H \"Content-Type: application/json\" \\",
            f"  -d '{{\"userId\":\"{user_id}\",\"coachId\":\"{coach_id}\",\"adjustment\":1,"
            "\"reason\":\"manual correction — session credit refund\"}}'",
            "```",
        ]

    elif verdict == "COACH_NOT_BOOKABLE":
        lines += [
            "### Fix — enable coach bookability",
            "",
            "```bash",
            "curl -X PUT https://services.aurahealth.io/scheduling/coaches/bookable \\",
            "  -H \"Authorization: Bearer $AURA_API_TOKEN\" \\",
            "  -H \"Content-Type: application/json\" \\",
            f"  -d '{{\"coachId\":\"{coach_id}\",\"bookable\":true}}'",
            "```",
            "",
            "> If the coach has no `resourceId`, their calendar is not connected.",
            "> They must reconnect via their coaching settings page — this API call alone will not be sufficient.",
        ]

    elif verdict == "APPOINTMENT_UNCONFIRMED" and appointment:
        appt_id = appointment.get("id", appointment.get("appointmentId", "<appointmentId>"))
        lines += [
            "### Fix — confirm the appointment",
            "",
            "```bash",
            f"curl -X PUT https://services.aurahealth.io/scheduling/appointments/{appt_id}/confirm \\",
            "  -H \"Authorization: Bearer $AURA_API_TOKEN\" \\",
            "  -H \"Content-Type: application/json\" \\",
            "  -d '{}'",
            "```",
        ]

    elif verdict == "CANCELLED" and appointment:
        appt_id = appointment.get("id", appointment.get("appointmentId", "<appointmentId>"))
        lines += [
            "### Fix — reschedule the cancelled appointment",
            "",
            "Replace `<new-start-time>` with an ISO-8601 datetime before running:",
            "",
            "```bash",
            f"curl -X PUT https://services.aurahealth.io/scheduling/appointments/{appt_id}/reschedule \\",
            "  -H \"Authorization: Bearer $AURA_API_TOKEN\" \\",
            "  -H \"Content-Type: application/json\" \\",
            "  -d '{\"start\":\"<new-start-time>\"}'",
            "```",
        ]

    return "\n".join(lines) if lines else None


def render_report(
    *,
    user_id: str,
    coach_id: str,
    coach: dict[str, Any] | None,
    coach_status: int,
    sub: dict[str, Any] | None,
    sub_status: int,
    credits_payload: Any,
    credits_status: int,
    history_payload: Any,
    appointment: dict[str, Any] | None,
    verdict: str,
) -> str:
    lines: list[str] = []
    lines.append(f"## Coaching session investigation: `{user_id}` → coach `{coach_id}`")
    lines.append("")

    # --- Signal table ---
    lines.append("| Signal | Status | Detail |")
    lines.append("|---|---|---|")

    # Coach
    if coach_status == 404:
        lines.append("| Coach record | ✗ 404 | Coach not found |")
    elif coach_status >= 400:
        lines.append(f"| Coach record | ✗ error | HTTP {coach_status} |")
    elif isinstance(coach, dict):
        bookable = coach.get("bookable", "?")
        resource_id = coach.get("resourceId") or ""
        bookable_icon = "✓" if bookable is True else "✗"
        resource_icon = "✓ present" if resource_id else "✗ missing (calendar not connected)"
        lines.append(
            f"| Coach bookable | {bookable_icon} {bookable} | resourceId: {resource_icon} |"
        )
    else:
        lines.append(f"| Coach record | ✗ error | HTTP {coach_status} |")

    # Subscription
    if sub_status == 404 or sub is None:
        lines.append("| Subscription | ✗ not found | No coaching subscription for this pair |")
    elif sub_status >= 400:
        lines.append(f"| Subscription | ✗ error | HTTP {sub_status} |")
    elif isinstance(sub, dict):
        sub_id = sub.get("id", sub.get("subscriptionId", "?"))
        state = sub.get("status", sub.get("state", "active"))
        lines.append(f"| Subscription | ✓ {state} | id: {sub_id} |")
    else:
        lines.append("| Subscription | ✓ found | (unexpected shape) |")

    # Credits
    cred = _credits_remaining(credits_payload)
    last_event = _last_credit_event(history_payload)
    if credits_status >= 400 or credits_payload is None:
        lines.append(f"| Credits remaining | ✗ error | HTTP {credits_status} |")
    elif cred is None:
        lines.append("| Credits remaining | ? unknown | unrecognised response shape |")
    elif cred == 0:
        lines.append(f"| Credits remaining | ✗ 0 | last event: {last_event} |")
    else:
        lines.append(f"| Credits remaining | ✓ {cred} | last event: {last_event} |")

    # Appointment
    if appointment is None:
        lines.append("| Last appointment | — | No appointments found |")
    elif isinstance(appointment, dict):
        status = appointment.get("status", "?")
        appt_id = appointment.get("id", appointment.get("appointmentId", "?"))
        start = (appointment.get("start") or "?")[:10]
        status_icon = "✓" if status.lower() in {"confirmed", "completed", "attended"} else "⚠"
        lines.append(f"| Last appointment | {status_icon} {status} | {start} — id: {appt_id} |")

    # --- Verdict ---
    lines.append("")
    lines.append(f"**Verdict: {verdict}**")

    # --- Fix block ---
    fix = _fix_block(verdict, user_id, coach_id, appointment)
    if fix:
        lines.append("")
        lines.append(fix)

    # --- Suggested next steps for non-fixable verdicts ---
    if verdict == "NO_SUBSCRIPTION":
        lines.append("")
        lines.append("### Suggested next step")
        lines.append(
            "If the user reports they paid for coaching, check whether PaymentService"
            " registered the purchase:"
        )
        lines.append("")
        lines.append("```")
        lines.append(f"eng:check-user-receipts {user_id}")
        lines.append("```")
        lines.append("")
        lines.append(
            "Or run `eng:investigate-missing-premium` if you suspect a subscription"
            " sync issue."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id", metavar="userId", help="Aura Firebase user ID")
    parser.add_argument("coach_id", metavar="coachId", help="CoachService coach ID")
    parser.add_argument(
        "--appointment-id",
        metavar="ID",
        help="Investigate a specific appointment instead of the most-recent match",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw API payloads as JSON instead of the verdict table",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Print only the verdict word (for scripting)",
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

    user_id = args.user_id
    coach_id = args.coach_id

    # --- Fetch all signals ---
    coach_status, coach = fetch_coach(coach_id, token)
    sub_status, sub = fetch_subscription(user_id, coach_id, token)
    credits_status, credits_payload = fetch_credits(user_id, token)
    _hist_status, history_payload = fetch_credits_history(user_id, token)

    appts_status, appts_payload = fetch_appointments(user_id, coach_id, token)
    appointments: list[dict[str, Any]] = (
        appts_payload if isinstance(appts_payload, list)
        else appts_payload.get("appointments", appts_payload.get("data", []))
        if isinstance(appts_payload, dict)
        else []
    )

    # Resolve appointment to inspect.
    appointment: dict[str, Any] | None = None
    if args.appointment_id:
        appt_status, appt_payload = fetch_appointment(args.appointment_id, token)
        if appt_status < 400 and isinstance(appt_payload, dict):
            appointment = appt_payload
        else:
            print(
                f"warning: GET /appointments/{args.appointment_id} returned HTTP {appt_status}",
                file=sys.stderr,
            )
    else:
        appointment = _most_recent_appointment(appointments)

    if not isinstance(coach, dict) and coach_status < 400:
        coach = None
    if not isinstance(sub, dict) and sub_status < 400:
        sub = None

    cred = _credits_remaining(credits_payload)

    # --- Raw JSON mode ---
    if args.json:
        print(
            json.dumps(
                {
                    "coach": {"status": coach_status, "payload": coach},
                    "subscription": {"status": sub_status, "payload": sub},
                    "credits": {"status": credits_status, "payload": credits_payload},
                    "creditsHistory": {"status": _hist_status, "payload": history_payload},
                    "appointments": {"status": appts_status, "payload": appts_payload},
                    "selectedAppointment": appointment,
                },
                indent=2,
                default=str,
            )
        )
        return 0

    # --- Verdict ---
    verdict = determine_verdict(
        coach=coach,
        coach_status=coach_status,
        sub=sub,
        sub_status=sub_status,
        credits_remaining=cred,
        appointment=appointment,
    )

    if args.quiet:
        print(verdict)
        return 0 if verdict == "HEALTHY" else 1

    report = render_report(
        user_id=user_id,
        coach_id=coach_id,
        coach=coach,
        coach_status=coach_status,
        sub=sub,
        sub_status=sub_status,
        credits_payload=credits_payload,
        credits_status=credits_status,
        history_payload=history_payload,
        appointment=appointment,
        verdict=verdict,
    )
    print(report)
    return 0 if verdict == "HEALTHY" else 1


if __name__ == "__main__":
    sys.exit(main())
