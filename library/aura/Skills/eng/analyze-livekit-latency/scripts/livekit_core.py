"""
Shared LiveKit-log query + parse core for the LiveKit monitoring skill family.

The Aurie voice agent runs on LiveKit Cloud (not the GKE cluster), and its logs are
forwarded to GCP Cloud Logging under a dedicated log stream — NOT the k8s_container
resource the analyze-gcp-logs / investigate-gcp-logs skills query. This module is the
single seam every LiveKit skill uses to reach those forwarded logs:

    logName   = projects/firebase-auratech16/logs/aurie-voice-agent
    resource  = global

It carries the same GCP auth ladder as the k8s log skills (GCP_KEY → GAC → ADC) plus
LiveKit-specific parsing: session aggregation (one "session" == one room), turn-latency
metric extraction, and PII redaction.

There is deliberately NO LiveKit billing/usage API call here — the `lk` CLI exposes none
(spend lives only in the LiveKit Cloud dashboard). Cost is DERIVED from session minutes ×
a configured rate; see analyze-livekit-cost/references/methodology.md.

Self-contained by design: this file is copied verbatim into each LiveKit skill's scripts/
dir (the repo's "skills are self-contained — no cross-skill imports" rule). Keep the copies
in sync when editing.

Read-only. No writes anywhere.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

try:
    from google.api_core.gapic_v1.client_info import ClientInfo
    from google.cloud import logging as cloud_logging
    from google.oauth2 import service_account
    import google.auth
except ImportError as e:
    print(
        f"ERROR: {sys.executable} has no {e.name}. "
        f"Run it with ~/.claude/python-env/bin/python3 (built by shared:setup-ainative).",
        file=sys.stderr,
    )
    sys.exit(2)

# ---------------------------------------------------------------------------
# LiveKit-specific constants
# ---------------------------------------------------------------------------

# The forwarded log stream for the Aurie voice agent. Every LiveKit skill anchors on this
# logName so it never collides with the k8s_container logs the other GCP skills read.
LIVEKIT_LOG_NAME = "projects/firebase-auratech16/logs/aurie-voice-agent"
DEFAULT_PROJECT = "firebase-auratech16"
SERVICE_NAME = "aurie-voice-agent"

# Turn-latency metrics the agent emits (one "Turn latency" line per metric per turn, carrying
# jsonPayload.metric + jsonPayload.value_ms). Only analyze-livekit-latency iterates this tuple;
# the other copies carry it unused, so widening it changes no other skill's output. Extend it in
# the canonical and propagate to every copy — extending one copy alone is what drifted this file.
LATENCY_METRICS = (
    "e2e_latency_ms",
    "eou_delay_ms",
    "transcription_delay_ms",
    "llm_ttft_ms",
    "tts_ttfb_ms",
)

_SINCE_RE = re.compile(r"^(\d+)([smhd])$")


# ---------------------------------------------------------------------------
# Auth ladder (mirrors analyze-gcp-logs/scripts/gcp.py — prefer ADC over long-lived keys)
# ---------------------------------------------------------------------------


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
    """`AuraClaude/<who>/<plugin>:<skill>@<version> (session <id>)` -- labels this skill's calls in RTDB and GCP logs.

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


class AuthError(RuntimeError):
    pass


@dataclass
class AuthResult:
    client: "cloud_logging.Client"
    project: str
    source: str  # "GCP_KEY" | "GOOGLE_APPLICATION_CREDENTIALS" | "ADC"
    principal: str


def get_client(project_override: Optional[str] = None) -> AuthResult:
    """Resolve credentials per the auth ladder and return a Cloud Logging client.

    1. GCP_KEY (inline JSON in env) → SA
    2. GOOGLE_APPLICATION_CREDENTIALS (file path) → SA
    3. ADC (gcloud auth application-default login) → user creds
    """
    gcp_key = os.environ.get("GCP_KEY")
    if gcp_key:
        try:
            info = json.loads(gcp_key)
        except json.JSONDecodeError as e:
            raise AuthError(
                f"GCP_KEY is set but did not parse as JSON ({e}). It must be a single-line JSON "
                f"string in ~/.claude/settings.json (internal quotes escaped)."
            )
        try:
            creds = service_account.Credentials.from_service_account_info(info)
        except (KeyError, ValueError) as e:
            raise AuthError(f"GCP_KEY parsed as JSON but is not a valid service-account key ({e}).")
        project = project_override or info.get("project_id") or DEFAULT_PROJECT
        return AuthResult(
            client=cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())),
            project=project,
            source="GCP_KEY",
            principal=info.get("client_email", "<unknown>"),
        )

    gac_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if gac_path:
        if not os.path.isfile(gac_path):
            raise AuthError(f"GOOGLE_APPLICATION_CREDENTIALS points to {gac_path} but no such file.")
        try:
            with open(gac_path) as f:
                info = json.load(f)
            creds = service_account.Credentials.from_service_account_file(gac_path)
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            raise AuthError(f"SA key at {gac_path} could not be parsed ({e}).")
        project = project_override or info.get("project_id") or DEFAULT_PROJECT
        return AuthResult(
            client=cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())),
            project=project,
            source="GOOGLE_APPLICATION_CREDENTIALS",
            principal=info.get("client_email", "<unknown>"),
        )

    try:
        creds, default_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/logging.read"]
        )
    except Exception as e:
        raise AuthError(
            f"No GCP_KEY or GOOGLE_APPLICATION_CREDENTIALS set, and ADC failed ({e}).\n"
            f"Fix: set GCP_KEY in ~/.claude/settings.json or run: gcloud auth application-default login"
        )
    project = project_override or default_project or DEFAULT_PROJECT
    principal = getattr(creds, "service_account_email", None) or "<adc-user>"
    return AuthResult(
        client=cloud_logging.Client(project=project, credentials=creds, client_info=ClientInfo(user_agent=user_agent())),
        project=project,
        source="ADC",
        principal=principal,
    )


# ---------------------------------------------------------------------------
# Time-window helpers
# ---------------------------------------------------------------------------


def parse_since(since: str) -> tuple[timedelta, str]:
    """'4h' → (timedelta(hours=4), '4h'). Raises ValueError on bad input."""
    m = _SINCE_RE.match(since)
    if not m:
        raise ValueError(f"--since must be like 30m, 4h, 2d (got {since!r})")
    n, unit = int(m.group(1)), m.group(2)
    seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit] * n
    return timedelta(seconds=seconds), since


def utc_day_bounds(day_offset: int = 1) -> tuple[datetime, datetime, str]:
    """Return (start, end, label) for a whole UTC day. day_offset=1 → yesterday, 0 → today-so-far."""
    now = datetime.now(timezone.utc)
    target = (now - timedelta(days=day_offset)).replace(hour=0, minute=0, second=0, microsecond=0)
    end = target + timedelta(days=1)
    if day_offset == 0:
        end = now
    label = target.strftime("%Y-%m-%d") + (" (UTC)" if day_offset else " (UTC, so far)")
    return target, end, label


def range_filter(start: datetime, end: datetime) -> str:
    return (
        f'timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}" '
        f'AND timestamp<"{end.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )


def since_filter(window: timedelta) -> str:
    cutoff = datetime.now(timezone.utc) - window
    return f'timestamp>="{cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")}"'


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def base_filter(time_clause: str, extra: Optional[str] = None) -> str:
    """Assemble a Cloud Logging filter anchored to the LiveKit agent log stream."""
    parts = [f'logName="{LIVEKIT_LOG_NAME}"', time_clause]
    if extra:
        parts.append(extra)
    return " AND ".join(parts)


class TruncatedReadError(RuntimeError):
    """A windowed read hit `max_entries`, so the window is not fully represented.

    Carries the partial `entries` so a caller that genuinely wants a best-effort read can
    use them — but only after catching this, which is the point. A truncated walk yields
    fewer sessions and fewer minutes with nothing to distinguish it from a quiet period,
    so the quantity it produces is wrong in the reassuring direction. Same rule as the
    quota rejection in the daily-cost store's `livekit_minutes.py` (ENG-1214): surface it,
    never return it as if it were whole.
    """

    def __init__(self, entries: list, max_entries: int, order_by: str):
        self.entries = entries
        self.max_entries = max_entries
        self.order_by = order_by
        super().__init__(
            f"read hit the {max_entries}-entry cap, so this window is truncated to its most "
            f"recent {len(entries)} entries — session and minute totals derived from it "
            f"UNDERCOUNT. Widen max_entries, narrow the window, or catch "
            f"TruncatedReadError and report the shortfall."
        )


def list_entries(
    auth: AuthResult,
    filter_str: str,
    page_size: int = 1000,
    max_entries: int = 20000,
    order_by: str = "timestamp asc",
    allow_truncated: bool = False,
) -> Iterator[dict]:
    """Yield Cloud Logging entries as API-repr dicts, oldest-first by default.

    When a window's entry count exceeds max_entries, fetching in the requested order_by
    ("timestamp asc") would silently keep the OLDEST entries and drop the most recent
    activity — the opposite of what every caller wants on a busy day. So when the caller
    asks for ascending order, this fetches newest-first internally, caps there, and reverses
    before yielding — the retained entries are always the most recent ones, still delivered
    in ascending order to match the docstring / every existing caller's expectations.

    Hitting the cap raises `TruncatedReadError` unless `allow_truncated=True`. Deciding
    *which* entries to keep is not the same as telling the caller that a choice was made:
    the newest-first policy above was correct and still produced a silent 3x under-report
    in `eng:report-livekit-quota`, which measured the most recent ~4.5 days of a month and
    rendered it as the month (ENG-1297). The sibling `gcp.py` family checks
    `len(raw) >= max_entries` at each call site; six callers of this file did not, which is
    why the check lives here instead — a contract nobody can forget to apply.
    """
    fetch_order = "timestamp desc" if order_by == "timestamp asc" else order_by
    buffer: list[dict] = []
    count = 0
    for entry in auth.client.list_entries(filter_=filter_str, page_size=page_size, order_by=fetch_order):
        buffer.append(entry.to_api_repr())
        count += 1
        if count >= max_entries:
            break
    if fetch_order != order_by:
        buffer.reverse()
    if count >= max_entries and not allow_truncated:
        raise TruncatedReadError(buffer, max_entries, order_by)
    yield from buffer


def payload(entry: dict) -> dict:
    """Return the entry's jsonPayload (empty dict when absent)."""
    return entry.get("jsonPayload") or {}


# ---------------------------------------------------------------------------
# Session aggregation
# ---------------------------------------------------------------------------


def _parse_ts(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        if "." in ts:
            head, frac = ts.split(".")
            frac = frac.rstrip("Z")[:6]
            iso = f"{head}.{frac}+00:00"
        else:
            iso = ts.rstrip("Z") + "+00:00"
        return datetime.fromisoformat(iso).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


@dataclass
class Session:
    room: str
    conversation_id: Optional[str] = None
    user_id: Optional[str] = None
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    versions: set = field(default_factory=set)
    error_count: int = 0
    turn_count: int = 0

    @property
    def minutes(self) -> float:
        if self.first_ts and self.last_ts:
            return max(0.0, (self.last_ts - self.first_ts).total_seconds() / 60.0)
        return 0.0


_ERROR_LEVELS = {"error", "crit", "critical"}


def is_error(entry: dict) -> bool:
    """True when the entry is an error/critical line (by GCP severity or the agent's own level)."""
    sev = (entry.get("severity") or "").upper()
    if sev in ("ERROR", "CRITICAL", "ALERT", "EMERGENCY"):
        return True
    return (payload(entry).get("level") or "").lower() in _ERROR_LEVELS


def build_sessions(entries: list[dict]) -> dict[str, Session]:
    """Group entries into sessions keyed by `room`.

    One LiveKit room == one voice session. Session duration is (last log ts − first log ts)
    for the room — the honest proxy for connection minutes, since no explicit duration field
    is emitted. Entries with no room (e.g. worker-level lines, webhook events) are skipped.
    """
    sessions: dict[str, Session] = {}
    for e in entries:
        p = payload(e)
        room = p.get("room")
        if not room:
            continue
        s = sessions.get(room)
        if s is None:
            s = Session(room=room)
            sessions[room] = s
        ts = _parse_ts(e.get("timestamp", ""))
        if ts:
            if s.first_ts is None or ts < s.first_ts:
                s.first_ts = ts
            if s.last_ts is None or ts > s.last_ts:
                s.last_ts = ts
        s.conversation_id = s.conversation_id or p.get("conversationId")
        s.user_id = s.user_id or p.get("userId")
        ver = (p.get("serviceContext") or {}).get("version")
        if ver:
            s.versions.add(ver)
        if is_error(e):
            s.error_count += 1
        if p.get("message") == "Turn latency" and p.get("metric") == "eou_delay_ms":
            s.turn_count += 1  # one eou_delay line per user turn
    return sessions


def peak_concurrency(sessions: list[Session]) -> int:
    """Max number of sessions whose [first_ts, last_ts] intervals overlap simultaneously."""
    events: list[tuple[datetime, int]] = []
    for s in sessions:
        if s.first_ts and s.last_ts:
            events.append((s.first_ts, 1))
            events.append((s.last_ts, -1))
    # Sort by time; on ties process ends (-1) before starts (+1) so touching intervals
    # don't inflate the peak.
    events.sort(key=lambda x: (x[0], x[1]))
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


# ---------------------------------------------------------------------------
# Latency metric extraction
# ---------------------------------------------------------------------------


def latency_values(entries: list[dict], metric: str) -> list[float]:
    """All per-turn value_ms for a given metric (e.g. 'eou_delay_ms')."""
    out: list[float] = []
    for e in entries:
        p = payload(e)
        if p.get("message") == "Turn latency" and p.get("metric") == metric:
            v = p.get("value_ms")
            if isinstance(v, (int, float)):
                out.append(float(v))
    return out


def percentiles(values: list[float]) -> dict[str, float]:
    """p50/p90/p95/p99/max/mean over a value list (empty → all zeros)."""
    if not values:
        return {k: 0.0 for k in ("p50", "p90", "p95", "p99", "max", "mean", "count")}
    s = sorted(values)
    n = len(s)

    def pct(q: float) -> float:
        # Nearest-rank percentile.
        idx = max(0, min(n - 1, int(round(q * (n - 1)))))
        return s[idx]

    return {
        "p50": pct(0.50),
        "p90": pct(0.90),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "max": s[-1],
        "mean": sum(s) / n,
        "count": float(n),
    }


# ---------------------------------------------------------------------------
# PII redaction (mirrors investigate-livekit-logs)
# ---------------------------------------------------------------------------


def short_uid(uid: Optional[str]) -> str:
    """Firebase UID → 8-char prefix + ellipsis. Empty → '(none)'."""
    if not uid:
        return "(none)"
    return uid if len(uid) <= 8 else uid[:8] + "…"


_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def redact(text: str) -> str:
    """Scrub emails from a free-text string before display."""
    if not text:
        return text
    return _EMAIL_RE.sub("<email>", text)
