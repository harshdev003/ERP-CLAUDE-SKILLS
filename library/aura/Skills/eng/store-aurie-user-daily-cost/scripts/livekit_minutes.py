#!/usr/bin/env python3
"""
Self-contained LiveKit per-user voice-minute helper for the daily-cost store.

The Aurie voice agent runs on LiveKit Cloud (not the GKE cluster); its logs are
forwarded to GCP Cloud Logging under a dedicated stream:

    logName   = projects/firebase-auratech16/logs/aurie-voice-agent
    resource  = global

This module reads those logs for a single UTC day and returns {userId: minutes}
plus the total session-minutes for the day — the LiveKit-path allocation key the
store uses to spread Cartesia / Deepgram / LiveKit workspace-daily-total $ across
users by their LiveKit voice-minute share.

The LiveKit agent is MULTI-TTS: each session picks a TTS provider
(Cartesia / ElevenLabs / OpenAI / Inworld) and logs a line
`TTS provider created: <name>` (and an `Initializing <Name> TTS` line). To
attribute per-TTS-provider vendor $ correctly (ElevenLabs-TTS $ only to the
sessions that actually used ElevenLabs TTS, Cartesia $ only to Cartesia
sessions, etc.), this module also buckets minutes per (userId, TTS-provider).
Deepgram STT and LiveKit transport run on EVERY session regardless of the TTS
choice, so those still allocate on overall minutes — see the two return shapes
exposed by `day_minutes_by_user`.

PARTIAL DERIVED COPY (see AINative CLAUDE.md "Canonical source for
`livekit_core.py`"). This is NOT a byte-identical copy of
`analyze-livekit-usage/scripts/livekit_core.py` — only the pieces the store
needs are lifted verbatim and trimmed:
  - the GCP auth ladder (GCP_KEY inline JSON → GOOGLE_APPLICATION_CREDENTIALS → ADC),
  - the forwarded-log query helpers anchored to the LiveKit log stream,
  - the per-room session aggregation + minutes proxy (last_ts − first_ts).
The turn-latency metric extraction, dispatch-failure heuristics, percentiles, and
PII redaction from the canonical are intentionally omitted here. Keep the auth +
query + minutes logic in sync with the canonical when it changes.

Read-only. No writes anywhere.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

try:
    from google.api_core.gapic_v1.client_info import ClientInfo
    from google.cloud import logging as cloud_logging
    from google.oauth2 import service_account
    import google.auth
    from google.api_core import exceptions as gexc
except ImportError as e:
    print(
        f"ERROR: {sys.executable} has no {e.name}. "
        f"Run it with ~/.claude/python-env/bin/python3 (built by shared:setup-ainative).",
        file=sys.stderr,
    )
    sys.exit(2)

# The forwarded log stream for the Aurie voice agent (same anchor as the
# canonical livekit_core.py). This never collides with the k8s_container logs
# the other GCP skills read.
LIVEKIT_LOG_NAME = "projects/firebase-auratech16/logs/aurie-voice-agent"
DEFAULT_PROJECT = "firebase-auratech16"

# Cloud Logging read quota. Every PAGE of an entries.list walk is one read
# request against `logging.googleapis.com/read_requests`
# (ReadRequestsPerMinutePerProject), which is 60/min on firebase-auratech16.
# One busy Aurie day is ~21k forwarded entries = ~22 pages, so a caller looping
# this module over a multi-day window exhausts the minute after ~3 days and the
# rest of the walk raises ResourceExhausted (ENG-1214).
#
# Two defences, because they fail differently: the pacer keeps a well-behaved
# caller under the cap, and the retry recovers when something else on the
# project has already spent it. Both are needed — the pacer only knows about
# reads issued through this module.
READS_PER_MIN = int(os.environ.get("LIVEKIT_LOG_READS_PER_MIN", "50"))
QUOTA_RETRIES = int(os.environ.get("LIVEKIT_LOG_QUOTA_RETRIES", "4"))

# Ceiling on forwarded entries read for one day. A day with all prod voice on the
# LiveKit path runs ~105–135k entries (late Sep 2026), so the ceiling sits an order
# of magnitude above that. It is a runaway guard, not a sampling knob: reaching it
# raises TruncatedReadError rather than returning the newest slice of the day.
MAX_ENTRIES = int(os.environ.get("LIVEKIT_LOG_MAX_ENTRIES", "1000000"))

# Read timestamps (monotonic seconds) inside the trailing 60s window.
_read_times: "deque[float]" = deque()


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


def _await_read_slot() -> None:
    """Block until another entries.list page fits inside the per-minute quota."""
    if READS_PER_MIN <= 0:  # explicit opt-out
        return
    while True:
        now = time.monotonic()
        while _read_times and now - _read_times[0] >= 60.0:
            _read_times.popleft()
        if len(_read_times) < READS_PER_MIN:
            _read_times.append(now)
            return
        time.sleep(max(0.05, 60.0 - (now - _read_times[0])))

# The LiveKit agent's server-side default TTS when a session's provider can't be
# detected from its logs (no `TTS provider created:` line landed in the window).
# Matches the agent's `TTS_DEFAULT_PROVIDER` (LivekitVoiceAgent `src/config/tts.py`,
# and the deployed .env) so undetected sessions don't skew the per-vendor split.
# The agent's default moved cartesia -> elevenlabs in PROD-4079 (merged 2026-07-31), so
# a day before that keeps the old default: a rebook of a July day must not move its
# undetected minutes onto ElevenLabs after the fact.
DEFAULT_TTS_PROVIDER = "elevenlabs"
_LEGACY_DEFAULT_TTS_PROVIDER = "cartesia"
_DEFAULT_TTS_PROVIDER_SINCE = "2026-08-01"


def default_tts_provider(day: Optional[str] = None) -> str:
    """The agent's default TTS on `day` (YYYY-MM-DD); today's default when no day is given."""
    if day and day < _DEFAULT_TTS_PROVIDER_SINCE:
        return _LEGACY_DEFAULT_TTS_PROVIDER
    return DEFAULT_TTS_PROVIDER

# Canonical TTS-provider names the store keys allocation on. Every raw log token
# is normalised into one of these (see `_normalize_tts_provider`).
TTS_PROVIDERS = ("cartesia", "elevenlabs", "openai", "inworld", "fish")

# Bucket key for a session whose provider is known but whose voice id never reached the
# logs. Explicit rather than an empty string or None so a per-voice split can never fold
# "we don't know" into a real voice — and so its minutes stay countable as a gap. Sessions
# logged before the agent emitted `voice_id` as a structured field all land here.
UNKNOWN_VOICE_ID = "(unknown)"

# The one row whose `voice_id` is the RESOLVED voice — read off the live TTS instance after
# defaults applied — as opposed to `Session config resolved`, which carries what the client
# ASKED for (absent when the provider default applies). A mid-session hot-swap re-emits it
# with the new voice; the last one seen wins because the arm changed.
_TTS_CREATED_PREFIX = "tts provider created"
_TTS_SWAPPED_PREFIX = "tts hot-swapped"

# Log lines that name the per-session TTS provider, e.g.
#   "TTS provider created: cartesia (voice: ...)"
#   "Initializing Cartesia TTS with voice: ..."
_TTS_CREATED_RE = re.compile(r"TTS provider created:\s*([A-Za-z0-9_-]+)", re.IGNORECASE)
_TTS_INIT_RE = re.compile(r"Initializing\s+([A-Za-z0-9_-]+)\s+TTS", re.IGNORECASE)


def _normalize_tts_provider(raw: Optional[str]) -> Optional[str]:
    """Map a raw TTS-provider log token to a canonical TTS_PROVIDERS name.

    Handles casing and the common vendor spellings (elevenlabs / 11labs / eleven,
    openai / open_ai). Returns None for an unrecognised token so callers can fall
    back to the configured default rather than silently mis-bucketing.
    """
    if not raw:
        return None
    t = raw.strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    # `cartesia-inference` (the STT key's convention) is still Cartesia's voice; the
    # transport is flagged separately by `_tts_via_inference`.
    t = t.removesuffix("inference")
    if t in ("elevenlabs", "eleven", "11labs", "elevenlab"):
        return "elevenlabs"
    if t in ("openai", "oai"):
        return "openai"
    if t in ("cartesia",):
        return "cartesia"
    if t in ("inworld",):
        return "inworld"
    if t in ("fish", "fishaudio"):
        return "fish"
    return None


# ---------------------------------------------------------------------------
# Auth ladder (verbatim from livekit_core.py — prefer ADC over long-lived keys)
# ---------------------------------------------------------------------------


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
# Query (trimmed from livekit_core.py)
# ---------------------------------------------------------------------------


def _range_filter(start: datetime, end: datetime) -> str:
    return (
        f'timestamp>="{start.strftime("%Y-%m-%dT%H:%M:%SZ")}" '
        f'AND timestamp<"{end.strftime("%Y-%m-%dT%H:%M:%SZ")}"'
    )


def _base_filter(time_clause: str) -> str:
    return " AND ".join([f'logName="{LIVEKIT_LOG_NAME}"', time_clause])


class TruncatedReadError(RuntimeError):
    """The day has more forwarded entries than MAX_ENTRIES allows reading.

    Raised for the same reason as QuotaExhausted: a capped read keeps only the
    newest part of the day, which yields fewer session-minutes and fewer users with
    nothing to distinguish it from a quiet day. Every LiveKit-path vendor allocates
    on these minutes, and LiveKit transport is priced from them.
    """


class QuotaExhausted(RuntimeError):
    """The Cloud Logging read quota stayed exhausted across every retry.

    Raised instead of returning a short read, because a truncated entry list
    yields fewer session-minutes with nothing to distinguish it from a quiet
    day — the failure mode ENG-1214 exists to remove.
    """


def _walk_pages(
    auth: AuthResult,
    filter_str: str,
    page_size: int,
    max_entries: int,
) -> Iterator[dict]:
    """Yield API-repr entries newest-first, taking a read slot per page fetched.

    `Client.list_entries` flattens pages into a single generator and exposes no
    page hook, so slots are taken on `page_size` entry boundaries instead: the
    pager fetches the next page exactly when it exhausts the current one, so
    entry 0, entry `page_size`, entry `2 * page_size` … each sit immediately
    after a request.

    That equivalence holds only while the API honours the requested page size.
    It does here — a day of ~21k entries costs ~20 read requests, which is what
    exhausted the 60/min cap after ~3 days in ENG-1214 — but it is an assumption
    rather than a guarantee, which is why READS_PER_MIN is set below the real cap
    rather than at it.
    """
    count = 0
    for entry in auth.client.list_entries(
        filter_=filter_str, page_size=page_size, order_by="timestamp desc"
    ):
        if count >= max_entries:
            raise TruncatedReadError(
                f"LiveKit log read hit the {max_entries:,}-entry ceiling with entries "
                f"still remaining — the day's minutes would be the newest slice only. "
                f"Raise LIVEKIT_LOG_MAX_ENTRIES and re-run."
            )
        if count % page_size == 0:
            _await_read_slot()
        yield entry.to_api_repr()
        count += 1


def _list_entries(
    auth: AuthResult,
    filter_str: str,
    page_size: int = 1000,
    max_entries: int = MAX_ENTRIES,
) -> Iterator[dict]:
    """Yield Cloud Logging entries (API-repr dicts), fetched newest-first then reversed.

    Reads the whole day, then reverses so callers see ascending order. A day with
    more than `max_entries` entries raises TruncatedReadError instead of yielding a
    newest-only slice.

    A read-quota rejection restarts the whole walk after a backoff rather than
    resuming it: `entries.list` page tokens aren't exposed here, and a partial
    walk would silently undercount. Buffering to a list before yielding anything
    is what makes that safe — a caller never sees entries from an attempt that
    later failed.
    """
    for attempt in range(QUOTA_RETRIES + 1):
        try:
            buffer = list(_walk_pages(auth, filter_str, page_size, max_entries))
        except (gexc.ResourceExhausted, gexc.ServiceUnavailable,
                gexc.DeadlineExceeded) as ex:
            if attempt >= QUOTA_RETRIES:
                raise QuotaExhausted(
                    f"Cloud Logging read quota still exhausted after "
                    f"{QUOTA_RETRIES + 1} attempts ({type(ex).__name__}: {ex}). "
                    f"Reduce concurrent readers, lower LIVEKIT_LOG_READS_PER_MIN, "
                    f"or read stored days from BigQuery instead."
                ) from ex
            # The quota window is one minute, so waiting less than that cannot
            # clear it; back off past the window and retry the full walk.
            wait = min(60.0 * (attempt + 1), 180.0)
            print(f"# livekit: {type(ex).__name__} on the log read — retrying the "
                  f"walk in {wait:.0f}s (attempt {attempt + 1}/{QUOTA_RETRIES})",
                  file=sys.stderr)
            time.sleep(wait)
            continue
        buffer.reverse()
        yield from buffer
        return


def _payload(entry: dict) -> dict:
    return entry.get("jsonPayload") or {}


# ---------------------------------------------------------------------------
# Session aggregation (trimmed from livekit_core.py)
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
class _Session:
    room: str
    user_id: Optional[str] = None
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None
    # Canonical TTS-provider name for this session (from its `TTS provider
    # created:` / `Initializing <Name> TTS` log line). None until detected;
    # callers apply `default_tts_provider(day)` when a session never logged one.
    tts_provider: Optional[str] = None
    # STT transport for the session (`deepgram` direct vs `deepgram-inference`), from the
    # per-turn / session-config lines PROD-5996 added. It decides WHO bills the speech:
    # Deepgram's own invoice, or LiveKit against the plan's credit block.
    stt_provider: Optional[str] = None
    # Seconds of SPEECH the recognizer processed, from the `Session usage totals` line
    # (PROD-5995). Deliberately distinct from `minutes` below: that is the room's
    # wall-clock span, this is the billable STT duration. Two different quantities on
    # two different clocks -- the RATIO between them is not measured, and must not be
    # guessed at: the only observation so far is 1.14x on a single 53-second session,
    # which is far too small a sample to generalise from (a long call carries more
    # silence and should sit further apart).
    # None means "never logged" — a session that predates the field, NOT zero speech.
    stt_seconds: Optional[float] = None
    # The voice the session's TTS ran with, from the structured `voice_id` field. Two
    # experiment arms can differ by voice id alone (PROD-6068 runs two ElevenLabs arms),
    # so the provider on its own cannot tell them apart. None until seen; callers apply
    # UNKNOWN_VOICE_ID when a session never logged one.
    tts_voice_id: Optional[str] = None
    # Characters the session's TTS synthesised, from the `Session usage totals` row the
    # agent emits at session end (LiveKit's UsageCollector `characters_count`). It is the
    # usage a per-character TTS vendor bills on when that vendor has no usage API (Fish
    # exposes only a credit balance). None means "never logged", not zero speech.
    tts_characters: Optional[int] = None
    # Whether `tts_voice_id` came from a resolved-voice row (`TTS provider created` /
    # `TTS hot-swapped`) rather than the requested-voice row. A requested id is only a
    # fallback for sessions logged before the agent emitted the resolved one.
    tts_voice_resolved: bool = False
    # Any row of the session said its TTS ran through LiveKit Inference (see
    # `_tts_via_inference`). Sticky: one such row is enough to make the day's split suspect.
    tts_via_inference: bool = False

    @property
    def minutes(self) -> float:
        if self.first_ts and self.last_ts:
            return max(0.0, (self.last_ts - self.first_ts).total_seconds() / 60.0)
        return 0.0


# A LiveKit Inference model id is `<vendor>/<model>` (`cartesia/sonic-3`); the direct
# plugins log bare model ids (`sonic-3`, `eleven_flash_v2`, `inworld-tts-1`).
_INFERENCE_MODEL_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _tts_via_inference(payload: dict) -> bool:
    """Whether a log row says the session's TTS is served through LiveKit Inference.

    Every Aurie voice runs on a direct vendor plugin today, so this should never fire. If a
    voice moves onto `inference.TTS`, its spend draws on the LiveKit Inference credits —
    which the store prices as Deepgram STT only — while the vendor's own usage API reads
    $0, so the voice vanishes from the per-vendor split. Signals: an `-inference` provider
    token (structured `tts_provider` or the `TTS provider created:` message), or a
    `<vendor>/<model>` `tts_model`.
    """
    provider = payload.get("tts_provider")
    if isinstance(provider, str) and "inference" in provider.lower():
        return True
    m = _TTS_CREATED_RE.search(str(payload.get("message") or ""))
    if m and "inference" in m.group(1).lower():
        return True
    model = payload.get("tts_model")
    return isinstance(model, str) and bool(_INFERENCE_MODEL_RE.match(model.strip()))


def _tts_provider_of(message: str) -> Optional[str]:
    """Return the canonical TTS provider named in a log message, or None.

    Prefers the explicit `TTS provider created: <name>` line; falls back to the
    `Initializing <Name> TTS` line. Both normalise through TTS_PROVIDERS.
    """
    m = _TTS_CREATED_RE.search(message) or _TTS_INIT_RE.search(message)
    return _normalize_tts_provider(m.group(1)) if m else None


def _capture_voice_id(s: "_Session", payload: dict) -> None:
    """Record the session's TTS voice id from a structured `voice_id` field.

    A resolved-voice row (`TTS provider created`, or `TTS hot-swapped` after a mid-session
    change) always wins and the LAST one seen is kept, because a swap changes the voice the
    rest of the session runs with. Any other row carrying `voice_id` (the requested id on
    `Session config resolved`) only fills the gap while no resolved row has been seen, so
    a day logged before the agent emitted the resolved field is still split — on what the
    client asked for, which is the best that window can offer.
    """
    voice = payload.get("voice_id")
    if not voice or not isinstance(voice, str):
        return
    message = str(payload.get("message") or "").strip().lower()
    if message.startswith(_TTS_CREATED_PREFIX) or message.startswith(_TTS_SWAPPED_PREFIX):
        s.tts_voice_id, s.tts_voice_resolved = voice, True
    elif s.tts_voice_id is None:
        s.tts_voice_id = voice


def _build_sessions(entries: list[dict]) -> dict[str, _Session]:
    """Group entries into sessions keyed by `room`.

    One LiveKit room == one voice session. Session duration is (last log ts −
    first log ts) for the room — the honest proxy for connection minutes, since
    no explicit duration field is emitted. Entries with no room are skipped.

    Each session's TTS provider is captured from its `TTS provider created:` /
    `Initializing <Name> TTS` log line (only lines that carry the room are
    associated — early init lines with room=None are skipped here). Sessions with
    no detectable provider stay None and get `default_tts_provider(day)` downstream.
    """
    sessions: dict[str, _Session] = {}
    for e in entries:
        p = _payload(e)
        room = p.get("room")
        if not room:
            continue
        s = sessions.get(room)
        if s is None:
            s = _Session(room=room)
            sessions[room] = s
        ts = _parse_ts(e.get("timestamp", ""))
        if ts:
            if s.first_ts is None or ts < s.first_ts:
                s.first_ts = ts
            if s.last_ts is None or ts > s.last_ts:
                s.last_ts = ts
        s.user_id = s.user_id or p.get("userId")
        if s.tts_provider is None:
            provider = _tts_provider_of(str(p.get("message") or ""))
            if provider:
                s.tts_provider = provider
        # Structured fields, not parsed out of the message — PROD-5996/5995 emit both as
        # `extra=`, so there is nothing to regex and a wording change cannot break this.
        if s.stt_provider is None and p.get("stt_provider"):
            s.stt_provider = str(p["stt_provider"])
        _capture_voice_id(s, p)
        if not s.tts_via_inference and _tts_via_inference(p):
            s.tts_via_inference = True
        if p.get("tts_characters") is not None:
            try:
                s.tts_characters = int(float(p["tts_characters"]))
            except (TypeError, ValueError):
                pass  # a malformed value is "never logged", not zero
        if p.get("stt_seconds") is not None:
            try:
                s.stt_seconds = float(p["stt_seconds"])
            except (TypeError, ValueError):
                pass  # a malformed value is "never logged", not zero
    return sessions


def _bucket_provider(s: "_Session", default: str = DEFAULT_TTS_PROVIDER) -> str:
    """The TTS_PROVIDERS key a session's minutes are booked under.

    A session with no detectable provider defaults to the agent's configured default TTS
    (`default`, see `default_tts_provider`) so it isn't dropped from the split; an
    unrecognised one is folded the same way.
    """
    provider = s.tts_provider or default
    return provider if provider in TTS_PROVIDERS else default


def _aggregate_sessions(sessions: dict, default_provider: str = DEFAULT_TTS_PROVIDER
                        ) -> tuple[dict[str, float], float, dict]:
    """Roll sessions up into the per-user / per-provider / per-voice minute buckets.

    Pure (no I/O) so the split can be pinned by tests, and so both the per-provider and
    the per-voice views are built from ONE pass over the same sessions — which is what
    guarantees the voice split sums back to the provider total to the last minute.

    Returns `(by_user, total_minutes, buckets)` where `buckets` carries:
      - `by_user_tts[provider][userId]` and `minutes_by_provider[provider]` — the
        allocation shape the store has always consumed (unchanged);
      - `by_user_tts_voice[provider][voice_id][userId]` and
        `minutes_by_provider_voice[provider][voice_id]` — the same minutes, further split by
        the voice the session ran with. A session whose provider is known but whose voice
        never reached the logs books under `UNKNOWN_VOICE_ID`, never under a real voice;
      - `tts_characters_by_provider` with `tts_sessions_by_provider` /
        `tts_sessions_measured_by_provider` — synthesised characters per provider from the
        `Session usage totals` row, for a vendor billed per character with no usage API;
      - `sessions_without_provider`, `sessions_without_voice_id` and
        `sessions_with_requested_voice_only` — the gap counts a reader needs before trusting
        a per-voice figure (a day where the last is the majority predates the resolved field).
    """
    by_user: dict[str, float] = {}
    total = 0.0
    # Per-provider minute buckets. `by_user_tts[provider]` maps userId → minutes
    # for sessions that used that TTS provider; `minutes_by_provider[provider]` is
    # the day's total (incl. userId-less sessions) — the allocation denominator
    # for that provider's workspace-daily $.
    by_user_tts: dict[str, dict[str, float]] = {p: {} for p in TTS_PROVIDERS}
    minutes_by_provider: dict[str, float] = {p: 0.0 for p in TTS_PROVIDERS}
    by_user_tts_voice: dict[str, dict[str, dict[str, float]]] = {p: {} for p in TTS_PROVIDERS}
    minutes_by_provider_voice: dict[str, dict[str, float]] = {p: {} for p in TTS_PROVIDERS}
    # Synthesised characters per provider, and how many of that provider's sessions
    # actually logged the figure — a total with a low measured count is a partial day,
    # not a quiet one, and the caller has to be able to tell.
    tts_characters_by_provider: dict[str, int] = {p: 0 for p in TTS_PROVIDERS}
    tts_sessions_by_provider: dict[str, int] = {p: 0 for p in TTS_PROVIDERS}
    tts_sessions_measured_by_provider: dict[str, int] = {p: 0 for p in TTS_PROVIDERS}
    for s in sessions.values():
        m = s.minutes
        total += m
        if s.user_id:
            by_user[s.user_id] = by_user.get(s.user_id, 0.0) + m
        provider = _bucket_provider(s, default_provider)
        minutes_by_provider[provider] += m
        tts_sessions_by_provider[provider] += 1
        if s.tts_characters is not None:
            tts_characters_by_provider[provider] += s.tts_characters
            tts_sessions_measured_by_provider[provider] += 1
        voice = s.tts_voice_id or UNKNOWN_VOICE_ID
        per_voice = minutes_by_provider_voice[provider]
        per_voice[voice] = per_voice.get(voice, 0.0) + m
        if s.user_id:
            bucket = by_user_tts[provider]
            bucket[s.user_id] = bucket.get(s.user_id, 0.0) + m
            voice_bucket = by_user_tts_voice[provider].setdefault(voice, {})
            voice_bucket[s.user_id] = voice_bucket.get(s.user_id, 0.0) + m
    buckets = {
        "by_user_tts": by_user_tts,
        "minutes_by_provider": minutes_by_provider,
        "by_user_tts_voice": by_user_tts_voice,
        "minutes_by_provider_voice": minutes_by_provider_voice,
        "tts_characters_by_provider": tts_characters_by_provider,
        "tts_sessions_by_provider": tts_sessions_by_provider,
        "tts_sessions_measured_by_provider": tts_sessions_measured_by_provider,
        "sessions_without_provider": sum(1 for s in sessions.values() if s.tts_provider is None),
        "default_tts_provider": default_provider,
        "sessions_without_voice_id": sum(1 for s in sessions.values() if s.tts_voice_id is None),
        # Sessions whose TTS ran through LiveKit Inference, by bucketed provider. Expected
        # empty; the store warns on any entry (see `inference_tts_note`).
        "tts_inference_sessions_by_provider": {
            p: n for p in TTS_PROVIDERS
            if (n := sum(1 for s in sessions.values()
                         if s.tts_via_inference and _bucket_provider(s, default_provider) == p))
        },
        "sessions_with_requested_voice_only": sum(
            1 for s in sessions.values() if s.tts_voice_id is not None and not s.tts_voice_resolved
        ),
    }
    return by_user, total, buckets


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _stt_totals(sessions: dict) -> dict:
    """Aggregate per-session STT into the meta block: minutes, measured count, transport.

    STT is metered by SPEECH seconds, on a different clock from room minutes — two
    different quantities, whose ratio is NOT measured (one 53-second session gave 1.14x;
    far too small to generalise) — and is billed by whoever the transport routed it
    through, so the split by transport is the field that decides which vendor's invoice
    is authoritative.

    A session that never logged a duration is EXCLUDED from the minutes rather than folded
    in as zero, and the measured count is reported alongside. A day where the field is
    simply absent must read as unmeasured, not as a day with no speech: those two are
    indistinguishable once a zero is written, and the zero is the one that persists.

    **A stated transport is recorded even when the duration is not** — a `deepgram-inference`
    key at 0.0 minutes, not an absent key. Dropping it would make an unmeasured Inference
    day indistinguishable from a day with no Inference at all, so the store would fall back
    to Deepgram's usage API and book the well-formed $0 this whole change exists to remove.
    Tag, don't drop: `stt_sessions_with_transport` is the count that keeps the tag legible.
    """
    seconds = 0.0
    measured = 0
    stated = 0
    by_transport: dict[str, float] = {}
    for s in sessions.values():
        if s.stt_provider:
            stated += 1
            by_transport.setdefault(s.stt_provider, 0.0)
        if s.stt_seconds is None:
            continue
        seconds += s.stt_seconds
        measured += 1
        t = s.stt_provider or "unknown"
        by_transport[t] = by_transport.get(t, 0.0) + s.stt_seconds
    return {
        "stt_minutes": round(seconds / 60.0, 4),
        "stt_sessions_measured": measured,
        "stt_sessions_with_transport": stated,
        "stt_minutes_by_transport": {
            t: round(v / 60.0, 4) for t, v in sorted(by_transport.items())
        },
    }


def day_minutes_by_user(
    day: str,
    project_override: Optional[str] = None,
    max_entries: int = MAX_ENTRIES,
) -> tuple[dict[str, float], float, dict]:
    """Return ({userId: minutes}, total_minutes, meta) for one UTC calendar day.

    - `day` is `YYYY-MM-DD`, interpreted as a full UTC day [00:00, next-00:00).
    - Sessions with no `userId` still count toward `total_minutes` (so the
      LiveKit workspace-total allocation denominator is the real total), but are
      excluded from the per-user map (nothing to attribute them to).
    - `meta` carries auth source/principal + fetched-entry count for logging, plus
      a per-TTS-provider minute breakdown (`by_user_tts`, `minutes_by_provider`) so
      the store can allocate ElevenLabs-TTS $ and Cartesia $ to only the sessions
      that used each provider, and the same minutes split one level further by voice
      (`by_user_tts_voice`, `minutes_by_provider_voice`) so two arms of a voice
      experiment on one provider stay distinguishable — see `_aggregate_sessions`.
      Deepgram STT and LiveKit transport still allocate on the overall `by_user` / `total`.

    Raises AuthError if credentials can't be resolved (caller decides whether to
    treat that as fatal or as a graceful $0 LiveKit contribution).
    """
    start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    auth = get_client(project_override=project_override)
    filter_str = _base_filter(_range_filter(start, end))
    entries = list(_list_entries(auth, filter_str, max_entries=max_entries))
    sessions = _build_sessions(entries)
    by_user, total, buckets = _aggregate_sessions(sessions, default_tts_provider(day))
    stt = _stt_totals(sessions)
    meta = {
        "auth_source": auth.source,
        "auth_principal": auth.principal,
        "project": auth.project,
        "entries": len(entries),
        "sessions": len(sessions),
        **buckets,
        # STT (PROD-5995). `stt_sessions_measured` vs `sessions` is what tells a caller
        # whether `stt_minutes` is the day's real total or a partial one — the field
        # only exists from the 2026-09 agent release onward, so an older day has none.
        **stt,
    }
    return by_user, total, meta
